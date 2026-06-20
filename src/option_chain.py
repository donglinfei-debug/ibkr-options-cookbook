"""
Option Chain Helper — IBKR Options Contract & Chain Data

Utilities for building option contracts, resolving conIds, and fetching
option chain data (bid / ask / delta) in batches from the IB API.

Designed for SPXW but the patterns apply to any option symbol.
"""

import time
import logging
import threading
from typing import List, Dict, Optional, Tuple

import pandas as pd
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract

logger = logging.getLogger(__name__)


# ======================================================================
# Contract builders
# ======================================================================

def make_option_contract(
    symbol: str = "SPX",
    last_trade_date: str = "",
    strike: float = 0.0,
    right: str = "C",
    exchange: str = "CBOE",
    trading_class: str = "SPXW",
) -> Contract:
    """
    Build an IB option Contract.

    Parameters
    ----------
    last_trade_date:
        Expiry in YYYYMMDD or YYMMDD format (6-digit inputs are
        automatically converted to 8-digit).
    right:
        "C" for Call, "P" for Put.
    """
    if last_trade_date and len(last_trade_date) == 6:
        last_trade_date = "20" + last_trade_date

    contract = Contract()
    contract.symbol = symbol
    contract.secType = "OPT"
    contract.exchange = exchange
    contract.currency = "USD"
    contract.lastTradeDateOrContractMonth = last_trade_date
    contract.strike = strike
    contract.right = right[0].upper()           # "PUT" → "P", "CALL" → "C"
    if trading_class:
        contract.tradingClass = trading_class
    return contract


def make_combo_contract(legs: List[Tuple[int, int, str]], symbol: str = "SPX") -> Contract:
    """
    Build a BAG (bundle / combo) contract from a list of leg tuples.

    Each tuple: ``(conId, ratio, action)`` where action is ``"BUY"`` or ``"SELL"``.
    """
    from ibapi.contract import ComboLeg

    contract = Contract()
    contract.symbol = symbol
    contract.secType = "BAG"
    contract.currency = "USD"
    contract.exchange = "SMART"

    combo_legs = []
    for con_id, ratio, action in legs:
        leg = ComboLeg()
        leg.conId = con_id
        leg.ratio = ratio
        leg.action = action
        leg.exchange = "SMART"
        combo_legs.append(leg)
    contract.comboLegs = combo_legs
    return contract


# ======================================================================
# conId resolver
# ======================================================================

class ContractDetailResolver(EWrapper, EClient):
    """
    One-shot helper to resolve a contract's conId.

    Usage::

        resolver = ContractDetailResolver()
        con_id = resolver.resolve(contract)
    """

    def __init__(self):
        EClient.__init__(self, self)
        self.details: Optional[Contract] = None
        self._event = threading.Event()
        self._logger = logging.getLogger("ContractDetailResolver")

    def contractDetails(self, req_id: int, contract_details) -> None:
        self.details = contract_details.contract
        self._event.set()

    def error(self, req_id: int, error_code: int, error_string: str, advanced_order_reject_json: str = ""):
        if error_code not in (2104, 2106, 2158):
            self._logger.warning("Error (req=%s, code=%s): %s", req_id, error_code, error_string)

    def resolve(self, contract: Contract, timeout: float = 5.0) -> Optional[int]:
        """
        Connect to TWS, resolve the conId, and disconnect.

        Returns the conId or ``None`` on failure.
        """
        import random

        try:
            client_id = random.randint(1, 1000)
            self.connect("127.0.0.1", 7497, clientId=client_id)
            thread = threading.Thread(target=self.run, daemon=True)
            thread.start()
            time.sleep(2)

            if not self.isConnected():
                raise ConnectionError("Not connected")

            self.details = None
            self._event.clear()
            req_id = 1
            self.reqContractDetails(req_id, contract)

            if self._event.wait(timeout):
                if self.details:
                    con_id = self.details.conId
                    logger.info("Resolved conId %d for %s", con_id, contract)
                    return con_id
                else:
                    logger.error("Contract details returned empty")
            else:
                logger.error("Contract detail request timed out (%s s)", timeout)

        except Exception as exc:
            logger.error("resolve failed: %s", exc)
        finally:
            try:
                self.disconnect()
            except Exception:
                pass
        return None


# ======================================================================
# OptionChainFetcher
# ======================================================================

class OptionChainFetcher(EWrapper, EClient):
    """
    Fetch option chain data (bid, ask, delta) for a list of strikes.

    Designed for SPXW but generic enough for any option chain.

    Typical flow::

        fetcher = OptionChainFetcher()
        chain = fetcher.fetch(
            expiry="20250919",
            strikes=[4500, 4550, ..., 5500],
            underlying_price=5480.0,
            port=7497,
        )
        # chain is a DataFrame with columns: strike, type, delta, bid, ask, mid
    """

    def __init__(self):
        EClient.__init__(self, self)

        self._data: Dict[int, dict] = {}        # req_id → record
        self._completed: set = set()
        self._data_lock = threading.Lock()
        self._next_req_id = 1
        self._req_count = 0

        self.timeout: float = 30.0
        self.max_retries: int = 3
        self.retry_interval: float = 5.0
        self.batch_size: int = 50

        self.logger = logging.getLogger("OptionChainFetcher")

    # ------------------------------------------------------------------
    # IB callbacks
    # ------------------------------------------------------------------

    def error(self, req_id: int, error_code: int, error_string: str, advanced_order_reject_json: str = ""):
        if error_code not in (2104, 2106, 2158) and not (2000 <= error_code <= 2199):
            self.logger.error("Error [%s]: code=%s msg=%s", req_id, error_code, error_string)
        with self._data_lock:
            if req_id in self._data:
                self._data[req_id]["completed"] = True
            self._completed.add(req_id)

    def tickPrice(self, req_id: int, tick_type: int, price: float, attrib):
        with self._data_lock:
            record = self._data.get(req_id)
            if record is None:
                return
            if tick_type == 1:        # BID
                record["bid"] = price
            elif tick_type == 2:      # ASK
                record["ask"] = price
            self._check_completion(req_id)

    def tickOptionComputation(self, req_id: int, tick_type: int, tick_attrib: object,
                              implied_vol: float, delta: float, opt_price: float,
                              pv_dividend: float, gamma: float, vega: float,
                              theta: float, und_price: float):
        if tick_type == 13 and req_id in self._data:      # 13 = model/ Greeks
            with self._data_lock:
                self._data[req_id]["delta"] = delta
                self._check_completion(req_id)

    def _check_completion(self, req_id: int):
        record = self._data.get(req_id)
        if record and None not in (record["bid"], record["ask"], record["delta"]):
            record["completed"] = True
            self._completed.add(req_id)
            self.cancelMktData(req_id)

    # ------------------------------------------------------------------
    # Core fetch
    # ------------------------------------------------------------------

    def fetch(
        self,
        expiry: str,
        strikes: List[float],
        underlying_price: float,
        port: int = 7497,
        is_monitoring: bool = False,
    ) -> pd.DataFrame:
        """
        Fetch option chain data for the given expiry and strikes.

        Parameters
        ----------
        expiry:
            Expiry date in YYYYMMDD format.
        strikes:
            List of strike prices to query.
        underlying_price:
            Current underlying price (used for OTM filtering).
        port:
            TWS port.
        is_monitoring:
            ``True`` reduces retries and timeout for faster refresh cycles.

        Returns
        -------
        DataFrame with columns (strike, type, delta, bid, ask, mid).
        Empty DataFrame on failure.
        """
        max_attempts = self.max_retries if not is_monitoring else 2
        self.timeout = 15 if is_monitoring else 30

        for attempt in range(max_attempts):
            try:
                return self._fetch_once(expiry, strikes, underlying_price, port, is_monitoring, attempt)
            except Exception as exc:
                self.logger.error("Fetch attempt %d/%d failed: %s", attempt + 1, max_attempts, exc)
                if attempt < max_attempts - 1:
                    time.sleep(self.retry_interval)

        self.logger.error("All %d fetch attempts exhausted", max_attempts)
        return pd.DataFrame()

    def _fetch_once(self, expiry, strikes, underlying_price, port, is_monitoring, attempt):
        # Reset state
        self._data.clear()
        self._completed.clear()
        self._req_count = 0
        self._next_req_id = 1

        # Determine OTM strikes
        otm_puts = [s for s in strikes if s < underlying_price]
        otm_calls = [s for s in strikes if s > underlying_price]
        all_strikes = otm_puts + otm_calls

        if not all_strikes:
            self.logger.warning("No OTM strikes found for price %.2f", underlying_price)
            return pd.DataFrame()

        self.logger.info(
            "Fetching chain: %d puts, %d calls (batch size %d, attempt %d, monitoring=%s)",
            len(otm_puts), len(otm_calls), self.batch_size, attempt + 1, is_monitoring,
        )

        # Connect
        if not is_monitoring or not self.isConnected():
            if self.isConnected():
                self.disconnect()
                time.sleep(1)
            self.connect("127.0.0.1", port, clientId=2)
            thread = threading.Thread(target=self.run, daemon=True)
            thread.start()
            time.sleep(2)
            if not self.isConnected():
                raise ConnectionError("Connection failed")

        # Batch requests
        for i in range(0, len(all_strikes), self.batch_size):
            if not self.isConnected():
                raise ConnectionError("Connection lost mid-fetch")
            batch = all_strikes[i:i + self.batch_size]
            for strike in batch:
                opt_type = "P" if strike in otm_puts else "C"
                contract = make_option_contract(
                    last_trade_date=expiry, strike=strike, right=opt_type,
                )
                self._request_data(contract, strike, opt_type, is_monitoring)
            time.sleep(2 if is_monitoring else 3)

        self.logger.info("Sent %d requests, waiting for completion ...", self._req_count)
        self._wait_for_completion()

        # Build DataFrame
        return self._build_dataframe(underlying_price)

    def _request_data(self, contract: Contract, strike: float, opt_type: str, is_monitoring: bool):
        req_id = self._next_req_id
        self._next_req_id += 1
        self._req_count += 1
        with self._data_lock:
            self._data[req_id] = {
                "strike": strike,
                "type": opt_type,
                "bid": None,
                "ask": None,
                "delta": None,
                "completed": False,
            }
        self.reqMktData(
            reqId=req_id,
            contract=contract,
            genericTickList="",
            snapshot=not is_monitoring,
            regulatorySnapshot=False,
            mktDataOptions=[],
        )

    def _wait_for_completion(self):
        t0 = time.time()
        while len(self._completed) < self._req_count:
            if time.time() - t0 > self.timeout:
                incomplete = [
                    rid for rid, rec in self._data.items()
                    if not rec["completed"]
                ]
                self.logger.warning(
                    "Timeout: %d/%d incomplete — cancelling",
                    len(incomplete), self._req_count,
                )
                for rid in incomplete:
                    self.cancelMktData(rid)
                    self._completed.add(rid)
                break
            time.sleep(0.1)

    def _build_dataframe(self, underlying_price: float) -> pd.DataFrame:
        rows = []
        for rec in self._data.values():
            if rec["bid"] is not None and rec["ask"] is not None:
                if rec["bid"] > 0 and rec["ask"] > 0:
                    mid = (rec["bid"] + rec["ask"]) / 2
                else:
                    mid = None
            else:
                mid = None

            # Only include OTM options
            if rec["type"] == "P" and rec["strike"] >= underlying_price:
                continue
            if rec["type"] == "C" and rec["strike"] <= underlying_price:
                continue

            rows.append({
                "strike": rec["strike"],
                "type": "PUT" if rec["type"] == "P" else "CALL",
                "delta": rec["delta"],
                "bid": rec["bid"],
                "ask": rec["ask"],
                "mid": mid,
            })

        df = pd.DataFrame(rows)
        if df.empty:
            return df

        df.sort_values(["strike", "type"], inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df

    # ------------------------------------------------------------------
    # Delta-based strike filtering (example methodology)
    # ------------------------------------------------------------------

    @staticmethod
    def find_strikes_by_delta(
        chain: pd.DataFrame,
        underlying_price: float,
        put_delta_target: float = -0.05,
        call_delta_target: float = 0.05,
    ) -> Tuple[Optional[float], Optional[float]]:
        """
        Find the OTM put and call strikes whose delta is closest to the
        given targets.

        This is the method used by Iron Condor strategies: select strikes
        with a target delta (e.g. 0.05 delta Call and -0.05 delta Put)
        to define the short legs.

        Returns
        -------
        (put_strike, call_strike)  or  (None, None) if not found.
        """
        puts = chain[
            (chain["type"] == "PUT")
            & (chain["strike"] < underlying_price)
            & (chain["delta"].notna())
            & (chain["delta"] >= put_delta_target)
        ]
        calls = chain[
            (chain["type"] == "CALL")
            & (chain["strike"] > underlying_price)
            & (chain["delta"].notna())
            & (chain["delta"] <= call_delta_target)
        ]

        put_strike = None
        if not puts.empty:
            puts = puts.sort_values("strike")
            puts["delta_diff"] = (puts["delta"] - put_delta_target).abs()
            put_strike = puts.sort_values("delta_diff").iloc[0]["strike"]

        call_strike = None
        if not calls.empty:
            calls = calls.sort_values("strike", ascending=False)
            calls["delta_diff"] = (calls["delta"] - call_delta_target).abs()
            call_strike = calls.sort_values("delta_diff").iloc[0]["strike"]

        return put_strike, call_strike
