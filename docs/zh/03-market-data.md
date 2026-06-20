# 市场数据获取

## 问题：如何获取 SPX 实时行情？

在 IB API 中获取市场数据有两种模式：**快照（snapshot）** 和 **流式（streaming）**。

| 模式 | 工作方式 | 使用场景 |
|:-----|:---------|:---------|
| **快照** | 请求一次 → 收到一个数据点 → 自动取消订阅 | 开仓前获取当前价格、定时轮询 |
| **流式** | 请求后持续接收更新，直到手动取消 | 持仓期间实时监控、高频策略 |

两种模式通过 `reqMktData()` 的 `snapshot` 参数切换：

```python
self.reqMktData(
    reqId=1,
    contract=contract,
    genericTickList="",
    snapshot=True,        # True = 快照, False = 流式
    regulatorySnapshot=False,
    mktDataOptions=[]
)
```

---

## SPX 合约构建

SPX 标普 500 指数有固定的合约参数：

```python
def create_spx_contract():
    contract = Contract()
    contract.symbol = "SPX"
    contract.secType = "IND"       # 指数类型
    contract.currency = "USD"
    contract.exchange = "CBOE"
    contract.conId = 416904        # SPX 的固定 conId
    return contract
```

注意 `conId = 416904` 是 SPX 指数的标准标识，可以直接使用，无需额外查询。

---

## 数据处理：TickType 说明

`tickPrice()` 回调中收到的 `tickType` 参数表示数据类型：

| tickType | 常量 | 含义 |
|:---------|:-----|:-----|
| 1 | BID | 买入价 |
| 2 | ASK | 卖出价 |
| 4 | **LAST** | **最新成交价** |
| 6 | HIGH | 当日最高价 |
| 7 | LOW | 当日最低价 |
| 8 | VOLUME | 成交量 |
| 9 | **CLOSE** | **收盘价** |
| 14 | OPEN | 开盘价 |

在铁鹰策略中，优先使用 **LAST（最新价）** 作为标的价格，收盘后没有最新价时回退到 **CLOSE（收盘价）**：

```python
def get_current_price(self):
    if self.latest_price is not None:
        return self.latest_price       # 流式模式的最新价
    if self.spx_last_price is not None:
        return self.spx_last_price     # 快照模式的最新价
    if self.spx_close_price is not None:
        return self.spx_close_price    # 收盘价（盘后使用）
    return None
```

---

## 交易时段检测

SPX 期权交易时间：美东时间 **周一至周五 09:30—16:00**。

```python
def is_trading_hours(dt=None):
    now = dt or datetime.now(EST)
    if now.weekday() >= 5:       # 周末
        return False
    open_t = now.replace(hour=9, minute=30, second=0)
    close_t = now.replace(hour=16, minute=0, second=0)
    return open_t <= now <= close_t
```

为什么要做这个检查？

- **交易时段内**：使用 `reqMarketDataType(3)`（实时数据），请求最新行情
- **非交易时段**：无法获取最新价，只能获取收盘价。此时请求快照模式，价格不会更新

---

## 行权价区间生成

拿到 SPX 价格后，需要计算"在哪些行权价范围内筛选期权合约"。

公式很简单：

```python
def generate_strike_prices(price, upper_pct=3.0, lower_pct=4.0, step=5.0):
    upper = price * (1 + upper_pct / 100)   # 上浮 3%
    lower = price * (1 - lower_pct / 100)    # 下浮 4%

    lower_strike = ceil(lower / step) * step
    upper_strike = floor(upper / step) * step

    strikes = []
    cur = lower_strike
    while cur <= upper_strike:
        strikes.append(cur)
        cur += step
    return strikes
```

以上浮 3%、下浮 4%、步长 5 点为例，SPX = 5480 时：

- 下界：5480 × 0.96 = 5260.8 → 取整到 **5265**
- 上界：5480 × 1.03 = 5644.4 → 取整到 **5640**
- 共约 (5640 - 5265) / 5 = **75 个行权价**

上下浮动不对称（上浮 3%、下浮 4%）是因为期权链中 Put 侧通常需要比 Call 侧更宽的范围，以覆盖足够的价外合约。

---

## 错误处理

获取市场数据时的常见错误码：

| 错误码 | 含义 | 解决方案 |
|:-------|:-----|:---------|
| 10197 | 未订阅该合约的市场数据 | 在 TWS 中订阅 SPX/SPXW 行情 |
| 200 | 合约参数无效（如 conId 错误） | 检查合约参数，建议使用 `reqContractDetails` 验证 |
| 502 | 无法连接到 TWS | 确认 TWS 已启动，API 端口已启用 |
| 504 | 未连接 | 触发自动重连机制 |
| 2104/2106 | 市场数据连接已建立/已断开 | 系统通知，无需处理 |

---

## 以铁鹰策略为例的应用场景

### 开仓前获取 SPX 价格

```
用户触发开仓
  → MarketDataFetcher.fetch_snapshot(port=7497)
  → 连接 TWS → 请求 SPX 快照 → 收到 LAST=5480.25 → 断开连接
  → generate_strike_prices(5480.25)
    → 返回 [5265, 5270, ..., 5640]
  → 这组行权价传给 OptionChainFetcher 去获取期权链
```

整个过程耗时约 3-5 秒（连接 + 数据传输）。因为是快照模式，获取到数据后立即断开，不占用连接资源。

### 收盘时的备选逻辑

如果开仓时间接近收盘（16:00 之后），`fetch_snapshot()` 会检测到非交易时段，自动请求收盘价而非最新价。此时返回的 CLOSE 数据足以支撑期权链查询和策略构建——行权价计算只需要一个参考价格，不要求绝对实时。

---

下一章将深入期权链数据的获取与处理：如何批量请求上百个合约？如何筛选 OTM 合约？如何处理数据不完整的情况？
