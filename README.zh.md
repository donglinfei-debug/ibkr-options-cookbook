# ibkr-options-cookbook 🥘

> IBKR Python 期权自动化交易：架构设计模式与实战指南

一个面向 **Interactive Brokers (IBKR) API** 期权交易者的开源知识库。不是"可复制的交易机器人"，而是**一本以代码形式呈现的架构设计食谱**。

## 📖 这是什么？

如果你正在用 IB API 做期权自动化交易，一定会遇到这些问题：

- 多模块连接 TWS 时 ClientID 总是冲突？
- 期权链数据怎么批量获取才高效？
- 限价单提交后怎么自动调整价格提高成交率？
- 震荡行情中如何避免风控系统被假信号刷爆？

**ibkr-options-cookbook** 用 **SPX 铁鹰策略（Iron Condor）** 作为贯穿案例，逐一回答这些问题。每章对应一个独立的设计主题，配有精选的模块化代码。

## 📂 目录结构

```
docs/
├── zh/      ← 中文文档（9 章）
└── en/      ← English docs (9 chapters)

src/         ← 精选代码模块（英文注释，可独立使用）
├── connection_manager.py    ← 单例连接管理
├── market_data.py           ← 市场数据获取
├── option_chain.py          ← 期权链工具
├── order_lifecycle.py       ← 订单生命周期管理
├── risk_controls.py         ← 风控组件（Debounce/RateLimiter）
├── notifier.py              ← Webhook 通知
└── trade_recorder.py        ← Excel 交易记录

examples/
└── iron_condor_demo.py      ← 铁鹰策略案例演示
```

## 📚 文档章节

| # | 中文 | English |
|:-|:-----|:--------|
| 1 | [系统架构全景](docs/zh/01-architecture.md) | [Architecture Overview](docs/en/01-architecture.md) |
| 2 | [连接管理设计模式](docs/zh/02-connection-pattern.md) | [Connection Pattern](docs/en/02-connection-pattern.md) |
| 3 | [市场数据获取](docs/zh/03-market-data.md) | [Market Data](docs/en/03-market-data.md) |
| 4 | [合约管理与期权链](docs/zh/04-contracts-and-chain.md) | [Contracts & Chain](docs/en/04-contracts-and-chain.md) |
| 5 | [订单执行与生命周期](docs/zh/05-order-lifecycle.md) | [Order Lifecycle](docs/en/05-order-lifecycle.md) |
| 6 | [价格退让机制](docs/zh/06-price-adjustment.md) | [Price Adjustment](docs/en/06-price-adjustment.md) |
| 7 | [风控机制](docs/zh/07-risk-control.md) | [Risk Control](docs/en/07-risk-control.md) |
| 8 | [交易通知与记录](docs/zh/08-notification-and-recording.md) | [Notification & Recording](docs/en/08-notification-and-recording.md) |
| 9 | [铁鹰策略案例全流程](docs/zh/09-iron-condor-case-study.md) | [Case Study: Iron Condor](docs/en/09-iron-condor-case-study.md) |

## 💻 技术栈

- **交易平台**：Interactive Brokers（TWS / IB Gateway）
- **API 库**：`ibapi`（IB官方 Python API）
- **Python 版本**：3.8+
- **依赖**：`ibapi`, `pytz`, `pandas`, `openpyxl`, `requests`

## ⚠️ 重要说明

1. **这不是一个可运行的交易机器人**。这是一个知识库，提供的是设计模式、架构思路和精选代码片段，而非完整的交易策略实现。
2. **交易有风险**。所有代码仅供学习参考，实盘使用前请充分测试，盈亏自负。
3. **核心策略参数不在此公开**。铁鹰策略的具体参数（Delta 阈值、信用区间、退让特殊值等）是作者的实战积累，不在本仓库范围内。

## 📄 许可

MIT License

## 🌟 给星

如果这个仓库对你有帮助，欢迎Star ⭐ 你的支持是持续更新的动力。

## 📬 联系方式

- **作者**: Ryan Dong
- **Email**: donglinfei@gmail.com（商务/招聘联系）
