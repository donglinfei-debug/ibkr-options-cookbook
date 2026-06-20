"""
Order Lifecycle Management — Submission, Tracking & Modification

Provides the scaffolding for managing order lifecycles via the IB API:

- ``OrderTracker``: order-status data model and callback handling
- ``OrderModificationGuard``: prevents duplicate concurrent modifications
- ``LegacyOrderCleaner``: cancels stale orders on startup
- ``OrderManager``: lightweight EWrapper/EClient that ties the above together

This module covers the *framework* layer. Strategy-specific order
construction (e.g. building a 4-leg Iron Condor combo), custom price
adjustment schedules, and monitoring loops live in application code
that imports these building blocks.
"""

import time
import logging
import threading
from collections import defaultdict
from datetime import datetime
from typing import Dict, Optional, Set

from pytz import timezone
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.common import TickerId, OrderId

EST = timezone("US/Eastern")
logger = logging.getLogger(__name__)


# ======================================================================
# Order status model
# ======================================================================

class OrderStatus:
    """
    Lightweight snapshot of an order's current state.

    Populated by the ``orderStatus`` IB callback.
    """

    __slots__ = ("status", "filled", "remaining", "avg_fill_price", "last_update")

    def __init__(self):
        self.status: str = ""
        self.filled: float = 0.0
        self.remaining: float = 0.0
        self.avg_fill_price: Optional[float] = None
        self.last_update: Optional[datetime] = None

    def update(self, status: str, filled: float, remaining: float,
               avg_fill_price: float) -> None:
        self.status = status
        self.filled = filled
        self.remaining = remaining
        self.avg_fill_price = avg_fill_price
        self.last_update = datetime.now(EST)

    @property
    def is_complete(self) -> bool:
        """True if the order is fully filled (remaining == 0)."""
        return self.remaining <= 0

    def __repr__(self) -> str:
        return (
            f"OrderStatus(status={self.status}, filled={self.filled}, "
            f"remaining={self.remaining})"
        )


# ======================================================================
# Order modification guard
# ======================================================================

class OrderModificationGuard:
    """
    Prevents concurrent modification attempts on the same order.

    The IB API processes modifications asynchronously; sending a second
    modify request before the first is acknowledged can lead to race
    conditions or rejected prices.  This guard serializes modifications
    per order ID.

    Parameters
    ----------
    cooldown_seconds:
        Minimum interval (in seconds) between modifications of the same
        order.  Defaults to 180 s (3 minutes).
    """

    def __init__(self, cooldown_seconds: float = 180.0):
        self._busy: Set[int] = set()
        self._cooldown = cooldown_seconds
        self._last_modified: Dict[int, float] = {}

    def can_modify(self, order_id: int, current_status: str) -> bool:
        """Return True if the order is eligible for modification."""
        if order_id in self._busy:
            return False
        if current_status not in ("Submitted",):
            return False
        last_ts = self._last_modified.get(order_id, 0.0)
        if time.time() - last_ts < self._cooldown:
            return False
        return True

    def start_modification(self, order_id: int) -> None:
        """Mark order as being modified (blocks concurrent attempts)."""
        self._busy.add(order_id)
        logger.debug("Modification guard locked for order %d", order_id)

    def finish_modification(self, order_id: int) -> None:
        """Unlock the order after modification completes or fails."""
        self._busy.discard(order_id)
        self._last_modified[order_id] = time.time()
        logger.debug("Modification guard released for order %d", order_id)


# ======================================================================
# Legacy order cleaner
# ======================================================================

class LegacyOrderCleaner:
    """
    Cancel all active orders on startup.

    When a trading application restarts after an unclean shutdown, stale
    orders (e.g. GTC limit orders from a previous session) may still be
    alive in TWS.  This cleaner issues a global cancel to reset the order
    state before the new session begins.

    Usage::

        cleaner = LegacyOrderCleaner(app)
        cleaner.clean()
    """

    def __init__(self, app: EClient):
        self._app = app
        self._done = False

    def clean(self) -> None:
        """Issue a global cancel (idempotent)."""
        if self._done:
            return
        logger.info("Cleaning legacy orders (global cancel) ...")
        try:
            self._app.reqGlobalCancel()
            time.sleep(2)
            logger.info("Legacy orders cancelled")
        except Exception as exc:
            logger.error("Failed to cancel legacy orders: %s", exc)
        self._done = True


# ======================================================================
# OrderManager (EWrapper / EClient)
# ======================================================================

class OrderManager(EWrapper, EClient):
    """
    Lightweight wrapper around EWrapper/EClient for order lifecycle management.

    Provides:
    - Order-status tracking (``orderStatus`` callback → internal dict)
    - Active-order registry
    - Modification guard integration
    - Legacy order cleanup on connect

    Usage::

        mgr = OrderManager()
        mgr.connect("127.0.0.1", 7497, clientId=1)
        thread = threading.Thread(target=mgr.run, daemon=True)
        thread.start()
        # ... wait for nextValidId ...

        mgr.placeOrder(order_id, contract, order)
        # Status updates arrive via mgr.order_status[...]
    """

    def __init__(self):
        EClient.__init__(self, self)

        self.nextorder_id: Optional[int] = None

        # --- active orders & status registry ---
        self.active_orders: Dict[int, dict] = {}
        self.order_status: Dict[int, OrderStatus] = defaultdict(OrderStatus)
        self._order_lock = threading.Lock()

        # --- modification guard ---
        self.modification_guard = OrderModificationGuard()

        # --- signal flags ---
        self.disconnected: bool = False
        self.terminate_monitor: bool = False

        # --- adjustment history (for debugging / post-trade analysis) ---
        self.adjustment_history: list = []

        self.logger = logging.getLogger("OrderManager")

    # ------------------------------------------------------------------
    # IB callbacks
    # ------------------------------------------------------------------

    def nextValidId(self, order_id: int) -> None:
        super().nextValidId(order_id)
        self.nextorder_id = order_id
        self.logger.info("nextValidId = %d", order_id)

        # Clean up legacy orders on first connect
        cleaner = LegacyOrderCleaner(self)
        cleaner.clean()

    def error(self, req_id: TickerId, error_code: int, error_string: str,
              advanced_order_reject_json: str = "") -> None:
        _ignored = {2103, 2104, 2105, 2106, 2107, 2157, 2158, 1100, 1101, 1102}

        if error_code == 502:
            self.disconnected = True
            self.logger.error("TWS disconnected! %s", error_string)
        elif error_code not in _ignored:
            self.logger.error("Error (req=%s, code=%s): %s", req_id, error_code, error_string)
        else:
            self.logger.debug("System (req=%s, code=%s): %s", req_id, error_code, error_string)

    def orderStatus(self, order_id: OrderId, status: str, filled: float,
                    remaining: float, avg_fill_price: float, perm_id: int,
                    parent_id: int, last_fill_price: float, client_id: int,
                    why_held: str, mkt_cap_price: float) -> None:
        super().orderStatus(order_id, status, filled, remaining, avg_fill_price,
                            perm_id, parent_id, last_fill_price, client_id,
                            why_held, mkt_cap_price)

        with self._order_lock:
            # Update structured status record
            self.order_status[order_id].update(status, filled, remaining, avg_fill_price)

            # Update active-order registry
            info = self.active_orders.get(order_id)
            if info:
                info["last_status_change"] = datetime.now(EST)

                if status == "Submitted" and "activation_time" not in info:
                    info["activation_time"] = datetime.now(EST)
                    self.logger.info("[%s] Order activated", info.get("name", order_id))

                if filled > 0 and filled != info.get("last_filled", 0):
                    info["last_filled"] = filled
                    info["has_traded"] = True
                    self.logger.info("[%s] Partial fill: %s units", info.get("name", order_id), filled)

            self.logger.info(
                "Order %d: status=%s filled=%s remaining=%s",
                order_id, status, filled, remaining,
            )

    # ------------------------------------------------------------------
    # Order registration
    # ------------------------------------------------------------------

    def register_order(self, order_id: int, name: str, contract, price: float,
                       quantity: int, **extra) -> None:
        """
        Register an order in the active-orders registry.

        Call this immediately after ``placeOrder()``.
        """
        now = datetime.now(EST)
        with self._order_lock:
            self.active_orders[order_id] = {
                "name": name,
                "original_price": price,
                "current_price": price,
                "contract": contract,
                "adjustments": 0,
                "last_action_time": now,
                "last_status_change": now,
                "activation_time": None,
                "quantity": quantity,
                "server_ack_time": now,
                "has_traded": False,
                "last_filled": 0,
                **extra,
            }

    # ------------------------------------------------------------------
    # Order modification (framework-level)
    # ------------------------------------------------------------------

    def modify_price(self, order_id: int, new_price: float, **order_kwargs) -> bool:
        """
        Modify an order's limit price.

        This is a *framework* method.  Concrete price-adjustment logic
        (e.g. stepping by 0.05 every 3 minutes) belongs in the strategy
        layer that calls this method.

        Returns True if the modification request was sent successfully.
        """
        with self._order_lock:
            info = self.active_orders.get(order_id)
            if info is None:
                self.logger.error("Order %d not found in registry", order_id)
                return False

            old_price = info["current_price"]
            contract = info["contract"]
            qty = info["quantity"]

        # Build modified order
        from ibapi.order import Order
        mod = Order()
        mod.orderId = order_id
        mod.action = order_kwargs.pop("action", "BUY")
        mod.orderType = order_kwargs.pop("order_type", "LMT")
        mod.totalQuantity = qty
        mod.lmtPrice = new_price
        mod.tif = order_kwargs.pop("tif", "GTC")
        mod.outsideRth = order_kwargs.pop("outside_rth", True)
        mod.transmit = True

        # Pass through any extra order fields
        for k, v in order_kwargs.items():
            setattr(mod, k, v)

        try:
            self.placeOrder(mod.orderId, contract, mod)
        except Exception as exc:
            self.logger.error("Failed to modify order %d: %s", order_id, exc)
            return False

        # Update local registry
        with self._order_lock:
            info = self.active_orders.get(order_id)
            if info:
                info["current_price"] = new_price
                info["adjustments"] += 1
                info["last_action_time"] = datetime.now(EST)

        self.adjustment_history.append({
            "time": datetime.now(EST),
            "order_id": order_id,
            "old_price": old_price,
            "new_price": new_price,
        })

        self.logger.info("Order %d price modified: %.4f → %.4f", order_id, old_price, new_price)
        return True

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def is_order_complete(self, order_id: int, required_qty: float = 1.0) -> bool:
        """Check whether the order has been fully filled."""
        status = self.order_status.get(order_id)
        if status is None:
            return False
        return status.filled >= required_qty

    def safe_disconnect(self) -> None:
        """Disconnect cleanly if still connected."""
        if not self.disconnected:
            try:
                self.disconnect()
                self.logger.info("Disconnected from TWS")
            except Exception:
                pass
