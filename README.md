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

## 说明：Solana

你提供的 Solana 地址已经单独保存到 `config/solana_wallets.yaml`，但当前监听器基于 EVM JSON-RPC 和 EVM 日志，不能直接用于 Solana。后续需要增加 Solana RPC、WebSocket / `logsSubscribe`、SPL Token 解析及 Solana DEX 适配器后，才能启用该地址。

## 下一步

V0.2 将继续加入同币种多钱包聚合、价格估值和信号评分；Solana 适配器可作为后续跨链扩展加入。

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

BUY / SELL 告警现在使用 HTML 格式，包含：

- 钱包标签与来源
- BUY / SELL / SWAP 类型
- action token 与报价币
- Token 流入流出数量
- DEX 与 Router
- 解析置信度 / Signal Score
- Transfer、Token metadata、DEX route、Contract identity 风险快照
- Chart、Contract、Wallet、Transaction 快捷按钮

没有价格源时不会伪造美元金额、Market Cap、Liquidity 或 Token Age；这些字段将在接入价格与风控数据源后显示。

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
