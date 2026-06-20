# System Architecture Overview

## Why Start with Architecture?

Before writing any code, understanding the overall architecture helps answer several critical questions:

- How many layers should an automated trading system have?
- How do layers communicate and stay decoupled?
- When adding a new strategy, which parts of the codebase need to change and which remain untouched?

This document uses the **SPX Iron Condor** strategy as a running example to trace a complete trade request through the system.

---

## 1. Six-Layer Architecture

The system is organized into six logical layers, from top to bottom:

```
┌───────────────────────────────────────────────────────────────────┐
│  1. Connection Layer                                              │
│     Manages the TWS/IB Gateway connection lifecycle               │
├───────────────────────────────────────────────────────────────────┤
│  2. Data Layer                                                    │
│     Fetches market data (index prices, option chains)             │
├───────────────────────────────────────────────────────────────────┤
│  3. Strategy Layer                                                │
│     Builds trading strategies, selects strike prices,             │
│     calculates target prices                                      │
├───────────────────────────────────────────────────────────────────┤
│  4. Execution Layer                                               │
│     Places orders, tracks status, handles price adjustments       │
├───────────────────────────────────────────────────────────────────┤
│  5. Recording Layer                                               │
│     Persists trade records to Excel                               │
├───────────────────────────────────────────────────────────────────┤
│  6. Notification Layer                                            │
│     Pushes trade notifications via Webhook                        │
└───────────────────────────────────────────────────────────────────┘
```

### Layer Responsibilities

| Layer | Core Class (Public) | Core Responsibility |
|:------|:--------------------|:--------------------|
| Connection | `ConnectionManager` | Singleton connection management, fixed ClientID assignment, auto-reconnect |
| Data | `MarketDataFetcher` + `OptionChainFetcher` | SPX price feed, batch option chain requests, OTM filtering |
| Strategy | User-defined (example: Iron Condor) | Strike selection, credit calculation, combo contract construction |
| Execution | `OrderManager` | Combo order placement, status tracking, price modification |
| Recording | `TradeRecorder` | Auto-creates Excel sheets, appends records, maintains headers |
| Notification | `WebhookNotifier` | HMAC-signed message push |

---

## 2. Complete Request Flow: The Iron Condor Example

Suppose we are opening an Iron Condor position with SPX at 5480. Here is how the request flows through each layer:

### Step 1: Connection

```
[User] -> calls ConnectionManager.connect()
         -> singleton check (skip if already connected)
         -> assign ClientID -> connect to TWS:7497
         -> wait for nextValidId -> ready
```

**Why a singleton?**

In an automated system, data fetching, option chain queries, and order execution may be triggered by different modules. If each module creates its own connection, TWS will raise ClientID conflicts ("already in use" errors). A singleton with fixed ClientID assignment ensures all modules share a single connection with predictable IDs — no conflicts even after restarts.

### Step 2: Fetch Market Data

```
[Connection ready] -> MarketDataFetcher.fetch_snapshot()
                    -> create SPX index contract (conId=416904)
                    -> reqMktData(snapshot=true)
                    -> callback tickPrice -> get LAST=5480.25
                    -> cancel subscription -> disconnect
```

Use `generate_strike_prices()` to compute the potential strike range:

- Down 4%: 5480 x 0.96 = 5260.8 -> rounded to 5265
- Up 3%: 5480 x 1.03 = 5644.4 -> rounded to 5640
- Step 5 points, ~76 strikes total

### Step 3: Fetch Option Chain

```
-> OptionChainFetcher.fetch(expiry="20250919",
                              strikes=[5265, 5270, ..., 5640],
                              underlying=5480.25)
   -> filter OTM contracts (Put < 5480.25, Call > 5480.25)
   -> batch 50 requests/batch -> reqMktData(snapshot=true)
   -> wait for bid/ask/delta data -> assemble DataFrame
```

The returned data looks like this:

| strike | type | delta | bid | ask | mid |
|--------|------|-------|-----|-----|-----|
| 5265 | PUT | -0.03 | 1.20 | 1.35 | 1.275 |
| 5270 | PUT | -0.04 | 1.45 | 1.60 | 1.525 |
| ... | ... | ... | ... | ... | ... |
| 5485 | CALL | 0.03 | 2.10 | 2.30 | 2.200 |

### Step 4: Strategy Construction

With the option chain data in hand, the strategy layer applies the Iron Condor selection logic:

```
-> On the PUT side: among OTM puts, find the strike with delta closest to the target (e.g., -0.05)
-> On the CALL side: among OTM calls, find the strike with delta closest to the target (e.g., 0.05)
-> Determine the 4 legs:
   - Sell Put @ target strike
   - Buy Put @ target strike - 5 points (protective leg)
   - Sell Call @ target strike
   - Buy Call @ target strike + 5 points (protective leg)
-> Calculate theoretical credit = (Sell Put mid - Buy Put mid) + (Sell Call mid - Buy Call mid)
-> Check if credit falls within the target range
-> Output strategy dict: {put_sell_strike, put_buy_strike, call_sell_strike, call_buy_strike, ...}
```

Note: **The specific parameters (Delta thresholds, credit ranges, etc.) are core strategy parameters of this system and are not disclosed here.** The publicly available `option_chain.py` provides the `find_strikes_by_delta()` utility method, but the actual target values are determined by the user in the strategy layer.

### Step 5: Execute Order

```
[Strategy built] -> OrderManager connects to TWS
                  -> create BAG combo contract (4 ComboLegs)
                  -> create Order(action=BUY, orderType=LMT, price=calculated)
                  -> placeOrder() -> register in active_orders
                  -> enter monitoring loop:
                      check orderStatus every 1 second
                      check if price concession needed every N minutes
                      filled -> record fill price
                      timeout -> return failure
```

### Step 6: Record Trade

```
[Order filled] -> TradeRecorder.record({
                    date, time, expiry,
                    put legs, call legs,
                    fill_price, quantity
                })
                -> auto-create/open Excel -> append row -> format date -> save
```

### Step 7: Send Notification

```
[Record saved] -> WebhookNotifier.send_trade_update(
                    title="Iron Condor Filled",
                    execution_time="2025-09-03 15:45:00 ET",
                    details="... market data ..."
                )
                -> generate HMAC-SHA256 signature -> POST -> DingTalk bot push
```

### Full Flow Sequence Diagram

```
User    Connection   MarketData   OptionChain   Strategy   OrderManager   Recorder  Notifier
 │          │            │            │           │          │             │          │
 ├─connect()→│            │            │           │          │             │          │
 │          ├─snapshot()→│            │           │          │             │          │
 │          │            ├─fetch()───→│           │          │             │          │
 │          │            │            ├─build()──→│          │             │          │
 │          │            │            │           ├─execute()→│             │          │
 │          │            │            │           │          ├─record()───→│          │
 │          │            │            │           │          ├─notify()───→│          │
 │          │            │            │           │          │             │          │
```

---

## 3. Design Patterns in Use

The system employs four classic design patterns, each chosen for a concrete operational reason:

| Pattern | Where It Appears | Why It Is Needed |
|:--------|:-----------------|:-----------------|
| **Singleton** | `ConnectionManager` | Guarantees a single TWS connection globally, avoiding ClientID conflicts |
| **Factory** | Strategy selection (not public) | Creates different strategy instances at runtime based on user selection |
| **Strategy** | Base strategy class + subclasses | Each trading strategy (Iron Condor, Vertical Spread, etc.) can be independently implemented and swapped |
| **Template Method** | `BaseStrategy.build_strategy()` | Defines the standard skeleton of strategy construction; subclasses fill in the details |

---

## 4. Architecture Summary

1. **Layered Decoupling**: Layers communicate through well-defined interfaces. The data layer has no knowledge of strategy logic; the execution layer does not care about strategy internals.
2. **Singleton Connection**: All modules share a single TWS connection, avoiding multi-connection conflicts.
3. **Pluggable Strategies**: Adding a new strategy only requires implementing the base strategy interface — no other layers need modification.
4. **Error Isolation**: Recording failures do not interrupt trading; notification failures do not interrupt recording. Each layer handles errors independently.
5. **Configuration-Driven**: Ports, timeouts, retry counts and all other parameters are externalized — never hardcoded.

The next chapter dives into the connection layer design: Why is the singleton pattern best suited for TWS connection management? How should ClientIDs be allocated?
