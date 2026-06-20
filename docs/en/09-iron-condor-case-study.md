# Iron Condor: End-to-End Case Study

> This chapter ties together the concepts from the previous 8 chapters into a complete practical walkthrough.

---

## Background

- **Strategy**: SPX Iron Condor
- **Current SPX Price**: 5480
- **Expiration**: 2025-09-19 (16 days to expiry)
- **Trading Mode**: Paper account (port 7497)
- **Objective**: Open one Iron Condor spread and collect premium

---

## Phase 1: Connection & Data Preparation

### Step 1: Establish Connection

```python
from connection_manager import ConnectionManager

manager = ConnectionManager.get_instance()
success = manager.connect(port=7497, module_name="trading_session")
# → ClientID assigned = 101 (example)
# → Wait for nextValidId → connection ready
```

At this point `manager.connected = True` and `manager.nextorder_id = 1`.

### Step 2: Fetch SPX Latest Price

```python
from market_data import MarketDataFetcher

fetcher = MarketDataFetcher()
spx_price = fetcher.fetch_snapshot(port=7497)
# → Connect to TWS → request SPX snapshot → receive LAST = 5480.25 → disconnect

print(f"SPX Current Price: {spx_price}")
```

### Step 3: Calculate Strike Range

```python
from market_data import generate_strike_prices

strikes, lower, upper, ls, us = generate_strike_prices(
    underlying_price=spx_price,   # 5480.25
    upper_pct=3.0,                # 3% above
    lower_pct=4.0,                # 4% below
    step=5.0,                     # 5-point increments
)

print(f"Strike range: {ls} ~ {us}")
print(f"Total strikes: {len(strikes)}")
# → 5265 ~ 5640, approximately 75 strikes
```

### Step 4: Fetch Option Chain Data

```python
from option_chain import OptionChainFetcher

chain_fetcher = OptionChainFetcher()
chain = chain_fetcher.fetch(
    expiry="20250919",
    strikes=strikes,
    underlying_price=spx_price,
    port=7497,
)

print(f"Retrieved {len(chain)} OTM contracts")
# → ~43 PUTs + ~32 CALLs
```

Sample DataFrame output:

```
  strike  type    delta    bid    ask    mid
0  5265.0  PUT  -0.032   1.20   1.35  1.275
1  5270.0  PUT  -0.041   1.45   1.60  1.525
...            ...        ...    ...    ...
42 5475.0  PUT  -0.138   5.80   6.10  5.950
43 5485.0  CALL  0.048   2.10   2.30  2.200
...            ...        ...    ...    ...
74 5640.0  CALL  0.038   0.80   0.95  0.875
```

---

## Phase 2: Strategy Construction

### Step 5: Filter Strikes by Delta

Use the delta-threshold method to determine the 4 legs of the Iron Condor:

```python
from option_chain import OptionChainFetcher

put_strike, call_strike = OptionChainFetcher.find_strikes_by_delta(
    chain=chain,
    underlying_price=spx_price,
    put_delta_target=-0.05,     # target Put delta
    call_delta_target=0.05,     # target Call delta
)

# Assuming the result:
# put_strike  = 5395 (delta of this strike ≈ -0.05)
# call_strike = 5585 (delta of this strike ≈  0.05)
```

### Step 6: Determine the 4 Legs

```
Iron Condor:

Sell PUT @ 5395 (Short Put)
Buy  PUT @ 5390 (Long Put, protective leg, 5-point spread)

Sell CALL @ 5585 (Short Call)
Buy  CALL @ 5590 (Long Call, protective leg, 5-point spread)

Max Profit: Premium collected
Max Loss: Spread width (5 points = $500) - Premium collected
Breakeven: 5395 - Premium and 5585 + Premium
```

Look up the mid prices for these 4 strikes from the option chain:

```
Put Side:
  Sell Put @ 5395 → mid = 2.850
  Buy  Put @ 5390 → mid = 2.700
  Put credit = 2.700 - 2.850 = -0.150

Call Side:
  Sell Call @ 5585 → mid = 1.950
  Buy  Call @ 5590 → mid = 1.800
  Call credit = 1.800 - 1.950 = -0.150

Total credit = (-0.150) + (-0.150) = -0.300
```

### Step 7: Build Combo Contract & Resolve conIds

```python
from option_chain import make_option_contract, ContractDetailResolver

resolver = ContractDetailResolver()

# Resolve conId for each leg
legs = [
    ("SELL", 5395, "P"),
    ("BUY",  5390, "P"),
    ("SELL", 5585, "C"),
    ("BUY",  5590, "C"),
]

con_ids = []
for action, strike, right in legs:
    contract = make_option_contract(
        last_trade_date="20250919",
        strike=strike,
        right=right,
    )
    con_id = resolver.resolve(contract)
    con_ids.append((con_id, 1, action))
```

---

## Phase 3: Order Execution

### Step 8: Submit Limit Order

```python
from order_lifecycle import OrderManager
from option_chain import make_combo_contract

mgr = OrderManager()
mgr.connect("127.0.0.1", 7497, clientId=1)
thread = threading.Thread(target=mgr.run, daemon=True)
thread.start()
# Wait for nextValidId ...

# Create BAG combo contract
combo_contract = make_combo_contract(legs=con_ids)

# Calculate initial price (example value)
initial_price = -0.30  # aligned to minimum tick

# Create order
from ibapi.order import Order
order = Order()
order.action = "BUY"
order.orderType = "LMT"
order.totalQuantity = 1
order.lmtPrice = initial_price
order.tif = "GTC"
order.outsideRth = True

# Submit
order_id = mgr.nextorder_id
mgr.nextorder_id += 1
mgr.placeOrder(order_id, combo_contract, order)
mgr.register_order(order_id, "Iron Condor", combo_contract, initial_price, 1)

print(f"Order submitted, ID: {order_id}, Price: {initial_price}")
```

### Step 9: Monitor for Fill

The order-monitoring loop checks the following conditions:

| Check | Frequency | Action |
|:------|:----------|:-------|
| Fully filled? | Every second | Yes → record fill price, exit loop |
| Partially filled? | Every second | Yes → continue waiting for remainder |
| Need to improve price? | Every 3 minutes | Yes → improve by 0.05, check pre-emptive conditions |
| Deadline reached? | Every second | Yes → return failure |

### Step 10: Fill

Simulated fill at 15:45:

```python
# orderStatus callback triggered:
# status="Filled", filled=1, remaining=0, avgFillPrice=-1.20

fill_price = mgr.order_status[order_id].avg_fill_price
print(f"Iron Condor filled! Price: {fill_price}")
```

---

## Phase 4: Recording & Notification

### Step 11: Record to Excel

```python
from trade_recorder import TradeRecorder

recorder = TradeRecorder(
    file_path="./trades/journal.xlsx",
    headers=[
        "Date", "Time", "Symbol", "Strategy",
        "Sell Put", "Buy Put", "Sell Call", "Buy Call",
        "Fill Price", "Quantity", "Premium",
    ],
)
recorder.record({
    "Date": "2025-09-03",
    "Time": "15:45:00",
    "Symbol": "SPX",
    "Strategy": "Iron Condor",
    "Sell Put": 5395,
    "Buy Put": 5390,
    "Sell Call": 5585,
    "Buy Call": 5590,
    "Fill Price": -1.20,
    "Quantity": 1,
    "Premium": -1.20,
})
```

### Step 12: Send Notification

```python
from notifier import WebhookNotifier

import os
notifier = WebhookNotifier(
    webhook_url=os.environ["DINGTALK_URL"],
    secret=os.environ["DINGTALK_SECRET"],
)
notifier.send_trade_update(
    title="Iron Condor Filled",
    execution_time="2025-09-03 15:45:00 ET",
    details=(
        "Symbol: SPX\n"
        "Strategy: Iron Condor\n"
        "Sell Put: 5395 | Buy Put: 5390\n"
        "Sell Call: 5585 | Buy Call: 5590\n"
        "Fill Price: -1.20\n"
        "Quantity: 1"
    ),
)
```

---

## Full Timeline

```
T + 0s     Connect to TWS (ConnectionManager.connect)
T + 3s     SPX snapshot received (5480.25)
T + 3s     Strike range computed (5265 ~ 5640)
T + 4s     Begin option chain data request
T + 18s    Option chain data returned (75 OTM contracts)
T + 19s    Delta filter applied → strikes determined (5395/5390/5585/5590)
T + 24s    All 4 leg conIds resolved
T + 25s    Order manager connected
T + 26s    BAG contract created + limit order submitted
T + 27s    Order enters monitoring loop
...        Price improvement (if needed)
T + 900s   Order filled @ -1.20 (~15 minutes later)
T + 900s   Excel recording complete
T + 901s   DingTalk notification pushed
```

The actual process takes about 25–30 seconds to open the position, plus fill time depending on market liquidity.

---

## Production Deployment Checklist

Before deploying the above flow to a live environment, verify the following:

### Connection & Security

- [ ] TWS/IB Gateway configured with a fixed port and added to the trusted IP list
- [ ] Paper and live account ports separated via `.env` file
- [ ] Environment variables set (Webhook URL / Secret / Account ID)
- [ ] `.env` file added to `.gitignore`

### Data & Strategy

- [ ] SPX/SPXW market data subscription activated (TWS account settings)
- [ ] Strategy parameters (delta threshold / credit range / execution time) confirmed
- [ ] Minimum tick and step size aligned

### Order Execution

- [ ] Order deadline logic confirmed (intraday / after-hours / next day)
- [ ] Price improvement parameters (step / interval / floor / special prices) configured
- [ ] LegacyOrderCleaner confirmed safe for clearing orphaned orders

### Risk Control

- [ ] Debounce parameters (threshold / window) tuned for the strategy
- [ ] Rate Limiter parameters (max_ops / window) tuned for the strategy
- [ ] Timeout Guard timeout value confirmed
- [ ] Emergency stop mechanism for manual intervention ready

### Monitoring & Recovery

- [ ] Logging system configured (file rotation / level / format)
- [ ] Auto-restart script ready for program crashes
- [ ] Daily trade review process established
- [ ] Regular backup strategy for Excel trade journal confirmed
