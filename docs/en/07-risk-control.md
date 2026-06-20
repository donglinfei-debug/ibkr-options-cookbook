# Risk Control: Debounce, Rate Limiter & Timeout Guard

Automated trading systems face a fundamental tension:

> **Market noise is the norm, but acting on every noise event is a disaster.**

If the system reacts to every price fluctuation, it will trade excessively in choppy markets, racking up transaction costs. The three risk-control components introduced in this chapter are designed to strike a balance between "responding in time" and "avoiding overreaction."

---

## 1. Debounce

### Problem

SPX price oscillates between 5475 and 5485. Your stop-loss condition is "price moves outside the 5420–5540 range."

```
Time  Price  Trigger?
T1   5485   No (within range)
T2   5410   **Yes!** (below lower bound)
T3   5490   No (back in range)
T4   5405   **Yes!** (below again)
T5   5415   No
T6   5400   **Yes!** ...
```

If every trigger immediately fires an order, you end up sending multiple stop-loss orders in quick succession—some of which must be canceled when price reverts, generating a flood of unnecessary trades.

### Solution: Consecutive Confirmation

```python
class Debounce:
    """
    Requires threshold consecutive triggers before confirming a signal.

    threshold: number of consecutive triggers required (default 3)
    window: time window in seconds (default 5); resets the counter if exceeded
    """

    def record(self, condition: bool) -> bool:
        """
        Records one observation.
        condition=True means the condition is met (price out of bounds)
        condition=False means the condition is not met

        Returns True when the threshold is reached and the signal is confirmed.
        """
        if time expired since last reset:
            self.counter = 0          # window expired, reset

        if condition:
            self.counter += 1
            if self.counter >= self.threshold:
                self.counter = 0      # threshold reached, confirm
                return True
        else:
            self.counter = 0          # one miss resets the counter
        return False
```

### Comparison

```
Time  Price  Out of Bounds  Debounce Counter  Debounce Output
T1   5410   Yes            1/3               ❌ No trigger
T2   5412   Yes            2/3               ❌ No trigger
T3   5405   Yes            3/3               ✅ **Trigger!**
T4   5420   No             0/3 (reset)       ❌
T5   5402   Yes            1/3               ❌ No trigger
T6   5400   Yes            2/3               ❌ No trigger
T7   5398   Yes            3/3               ✅ **Trigger!**
```

Compared to the naive approach (triggering on every breach), Debounce cuts false signals from 6 to 2—a **67% reduction in wasted operations**.

### Mathematical Rationale

Assuming price noise is random, let p be the probability of a single false trigger:

- Naive: triggers on every breach → error probability = p
- Debounce(N=3): error probability = p³

If p = 20% (1 in 5 price moves is noise), the naive approach has a 20% error rate, while Debounce(N=3) reduces it to 0.8%—**25x lower**.

---

## 2. Rate Limiter

### Problem

In extreme market conditions, price may break through multiple thresholds in succession:

```
T1: Price breaks below lower bound  → triggers stop-loss
T2: Price breaks second lower bound → triggers again
T3: Price breaks third lower bound  → triggers again
...
```

The system could fire 5–10 orders within seconds. This not only wastes commission fees but can create a "snowball effect" on the exchange—your own orders compete against each other, driving up execution costs.

### Solution: Sliding Time Window

```python
class RateLimiter:
    """
    Limits the number of operations within a sliding time window.

    max_operations: maximum allowed operations in the window (default 3)
    window: length of the time window in seconds (default 60)
    """

    def allow(self) -> bool:
        now = time.time()
        # purge records outside the window
        while self.timestamps and self.timestamps[0] < now - self.window:
            self.timestamps.popleft()

        if len(self.timestamps) >= self.max_operations:
            return False          # limit exceeded, reject

        self.timestamps.append(now)
        return True
```

### Effect

```
Time  RateLimiter State        Decision
T1    [t1]                     ✅ Allow (1/3)
T2    [t1, t2]                 ✅ Allow (2/3)
T3    [t1, t2, t3]             ✅ Allow (3/3)
T4    [t1, t2, t3, t4]         ❌ **Reject** (exceeded)
T5    [t2, t3, t4, t5] ← t1 expired ❌ Reject (exceeded)
T6    [t3, t4, t5, t6]         ❌ Reject
T7    [t4, t5, t6, t7]         ❌ Reject
T8    [t5, t6, t7, t8] ← t2 expired ✅ Allow (only 2 ops in window)
```

A 60-second window with a 3-operation cap means the system can execute at most 3 orders per minute. In volatile markets, this prevents an order avalanche.

### Coordinating with Debounce

```
Debounce answers: "Is this signal trustworthy?"
Rate Limiter answers: "Are we operating too often right now?"

Actual flow:
Price breach detected
  → Debounce confirms (requires 3 consecutive readings)
    → Rate Limiter checks (are we above the frequency limit?)
      → Execute operation
```

Two layers of filtering: the first prevents false positives, the second prevents over-frequency.

---

## 3. Timeout Guard

### Problem

In asynchronous operations, you send a modify-order request and wait for confirmation. But what if the confirmation never arrives?

- TWS may have crashed
- The network connection may have dropped
- The order may have been rejected by TWS but the error callback was lost

Without a timeout mechanism, the system hangs indefinitely in a "waiting for confirmation" state, blocking all subsequent operations.

### Solution: Lock with Auto-Release on Timeout

```python
class TimeoutGuard:
    """
    Tracks a pending operation and automatically releases the lock on timeout.

    timeout: timeout in seconds (default 10)
    """

    def start(self, label="operation") -> bool:
        """Marks an operation as pending. Returns False if another is already in flight."""
        if self._pending and (now - self._pending_ts < self.timeout):
            return False              # another operation is already pending

        self._pending_label = label
        self._pending_ts = now
        return True

    def finish(self):
        """Completes the operation and releases the lock."""
        self._pending_label = None

    # After 10 seconds, the lock auto-releases even if finish() is never called
    @property
    def is_pending(self):
        if self._pending_label is None:
            return False
        if time.time() - self._pending_ts > self.timeout:
            return False              # timed out, treated as released
        return True
```

### Why 10 Seconds?

- IB API order confirmation typically arrives within 1–3 seconds
- 10 seconds is a 3x buffer over normal latency, enough to handle network jitter
- Beyond 10 seconds, something is almost certainly wrong—releasing the lock lets the system continue

---

## 4. Coordinating the Three Components

In a complete monitoring loop, all three components work together:

```python
debounce = Debounce(threshold=3, window_seconds=5)
rate_limiter = RateLimiter(max_operations=3, window_seconds=60)
timeout_guard = TimeoutGuard(timeout_seconds=10)

while monitoring:
    price = get_current_price()
    is_out_of_bounds = (price < lower_bound or price > upper_bound)

    # Stage 1: Debounce
    if debounce.record(is_out_of_bounds):
        # Stage 2: Rate Limiter
        if rate_limiter.allow():
            # Stage 3: Timeout Guard
            if timeout_guard.start("stop_loss"):
                send_stop_loss_order()
                # ... wait for confirmation ...
                timeout_guard.finish()
```

| Stage | Component | Protection Target |
|:------|:----------|:------------------|
| Stage 1 | Debounce | False signals from market noise |
| Stage 2 | Rate Limiter | Order avalanche during extreme volatility |
| Stage 3 | Timeout Guard | Deadlock from lost async confirmations |

Three independent layers, each focused on a single concern. You can tune the parameters of each layer independently without affecting the others—this is the value of decoupled design.

---

## Application to Iron Condor

The stop-loss monitoring in the Iron Condor strategy (proprietary details omitted) uses:

- **Debounce**: After SPX price exits the safety range, it must breach the boundary 3 consecutive times (~1 second apart) to confirm a true violation. Prevents stop-loss triggers from single anomalous quotes.
- **Rate Limiter**: If price oscillates around the boundary, caps operations at 3 per 60 seconds. Prevents the "stop-loss → cancel → stop-loss again → cancel" loop.
- **Timeout Guard**: After sending a stop-loss order, resets state if no confirmation arrives within 10 seconds. If the order truly failed to send, the system can retry instead of freezing.
