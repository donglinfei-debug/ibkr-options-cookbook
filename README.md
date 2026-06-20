# ibkr-options-cookbook 🥘

> IBKR Python Options Automation: Architecture Patterns & Practical Guide

An open-source knowledge base for **Interactive Brokers (IBKR) API** options traders. This is not a "copy-paste trading bot" — it's a **cookbook of architecture patterns**, with code as the medium.

## 📖 What Is This?

If you're building automated options trading systems on the IB API, you've faced these questions:

- How to manage multiple modules sharing a single TWS connection without ClientID conflicts?
- How to batch-fetch option chain data efficiently?
- How to automatically adjust limit order prices to improve fill rates?
- How to prevent risk controls from being overwhelmed by market noise in volatile conditions?

**ibkr-options-cookbook** answers these questions using the **SPX Iron Condor** strategy as a running case study. Each chapter covers one design topic, accompanied by clean, modular reference code.

## 📂 Structure

```
docs/
├── zh/      ← Chinese documentation (9 chapters)
└── en/      ← English documentation (9 chapters)

src/         ← Reference code modules (English comments, independently usable)
├── connection_manager.py    ← Singleton connection management
├── market_data.py           ← Market data fetching
├── option_chain.py          ← Option chain utilities
├── order_lifecycle.py       ← Order lifecycle management
├── risk_controls.py         ← Risk control components (Debounce/RateLimiter)
├── notifier.py              ← Webhook notifications
└── trade_recorder.py        ← Excel trade journal

examples/
└── iron_condor_demo.py      ← Iron Condor demo script
```

## 📚 Chapters

| # | English | Chinese |
|:-|:--------|:--------|
| 1 | [Architecture Overview](docs/en/01-architecture.md) | [系统架构全景](docs/zh/01-architecture.md) |
| 2 | [Connection Pattern](docs/en/02-connection-pattern.md) | [连接管理设计模式](docs/zh/02-connection-pattern.md) |
| 3 | [Market Data](docs/en/03-market-data.md) | [市场数据获取](docs/zh/03-market-data.md) |
| 4 | [Contracts & Chain](docs/en/04-contracts-and-chain.md) | [合约管理与期权链](docs/zh/04-contracts-and-chain.md) |
| 5 | [Order Lifecycle](docs/en/05-order-lifecycle.md) | [订单执行与生命周期](docs/zh/05-order-lifecycle.md) |
| 6 | [Price Adjustment](docs/en/06-price-adjustment.md) | [价格退让机制](docs/zh/06-price-adjustment.md) |
| 7 | [Risk Control](docs/en/07-risk-control.md) | [风控机制](docs/zh/07-risk-control.md) |
| 8 | [Notification & Recording](docs/en/08-notification-and-recording.md) | [交易通知与记录](docs/zh/08-notification-and-recording.md) |
| 9 | [Case Study: Iron Condor](docs/en/09-iron-condor-case-study.md) | [铁鹰策略案例全流程](docs/zh/09-iron-condor-case-study.md) |

## 💻 Tech Stack

- **Platform**: Interactive Brokers (TWS / IB Gateway)
- **API**: `ibapi` (official IB Python API)
- **Python**: 3.8+
- **Dependencies**: `ibapi`, `pytz`, `pandas`, `openpyxl`, `requests`

## ⚠️ Important Notes

1. **This is NOT a runnable trading bot.** It is a knowledge base covering design patterns, architecture decisions, and curated code snippets — not a complete trading strategy implementation.
2. **Trading involves risk.** All code is for educational reference only. Test thoroughly before live use. Trade at your own risk.
3. **Core strategy parameters are not published here.** The Iron Condor's specific parameters (Delta thresholds, credit ranges, special adjustment prices, etc.) are the author's proprietary trading experience and are outside the scope of this repository.

## 📄 License

MIT

## 🌟 Star

If you find this useful, please consider starring ⭐ the repository — it motivates continued updates.

## 📬 Contact

- **Email**: donglinfei@gmail.com (business / recruiting inquiries)
