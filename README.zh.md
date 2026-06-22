<div align="center">

# 🥘 IBKR Options Cookbook

**IBKR Python 期权自动化交易：架构设计模式与实战指南**

[![GitHub Stars](https://img.shields.io/github/stars/donglinfei-debug/ibkr-options-cookbook?style=flat-square&logo=github)](https://github.com/donglinfei-debug/ibkr-options-cookbook/stargazers)
[![GitHub Issues](https://img.shields.io/github/issues/donglinfei-debug/ibkr-options-cookbook?style=flat-square&logo=github)](https://github.com/donglinfei-debug/ibkr-options-cookbook/issues)
[![GitHub Forks](https://img.shields.io/github/forks/donglinfei-debug/ibkr-options-cookbook?style=flat-square&logo=github)](https://github.com/donglinfei-debug/ibkr-options-cookbook/forks)
[![License](https://img.shields.io/github/license/donglinfei-debug/ibkr-options-cookbook?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg?style=flat-square&logo=python)](https://www.python.org/)
[![IB API](https://img.shields.io/badge/IB_API-Compatible-orange.svg?style=flat-square)](https://www.interactivebrokers.com/)

🌏 **语言 / Language**：[🇨🇳 中文](README.zh.md) | [🇬🇧 English](README.md)

</div>

---

Interactive Brokers (IBKR) API 期权自动化交易的开源知识库。这不是一个"复制粘贴就能跑的交易机器人"——而是一本**架构设计模式的食谱**，以干净、模块化的代码作为载体。

## 🏗️ 架构总览

```mermaid
flowchart TB
    subgraph Demo["🧪 策略示例"]
        ICD[iron_condor_demo.py]
    end

    subgraph Core["🧱 核心模块 (src/)"]
        CM[connection_manager.py<br/>单例 · 线程安全 ·<br/>ClientID 分配 · 自动重连]
        MD[market_data.py<br/>SPX 快照与流式数据<br/>交易时段 · 行权价生成]
        OC[option_chain.py<br/>合约构建 · 期权链获取<br/>批量解析 · Delta 过滤]
        OL[order_lifecycle.py<br/>订单追踪 · 修改保护<br/>残留订单清理]
        RC[risk_controls.py<br/>防抖 · 限流器 ·<br/>超时保护]
        NF[notifier.py<br/>Webhook · HMAC-SHA256<br/>钉钉 / 自定义端点]
        TR[trade_recorder.py<br/>Excel 交易日志<br/>自动创建 · 追加写入]
    end

    subgraph External["🌐 外部系统"]
        TWS[TWS / IB Gateway]
        WEB[Webhook 端点]
        XLS[Excel 文件]
    end

    ICD --> CM
    ICD --> MD
    ICD --> OC
    ICD --> OL
    ICD --> RC
    ICD --> NF
    ICD --> TR

    CM <--> TWS
    MD <--> TWS
    OC <--> TWS
    OL <--> TWS

    NF --> WEB
    TR --> XLS

    style ICD fill:#6366f1,color:#fff,stroke:none
    style CM fill:#0ea5e9,color:#fff,stroke:none
    style MD fill:#0ea5e9,color:#fff,stroke:none
    style OC fill:#0ea5e9,color:#fff,stroke:none
    style OL fill:#0ea5e9,color:#fff,stroke:none
    style RC fill:#0ea5e9,color:#fff,stroke:none
    style NF fill:#0ea5e9,color:#fff,stroke:none
    style TR fill:#0ea5e9,color:#fff,stroke:none
    style TWS fill:#f59e0b,color:#fff,stroke:none
    style WEB fill:#10b981,color:#fff,stroke:none
    style XLS fill:#10b981,color:#fff,stroke:none
```

## 📖 这是什么？

如果你在用 IB API 构建自动化期权交易系统，你可能遇到过这些问题：

- 多个模块共享一个 TWS 连接时，如何避免 **ClientID 冲突**？
- 如何高效地**批量获取期权链数据**？
- 如何**自动调整限价单价格**以提高成交率？
- 如何在剧烈波动行情中**防止风控被假信号击穿**？

**ibkr-options-cookbook** 以 **SPX 铁鹰策略（Iron Condor）** 作为贯穿案例来回答这些问题。每章覆盖一个设计主题，并附有干净、可独立使用的参考代码。

## 📦 系统要求

| 要求 | 最低 | 推荐 |
|:-----|:-----|:-----|
| **Python** | 3.8 | 3.11+ |
| **TWS / IB Gateway** | Build 978+ | 最新稳定版 |
| **内存** | 256 MB | 512 MB+ |
| **行情数据订阅** | 仅快照 | 实时流式数据 |
| **操作系统** | Windows / macOS / Linux | — |

## 📂 目录结构

```
ibkr-options-cookbook/
├── docs/
│   ├── zh/          ← 中文文档（共 9 章）
│   └── en/          ← 英文文档（共 9 章）
├── src/
│   ├── connection_manager.py   ← 连接：单例、ClientID、自动重连
│   ├── market_data.py          ← 数据：SPX 快照/流式、行权价生成
│   ├── option_chain.py         ← 链：合约构建、批量获取、Delta 过滤
│   ├── order_lifecycle.py      ← 订单：追踪、修改保护、残留清理
│   ├── risk_controls.py        ← 风控：防抖、限流器、超时保护
│   ├── notifier.py             ← 通知：钉钉 Webhook、HMAC-SHA256
│   └── trade_recorder.py       ← 日志：Excel 交易记录
├── examples/
│   └── iron_condor_demo.py     ← 示例：铁鹰策略流程演示
├── README.md
├── README.zh.md
├── LICENSE                     ← MIT
└── .env.example                ← 配置模板
```

## 📚 章节

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

## 🚀 快速开始

```bash
# 1. 克隆仓库
git clone https://github.com/donglinfei-debug/ibkr-options-cookbook.git
cd ibkr-options-cookbook

# 2. （推荐）创建虚拟环境
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. 安装依赖
pip install ibapi pytz pandas openpyxl requests

# 4. 开始阅读
# 从 docs/zh/01-architecture.md 开始
```

> **注意**：`ibapi` 是盈透证券官方 Python API，**不在 PyPI 上**。请从 TWS/IB Gateway 安装目录或 [IB GitHub](https://github.com/InteractiveBrokers/tws-api) 获取。

## 💻 技术栈

| 组件 | 技术 |
|:-----|:-----|
| **券商 API** | Interactive Brokers (`ibapi`) |
| **数据处理** | `pandas`、`pytz` |
| **消息通知** | `requests`（钉钉 Webhook、HMAC-SHA256） |
| **交易记录** | `openpyxl`（Excel） |
| **Python** | 3.8+ |

## ⚠️ 重要说明

1. **这不是一个可直接运行的交易机器人。** 它是一本架构设计模式的知识库，包含设计决策和精选代码片段——不是完整的交易策略实现。
2. **交易有风险。** 所有代码仅用于学习参考。实盘前务必充分测试。交易风险自负。
3. **核心策略参数未公开。** 铁鹰策略的具体参数（Delta 阈值、权利金范围、特殊调整价格等）是作者的专有交易经验，不在本仓库范围内。

## 📄 许可证

[MIT](LICENSE)

## 🌟 Star 历史

[![Star History Chart](https://api.star-history.com/svg?repos=donglinfei-debug/ibkr-options-cookbook&type=Date)](https://star-history.com/#donglinfei-debug/ibkr-options-cookbook&Date)

如果你觉得这个项目有用，欢迎点 ⭐ Star——这是对持续更新的最大鼓励。

## 📬 联系

- **作者**: Ryan Dong
- **邮箱**: donglinfei@gmail.com（商务 / 招聘联系）
