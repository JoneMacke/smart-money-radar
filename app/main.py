from __future__ import annotations

import asyncio
import logging

import typer
from rich.console import Console

from app.chains.rpc import JsonRpcClient
from app.config import Settings, load_wallets
from app.listeners.blocks import watch_chain
from app.notifiers.telegram import TelegramNotifier

app = typer.Typer(no_args_is_help=True)
console = Console()


def clients(settings: Settings) -> dict[str, JsonRpcClient]:
    return {
        "bsc": JsonRpcClient(settings.bsc_rpc_url, settings.bsc_wss_url),
        "robinhood": JsonRpcClient(settings.robinhood_rpc_url, settings.robinhood_wss_url),
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
    # RPC request logging is noisy; watcher warnings and matched activities remain visible.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    rpc_by_chain = clients(settings)

    async def consume(chain: str) -> None:
        async for activity in watch_chain(
            chain,
            wallets,
            rpc_by_chain[chain],
            settings.poll_interval_seconds,
        ):
            console.print(activity.short_text())
            await notifier.send(activity.short_text())

    async def run_all() -> None:
        await asyncio.gather(consume("bsc"), consume("robinhood"))

    asyncio.run(run_all())


def cli() -> None:
    app()


if __name__ == "__main__":
    cli()
