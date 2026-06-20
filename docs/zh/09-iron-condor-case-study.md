# 铁鹰策略案例全流程

> 本章将前 8 章的知识点串联为一个完整的实战案例。

---

## 案例背景

- **策略**：SPX 铁鹰价差（Iron Condor）
- **当前 SPX 价格**：5480 点
- **到期日**：2025-09-19（距到期 16 天）
- **交易模式**：模拟账户（端口 7497）
- **目标**：开仓一组铁鹰组合，收取权利金

---

## 第一阶段：连接与数据准备

### 步骤 1：建立连接

```python
from connection_manager import ConnectionManager

manager = ConnectionManager.get_instance()
success = manager.connect(port=7497, module_name="trading_session")
# → 分配 ClientID = 101（example）
# → 等待 nextValidId → 连接就绪
```

此时 `manager.connected = True`，`manager.nextorder_id = 1`。

### 步骤 2：获取 SPX 最新价

```python
from market_data import MarketDataFetcher

fetcher = MarketDataFetcher()
spx_price = fetcher.fetch_snapshot(port=7497)
# → 连接 TWS → 请求 SPX 快照 → 收到 LAST = 5480.25 → 断开

print(f"SPX 当前价格: {spx_price}")
```

### 步骤 3：计算行权价范围

```python
from market_data import generate_strike_prices

strikes, lower, upper, ls, us = generate_strike_prices(
    underlying_price=spx_price,   # 5480.25
    upper_pct=3.0,                # 上浮 3%
    lower_pct=4.0,                # 下浮 4%
    step=5.0,                     # 步长 5 点
)

print(f"行权价范围: {ls} ~ {us}")
print(f"共 {len(strikes)} 个行权价")
# → 5265 ~ 5640，约 75 个行权价
```

### 步骤 4：获取期权链数据

```python
from option_chain import OptionChainFetcher

chain_fetcher = OptionChainFetcher()
chain = chain_fetcher.fetch(
    expiry="20250919",
    strikes=strikes,
    underlying_price=spx_price,
    port=7497,
)

print(f"获取到 {len(chain)} 个 OTM 合约")
# → PUT 约 43 个 + CALL 约 32 个
```

输出 DataFrame 示例：

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

## 第二阶段：策略构建

### 步骤 5：按 Delta 筛选行权价

使用 Delta 阈值法确定铁鹰的 4 条腿：

```python
from option_chain import OptionChainFetcher

put_strike, call_strike = OptionChainFetcher.find_strikes_by_delta(
    chain=chain,
    underlying_price=spx_price,
    put_delta_target=-0.05,     # 目标 Put Delta
    call_delta_target=0.05,     # 目标 Call Delta
)

# 假设返回结果：
# put_strike  = 5395（该行权价的 delta ≈ -0.05）
# call_strike = 5585（该行权价的 delta ≈  0.05）
```

### 步骤 6：确定 4 条腿

```
铁鹰组合：

卖出 PUT @ 5395（Short Put）
买入 PUT @ 5390（Long Put，保护腿，价差 5 点）

卖出 CALL @ 5585（Short Call）
买入 CALL @ 5590（Long Call，保护腿，价差 5 点）

最大收益：收取的权利金
最大亏损：价差宽度（5 点 = $500） - 收取的权利金
盈亏平衡点：5395 - 权利金 和 5585 + 权利金
```

从期权链数据中查找这 4 个行权价的中间价：

```
Put 侧：
  Sell Put @ 5395 → mid = 2.850
  Buy  Put @ 5390 → mid = 2.700
  Put 信用 = 2.700 - 2.850 = -0.150

Call 侧：
  Sell Call @ 5585 → mid = 1.950
  Buy  Call @ 5590 → mid = 1.800
  Call 信用 = 1.800 - 1.950 = -0.150

总信用 = (-0.150) + (-0.150) = -0.300
```

### 步骤 7：构建组合合约获取 conId

```python
from option_chain import make_option_contract, ContractDetailResolver

resolver = ContractDetailResolver()

# 查询每条腿的 conId
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

## 第三阶段：执行订单

### 步骤 8：提交限价单

```python
from order_lifecycle import OrderManager
from option_chain import make_combo_contract

mgr = OrderManager()
mgr.connect("127.0.0.1", 7497, clientId=1)
thread = threading.Thread(target=mgr.run, daemon=True)
thread.start()
# 等待 nextValidId ...

# 创建 BAG 组合合约
combo_contract = make_combo_contract(legs=con_ids)

# 计算初始价格（示例值）
initial_price = -0.30  # 对齐最小变动单位后

# 创建订单
from ibapi.order import Order
order = Order()
order.action = "BUY"
order.orderType = "LMT"
order.totalQuantity = 1
order.lmtPrice = initial_price
order.tif = "GTC"
order.outsideRth = True

# 提交
order_id = mgr.nextorder_id
mgr.nextorder_id += 1
mgr.placeOrder(order_id, combo_contract, order)
mgr.register_order(order_id, "铁鹰组合", combo_contract, initial_price, 1)

print(f"订单已提交，ID: {order_id}, 价格: {initial_price}")
```

### 步骤 9：监控成交

订单监控循环检查以下条件：

| 检查项 | 频率 | 动作 |
|:-------|:-----|:-----|
| 是否完全成交 | 每秒 | 是 → 记录成交价，退出循环 |
| 是否部分成交 | 每秒 | 是 → 继续等待剩余部分 |
| 是否需要价格退让 | 每 3 分钟 | 是 → 退让 0.05，检查预判条件 |
| 是否到达截止时间 | 每秒 | 是 → 返回失败 |

### 步骤 10：成交

模拟在 15:45 成交：

```python
# orderStatus 回调触发：
# status="Filled", filled=1, remaining=0, avgFillPrice=-1.20

fill_price = mgr.order_status[order_id].avg_fill_price
print(f"铁鹰组合成交! 价格: {fill_price}")
```

---

## 第四阶段：记录与通知

### 步骤 11：记录到 Excel

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

### 步骤 12：发送通知

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

## 全流程时间线

```
T + 0s    连接 TWS（ConnectionManager.connect）
T + 3s    SPX 快照获取完成（5480.25）
T + 3s    行权价范围计算完成（5265 ~ 5640）
T + 4s    开始期权链数据请求
T + 18s   期权链数据返回（75 个 OTM 合约）
T + 19s   Delta 筛选 → 确定行权价（5395/5390/5585/5590）
T + 24s   4 条腿 conId 解析完成
T + 25s   连接订单管理器
T + 26s   BAG 合约创建 + 限价单提交
T + 27s   订单进入监控循环
...       价格退让（如果需要）
T + 900s  订单成交 @ -1.20（约 15 分钟后）
T + 900s  Excel 记录完成
T + 901s  钉钉通知推送完成
```

实际耗时约 25-30 秒完成开仓操作，加上等待成交的时间，总耗时取决于市场流动性。

---

## 生产部署 Checklist

将上述流程部署到生产环境前，请确认以下事项：

### 连接与安全

- [ ] TWS/IB Gateway 已配置固定端口并加入可信 IP 列表
- [ ] 模拟账户和生产账户的端口配置分离（.env 文件）
- [ ] 环境变量已设置（Webhook URL / Secret / 账户号）
- [ ] `.env` 文件已加入 `.gitignore`

### 数据与策略

- [ ] SPX/SPXW 市场数据订阅已激活（TWS 账号设置）
- [ ] 策略参数（Delta 阈值/信用区间/执行时间）已确认
- [ ] 最小变动单位和步长已对齐

### 订单执行

- [ ] 订单截止时间逻辑已确认（盘中/盘后/次日）
- [ ] 价格退让参数（步长/间隔/底线/特殊价格）已设置
- [ ] LegacyOrderCleaner 确认可以安全清除遗留订单

### 风控

- [ ] Debounce 参数（threshold/window）已按策略调整
- [ ] RateLimiter 参数（max_ops/window）已按策略调整
- [ ] TimeoutGuard 超时设置已确认
- [ ] 手动干预的紧急停止机制已准备

### 监控与恢复

- [ ] 日志系统已配置（文件轮转/级别/格式）
- [ ] 程序崩溃后自动重启脚本已就绪
- [ ] 每日交易检查流程已制定
- [ ] Excel 记录文件定期备份策略已确认
