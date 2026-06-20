# 风控机制：防抖动、速率限制与超时控制

自动化交易系统面临一个核心矛盾：

> **市场噪声是常态，但每次噪声都触发操作是灾难。**

如果系统对每一次价格波动都做出反应，会在震荡行情中频繁买卖，产生大量交易成本。本章介绍的三种风控组件，用于在"及时响应"和"避免过度反应"之间找到平衡。

---

## 一、防抖动（Debounce）

### 问题

SPX 价格在 5475 和 5485 之间反复震荡。你的止损条件是"价格超出 5420-5540 区间"。

```
时间  价格  是否触发
T1   5485  否（在区间内）
T2   5410  **是！**（超出下界）
T3   5490  否（又回来了）
T4   5405  **是！**（又超出）
T5   5415  否
T6   5400  **是！** ...
```

如果每次触发都立即操作，你会连续发送多次止损订单——其中一些在价格回到区间后又要撤单，造成大量不必要的交易。

### 方案：连续确认

```python
class Debounce:
    """
    要求连续 threshold 次触发，才确认信号。

    threshold: 需要的连续触发次数（默认 3）
    window: 时间窗口（默认 5 秒），超出窗口重置计数器
    """

    def record(self, condition: bool) -> bool:
        """
        记录一次观察。
        condition=True 表示条件满足（价格超限）
        condition=False 表示条件不满足

        返回 True 表示达到阈值，信号确认。
        """
        if time expired since last reset:
            self.counter = 0          # 窗口过期，重置

        if condition:
            self.counter += 1
            if self.counter >= self.threshold:
                self.counter = 0      # 达到阈值，确认信号
                return True
        else:
            self.counter = 0          # 一次不满足就重置
        return False
```

### 效果对比

```
时间  价格  是否超限  Debounce计数器   Debounce输出
T1   5410  Yes       1/3             ❌ 不触发
T2   5412  Yes       2/3             ❌ 不触发
T3   5405  Yes       3/3             ✅ **触发！**
T4   5420  No        0/3 (重置)      ❌
T5   5402  Yes       1/3             ❌ 不触发
T6   5400  Yes       2/3             ❌ 不触发
T7   5398  Yes       3/3             ✅ **触发！**
```

相比原始方案（价格一超限就触发），Debounce 将假信号从 6 次减少到了 2 次，减少了 **67% 的无效操作**。

### 数学原理

假设价格噪声是随机的，单次误触发的概率为 p：

- 原始方案每次超出都触发：错误概率 = p
- Debounce(N=3)：错误概率 = p³

如果 p = 20%（5 次价格波动中有 1 次是噪声），原始方案的错误率为 20%，Debounce(N=3) 的错误率降到 0.8%，**降低了 25 倍**。

---

## 二、速率限制（Rate Limiter）

### 问题

极端行情下，价格可能连续突破多个阈值：

```
T1: 价格跌破下界 → 触发止损
T2: 价格跌破第二个下界 → 再次触发
T3: 价格跌破第三个下界 → 再次触发
...
```

此时系统可能在几秒钟内发出 5-10 个订单。这不仅浪费手续费，而且可能在交易所造成"雪崩效应"——你的多个订单相互竞争，推高成交成本。

### 方案：滑动时间窗口

```python
class RateLimiter:
    """
    在滑动时间窗口内限制操作次数。

    max_operations: 窗口内最多允许的操作次数（默认 3）
    window: 时间窗口长度（默认 60 秒）
    """

    def allow(self) -> bool:
        now = time.time()
        # 清除窗口外的记录
        while self.timestamps and self.timestamps[0] < now - self.window:
            self.timestamps.popleft()

        if len(self.timestamps) >= self.max_operations:
            return False          # 超出限制，拒绝

        self.timestamps.append(now)
        return True
```

### 效果

```
时间  RateLimiter状态        决策
T1    [t1]                  ✅ 允许（1/3）
T2    [t1, t2]              ✅ 允许（2/3）
T3    [t1, t2, t3]          ✅ 允许（3/3）
T4    [t1, t2, t3, t4]      ❌ **拒绝**（超出上限）
T5    [t2, t3, t4, t5] ← t1过期 ❌ 拒绝（超出上限）
T6    [t3, t4, t5, t6]      ❌ 拒绝
T7    [t4, t5, t6, t7]      ❌ 拒绝
T8    [t5, t6, t7, t8] ← t2过期 ✅ 允许（窗口内只有2个操作了）
```

60 秒窗口 + 3 次上限，意味着系统每分钟最多进行 3 次订单操作。在极端行情下，这避免了操作雪崩。

### 与 Debounce 的配合

```
Debounce 负责"这个信号可信吗？"
RateLimiter 负责"我们现在操作太频繁了吗？"

实际流程：
收到价格超限信号
  → Debounce 确认（连续 3 次才放行）
    → RateLimiter 检查（是否超频）
      → 执行操作
```

两层过滤，第一层防误报，第二层防超频。

---

## 三、超时控制（TimeoutGuard）

### 问题

异步操作中，你发送了一个修改订单的请求，然后等待确认。但如果确认永远不来怎么办？

- TWS 可能挂了
- 网络连接可能断了
- 订单可能被 TWS 拒绝了但错误回调丢失了

如果没有超时机制，系统会永久卡在"等待确认"状态，后续所有操作都被阻塞。

### 方案：锁定 + 超时自动释放

```python
class TimeoutGuard:
    """
    跟踪一个待处理操作，在超时后自动释放。

    timeout: 超时时间（默认 10 秒）
    """

    def start(self, label="operation") -> bool:
        """标记一个操作为待处理。返回 False 表示已有操作在途。"""
        if self._pending and (now - self._pending_ts < self.timeout):
            return False              # 已经有操作在等待确认

        self._pending_label = label
        self._pending_ts = now
        return True

    def finish(self):
        """操作完成，释放锁。"""
        self._pending_label = None

    # 10 秒后，即使 finish() 没被调用，锁也会自动释放
    @property
    def is_pending(self):
        if self._pending_label is None:
            return False
        if time.time() - self._pending_ts > self.timeout:
            return False              # 超时，视为已释放
        return True
```

### 为什么是 10 秒？

- IB API 的订单确认通常在 1-3 秒内到达
- 10 秒是 3 倍于正常值的缓冲，足够应对网络延迟
- 超过 10 秒几乎肯定是出问题了，释放锁让系统可以继续运行

---

## 四、三个组件的配合

在一个完整的监控循环中，这三个组件协同工作：

```python
debounce = Debounce(threshold=3, window_seconds=5)
rate_limiter = RateLimiter(max_operations=3, window_seconds=60)
timeout_guard = TimeoutGuard(timeout_seconds=10)

while monitoring:
    price = get_current_price()
    is_out_of_bounds = (price < lower_bound or price > upper_bound)

    # 第一关：防抖动
    if debounce.record(is_out_of_bounds):
        # 第二关：速率限制
        if rate_limiter.allow():
            # 第三关：超时保护
            if timeout_guard.start("stop_loss"):
                send_stop_loss_order()
                # ... 等待确认 ...
                timeout_guard.finish()
```

| 关卡 | 组件 | 防护目标 |
|:-----|:-----|:---------|
| 第一关 | Debounce | 市场噪声导致的假信号 |
| 第二关 | RateLimiter | 极端行情下的操作雪崩 |
| 第三关 | TimeoutGuard | 异步确认丢失导致的死锁 |

三层独立，每一层只关注一个问题。你可以单独调整每一层的参数，而不会影响其他两层——这正是解耦设计的价值。

---

## 以铁鹰策略为例的应用场景

铁鹰策略的止损监控（未公开部分）中使用：

- **Debounce**：SPX 价格超出安全区间后，需要连续 3 次（每次间隔约 1 秒）确认，才认为真的越界了。避免单次异常报价触发止损
- **RateLimiter**：如果价格在区间边界来回穿越，60 秒内最多操作 3 次。避免"止损→撤单→再止损→再撤单"的循环
- **TimeoutGuard**：发送止损订单后，10 秒内未确认则重置状态。如果订单确实没发出去，系统可以重试而不是卡死
