"""
Market Data Fetcher — IBKR Real-Time & Snapshot Data

Fetches real-time market data for SPX (S&P 500 Index) via the IB API.
Supports both snapshot mode (one-shot) and streaming mode (continuous monitoring).

Key features:
- SPX last-price and close-price retrieval
- US Eastern trading-hours detection (Mon-Fri 09:30-16:00 ET)
- Strike-price range generation (configurable percentage bands + fixed step)
- Snapshot / streaming mode switch via a single flag
"""

import math
import time
import logging
import threading
from datetime import datetime
from typing import Optional, Callable

from pytz import timezone
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract

# ---------------------------------------------------------------------------
# TickType helper — safe import across ibapi versions
# ---------------------------------------------------------------------------
try:
    from ibapi.common import TickTypeEnum
except ImportError:
    try:
        from ibapi.ticktype import TickTypeEnum
    except ImportError:

        class _TickTypeEnum:
            @staticmethod
            def to_str(tick_type: int) -> str:
                mapping = {
                    0: "BID_SIZE", 1: "BID", 2: "ASK", 3: "ASK_SIZE",
                    4: "LAST", 5: "LAST_SIZE", 6: "HIGH", 7: "LOW",
                    8: "VOLUME", 9: "CLOSE", 14: "OPEN",
                }
                return mapping.get(tick_type, f"TICK_{tick_type}")

        TickTypeEnum = _TickTypeEnum


EST = timezone("US/Eastern")
logger = logging.getLogger(__name__)


# ======================================================================
# Public helpers
# ======================================================================

def is_trading_hours(dt: Optional[datetime] = None) -> bool:
    """
    Check whether *dt* (or now in US/Eastern) falls within regular
    SPX trading hours: Monday–Friday, 09:30–16:00 ET.

    Returns
    -------
    bool
    """
    now = dt if dt is not None else datetime.now(EST)
    if now.weekday() >= 5:          # Saturday = 5, Sunday = 6
        return False
    open_t = now.replace(hour=9, minute=30, second=0, microsecond=0)
    close_t = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return open_t <= now <= close_t


def generate_strike_prices(
    underlying_price: float,
    upper_pct: float = 3.0,
    lower_pct: float = 4.0,
    step: float = 5.0,
) -> tuple:
    """
    Generate a list of strike prices around the current underlying price.

    Parameters
    ----------
    underlying_price:
        Current SPX price.
    upper_pct:
        Percentage above the price to include.
    lower_pct:
        Percentage below the price to include.
    step:
        Strike increment (SPXW standard is 5 points).

    Returns
    -------
    (strikes, lower_bound, upper_bound, lower_strike, upper_strike)
    """
    upper_bound = underlying_price * (1 + upper_pct / 100.0)
    lower_bound = underlying_price * (1 - lower_pct / 100.0)

    lower_strike = math.ceil(lower_bound / step) * step
    upper_strike = math.floor(upper_bound / step) * step

    strikes = []
    cur = lower_strike
    while cur <= upper_strike:
        strikes.append(cur)
        cur += step

    return strikes, lower_bound, upper_bound, lower_strike, upper_strike


def create_spx_contract() -> Contract:
    """Create an IB Contract object for the SPX index."""
    contract = Contract()
    contract.symbol = "SPX"
    contract.secType = "IND"
    contract.currency = "USD"
    contract.exchange = "CBOE"
    contract.conId = 416904       # standard SPX conId
    logger.debug("Created SPX contract: %s", contract)
    return contract


# ======================================================================
# MarketDataFetcher
# ======================================================================

class MarketDataFetcher(EWrapper, EClient):
    """
    Retrieve SPX market data via the IB API.

    Two usage modes:

    1. **Snapshot** (default) — connect, fetch one data point, disconnect.
       Suitable for pre-trade price checks.

    2. **Streaming** — keep the connection open and receive continuous
       tick updates.  Suitable for real-time strategy monitoring.

    Parameters
    ----------
    on_price_update:
        Optional callback invoked on every tick update.
        Signature: ``callable(price: float)``
    """

    def __init__(self, on_price_update: Optional[Callable[[float], None]] = None):
        EClient.__init__(self, self)

        # --- price state ---
        self.latest_price: Optional[float] = None
        self.spx_last_price: Optional[float] = None
        self.spx_close_price: Optional[float] = None
        self.data_received: bool = False
        self.price_updated: bool = False

        # --- connection state ---
        self.connected: bool = False
        self.is_monitoring: bool = False

        # --- optional callback ---
        self._on_price_update = on_price_update

        logger.info("MarketDataFetcher initialised")

    # ------------------------------------------------------------------
    # IB API callbacks
    # ------------------------------------------------------------------

    def error(self, req_id: int, error_code: int, error_string: str, advanced_order_reject_json: str = ""):
        """IB error callback — filter non-critical system messages."""
        if error_code in (2104, 2106, 2158):
            logger.debug("System message: req=%s code=%s msg=%s", req_id, error_code, error_string)
            return

        if error_code in (10197, 200, 502, 504):
            logger.error("Critical error: req=%s code=%s msg=%s", req_id, error_code, error_string)
        else:
            logger.warning("Warning: req=%s code=%s msg=%s", req_id, error_code, error_string)

    def nextValidId(self, order_id: int):
        """Connection established — start requesting data."""
        self.connected = True
        logger.info("MarketDataFetcher connected, requesting data ...")
        self._request_spx_data()

    def tickPrice(self, req_id: int, tick_type: int, price: float, attrib: object):
        """Handle incoming tick prices — only interested in LAST and CLOSE."""
        if tick_type not in (4, 9):          # 4 = LAST, 9 = CLOSE
            return

        tick_name = TickTypeEnum.to_str(tick_type)
        logger.debug("Tick: %s = %.2f", tick_name, price)

        self.latest_price = price
        self.price_updated = True

        # Fire user callback if one was registered
        if self._on_price_update:
            self._on_price_update(price)

        if tick_type == 4:                   # LAST
            if self.spx_last_price is None:
                self.spx_last_price = price
                self.data_received = True
                if not self.is_monitoring:
                    self.cancelMktData(1)
                    logger.info("SPX last price = %.2f (snapshot, unsubscribed)", price)

        elif tick_type == 9:                 # CLOSE
            if self.spx_last_price is None:  # only use close if no last price
                self.spx_close_price = price
                self.data_received = True
                if not self.is_monitoring:
                    self.cancelMktData(1)
                    logger.info("SPX close price = %.2f (snapshot, unsubscribed)", price)

    def connectionClosed(self):
        """Remote disconnection."""
        self.connected = False
        self.price_updated = False
        logger.info("MarketDataFetcher connection closed")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _request_spx_data(self, is_monitoring: bool = False):
        """
        Request SPX market data.

        Parameters
        ----------
        is_monitoring:
            ``True`` → streaming mode (keeps receiving ticks).
            ``False`` → snapshot mode (one-shot).
        """
        self.is_monitoring = is_monitoring
        contract = create_spx_contract()
        self.reqMarketDataType(3)            # 3 = real-time (delayed if no sub)

        if is_trading_hours():
            logger.info("Trading hours — requesting real-time data ...")
        else:
            logger.info("Outside trading hours — attempting to get closing price ...")

        # 233 = shortable ticks (meaningless for indices but harmless);
        # only used in streaming mode to avoid snapshot parameter conflicts.
        generic_ticks = "233" if is_monitoring else ""

        self.reqMktData(
            reqId=1,
            contract=contract,
            genericTickList=generic_ticks,
            snapshot=not is_monitoring,
            regulatorySnapshot=False,
            mktDataOptions=[],
        )
        logger.info("SPX data request sent (monitoring=%s)", is_monitoring)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_current_price(self) -> Optional[float]:
        """
        Return the best available SPX price.

        Priority: streaming latest → snapshot last → close.
        """
        if self.latest_price is not None:
            return self.latest_price
        if self.spx_last_price is not None:
            return self.spx_last_price
        if self.spx_close_price is not None:
            return self.spx_close_price
        return None

    def fetch_snapshot(self, port: int = 7497, timeout: float = 60.0) -> Optional[float]:
        """
        Convenience method: connect → fetch one price → disconnect.

        Parameters
        ----------
        port:
            TWS port (7496 = live, 7497 = paper).
        timeout:
            Max seconds to wait for a data point.

        Returns
        -------
        The SPX price, or ``None`` on failure.
        """
        import random

        try:
            if self.isConnected():
                self.disconnect()
                time.sleep(1)

            self.connected = False
            self.data_received = False

            client_id = random.randint(1, 1000)
            self.connect("127.0.0.1", port, clientId=client_id)
            mode = "live" if port == 7496 else "paper"
            logger.info("Connecting TWS (%s) port %d, clientId %d", mode, port, client_id)

            thread = threading.Thread(target=self.run, daemon=True)
            thread.start()
            time.sleep(3)

            if not self.connected:
                raise ConnectionError("Connection failed after 3 s")

            # Wait for data
            t0 = time.time()
            while time.time() - t0 < timeout:
                if self.data_received:
                    price = self.get_current_price()
                    if price is not None:
                        logger.info("SPX snapshot = %.2f", price)
                        return price
                logger.debug("Waiting for data ... %.0f / %.0f s", time.time() - t0, timeout)
                time.sleep(0.5)

            logger.warning("Timed out (%.0f s) — no SPX data received", timeout)
            return None

        except Exception as exc:
            logger.error("fetch_snapshot failed: %s", exc)
            return None

        finally:
            try:
                if self.isConnected():
                    self.disconnect()
                    logger.info("Disconnected after snapshot")
            except Exception:
                pass
            self.connected = False
            self.data_received = False

    def start_streaming(self, port: int = 7497) -> Optional[float]:
        """
        Open a persistent streaming connection.

        The connection stays alive and calls ``on_price_update`` (if set)
        on every tick.  Call ``disconnect()`` to stop.

        Returns the first price received, or ``None`` on failure.
        """
        import random

        try:
            if self.isConnected():
                self.disconnect()
                time.sleep(1)

            self.connected = False
            self.data_received = False
            self.latest_price = None
            self.price_updated = False

            client_id = random.randint(1, 1000)
            self.connect("127.0.0.1", port, clientId=client_id)
            logger.info("Streaming connection, clientId %d", client_id)

            thread = threading.Thread(target=self.run, daemon=True)
            thread.start()
            time.sleep(3)

            if not self.connected:
                raise ConnectionError("Connection failed after 3 s")

            self._request_spx_data(is_monitoring=True)

            t0 = time.time()
            while time.time() - t0 < 10:
                if self.price_updated:
                    logger.info("Streaming started, current price = %.2f", self.latest_price)
                    return self.latest_price
                time.sleep(0.5)

            raise TimeoutError("No initial price within 10 s")

        except Exception as exc:
            logger.error("start_streaming failed: %s", exc)
            try:
                if self.isConnected():
                    self.disconnect()
            except Exception:
                pass
            return None
