# Order Execution and Lifecycle

## Combo Contracts: BAG

An Iron Condor consists of 4 legs: Sell Put, Buy Put, Sell Call, Buy Call. In the IB API, multi-leg orders are implemented through **BAG** (Basket) contracts.

```python
from ibapi.contract import ComboLeg

def make_iron_condor_contract(legs_con_ids):
    """
    Construct an Iron Condor combo contract.
    legs_con_ids: [(conId, ratio, action), ...]
    """
    contract = Contract()
    contract.symbol = "SPX"
    contract.secType = "BAG"          # Combo contract type
    contract.currency = "USD"
    contract.exchange = "SMART"

    combo_legs = []
    for con_id, ratio, action in legs_con_ids:
        leg = ComboLeg()
        leg.conId = con_id            # conId for each leg
        leg.ratio = ratio             # Ratio (typically 1:1)
        leg.action = action           # "BUY" or "SELL"
        leg.exchange = "SMART"
        combo_legs.append(leg)

    contract.comboLegs = combo_legs
    return contract
```

The four legs correspond to the following conId designations:

| Leg | Action | Description |
|:----|:-------|:------------|
| Leg 1 | SELL | Sell PUT @ lower strike |
| Leg 2 | BUY | Buy PUT @ even lower strike (protective leg) |
| Leg 3 | SELL | Sell CALL @ higher strike |
| Leg 4 | BUY | Buy CALL @ even higher strike (protective leg) |

---

## Key Order Object Parameters

```python
order = Order()
order.action = "BUY"              # BAG contract buy/sell direction
order.orderType = "LMT"           # Limit order
order.totalQuantity = quantity    # Number of contract sets
order.lmtPrice = price            # Limit price
order.tif = "GTC"                 # Good-Till-Cancelled
order.outsideRth = True           # Allow trading outside regular hours
```

### Parameter Reference

| Parameter | Choices | Description |
|:----------|:--------|:------------|
| `orderType` | LMT / MKT / STP | Limit, Market, or Stop order. Automated trading almost exclusively uses LMT |
| `tif` | **GTC** / DAY / IOC | GTC = order remains active until filled or manually cancelled; DAY = good for the current session only |
| `outsideRth` | True / False | Whether to allow execution outside regular trading hours. SPX options can still move after hours, so enabling this is recommended |
| `action` | BUY / SELL | Direction for the BAG contract, related to whether the strategy produces a net credit or debit |

---

## Order State Machine

Once submitted, an order transitions through the following states:

```
      ┌──────────┐
      │ Submitted│  ← Order submitted to TWS, awaiting entry into the market
      └────┬─────┘
           │
           ▼
  ┌───────────────┐
  │ PreSubmitted  │  ← Only appears outside regular trading hours; waiting for market open
  └───────┬───────┘
           │
           ▼
     ┌─────────┐
     │ Working │  ← Order entered the market, awaiting a counterparty
     └────┬────┘
          │
      ┌───┴───┐
      ▼       ▼
  ┌───────┐ ┌──────────┐
  │Filled │ │Cancelled │
  └───────┘ └──────────┘
```

In the IB API, these states are delivered via the `orderStatus()` callback:

```python
def orderStatus(self, orderId, status, filled, remaining,
                avgFillPrice, ...):
    # status values: "Submitted", "PreSubmitted",
    #                "Working", "Filled", "Cancelled"
    self.order_status[orderId] = {
        "status": status,
        "filled": filled,
        "remaining": remaining,
        "avgFillPrice": avgFillPrice,
    }
```

### Partial Fills

Orders may be partially filled:

```
Time T1: status=Working,   filled=2,  remaining=1  ← 2 contracts partially filled
Time T2: status=Working,   filled=3,  remaining=0  ← Remaining 1 contract filled
Time T3: status=Filled,    filled=3,  remaining=0  ← Status updated to fully filled
```

When partial fills occur:
- `filled` increases incrementally but stays below `totalQuantity`
- You can wait for the remainder to fill on its own without adjusting the price
- If it remains unfilled for too long, consider modifying the price (see the next chapter)

---

## Order Information Registration

After submitting an order, register it immediately in the active orders list:

```python
self.active_orders[order_id] = {
    "name": "Iron Condor",
    "original_price": -1.25,        # Initial price
    "current_price": -1.25,         # Current price
    "contract": contract,           # Contract object
    "adjustments": 0,               # Number of adjustments made
    "activation_time": None,        # Order activation time
    "has_traded": False,            # Whether any fills have occurred
    "last_filled": 0,               # Last fill quantity
}
```

This registry is the foundation for all order monitoring. Subsequent price modifications, fill confirmations, and state tracking all depend on this data structure.

---

## LegacyOrderCleaner: Startup Cleanup

After an abnormal program exit and restart, the previous session's GTC orders may still be lingering in TWS. If new orders are submitted without cleaning them up, you could face:

- Both old and new orders active in the market simultaneously
- Duplicate positions (intending 1 set, ending up with 2)
- Capital usage exceeding expectations

Solution:

```python
class LegacyOrderCleaner:
    def clean(self):
        self.app.reqGlobalCancel()    # Cancel all orders globally
        time.sleep(2)                 # Wait for TWS to process
        self.app.active_orders.clear()
        self.app.order_status.clear()
```

This runs automatically after each connection is established (triggered in the `nextValidId` callback).

---

## Application Scenario: Iron Condor Example

The execution phase of an Iron Condor strategy can be summarized as:

```
① Obtain strategy parameters (4 strike prices, contract quantity, target price)
② Look up conId for each leg (4 reqContractDetails calls)
③ Create the BAG combo contract
④ Create the Order (LMT + GTC + outsideRth)
⑤ Submit via placeOrder()
⑥ Register in active_orders
⑦ Enter the monitoring loop (detailed in the next chapter)
⑧ Fill recorded → log fill price
```

Step ② is the most time-consuming part (approximately 4-12 seconds), since each conId query requires a network round trip. For production systems, consider caching conId values for frequently used contracts within the same trading day.

---

## Frequently Asked Questions

**Q: Why use GTC instead of DAY?**

An Iron Condor limit order can take hours or even half a day to fill. With DAY, unfilled orders would be automatically cancelled at market close, requiring resubmission the next day. GTC keeps the order active until it is filled or manually cancelled.

**Q: What are the risks of multi-leg orders?**

Multi-leg orders in a BAG contract use a fill-or-kill variant: either all legs fill or none do. However, note that TWS places each leg as a separate order on the market. If a partial fill occurs and the order is then cancelled, the already-filled legs cannot be reversed. This is also why the price concession mechanism needs careful design.

**Q: Why is the combo order price negative?**

In an Iron Condor, the net price of the combo order = premium received - premium paid. Since the premium received typically exceeds the premium paid, the net price is negative (indicating that you are the net recipient of premium). This is inherent to the strategy, and the order direction is controlled by `order.action`.
