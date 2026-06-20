# Contract Management and Options Chain

## Understanding the IB Contract Object

In the IB API, every tradable product is described by a `Contract` object. For options trading, the key contract fields are:

```python
contract = Contract()
contract.symbol = "SPX"          # Underlying symbol
contract.secType = "OPT"         # Security type: OPT=Option, IND=Index, BAG=Combo
contract.exchange = "CBOE"       # Exchange
contract.currency = "USD"        # Currency
contract.lastTradeDateOrContractMonth = "20250919"  # Expiration date YYYYMMDD
contract.strike = 5480.0         # Strike price
contract.right = "C"             # C=Call, P=Put
contract.tradingClass = "SPXW"   # Trading class (SPXW = SPX weekly option)
```

### SPX vs SPXW

SPX options have two distinct trading classes:

| Class | Description | Expiration |
|:------|:------------|:-----------|
| SPX | Standard monthly options | Third Friday of each month |
| **SPXW** | Weekly options (better liquidity) | **Every Monday/Wednesday/Friday** |

For Iron Condor strategies, we typically use **SPXW** because it offers more expiration dates, allowing precise control over holding periods. Specify it via `tradingClass = "SPXW"` when constructing the contract.

---

## conId: The Contract's Unique Identifier

`conId` is the unique identifier for each contract in the IB system. When building a combo contract (BAG), each leg must reference its contract by conId rather than by contract parameters.

The standard flow for resolving a conId:

```python
def resolve_con_id(contract):
    # 1. Create a temporary connection
    app = ContractDetailResolver()
    app.connect("127.0.0.1", 7497, clientId=random_id)
    thread = threading.Thread(target=app.run, daemon=True)
    thread.start()
    time.sleep(2)

    # 2. Request contract details
    app.reqContractDetails(reqId=1, contract=contract)

    # 3. Wait for callback (with timeout)
    if event.wait(5.0):        # 5-second timeout
        return app.details.conId
    else:
        raise TimeoutError("conId query timed out")
```

### Call Sequence

```
[Caller] ──reqContractDetails()──→ [TWS]
[Caller] ←──contractDetails()──── [TWS]
                                   ↓
                              Extract conId
```

### Performance Considerations

- Each `reqContractDetails` call takes approximately 1-3 seconds
- A 4-leg Iron Condor requires **4** conId lookups (one per option contract)
- Combined with options chain batch queries, the total conId resolution time is roughly 10-15 seconds

**Optimization Tip**: In production systems, conId values can be cached using `(symbol, expiry, strike, right)` as the key, eliminating redundant queries within the same trading day. However, cache expiration management is beyond the scope of this public document.

---

## Batch Requests for the Options Chain

An Iron Condor strategy needs bid/ask/delta data for dozens of OTM contracts. Requesting them one by one is too slow — batch processing is required.

### Batching Strategy

```python
batch_size = 50
for i in range(0, len(all_strikes), batch_size):
    batch = all_strikes[i:i + batch_size]
    for strike in batch:
        contract = make_option_contract(expiry, strike, opt_type)
        reqMktData(reqId, contract, snapshot=True)
    time.sleep(2)    # Cool-down between batches to avoid TWS rate limits
```

Why 50 per batch with a 2-second interval?

- TWS enforces rate limiting for a high volume of requests in a short period
- 50 requests per batch rarely timeout under typical network conditions
- The 2-second gap gives TWS time to process the previous batch

### Detecting Completion

Since `reqMktData` is asynchronous, you need a mechanism to determine when all data has been received:

```python
def wait_for_completion():
    t0 = time.time()
    while len(completed) < total_requests:
        if time.time() - t0 > timeout:   # 30-second global timeout
            # Cancel outstanding requests
            for rid in incomplete:
                cancelMktData(rid)
            break
        time.sleep(0.1)
```

A contract is marked as "complete" when all of the following conditions are met:

```
bid != None  AND  ask != None  AND  delta != None
```

---

## OTM Filtering

OTM (Out-of-The-Money) is straightforward:

- **PUT options**: Strike price < underlying price (Puts profit when price falls, so OTM Puts have lower strikes)
- **CALL options**: Strike price > underlying price (Calls profit when price rises, so OTM Calls have higher strikes)

In the options chain data retrieval code, the filtering logic is:

```python
otm_puts = [s for s in all_strikes if s < underlying_price]
otm_calls = [s for s in all_strikes if s > underlying_price]
```

---

## Data Validation

Never use raw data directly — always validate it first:

```python
# 1. Check for invalid data in the critical strike range (missing delta/bid/ask/mid, or values <= 0)
# 2. Verify there are PUT-side contracts with delta in a reasonable range
# 3. Verify there are CALL-side contracts with delta in a reasonable range
```

Validation failure handling:

- Retry immediately (up to 3 attempts)
- Return an empty DataFrame if retries fail
- Let the caller (strategy layer) decide whether to keep waiting or abort the opening

---

## Application Scenario: Iron Condor Example

When SPX = 5480.25, the full options chain retrieval process:

```
Input:
  expiry = "20250919"
  strikes = [5265, 5270, ..., 5640]   ← Strike list from the previous step
  underlying_price = 5480.25

Processing:
  ① Filter OTM contracts:
      PUT (strike < 5480.25): [5265, 5270, ..., 5475]  → ~43 contracts
      CALL (strike > 5480.25): [5485, 5490, ..., 5640] → ~32 contracts
      Total: 75 contracts

  ② Send batched requests:
      Batch 1: 50 contracts → wait 2 seconds
      Batch 2: 25 contracts → wait 2 seconds

  ③ Wait for all callbacks to complete (~10-15 seconds total)

  ④ Validate data:
      - Check data integrity in the critical strike range
      - Verify PUT delta range
      - Verify CALL delta range

  ⑤ Return DataFrame (75 rows × 6 columns)

Output:
  strike  type    delta    bid    ask    mid
  5265    PUT    -0.032   1.20   1.35   1.275
  5270    PUT    -0.041   1.45   1.60   1.525
  ...     ...    ...      ...    ...    ...
  5635    CALL    0.042   0.95   1.10   1.025
  5640    CALL    0.038   0.80   0.95   0.875
```

This data on 75 contracts is then passed to the strategy layer for strike price selection.

---

## Frequently Asked Questions

**Q: Why do some contracts show bid/ask as -1.00?**

A value of -1.00 means the contract's bid/ask is unavailable, possibly because:
- The contract has very poor liquidity with no market maker quotes
- It is too far from or too close to expiration
- The strike is deep out-of-the-money with virtually no trading activity

**Handling**: Exclude these contracts from the selection pool.

**Q: When will the delta value arrive?**

Delta values are delivered via the `tickOptionComputation()` callback, typically arriving after tickPrice. If a contract has no quotes, delta may also be absent. This is why the validation logic checks whether delta is None.

**Q: Can batch requests be sped up?**

You can try reducing the batch interval (from 2 seconds down to 1) or increasing the batch size (from 50 up to 100). However, this depends on your network environment and TWS configuration. If you encounter frequent timeouts or missing data, it is advisable to revert to the conservative defaults.
