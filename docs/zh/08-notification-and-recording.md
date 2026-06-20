# 交易通知与记录

交易系统的最后一步：把成交结果保存下来，并通知到人。

---

## 一、交易记录：Excel 持久化

### 为什么用 Excel？

生产级别的交易系统通常会使用数据库（SQLite/PostgreSQL）来存储交易记录。但对于个人交易者或小团队，Excel 有几个独特优势：

- **零依赖**：不需要安装数据库，操作系统自带 Excel/开源工具可打开
- **可直接编辑**：复盘时可以手动添加备注、调整格式
- **可视化方便**：直接生成图表做绩效分析
- **不易丢失**：文件备份方便，拷贝到 U 盘或云端都行

### 自动建表机制

```python
class TradeRecorder:
    def __init__(self, file_path, headers):
        self.file_path = file_path
        self.headers = headers      # 列标题

    def _ensure_loaded(self):
        if not os.path.exists(self.file_path):
            # 文件不存在 → 创建新文件 + 写入表头
            workbook = openpyxl.Workbook()
            sheet = workbook.active
            sheet.append(self.headers)
            workbook.save(self.file_path)
        else:
            # 文件存在 → 打开 + 检查表头是否需要更新
            workbook = openpyxl.load_workbook(self.file_path)
            sheet = workbook.active
            self._sync_headers(sheet)   # 补全缺失的列
```

关键设计决策：**懒加载（lazy loading）**。构造函数不打开文件，只在第一次 `record()` 调用时才打开或创建。这样可以避免在策略构建阶段因为 Excel 文件不可用而导致整个程序崩溃。

### 表头同步

随着系统迭代，表头可能会增加新字段。`_sync_headers()` 确保旧文件能自动扩充列：

```python
def _sync_headers(self, sheet):
    existing = [cell.value for cell in sheet[1]]
    if len(existing) < len(self.headers):
        # 在末尾追加缺失的列
        for i in range(len(existing), len(self.headers)):
            col_letter = get_column_letter(i + 1)
            sheet[f"{col_letter}1"] = self.headers[i]
        workbook.save()
```

### 错误隔离

```python
def record(self, data):
    try:
        self._ensure_loaded()
        sheet.append([data.get(h, "") for h in self.headers])
        workbook.save()
    except Exception as exc:
        logger.error("记录交易失败: %s", exc)
        # 绝不因为记录失败而中断交易流程
```

**记录失败不应影响交易**——这个原则很重要。如果在记录时报错（比如 Excel 文件被占用），交易执行不受影响，错误只记日志。

---

## 二、即时通知：Webhook + HMAC 签名

记录成交信息不够——你不可能一直盯着 Excel 看。当订单成交时，系统应该主动通知你。

### 钉钉/飞书/企业微信机器人的通用模式

国内即时通讯工具的机器人 API 遵循相同的模式：

1. 在群聊中创建一个机器人，获得一个 **Webhook URL**
2. 配置一个 **签名密钥（Secret）**
3. 发送 POST 请求，body 包含消息内容
4. 请求带 HMAC-SHA256 签名验证

### HMAC-SHA256 签名

```python
def _sign(secret, webhook_url):
    timestamp = str(round(datetime.now().timestamp() * 1000))
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    signed_url = f"{webhook_url}&timestamp={timestamp}&sign={sign}"
    return signed_url
```

签名的作用：防止 Webhook URL 泄露后被恶意调用——只有知道 Secret 的人才能生成有效的签名。

### 消息发送

```python
def send(self, message):
    signed_url = self._sign()
    payload = {"msgtype": "text", "text": {"content": message}}
    resp = requests.post(signed_url, json=payload, timeout=10)
    resp.raise_for_status()
```

### 消息模板

成交通知应该包含哪些信息？以铁鹰策略为例：

```
SPX 铁鹰策略交易成交通知

成交时间: 2025-09-03 15:45:00 ET
到期日: 2025-09-19
合约数量: 1

期权组合:
  - Buy Put 5265
  - Sell Put 5270
  - Sell Call 5485
  - Buy Call 5490

净收益: $1.25
```

完整的通知还可以包含 SPX 收盘价、VIX 波动率、区间宽度等市场数据，助你在手机上快速判断成交质量。

---

## 三、配置驱动：敏感信息管理

本节所有代码示例中，Webhook URL 和 Secret 都没有硬编码。

**安全实践**：

```python
import os

notifier = WebhookNotifier(
    webhook_url=os.environ["DINGTALK_WEBHOOK_URL"],
    secret=os.environ["DINGTALK_SECRET"],
)
```

在 `.env` 文件或环境变量中管理敏感信息，确保：
- 不提交到 Git 仓库
- 不写入日志文件
- 不嵌入代码

---

## 以铁鹰策略为例的完整流程

成交后，系统自动执行：

```
订单成交
  → 从 orderStatus 回调获取成交价
  → TradeRecorder.record({
       date, time,
       put_sell_strike, put_buy_strike,
       call_sell_strike, call_buy_strike,
       fill_price, quantity
     })
     → Excel 自动创建（如果不是第一次）→ 追加一行 → 保存
  → WebhookNotifier.send("""
       SPX 铁鹰策略交易成交通知
       成交时间: ...
       净收益: $1.25
     """)
     → 钉钉群机器人推送
     → 手机收到通知
```

整个过程耗时 < 1 秒（Excel 保存 + HTTP 请求），对交易流程几乎无影响。
