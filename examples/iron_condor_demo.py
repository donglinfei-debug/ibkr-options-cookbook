"""
Iron Condor Demo — Illustrative Walkthrough

This script demonstrates how the modules in ``src/`` fit together to
implement an Iron Condor trade flow.

It is NOT executable — it is a *narrative demo* that shows the sequence
of calls and data flow.  Real strategy logic (delta thresholds, credit
ranges, price adjustment values) lives in the strategy layer, which is
outside the scope of this public repository.
"""

# ======================================================================
# Phase 1 — Connection & Data
# ======================================================================

from connection_manager import ConnectionManager
from market_data import MarketDataFetcher, generate_strike_prices
from option_chain import OptionChainFetcher, ContractDetailResolver
from option_chain import make_option_contract, make_combo_contract

# 1. Connect to TWS
# manager = ConnectionManager.get_instance()
# manager.connect(port=7497, module_name="demo")
# → singleton, ClientID = 101 (example), waits for nextValidId

# 2. Fetch SPX snapshot
# fetcher = MarketDataFetcher()
# spx_price = fetcher.fetch_snapshot(port=7497)
# → connects → reqMktData(snapshot=True) → receives LAST → disconnects

# 3. Generate strike range
# strikes = generate_strike_prices(underlying_price=spx_price)
# → ±3% / 4% bands, 5-point steps → ~75 strikes

# 4. Fetch option chain
# chain = OptionChainFetcher().fetch(
#     expiry="20250919",
#     strikes=strikes,
#     underlying_price=spx_price,
#     port=7497,
# )
# → batch 50 reqs at a time, collect bid/ask/delta → DataFrame


# ======================================================================
# Phase 2 — Strategy Construction (user-provided logic)
# ======================================================================

# From the chain DataFrame, apply a delta-based filter:
#
#   from option_chain import OptionChainFetcher
#   put_strike, call_strike = OptionChainFetcher.find_strikes_by_delta(
#       chain, underlying_price=spx_price,
#       put_delta_target=-0.05,   # <-- YOUR TARGET
#       call_delta_target=0.05,   # <-- YOUR TARGET
#   )
#
# This yields 4 legs:
#   Sell Put @ put_strike     |  Buy Put  @ put_strike - 5
#   Sell Call @ call_strike   |  Buy Call @ call_strike + 5

# Resolve conIds for each leg:
# resolver = ContractDetailResolver()
# legs = []
# for action, strike, right in [(..."SELL"..., ...), ...]:
#     contract = make_option_contract(
#         last_trade_date="20250919", strike=strike, right=right,
#     )
#     con_id = resolver.resolve(contract)
#     legs.append((con_id, 1, action))

# Build combo contract:
# combo = make_combo_contract(legs=legs)


# ======================================================================
# Phase 3 — Order Execution
# ======================================================================

from order_lifecycle import OrderManager
from ibapi.order import Order

# mgr = OrderManager()
# mgr.connect("127.0.0.1", 7497, clientId=1)
# # ... run thread, wait for nextValidId ...

# order = Order()
# order.action = "BUY"
# order.orderType = "LMT"
# order.totalQuantity = 1
# order.lmtPrice = -0.30          # example initial price
# order.tif = "GTC"
# order.outsideRth = True

# order_id = mgr.nextorder_id
# mgr.nextorder_id += 1
# mgr.placeOrder(order_id, combo, order)
# mgr.register_order(order_id, "Iron Condor", combo, -0.30, 1)

# → monitor loop (user-implemented):
#   - check orderStatus every 1 s
#   - price adjustment every N minutes (with predictive jump logic)
#   - timeout at 16:10 ET


# ======================================================================
# Phase 4 — Recording & Notification
# ======================================================================

from trade_recorder import TradeRecorder
from notifier import WebhookNotifier

# recorder = TradeRecorder(file_path="./trades/journal.xlsx")
# recorder.record({
#     "Date": "2025-09-03",
#     "Time": "15:45:00",
#     "Symbol": "SPX",
#     "Strategy": "Iron Condor",
#     "Sell Put": 5395,
#     "Buy Put": 5390,
#     "Sell Call": 5585,
#     "Buy Call": 5590,
#     "Price": -1.20,
# })

# import os
# notifier = WebhookNotifier(
#     webhook_url=os.environ["DINGTALK_URL"],
#     secret=os.environ["DINGTALK_SECRET"],
# )
# notifier.send_trade_update(
#     title="Iron Condor Filled",
#     execution_time="2025-09-03 15:45:00 ET",
#     details="Sell Put 5395 | Buy Put 5390\nSell Call 5585 | Buy Call 5590",
# )

# ======================================================================
# End
# ======================================================================
