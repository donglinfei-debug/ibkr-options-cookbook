# 合约管理与期权链

## IB Contract 对象详解

在 IB API 中，任何可交易的产品都由 `Contract` 对象描述。对于期权交易，最核心的合约字段是：

```python
contract = Contract()
contract.symbol = "SPX"          # 标的代码
contract.secType = "OPT"         # 证券类型：OPT=期权，IND=指数，BAG=组合
contract.exchange = "CBOE"       # 交易所
contract.currency = "USD"        # 货币
contract.lastTradeDateOrContractMonth = "20250919"  # 到期日 YYYYMMDD
contract.strike = 5480.0         # 行权价
contract.right = "C"             # C=看涨，P=看跌
contract.tradingClass = "SPXW"   # 交易类别（SPXW = SPX 周度期权）
```

### SPX vs SPXW

SPX 期权的特殊之处在于存在两种交易类别：

| 类别 | 说明 | 到期日 |
|:-----|:-----|:-------|
| SPX | 标准月度期权 | 每月第三个周五 |
| **SPXW** | 周度期权（流动性更好） | **每周一/三/五** |

在铁鹰策略中，我们通常使用 **SPXW**，因为它有更多的到期日选择，便于精确控制持仓时间。构建合约时通过 `tradingClass = "SPXW"` 来指定。

---

## conId：合约的身份证

`conId` 是 IB 系统中每个合约的唯一标识。在构建组合合约（BAG）时，每条腿（leg）都必须使用 conId 而不是合约参数。

获取 conId 的标准流程：

```python
def resolve_con_id(contract):
    # 1. 创建临时连接
    app = ContractDetailResolver()
    app.connect("127.0.0.1", 7497, clientId=随机ID)
    thread = threading.Thread(target=app.run, daemon=True)
    thread.start()
    time.sleep(2)

    # 2. 请求合约详情
    app.reqContractDetails(reqId=1, contract=contract)

    # 3. 等待回调（带超时）
    if event.wait(5.0):        # 5 秒超时
        return app.details.conId
    else:
        raise TimeoutError("conId 查询超时")
```

### 调用时序

```
[调用方] ──reqContractDetails()──→ [TWS]
[调用方] ←──contractDetails()──── [TWS]
                                   ↓
                              提取 conId
```

### 性能考量

- 每次 `reqContractDetails` 耗时约 1-3 秒
- 一个 4 腿的铁鹰策略需要查询 **4 次** conId（每个期权合约一次）
- 再加上期权链批量查询中的 conId 解析，总耗时约 10-15 秒

**优化建议**：在实际生产系统中，可以缓存 conId（例如以 `(symbol, expiry, strike, right)` 为 key），当日有效期内无需重复查询。但缓存逻辑涉及过期管理，已超出本公开文档的范围。

---

## 期权链批量请求

铁鹰策略需要检查几十个 OTM 合约的 bid/ask/delta 数据。逐个请求效率太低，需要批量处理。

### 分批策略

```python
batch_size = 50
for i in range(0, len(all_strikes), batch_size):
    batch = all_strikes[i:i + batch_size]
    for strike in batch:
        contract = make_option_contract(expiry, strike, opt_type)
        reqMktData(reqId, contract, snapshot=True)
    time.sleep(2)    # 批次间冷却，避免触发 TWS 限流
```

为什么是 50 个一批 + 2 秒间隔？

- TWS 对短时间内大量请求有限流机制
- 50 个/批在大多数网络环境下不会超时
- 2 秒间隔给了 TWS 处理上一批数据的时间

### 请求完成判断

由于 `reqMktData` 是异步的，需要一种机制来判断"所有数据都收到了"：

```python
def wait_for_completion():
    t0 = time.time()
    while len(completed) < total_requests:
        if time.time() - t0 > timeout:   # 30 秒全局超时
            # 取消未完成的请求
            for rid in incomplete:
                cancelMktData(rid)
            break
        time.sleep(0.1)
```

对于每个合约，当同时满足以下条件时标记为"完成"：

```
bid != None  AND  ask != None  AND  delta != None
```

---

## OTM 筛选

OTM（价外）的定义很简单：

- **PUT 期权**：行权价 < 标的价格（Put 在价格下跌时赚钱，所以价外 PUT 的行权价更低）
- **CALL 期权**：行权价 > 标的价格（Call 在价格上涨时赚钱，所以价外 CALL 的行权价更高）

在期权链数据获取代码中，筛选逻辑：

```python
otm_puts = [s for s in all_strikes if s < underlying_price]
otm_calls = [s for s in all_strikes if s > underlying_price]
```

---

## 数据验证

拿到数据后不要直接拿去用，先做验证：

```python
# 1. 检查关键区间内是否有无效数据（delta/bid/ask/mid 缺失或 <= 0）
# 2. 检查 PUT 侧是否有 delta 在合理范围的合约
# 3. 检查 CALL 侧是否有 delta 在合理范围的合约
```

验证失败的处理策略：

- 立即重试（最多 3 次）
- 如果重试后仍然失败，返回空 DataFrame
- 由调用方（策略层）决定是继续等待还是放弃开仓

---

## 以铁鹰策略为例的应用场景

当 SPX = 5480.25 时，期权链获取的完整流程：

```
输入:
  expiry = "20250919"
  strikes = [5265, 5270, ..., 5640]   ← 前一步生成的行权价列表
  underlying_price = 5480.25

处理:
  ① 筛选 OTM 合约：
      PUT (行权价 < 5480.25): [5265, 5270, ..., 5475]  → 约 43 个
      CALL (行权价 > 5480.25): [5485, 5490, ..., 5640] → 约 32 个
      合计: 75 个合约

  ② 分批发请求：
      批次 1: 50 个 → 等待 2 秒
      批次 2: 25 个 → 等待 2 秒

  ③ 等待所有回调完成（累计等待约 10-15 秒）

  ④ 验证数据：
      - 关键行权价区间数据完整性检查
      - PUT delta 范围检查
      - CALL delta 范围检查

  ⑤ 返回 DataFrame（75 行 × 6 列）

输出:
  strike  type    delta    bid    ask    mid
  5265    PUT    -0.032   1.20   1.35   1.275
  5270    PUT    -0.041   1.45   1.60   1.525
  ...     ...    ...      ...    ...    ...
  5635    CALL    0.042   0.95   1.10   1.025
  5640    CALL    0.038   0.80   0.95   0.875
```

这 75 个合约数据随后传递给策略层进行行权价筛选。

---

## 常见问题

**Q：为什么有些合约的 bid/ask 是 -1.00？**

-1.00 表示该合约的买卖价不可用，可能是因为：
- 该合约流动性极差，没有做市商报价
- 距离到期太远或太近
- 行权价深度价外，几乎没有交易

处理方式：排除这些合约，不纳入筛选范围。

**Q：Delta 值什么时候能收到？**

Delta 值通过 `tickOptionComputation()` 回调传递，通常在 tickPrice 之后到达。如果合约没有报价，Delta 也可能为空。这就是为什么验证逻辑中要检查 delta 是否为 None。

**Q：可以加快批量请求的速度吗？**

可以尝试缩短批次间隔（从 2 秒降到 1 秒），或者增大批次大小（从 50 增到 100）。但这取决于你的网络环境和 TWS 配置。如果遇到频繁的超时或数据缺失，建议恢复保守值。
