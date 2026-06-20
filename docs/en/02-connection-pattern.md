# Connection Management Design Pattern

## The Problem: Multi-Module Connection Conflicts with the IB API

Every automated trading system built on the IB API faces the same fundamental question:

> Data fetching, option chain queries, order execution, strategy monitoring — all of these modules need to communicate with TWS. Should they share a single connection, or should each module create its own?

If each module creates its own connection:

```python
# Module A: Fetch SPX price
app_a = IBApp()
app_a.connect("127.0.0.1", 7497, clientId=random.randint(1, 1000))

# Module B: Query option chain
app_b = IBApp()
app_b.connect("127.0.0.1", 7497, clientId=random.randint(1, 1000))  # might conflict!
```

This leads to two problems:

1. **ClientID Conflicts**: TWS requires each connection to have a unique ClientID. Random generation risks collisions; fixed values invite modules to steal each other's IDs.
2. **Resource Waste**: Each connection maintains its own message thread and callback processing. Three connections mean triple the overhead for threads and callback handling.

## The Solution: Singleton + Fixed ClientID Allocation

### Singleton Pattern

```python
class ConnectionManager(EWrapper, EClient):
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:  # double-checked locking
                    cls._instance = super().__new__(cls)
        return cls._instance
```

Key design decisions:

- **Double-checked locking**: The first check avoids acquiring the lock on every call; the second check ensures thread safety during initialization.
- **`__init__` re-entry guard**: Uses the `_initialized` flag to ensure the constructor runs only once.

### Fixed ClientID Allocation

```python
CLIENT_ID_MAP = {
    "module_a": 101,
    "module_b": 102,
    "module_c": 103,
    "module_d": 104,
    "default": 100,
}
```

Each module registers a fixed ID. If Module A and Module B call `connect()` sequentially, the manager detects whether an existing connection with the same ID is already active — it skips reconnection if the ID matches, or disconnects and reconnects if the ID differs.

---

## Connection Lifecycle

A TWS connection goes through the following states from creation to destruction:

```
[Initializing] -> [Connecting] -> [Connected · Waiting for nextValidId] -> [Ready]
                     ↓ failure                                             ↓ disconnect
                  [Retrying] <- loop -> [Max retries reached] -> [Failed]   [Disconnected]
```

### Automatic Retry on Connection Failure

```python
for attempt in range(1, max_retries + 1):
    try:
        super().connect("127.0.0.1", port, clientId=cid)
        # Start the message thread
        thread = threading.Thread(target=self.run, daemon=True)
        thread.start()
        # Wait for nextValidId (connection confirmation signal)
        while self.nextorder_id is None:
            if time.time() - t0 > timeout:
                raise TimeoutError("Connection timeout")
            time.sleep(0.1)
        # Connection successful
        return True
    except Exception:
        # Retry
```

Important notes:

- **`run()` executes in a separate thread** — otherwise it blocks the main flow.
- **Waiting for `nextValidId` is the true indicator of a successful connection**, not the return of `connect()`.
- **Timeout protection** prevents the system from hanging indefinitely during network instability.

---

## Order ID Management

TWS maintains an incrementing order ID sequence per connection. In a multi-threaded environment, concurrent ID acquisition leads to duplicates.

Solution: a thread-safe counter.

```python
def get_next_order_id(self) -> int:
    with self._state_lock:
        if self.nextorder_id is None:
            raise RuntimeError("Not connected — cannot obtain order ID")
        oid = self.nextorder_id
        self.nextorder_id += 1
        return oid
```

---

## Connection Status Monitoring

`get_connection_status()` returns a complete snapshot of the current connection:

```python
{
    "connected": True,
    "port": 7497,
    "client_id": 101,
    "next_order_id": 42,
    "active_modules": ["module_a", "module_b"],
}
```

This is useful for:
- Periodic health checks (`check_connection()` supports auto-reconnect)
- Dashboard monitoring
- Triggering anomaly alerts

---

## Application in the Iron Condor Strategy

Over the lifecycle of an Iron Condor strategy, the connection manager is called by the following modules:

| Phase | Calling Module | ClientID | Notes |
|:------|:---------------|:---------|:------|
| Fetch SPX price | `MarketDataFetcher` | 102 | Snapshot mode; disconnect after retrieval |
| Query option chain | `OptionChainFetcher` | 103 | Batch requests, 50 per batch |
| Execute order | `OrderManager` | 104 | Continuous monitoring, long-lived |
| Close monitoring (not public) | Strategy monitor | 105 | Streaming mode, persistent connection |

When these four modules run concurrently, separate connections would require at least 4 distinct ClientIDs. With a singleton manager, they can share 1-2 connections (data modules share one, order execution gets a dedicated one), significantly reducing ID management complexity.

---

## Frequently Asked Questions

**Q: Why not use random ClientIDs?**

Random IDs typically do not conflict within a single session, but after a program restart, TWS may still hold sessions from the old connection. A new connection using a random ID could coincidentally match an old ID, triggering "already in use" (error code 501/502). Fixed IDs with a singleton connection eliminate this problem entirely.

**Q: What happens if TWS restarts?**

`check_connection()` detects the disconnection and triggers an automatic reconnect. When combined with periodic checks (e.g., every 30 seconds), the system can resume trading automatically once TWS is back online.

**Q: The connection succeeds but no data arrives?**

The two most common causes:
1. **Market data not subscribed**: Confirm that SPX/SPXW data subscriptions are active in TWS's "Market Data Subscriptions" tab (error code 10197).
2. **Wrong port**: Live accounts use port 7496, paper accounts use 7497. Mixing them up will prevent successful connections.
