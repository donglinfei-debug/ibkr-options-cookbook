# Price Concession Mechanism

## The Problem: Limit Orders May Never Fill

Limit orders (LMT) guarantee execution at or better than your specified price, but the downside is that they **may never fill at all**.

This is especially challenging for Iron Condors:

- You submit a limit order at -1.25
- The market's current combo mid-price is -1.30 (you need to beat the mid-price to attract a counterparty)
- Hold your price, and hours may pass without a fill
- Adjust too aggressively, and you risk filling at an unfavorable price

**Solution**: Timed step-down concessions combined with anticipatory jumps.

---

## Basic Strategy: Timed Step-Down Concession

### Core Logic

```python
# Pseudocode — the full monitoring loop
while not_filled and not_timed_out:
    if time_since_last_adjustment >= adjustment_interval:  # Default: 3 minutes
        if can_adjust(order_status == "Submitted"):
            new_price = current_price + step_size       # step_size = minimum tick = 0.05
            modify_order_price(order_id, new_price)
    wait 1 second
```

### Two Key Parameters

| Parameter | Description | Why This Value |
|:----------|:------------|:---------------|
| **Adjustment Interval** | Wait time between concessions | Too short: frequent modifications trigger TWS rate limits; too long: may miss the fill window |
| **Step Size** | Price change per adjustment | Must be an integer multiple of the minimum tick size (0.05 for SPX options) |

### Minimum Tick Alignment

SPX option prices are quoted in increments of 0.05, so every price must be a multiple of 0.05:

```python
import math
initial_price = math.floor(raw_price / 0.05) * 0.05
```

Rounding down makes the price more favorable for execution (for BUY-side orders, a lower price fills more easily).

---

## Advanced Strategy: Anticipatory Jump

The step-down approach has a flaw: if you keep stepping down one tick at a time, you might only fill once you reach your "floor price" — which could be well beyond your acceptable range.

**Solution**: Look ahead before each concession.

```python
# Lookahead logic
planned_new_price = current_price + step_size   # Calculate the next concession price

if planned_new_price >= FIXED_PRICE_BARRIER:     # If it would hit the floor
    # Skip the floor price entirely, jump directly to a special price
    modify_order_price(order_id, SPECIAL_PRICE)
else:
    # Normal step-down
    modify_order_price(order_id, planned_new_price)
```

### Why the Anticipatory Jump Works

```
Price axis (negative values mean you give up less premium):
  -1.40  ← Initial price (most favorable to you)
  -1.35  ← 1st concession
  -1.30  ← 2nd concession
  -1.25  ← 3rd concession
  -1.20  ← 4th concession
  -1.15  ← 5th concession
  -1.10  ← 6th concession
  ↓
  ┌──────────┐
  │ -1.05    │ ← Floor price (FIXED_PRICE_BARRIER)
  └──────────┘
  ↓
  ┌──────────┐
  │ -1.25    │ ← Special price (skip the floor, jump to a historically reasonable level)
  └──────────┘
```

When the lookahead detects that the next concession would hit the floor:

1. **Do not adjust to -1.05** (the floor price), as -1.05 itself is not an ideal fill level
2. **Jump directly to -1.25** (a zone with historically dense fills) and wait for execution at a more favorable price
3. **Stop automatic adjustments after the jump** — no further concessions

This "one-shot jump" design avoids the downward spiral of "hitting the floor, then conceding further, and getting worse."

---

## Duplicate Modification Prevention

In a multi-threaded environment, the monitoring loop and fill callbacks may trigger price modifications at the same time. A guard mechanism is needed:

```python
class OrderModificationGuard:
    def __init__(self, cooldown=180):
        self._busy = set()         # Orders currently being modified
        self._cooldown = cooldown  # Cool-down period (seconds)

    def can_modify(self, order_id, status):
        if order_id in self._busy:
            return False            # Modification already in progress
        if status != "Submitted":
            return False            # Only Submitted orders can be modified
        if time.time() - last_time < self._cooldown:
            return False            # Cool-down not yet elapsed
        return True

    def start_modification(self, order_id):
        self._busy.add(order_id)    # Lock

    def finish_modification(self, order_id):
        self._busy.discard(order_id)  # Unlock
```

Three layers of protection:

1. **Busy Lock**: Only one in-flight modification per order at a time
2. **State Check**: Only orders in `Submitted` status need modification
3. **Cool-down Timer**: Prevents excessive modification requests

---

## Deadline Management

Order monitoring cannot continue indefinitely. A deadline must be set:

```python
# Default deadline: 16:10 Eastern Time
end_time = now.replace(hour=16, minute=10, second=0)

# If current time has already passed 16:10, roll to the next day
if now > end_time:
    end_time += timedelta(days=1)
```

When the deadline is reached:

- If the order is still unfilled → cancel the order → strategy execution failed
- Log the failure reason ("Deadline reached")
- Optionally switch to a fallback strategy or notify the user

---

## Application Scenario: Iron Condor Example

Assume the Iron Condor's theoretical mid-price is -1.40, and the initial price is set to -1.40 (rounded down):

```
Order submission time: 15:30 ET
Deadline: 16:10 ET (same day)
Total available time: 40 minutes

Adjustment interval: 3 minutes
Maximum adjustments: ~13 (40 / 3)

15:30  Submit order @ -1.40
15:33  Not filled → adjust to -1.35
15:36  Not filled → adjust to -1.30
15:39  Not filled → adjust to -1.25
15:42  Not filled → adjust to -1.20
15:45  【Filled!】@ -1.20
       → Stop monitoring → Record fill price
```

If market liquidity is low, the price continues to step down:

```
15:30 → -1.40
15:33 → -1.35
15:36 → -1.30
15:39 → -1.25
15:42 → -1.20
15:45 → -1.15
15:48 → -1.10
15:51 → Lookahead: -1.05 would hit the floor
      → Jump directly to special price
      → Mark "special adjustment applied"
      → Wait for fill (no further concessions)
...
16:10 → Deadline reached, not filled, return failure
```

---

## Summary

The key principles of the price concession mechanism:

1. **Step down patiently**: A 3-minute interval gives the market time to react without dragging on too long
2. **Look ahead, avoid the trap**: Jump to a reasonable price before hitting the floor, rather than stepping into it one tick at a time
3. **One jump, then stop**: The special price adjustment executes only once, with no further concessions afterward
4. **Set a firm deadline**: Exit decisively at market close, leaving no overnight risk
