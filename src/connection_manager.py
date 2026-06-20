"""
IBKR API Connection Manager — Singleton Pattern

A thread-safe singleton that manages the connection to TWS/IB Gateway.
Provides fixed ClientID allocation to avoid conflicts between modules,
automatic reconnection with configurable retry, and connection status monitoring.

Usage:
    manager = ConnectionManager.get_instance()
    success = manager.connect(port=7497, module_name="my_module")
    if success:
        order_id = manager.get_next_order_id()
"""

import threading
import time
import logging
from typing import Optional, Dict, Set

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.common import TickerId


logger = logging.getLogger(__name__)


class ConnectionManager(EWrapper, EClient):
    """
    Singleton connection manager for TWS/IB Gateway API.

    Features:
    - Thread-safe singleton pattern with double-checked locking
    - Fixed ClientID allocation per module (avoids random ID conflicts)
    - Automatic reconnection with configurable retry count and interval
    - Connection status monitoring and health check
    - Thread-safe next-order-id management
    """

    _instance: Optional["ConnectionManager"] = None
    _lock: threading.Lock = threading.Lock()

    # Fixed ClientID allocation strategy.
    # Each module gets a dedicated ClientID to avoid conflicts
    # when multiple components share the same API connection.
    # Adjust these values for your own module layout.
    CLIENT_ID_MAP: Dict[str, int] = {
        "module_a": 101,
        "module_b": 102,
        "module_c": 103,
        "module_d": 104,
        "default": 100,
    }

    # ======================================================================
    # Singleton
    # ======================================================================

    def __new__(cls, *args, **kwargs) -> "ConnectionManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:  # double-checked locking
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized") and self._initialized:
            return  # prevent re-initialization

        EClient.__init__(self, self)

        # --- connection state ---
        self.connected: bool = False
        self.nextorder_id: Optional[int] = None
        self.port: int = 7497          # default: paper trading port
        self.current_client_id: Optional[int] = None
        self._last_client_id: Optional[int] = None

        # --- thread safety ---
        self._state_lock: threading.Lock = threading.Lock()

        # --- activity tracking ---
        self.active_modules: Set[str] = set()

        self._initialized = True
        logger.info("ConnectionManager initialized (singleton)")

    # ======================================================================
    # ClientID resolution
    # ======================================================================

    def get_client_id(self, module_name: str) -> int:
        """
        Resolve a fixed ClientID for the given module.

        Using fixed IDs instead of random ones prevents the common
        "TWS API client ID already in use" error when multiple modules
        connect from the same process.
        """
        cid = self.CLIENT_ID_MAP.get(module_name, self.CLIENT_ID_MAP["default"])
        logger.info("Resolved ClientID %d for module '%s'", cid, module_name)
        return cid

    # ======================================================================
    # Connection
    # ======================================================================

    def connect(
        self,
        port: Optional[int] = None,
        module_name: str = "default",
        max_retries: int = 3,
        retry_interval: float = 5.0,
        timeout: float = 10.0,
    ) -> bool:
        """
        Connect to TWS/IB Gateway.

        Parameters
        ----------
        port:
            TWS port (7496 = live, 7497 = paper). Falls back to previous value.
        module_name:
            Logical module name for ClientID allocation.
        max_retries:
            Number of connection attempts before giving up.
        retry_interval:
            Seconds to wait between retries.
        timeout:
            Seconds to wait for the nextValidId callback.

        Returns True on success, False if all retries exhausted.
        """
        with self._state_lock:
            if port is not None:
                self.port = port

            cid = self.get_client_id(module_name)
            self.current_client_id = cid

            # Already connected with the same ClientID → no-op
            if self.connected and self._last_client_id == cid:
                logger.info(
                    "Already connected on port %d (ClientID %d) — skipping",
                    self.port,
                    cid,
                )
                return True

            # ClientID changed → disconnect first
            if self.connected:
                logger.info(
                    "ClientID changed (%s → %s), reconnecting",
                    self._last_client_id,
                    cid,
                )
                self._disconnect_internal()

            # Retry loop
            for attempt in range(1, max_retries + 1):
                try:
                    logger.info(
                        "Connecting to TWS on port %d (module=%s, ClientID=%d) "
                        "attempt %d/%d",
                        self.port,
                        module_name,
                        cid,
                        attempt,
                        max_retries,
                    )
                    super().connect("127.0.0.1", self.port, clientId=cid)

                    # Start the API message-processing thread
                    api_thread = threading.Thread(target=self.run, daemon=True)
                    api_thread.start()

                    # Wait for nextValidId as confirmation
                    t0 = time.time()
                    while self.nextorder_id is None:
                        if time.time() - t0 > timeout:
                            raise TimeoutError(
                                f"Connection timed out after {timeout}s"
                                " (no nextValidId received)"
                            )
                        time.sleep(0.1)

                    self.connected = True
                    self._last_client_id = cid
                    self.active_modules.add(module_name)
                    logger.info(
                        "Connected to TWS on port %d (ClientID %d)",
                        self.port,
                        cid,
                    )
                    return True

                except Exception as exc:
                    logger.error(
                        "Connection attempt %d/%d failed: %s",
                        attempt,
                        max_retries,
                        exc,
                    )
                    if attempt < max_retries:
                        logger.info("Retrying in %.0f seconds ...", retry_interval)
                        time.sleep(retry_interval)

            logger.error("All %d connection attempts failed", max_retries)
            return False

    # ======================================================================
    # Disconnection
    # ======================================================================

    def _disconnect_internal(self) -> None:
        """Internal disconnect (caller must hold _state_lock)."""
        if self.connected:
            try:
                self.disconnect()
            except Exception as exc:
                logger.warning("Error during disconnect: %s", exc)
            finally:
                self.connected = False
                self.nextorder_id = None
                logger.info("Disconnected")

    def disconnect_from_tws(self) -> None:
        """Disconnect and clear all module registrations."""
        with self._state_lock:
            self._disconnect_internal()
            self.active_modules.clear()
            logger.info("Disconnected from TWS port %d", self.port)

    # ======================================================================
    # Health check
    # ======================================================================

    def check_connection(self, auto_reconnect: bool = True, module_name: str = "default") -> bool:
        """
        Check whether the connection is still alive.

        If the connection is lost and *auto_reconnect* is True, attempt
        to re-establish it automatically.
        """
        with self._state_lock:
            if not self.connected:
                logger.warning("Connection lost")
                if auto_reconnect:
                    return self.connect(module_name=module_name)
                return False
            return True

    # ======================================================================
    # Order ID management
    # ======================================================================

    def get_next_order_id(self) -> int:
        """
        Return the next valid order ID (thread-safe).

        Raises RuntimeError if not connected.
        """
        with self._state_lock:
            if self.nextorder_id is None:
                raise RuntimeError("Not connected — no order ID available")
            oid = self.nextorder_id
            self.nextorder_id += 1
            return oid

    # ======================================================================
    # Connection status snapshot
    # ======================================================================

    def get_connection_status(self) -> Dict[str, object]:
        """Return a snapshot of the current connection state."""
        with self._state_lock:
            return {
                "connected": self.connected,
                "port": self.port,
                "client_id": self.current_client_id,
                "next_order_id": self.nextorder_id,
                "active_modules": list(self.active_modules),
            }

    # ======================================================================
    # IB API callbacks
    # ======================================================================

    def nextValidId(self, order_id: int) -> None:
        """IB callback — fires when the connection is fully established."""
        super().nextValidId(order_id)
        self.nextorder_id = order_id
        logger.debug("Received nextValidId: %d", order_id)

    def error(self, req_id: TickerId, error_code: int, error_string: str, advanced_order_reject_json: str = "") -> None:
        """
        IB error callback.

        502 / 504 → marks the connection as lost.
        2104, 2106, 2158 → system info, logged at debug level.
        Everything else → logged as error.
        """
        if error_code in (502, 504):
            with self._state_lock:
                self.connected = False
            logger.error("Connection lost (code %d): %s", error_code, error_string)

        elif error_code not in (2104, 2106, 2158):
            logger.error("API error (req=%s, code=%d): %s", req_id, error_code, error_string)

    def connectionClosed(self) -> None:
        """IB callback — the connection was closed remotely."""
        with self._state_lock:
            self.connected = False
        logger.warning("TWS connection closed remotely")
