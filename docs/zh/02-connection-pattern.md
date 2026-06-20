# 连接管理设计模式

## 问题：IB API 多模块连接冲突

任何一个基于 IB API 的自动化交易系统都会面临同一个基础问题：

> 数据获取、期权链查询、订单执行、策略监控……这些模块都需要与 TWS 通信。它们应该共用同一个连接，还是各自创建连接？

如果各自创建连接：

```python
# 模块 A：获取 SPX 行情
app_a = IBApp()
app_a.connect("127.0.0.1", 7497, clientId=random.randint(1, 1000))

# 模块 B：查询期权链
app_b = IBApp()
app_b.connect("127.0.0.1", 7497, clientId=random.randint(1, 1000))  # 可能冲突！
```

这会带来两个问题：

1. **ClientID 冲突**：TWS 要求每个连接的 ClientID 唯一。如果用随机数生成，概率上会撞车；如果用固定值，模块间又可能互相抢占。
2. **资源浪费**：每个连接独立维护消息线程、独立处理回调。三个连接就意味着三倍的线程开销和回调处理逻辑。

## 方案：单例 + 固定 ClientID 分配

### 单例模式

```python
class ConnectionManager(EWrapper, EClient):
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:  # 双重检查
                    cls._instance = super().__new__(cls)
        return cls._instance
```

关键设计决策：

- **双重检查锁定（double-checked locking）**：第一次检查避免每次调用都加锁，第二次检查保证线程安全
- **`__init__` 防重复初始化**：通过 `_initialized` 标记确保构造函数只执行一次

### 固定 ClientID 分配

```python
CLIENT_ID_MAP = {
    "module_a": 101,
    "module_b": 102,
    "module_c": 103,
    "module_d": 104,
    "default": 100,
}
```

每个模块注册一个固定 ID。如果模块 A 和 B 先后调用 `connect()`，管理器检测到已有连接且 ID 相同则跳过，ID 不同则先断开再重连。

---

## 连接生命周期

一个 TWS 连接从创建到销毁经历以下状态：

```
[初始化] → [连接中] → [已连接·等待 nextValidId] → [已就绪]
              ↓ 失败                                  ↓ 断开
           [重试中] ← 循环 → [已达最大次数] → [失败]   [已断开]
```

### 连接受阻时的自动重试

```python
for attempt in range(1, max_retries + 1):
    try:
        super().connect("127.0.0.1", port, clientId=cid)
        # 启动消息线程
        thread = threading.Thread(target=self.run, daemon=True)
        thread.start()
        # 等待 nextValidId（连接确认信号）
        while self.nextorder_id is None:
            if time.time() - t0 > timeout:
                raise TimeoutError("连接超时")
            time.sleep(0.1)
        # 连接成功
        return True
    except Exception:
        # 重试
```

几个要点：

- **`run()` 在独立线程中执行**，否则会阻塞主流程
- **等待 `nextValidId` 才是真正的连接完成**，不是 `connect()` 返回就成功了
- **超时保护**：网络不稳定时不会卡死

---

## 订单 ID 管理

TWS 对于每个连接维护一个递增的订单 ID 序列。多线程环境下同时取号会导致重复 ID。

解决方案：线程安全的计数器。

```python
def get_next_order_id(self) -> int:
    with self._state_lock:
        if self.nextorder_id is None:
            raise RuntimeError("未连接，无法获取订单 ID")
        oid = self.nextorder_id
        self.nextorder_id += 1
        return oid
```

---

## 连接状态监控

`get_connection_status()` 返回当前连接的完整快照：

```python
{
    "connected": True,
    "port": 7497,
    "client_id": 101,
    "next_order_id": 42,
    "active_modules": ["module_a", "module_b"],
}
```

可用于：
- 定时健康检查（`check_connection()` 支持自动重连）
- 监控面板展示
- 异常告警触发

---

## 以铁鹰策略为例的应用场景

在铁鹰策略的生命周期中，连接管理器被以下模块调用：

| 阶段 | 调用模块 | ClientID | 说明 |
|:-----|:---------|:---------|:-----|
| 获取 SPX 行情 | `MarketDataFetcher` | 102 | 快照模式，获取即断 |
| 查询期权链 | `OptionChainFetcher` | 103 | 批量请求，50个/批 |
| 执行订单 | `OrderManager` | 104 | 持续监控，长时间占用 |
| 收盘监控（未公开） | 策略监控模块 | 105 | 流式模式，保持连接 |

这四个模块并发运行时，如果各自独立连接，至少需要 4 个不同的 ClientID。而通过单例管理器，它们可以共享 1-2 个连接（数据类共享一个，订单类独占一个），大幅降低了 ID 管理的复杂度。

---

## 常见问题

**Q：为什么不用随机 ClientID？**

随机 ID 在单次运行中通常不会冲突，但程序重启后，TWS 可能还保留着旧连接的会话，此时新连接用随机 ID 可能碰巧与旧 ID 相同，触发 "already in use"（错误码 501/502）。固定 ID 加单例连接彻底避免了这个问题。

**Q：如果 TWS 重启了怎么办？**

`check_connection()` 检测到连接断开时会自动触发重连。配合定时调用（例如每 30 秒检查一次），可以在 TWS 恢复后自动恢复交易。

**Q：连接成功但收不到数据？**

最常见的两个原因：
1. **未订阅市场数据**：在 TWS 的"市场数据订阅"中确认已订阅 SPX/SPXW 数据（错误码 10197）
2. **端口不对**：正式账户 7496，模拟账户 7497，混淆了会导致连接失败
