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
- `POLL_INTERVAL_SECONDS`（WebSocket 断线后的重试间隔）

## 说明：Solana

你提供的 Solana 地址已经单独保存到 `config/solana_wallets.yaml`，但当前监听器基于 EVM JSON-RPC 和 EVM 日志，不能直接用于 Solana。后续需要增加 Solana RPC、WebSocket / `logsSubscribe`、SPL Token 解析及 Solana DEX 适配器后，才能启用该地址。

## 下一步

V0.2 将加入 PostgreSQL 持久化、同币种多钱包聚合和信号评分；V0.3 再接入 DEX 路由解析、新币和 Dev 风控。Solana 适配器可作为后续跨链扩展加入。
