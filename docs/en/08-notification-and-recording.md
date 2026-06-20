# Trade Notifications & Recording

The final step in any trading system: persist the execution results and notify the right people.

---

## 1. Trade Recording: Excel Persistence

### Why Excel?

Production-grade trading systems typically use a database (SQLite/PostgreSQL) to store trade records. For individual traders or small teams, though, Excel offers several unique advantages:

- **Zero dependencies**: No database setup required; files can be opened with Excel or any spreadsheet tool
- **Directly editable**: Add annotations, adjust formatting, or tweak values during post-trade review
- **Easy visualization**: Generate charts and performance analyses on the fly
- **Portable**: Simple to back up—copy to a USB drive or cloud storage

### Auto-Creation on First Use

```python
class TradeRecorder:
    def __init__(self, file_path, headers):
        self.file_path = file_path
        self.headers = headers      # column headers

    def _ensure_loaded(self):
        if not os.path.exists(self.file_path):
            # file doesn't exist → create new workbook + write headers
            workbook = openpyxl.Workbook()
            sheet = workbook.active
            sheet.append(self.headers)
            workbook.save(self.file_path)
        else:
            # file exists → open + sync headers if needed
            workbook = openpyxl.load_workbook(self.file_path)
            sheet = workbook.active
            self._sync_headers(sheet)   # fill in missing columns
```

Key design decision: **lazy loading**. The constructor does not open the file; it only opens or creates it on the first `record()` call. This prevents an unavailable Excel file from crashing the entire program during strategy initialization.

### Header Synchronization

As the system evolves, new fields may be added to the headers. `_sync_headers()` ensures old files automatically expand their columns:

```python
def _sync_headers(self, sheet):
    existing = [cell.value for cell in sheet[1]]
    if len(existing) < len(self.headers):
        # append missing columns at the end
        for i in range(len(existing), len(self.headers)):
            col_letter = get_column_letter(i + 1)
            sheet[f"{col_letter}1"] = self.headers[i]
        workbook.save()
```

### Error Isolation

```python
def record(self, data):
    try:
        self._ensure_loaded()
        sheet.append([data.get(h, "") for h in self.headers])
        workbook.save()
    except Exception as exc:
        logger.error("Failed to record trade: %s", exc)
        # never let a recording failure disrupt the trading flow
```

**A recording failure must never block trading**—this principle is critical. If an error occurs during recording (e.g., the Excel file is locked by another process), trading continues unaffected and the error is simply logged.

---

## 2. Instant Notifications: Webhook + HMAC Signature

Recording trade data isn't enough—you can't stare at an Excel sheet all day. When an order fills, the system should proactively notify you.

### Common Pattern: DingTalk / Feishu / WeCom Bots

Domestic instant-messaging bots all follow the same pattern:

1. Create a bot in a group chat to obtain a **Webhook URL**
2. Configure a **signing secret**
3. Send a POST request with the message content in the body
4. Include an HMAC-SHA256 signature for verification

### HMAC-SHA256 Signing

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

Why signing matters: it prevents malicious calls if the Webhook URL is leaked—only someone who knows the secret can generate a valid signature.

### Sending Messages

```python
def send(self, message):
    signed_url = self._sign()
    payload = {"msgtype": "text", "text": {"content": message}}
    resp = requests.post(signed_url, json=payload, timeout=10)
    resp.raise_for_status()
```

### Message Template

What information should a fill notification contain? Using the Iron Condor as an example:

```
SPX Iron Condor Trade Execution

Fill Time: 2025-09-03 15:45:00 ET
Expiration: 2025-09-19
Quantity: 1

Option Legs:
  - Buy Put 5265
  - Sell Put 5270
  - Sell Call 5485
  - Buy Call 5490

Net Premium: $1.25
```

A comprehensive notification can also include market data such as the SPX close, VIX level, and spread width—helping you assess trade quality at a glance on your phone.

---

## 3. Configuration-Driven: Sensitive Information Management

In all code examples throughout this chapter, the Webhook URL and secret are never hardcoded.

**Security Best Practices**:

```python
import os

notifier = WebhookNotifier(
    webhook_url=os.environ["DINGTALK_WEBHOOK_URL"],
    secret=os.environ["DINGTALK_SECRET"],
)
```

Manage sensitive information in `.env` files or environment variables, ensuring:

- It is not committed to the Git repository
- It is not written to log files
- It is not embedded in source code

---

## End-to-End Flow: Iron Condor Example

After a fill, the system automatically executes:

```
Order filled
  → Receive fill price from orderStatus callback
  → TradeRecorder.record({
       date, time,
       put_sell_strike, put_buy_strike,
       call_sell_strike, call_buy_strike,
       fill_price, quantity
     })
     → Excel auto-creates (if first time) → append row → save
  → WebhookNotifier.send("""
       SPX Iron Condor Trade Execution
       Fill Time: ...
       Net Premium: $1.25
     """)
     → DingTalk group bot pushes notification
     → Phone receives alert
```

The entire process takes less than 1 second (Excel save + HTTP request), adding negligible overhead to the trading flow.
