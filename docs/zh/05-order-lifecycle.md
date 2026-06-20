# 订单执行与生命周期

## 组合合约：BAG

铁鹰策略由 4 条腿组成：卖 Put、买 Put、卖 Call、买 Call。在 IB API 中，多腿订单通过 **BAG（包）** 合约来实现。

```python
from ibapi.contract import ComboLeg

def make_iron_condor_contract(legs_con_ids):
    """
    构建铁鹰组合合约
    legs_con_ids: [(conId, ratio, action), ...]
    """
    contract = Contract()
    contract.symbol = "SPX"
    contract.secType = "BAG"          # 组合合约类型
    contract.currency = "USD"
    contract.exchange = "SMART"

    combo_legs = []
    for con_id, ratio, action in legs_con_ids:
        leg = ComboLeg()
        leg.conId = con_id            # 每条腿的 conId
        leg.ratio = ratio             # 比例（通常 1:1）
        leg.action = action           # "BUY" 或 "SELL"
        leg.exchange = "SMART"
        combo_legs.append(leg)

    contract.comboLegs = combo_legs
    return contract
```

四条腿的 conId 分别对应：

| 腿 | 方向 | 含义 |
|:---|:-----|:-----|
| Leg 1 | SELL | 卖出 PUT @ 较低行权价 |
| Leg 2 | BUY | 买入 PUT @ 更低行权价（保护腿） |
| Leg 3 | SELL | 卖出 CALL @ 较高行权价 |
| Leg 4 | BUY | 买入 CALL @ 更高行权价（保护腿） |

---

## Order 对象关键参数

```python
order = Order()
order.action = "BUY"              # BAG 合约的买卖方向
order.orderType = "LMT"           # 限价单
order.totalQuantity = quantity    # 合约组数
order.lmtPrice = price            # 限价
order.tif = "GTC"                 # 取消前有效（Good-Till-Cancelled）
order.outsideRth = True           # 允许盘外成交
```

### 参数详解

| 参数 | 选项 | 说明 |
|:-----|:-----|:------|
| `orderType` | LMT / MKT / STP | 限价单、市价单、止损单。自动化交易几乎只用 LMT |
| `tif` | **GTC** / DAY / IOC | GTC = 订单一直有效直到成交或手动取消；DAY = 当天有效 |
| `outsideRth` | True / False | 是否允许在常规交易时间外成交。SPX 期权盘后仍有波动，建议开启 |
| `action` | BUY / SELL | BAG 合约的 action 方向，与策略计算出的信用正负有关 |

---

## 订单状态机

提交一个订单后，它会经历以下状态流转：

```
  ┌──────────┐
  │ Submitted│  ← 订单已提交到 TWS，等待进入市场
  └────┬─────┘
       │
       ▼
  ┌──────────┐
  │ PreSubmitted │  ← 仅在盘外时段出现，等待开盘
  └────┬─────┘
       │
       ▼
  ┌──────────┐
  │  Working │  ← 订单已进入市场，等待对手方
  └────┬─────┘
       │
    ┌──┴──┐
    ▼     ▼
┌──────┐ ┌────────┐
│Filled│ │Cancelled│
└──────┘ └────────┘
```

在 IB API 中，这些状态通过 `orderStatus()` 回调传递：

```python
def orderStatus(self, orderId, status, filled, remaining,
                avgFillPrice, ...):
    # status 的取值: "Submitted", "PreSubmitted",
    #                "Working", "Filled", "Cancelled"
    self.order_status[orderId] = {
        "status": status,
        "filled": filled,
        "remaining": remaining,
        "avgFillPrice": avgFillPrice,
    }
```

### 部分成交

订单可能被部分成交：

```
时间 T1: status=Working,   filled=2,  remaining=1  ← 部分成交 2 张
时间 T2: status=Working,   filled=3,  remaining=0  ← 剩余 1 张也成交了
时间 T3: status=Filled,    filled=3,  remaining=0  ← 状态更新为完全成交
```

部分成交时：
- `filled` 递增但小于 `totalQuantity`
- 可以先不调整价格，等待剩余部分自行成交
- 如果长时间未完全成交，再考虑修改价格（见下一章）

---

## 订单信息注册

提交订单后，立即将其注册到活跃订单列表：

```python
self.active_orders[order_id] = {
    "name": "铁鹰组合",
    "original_price": -1.25,        # 初始价格
    "current_price": -1.25,         # 当前价格
    "contract": contract,           # 合约对象
    "adjustments": 0,               # 已调整次数
    "activation_time": None,        # 订单激活时间
    "has_traded": False,            # 是否有成交
    "last_filled": 0,               # 上次成交数量
}
```

这个注册表是整个订单监控的基础。后续的价格修改、成交确认、状态追踪，都依赖这个数据结构。

---

## LegacyOrderCleaner：启动时清理

程序异常退出后重启时，TWS 中可能残留着上次的 GTC 订单。如果不清理就提交新订单，可能导致：

- 新老订单同时存在于市场
- 重复开仓（原本只想要 1 组，结果变成 2 组）
- 资金占用超出预期

解决方案：

```python
class LegacyOrderCleaner:
    def clean(self):
        self.app.reqGlobalCancel()    # 全局取消所有订单
        time.sleep(2)                 # 等待 TWS 处理
        self.app.active_orders.clear()
        self.app.order_status.clear()
```

在每次连接建立后自动执行（`nextValidId` 回调中触发）。

---

## 以铁鹰策略为例的应用场景

铁鹰策略的执行阶段可以概括为：

```
① 获取策略参数（4 个行权价、合约数量、目标价格）
② 查询每条腿的 conId（4 次 reqContractDetails）
③ 创建 BAG 组合合约
④ 创建 Order（LMT + GTC + outsideRth）
⑤ placeOrder() 提交
⑥ 注册到 active_orders
⑦ 进入监控循环（下一章详解）
⑧ 成交 → 记录成交价
```

其中步骤 ② 是最耗时的部分（约 4-12 秒），因为每次 conId 查询都需要一个网络往返。改进方案：在生产系统中可以对常用合约做 conId 缓存，当日有效。

---

## 常见问题

**Q：为什么用 GTC 而不是 DAY？**

铁鹰策略的限价单可能几小时甚至半天才能成交。如果用 DAY，未成交的订单会在收盘后被自动取消，需要次日重新提交。GTC 让订单持续有效直到成交或手动取消。

**Q：多腿订单的风险？**

BAG 合约的多腿订单要么全部成交，要么全不成交（fill-or-kill 的变体）。但需要注意：TWS 会将组合订单的每条腿单独在市场挂单，一旦部分成交后撤单，已成交的腿无法撤销。这也是价格退让机制需要谨慎设计的原因。

**Q：组合订单的价格为什么是负数？**

在铁鹰策略中，组合订单的净价格 = 收入的权利金 - 支出的权利金。通常收入 > 支出，所以净价格为负（表示你是净收到权利金的一方）。这是策略本身的属性，下单方向由 `order.action` 控制。
