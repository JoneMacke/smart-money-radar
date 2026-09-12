from __future__ import annotations

import asyncio
import logging

import typer
from rich.console import Console

from app.chains.rpc import JsonRpcClient
from app.config import Settings, load_wallets
from app.listeners.blocks import watch_chain
from app.notifiers.telegram import TelegramNotifier
from app.models import WalletActivity
from app.services.aggregation import SmartMoneyAggregator
from app.services.market import DexScreenerClient
from app.services.scoring import calculate_signal
from app.services.security import GoPlusSecurityClient
from app.services.alerts import should_send_alert
from app.database.repository import PostgresRepository, persist_safely
from app.parsers.evm import activity_from_transaction

logger = logging.getLogger(__name__)

app = typer.Typer(no_args_is_help=True)
console = Console()


def clients(settings: Settings) -> dict[str, JsonRpcClient]:
    return {
        "bsc": JsonRpcClient(settings.bsc_rpc_url, settings.bsc_wss_url, rate_limit_cooldown_seconds=settings.rpc_rate_limit_cooldown_seconds, max_backoff_seconds=settings.rpc_max_backoff_seconds),
        "robinhood": JsonRpcClient(settings.robinhood_rpc_url, settings.robinhood_wss_url, rate_limit_cooldown_seconds=settings.rpc_rate_limit_cooldown_seconds, max_backoff_seconds=settings.rpc_max_backoff_seconds),
    }


@app.command()
def health() -> None:
    """Check configured RPC endpoints."""
    settings = Settings()

    async def run() -> None:
        for chain, rpc in clients(settings).items():
            try:
                result = await rpc.health()
                console.print(
                    f"[green]✓[/green] {chain}: "
                    f"chain_id={result['chain_id']} latest_block={result['latest_block']}"
                )
            except Exception as exc:  # noqa: BLE001
                console.print(f"[red]✗[/red] {chain}: {exc}")

    asyncio.run(run())


@app.command("inspect-tx")
def inspect_tx(
    tx_hash: str,
    wallet_label: str = typer.Option(..., "--wallet", "-w"),
    chain: str = typer.Option("bsc", "--chain", "-c"),
) -> None:
    """Inspect one historical transaction with the current parser."""
    settings = Settings()
    wallets = load_wallets()
    wallet = next((item for item in wallets if item.label.lower() == wallet_label.lower()), None)
    if not wallet:
        raise typer.BadParameter(f"Unknown wallet label: {wallet_label}")
    if chain not in wallet.chains:
        raise typer.BadParameter(f"Wallet {wallet.label} is not configured for {chain}")

    async def run() -> None:
        rpc = clients(settings)[chain]
        tx = await rpc.call("eth_getTransactionByHash", [tx_hash])
        if not tx:
            raise typer.BadParameter(f"Transaction not found: {tx_hash}")
        block_number = int(str(tx["blockNumber"]), 16)
        block = await rpc.get_block(block_number)
        activity = await activity_from_transaction(
            chain,
            wallet,
            tx,
            int(str(block["timestamp"]), 16),
            rpc,
        )
        if activity is None:
            raise typer.BadParameter(
                f"Transaction sender does not match wallet {wallet.label}"
            )
        console.print(activity.short_text())

    asyncio.run(run())


@app.command()
def run() -> None:
    """Run the read-only wallet watcher."""
    settings = Settings()
    wallets = load_wallets()
    if not wallets:
        raise typer.BadParameter("config/wallets.yaml 中还没有有效钱包地址")

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    rpc_by_chain = clients(settings)

    market = DexScreenerClient(timeout=settings.market_data_timeout_seconds)
    security = GoPlusSecurityClient(timeout=settings.market_data_timeout_seconds)
    aggregator = SmartMoneyAggregator(settings.aggregation_window_minutes)

    async def enrich_activity(activity: WalletActivity) -> WalletActivity:
        if settings.market_data_enabled:
            await market.enrich(activity)
        if settings.security_data_enabled:
            await security.enrich(activity)
        activity.smart_money = aggregator.add(activity)
        activity.signal = calculate_signal(activity)
        return activity

    async def run_all() -> None:
        repository = await PostgresRepository.create(settings.database_url)

        async def consume(chain: str) -> None:
            async for activity in watch_chain(
                chain,
                wallets,
                rpc_by_chain[chain],
                max(30, settings.poll_interval_seconds),
                settings.max_catchup_blocks,
                repository.get_last_processed_block if repository else None,
                repository.save_last_processed_block if repository else None,
                max_backoff_seconds=settings.rpc_max_backoff_seconds,
                candidate_concurrency=settings.candidate_concurrency,
                log_scan_enabled=settings.log_scan_enabled,
                block_batch_delay_seconds=settings.block_batch_delay_seconds,
            ):
                activity = await enrich_activity(activity)
                console.print(activity.short_text())
                await persist_safely(repository, activity)
                if should_send_alert(
                    activity,
                    settings.min_alert_score,
                    settings.alert_event_types,
                ):
                    try:
                        await notifier.send_alert(activity)
                    except Exception:  # noqa: BLE001
                        logger.exception("Could not send Telegram alert for %s", activity.tx_hash)
                else:
                    logger.debug(
                        "Telegram alert suppressed for %s: event=%s score=%.1f",
                        activity.tx_hash,
                        activity.event_type,
                        activity.signal.score if activity.signal else activity.confidence * 100,
                    )

        try:
            await asyncio.gather(consume("bsc"), consume("robinhood"))
        finally:
            if repository:
                await repository.close()
            for chain_name, rpc in rpc_by_chain.items():
                logger.info("%s RPC request stats: %s", chain_name, await rpc.stats())
                await rpc.close()

    asyncio.run(run_all())


def cli() -> None:
    app()


if __name__ == "__main__":
    cli()
