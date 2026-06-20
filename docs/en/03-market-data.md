# Market Data Fetching

## The Problem: How to Get SPX Real-Time Prices?

The IB API offers two modes for fetching market data: **snapshot** and **streaming**.

| Mode | How It Works | When to Use |
|:-----|:-------------|:------------|
| **Snapshot** | Request once -> receive one data point -> auto-unsubscribe | Getting the current price before opening a position, periodic polling |
| **Streaming** | Continuous updates after request until manually cancelled | Real-time monitoring during active positions, high-frequency strategies |

These two modes are toggled via the `snapshot` parameter of `reqMktData()`:

```python
self.reqMktData(
    reqId=1,
    contract=contract,
    genericTickList="",
    snapshot=True,        # True = snapshot, False = streaming
    regulatorySnapshot=False,
    mktDataOptions=[]
)
```

---

## SPX Contract Construction

The SPX S&P 500 index has fixed contract parameters:

```python
def create_spx_contract():
    contract = Contract()
    contract.symbol = "SPX"
    contract.secType = "IND"       # index type
    contract.currency = "USD"
    contract.exchange = "CBOE"
    contract.conId = 416904        # SPX's fixed conId
    return contract
```

Note that `conId = 416904` is the standard identifier for the SPX index — it can be used directly without an additional lookup.

---

## Data Handling: TickType Reference

The `tickType` parameter in the `tickPrice()` callback represents the data type:

| tickType | Constant | Meaning |
|:---------|:---------|:--------|
| 1 | BID | Bid price |
| 2 | ASK | Ask price |
| 4 | **LAST** | **Last traded price** |
| 6 | HIGH | Day's high |
| 7 | LOW | Day's low |
| 8 | VOLUME | Volume |
| 9 | **CLOSE** | **Closing price** |
| 14 | OPEN | Opening price |

For the Iron Condor strategy, **LAST (last price)** is preferred as the underlying price. After market close when no last price is available, it falls back to **CLOSE (closing price)**:

```python
def get_current_price(self):
    if self.latest_price is not None:
        return self.latest_price       # streaming mode latest price
    if self.spx_last_price is not None:
        return self.spx_last_price     # snapshot mode latest price
    if self.spx_close_price is not None:
        return self.spx_close_price    # closing price (after hours)
    return None
```

---

## Trading Session Detection

SPX options trade Monday through Friday, **09:30—16:00 Eastern Time**.

```python
def is_trading_hours(dt=None):
    now = dt or datetime.now(EST)
    if now.weekday() >= 5:       # weekend
        return False
    open_t = now.replace(hour=9, minute=30, second=0)
    close_t = now.replace(hour=16, minute=0, second=0)
    return open_t <= now <= close_t
```

Why this check matters:

- **During trading hours**: use `reqMarketDataType(3)` (real-time data) to request live quotes.
- **Outside trading hours**: the last price is not available — only the closing price can be retrieved. Snapshot requests during this period will not return updated prices.

---

## Strike Price Range Generation

Once the SPX price is obtained, the next step is calculating the range of strike prices to screen option contracts.

The formula is straightforward:

```python
def generate_strike_prices(price, upper_pct=3.0, lower_pct=4.0, step=5.0):
    upper = price * (1 + upper_pct / 100)   # up 3%
    lower = price * (1 - lower_pct / 100)    # down 4%

    lower_strike = ceil(lower / step) * step
    upper_strike = floor(upper / step) * step

    strikes = []
    cur = lower_strike
    while cur <= upper_strike:
        strikes.append(cur)
        cur += step
    return strikes
```

Using 3% up, 4% down, and a step of 5 points with SPX = 5480:

- Lower bound: 5480 x 0.96 = 5260.8 -> rounded to **5265**
- Upper bound: 5480 x 1.03 = 5644.4 -> rounded to **5640**
- Total: (5640 - 5265) / 5 = **75 strike prices**

The asymmetric range (3% up, 4% down) accounts for the fact that put-side coverage typically needs a wider range than call-side to include enough out-of-the-money contracts.

---

## Error Handling

Common error codes when fetching market data:

| Error Code | Meaning | Resolution |
|:-----------|:--------|:-----------|
| 10197 | Market data not subscribed for this contract | Subscribe to SPX/SPXW data feeds in TWS |
| 200 | Invalid contract parameters (e.g., wrong conId) | Verify contract parameters; consider using `reqContractDetails` for validation |
| 502 | Cannot connect to TWS | Confirm TWS is running and the API port is enabled |
| 504 | Not connected | Trigger the auto-reconnect mechanism |
| 2104/2106 | Market data connection established/lost | System notification; no action needed |

---

## Application in the Iron Condor Strategy

### Fetching SPX Price Before Opening a Position

```
User triggers open
  -> MarketDataFetcher.fetch_snapshot(port=7497)
  -> connect to TWS -> request SPX snapshot -> receive LAST=5480.25 -> disconnect
  -> generate_strike_prices(5480.25)
    -> returns [5265, 5270, ..., 5640]
  -> this strike set is passed to OptionChainFetcher for chain retrieval
```

The entire process takes about 3-5 seconds (connection + data transfer). Since it uses snapshot mode, the connection is released immediately after data retrieval, consuming no persistent resources.

### Fallback Logic Near Market Close

If the position is opened close to market close (after 16:00), `fetch_snapshot()` detects the off-hours session and automatically requests the closing price instead of the last price. The CLOSE data at this point is sufficient for option chain queries and strategy construction — strike price calculation only needs a reference price and does not require absolute real-time accuracy.

---

The next chapter dives into option chain data fetching and processing: How to batch-request hundreds of contracts? How to filter OTM contracts? How to handle incomplete data?
