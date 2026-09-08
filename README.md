# Multi-Chain Smart Money Radar

V0.1 MVP：只读监听 BSC 与 Robinhood Chain 上的指定钱包，并通过 Telegram 推送交易活动。

## 已录入钱包

- `Unipcs`（来源：FOMO）
  - Robinhood Chain：已启用监听
  - Solana：已记录，Solana 适配器尚未实现

## 当前能力

- HTTP JSON-RPC 健康检查：Chain ID、最新区块
- WebSocket `newHeads` 实时订阅
- 监听配置中的钱包地址
- 获取命中钱包的交易回执
- 解析 ERC-20 `Transfer` 日志
- 生成 Token 流向活动候选
- Telegram 通知（未配置 Bot Token 时只打印到日志）
- Docker Compose：Radar + Redis + PostgreSQL

> 当前版本是**只读监控**，不会请求私钥、助记词，也不会自动发送链上交易。

## 快速开始

1. 复制环境变量：

```powershell
Copy-Item .env.example .env
```

2. 填入 RPC / WSS 和 Telegram 配置。不要把真实 Key 提交到 Git。

3. 编辑 `config/wallets.yaml`，加入要监控的 EVM 钱包地址。

4. 本地运行：

```powershell
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
pip install -e .
python -m app.main health
python -m app.main run
```

或使用 Docker：

```powershell
docker compose up --build
```

## 环境变量

- `BSC_RPC_URL` / `BSC_WSS_URL`
- `ROBINHOOD_RPC_URL` / `ROBINHOOD_WSS_URL`
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`（可选）
- `DATABASE_URL`（可选；配置后启用 PostgreSQL 持久化）
- `POLL_INTERVAL_SECONDS`（WebSocket 断线后的重试间隔）
- `MARKET_DATA_ENABLED` / `SECURITY_DATA_ENABLED`
- `MARKET_DATA_TIMEOUT_SECONDS` / `AGGREGATION_WINDOW_MINUTES`
- `MIN_ALERT_SCORE`（Telegram 最低推送分数，默认 60）
- `ALERT_EVENT_TYPES`（Telegram 推送事件类型，默认 `BUY,SELL,SWAP`；其他活动仍会保存到 PostgreSQL）

## 说明：Solana

你提供的 Solana 地址已经单独保存到 `config/solana_wallets.yaml`，但当前监听器基于 EVM JSON-RPC 和 EVM 日志，不能直接用于 Solana。后续需要增加 Solana RPC、WebSocket / `logsSubscribe`、SPL Token 解析及 Solana DEX 适配器后，才能启用该地址。

## 下一步

价格、流动性、合约风控和 Smart Money 聚合已接入；后续可继续完善 Solana 适配器、更多 DEX 专属事件和后台 Dashboard。

## 历史交易解析验证

可以用当前解析器检查任意已确认交易：

```powershell
python -m app.main inspect-tx 0x交易哈希 --wallet test --chain bsc
```

输出会包含：

- BUY / SELL / SWAP 分类与置信度
- Router 名称
- Pair 所属 DEX/Factory
- action token 与 quote token
- Token symbol、decimals 和格式化后的数量

BSC 的 Router、Factory 与报价币配置位于 `config/dexes.yaml`。

## Telegram 告警格式

BUY / SELL 告警现在使用紧凑的 HTML 卡片格式，以中文为主，包含：

- 清晰的买入 / 卖出信号标题与 Token 交易对
- 钱包标签、来源、链、DEX 与 Router
- 交易金额、当前市值、流动性、交易对年龄
- 资金流向、Smart Money 共识与窗口合计
- 流动性、持仓集中、开发者、合约安全风险概览
- 0–100 信号评分与可视化评分条
- 中文快捷按钮：看图、合约、钱包、交易详情

完整交易哈希不再放在正文中，仅通过底部“交易详情”按钮访问，减少视觉噪音。没有价格源时不会伪造美元金额、Market Cap、Liquidity 或 Token Age；缺失字段显示为 `—`。

监听同时使用 WebSocket 新区块订阅和 HTTP 区块高度轮询兜底。WebSocket 断线时会自动退避重连，并从最近已处理区块继续补齐，降低 RPC 连接抖动造成的漏监听风险。

## Telegram 告警过滤

Telegram 默认只推送 `BUY`、`SELL`、`SWAP`，并且 Signal Score 必须达到 `MIN_ALERT_SCORE`（默认 60）。`CONTRACT_CALL`、`TRANSFER` 等低行动价值活动仍会写入 PostgreSQL，但不会发送 Telegram，从而减少提醒噪音。

## PostgreSQL 持久化

设置 `DATABASE_URL` 后，启动监听会自动创建以下表并保存：

- `radar_transactions`：钱包交易与 BUY / SELL 信号
- `radar_transfers`：ERC-20 Transfer 明细
- `radar_tokens`：Token symbol、name、decimals
- `radar_pairs`：Pair / Pool 的 token0、token1、Factory、协议
- `radar_pair_swaps`：V2 / V3 Pool Swap 明细
- `radar_signals`：历史信号分数

Docker Compose 中的 PostgreSQL 默认连接串已经写入 `.env.example`。不设置 `DATABASE_URL` 时，程序仍会正常监听，只是不保存数据库。

## BSC DEX 与 V3

DEX 注册表位于 `config/dexes.yaml`，当前包括 PancakeSwap V2、PancakeSwap V3、Biswap V2、PancakeSwap Smart Router 与已观察到的聚合 Router。解析器支持 Uniswap-compatible / PancakeSwap V3 的 `Swap` 事件，并与现有 V2 Pair 路由统一分类。

## 市场数据与完整评分

启动监听时会按需调用公共只读数据源：

- DexScreener：价格、Market Cap、Liquidity、Pair Age、交易估值
- GoPlus Token Security：开源、代理、Honeypot、税率、Top Holder、Creator 等风险字段

数据源不可用时，链上交易解析和 Telegram 告警仍会继续，缺失字段显示为 `—`，不会用估算值冒充真实数据。

Smart Money 聚合默认按 `AGGREGATION_WINDOW_MINUTES`（默认 60 分钟）统计同一 Token、同一方向的多个监控钱包。完整 Signal Score 由方向解析、钱包权重、交易规模、Smart Money 共识、流动性和合约风险组成。
