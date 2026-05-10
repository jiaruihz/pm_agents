"""Main entrypoint for the Unified Async Trading Engine."""

import asyncio
import logging
import signal
import sys
from typing import Optional

import argparse

from src.platform.engine.dispatcher import EventDispatcher
from src.platform.engine.registry import StrategyRegistry
from src.platform.engine.base import StrategyContext
from src.platform.market_data.feeder import MarketDataFeeder
from src.platform.execution.executor import ExecutionService
from src.platform.notification.telegram_bot import TelegramNotificationListener
from src.platform.strategy_runtime.store import StrategyRuntimeStore

from src.strategies.arb.unified_arb import UnifiedArbStrategy
from src.strategies.weather_edge_v1.tools.unified_strategy import UnifiedWeatherEdgeStrategy
from src.strategies.copy_trade.unified_copy_trade import UnifiedCopyTradingStrategy


# Configure basic logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


async def main() -> None:
    """Wire up the architecture and start the engine."""
    parser = argparse.ArgumentParser(description="Run the Unified Trading Engine.")
    parser.add_argument("--strategy", type=str, default="arb", choices=["arb", "weather", "copy_trade"],
                        help="The strategy to run.")
    args = parser.parse_args()

    # 0. Infrastructure & Configuration
    # We will use test tokens for Polymarket (e.g. Trump tokens)
    token_ids = [
        "25656910626330058925565578768222941968840428416801948480352526848777196014290", # Trump Yes
    ]
    db_path = "runtime/strategy_runtime.db"
    
    # Setup Telegram (Use env vars in real life)
    bot_token = ""
    chat_id = ""
    
    # 1. Instantiate Core Layers
    command_queue: asyncio.Queue = asyncio.Queue()
    
    dispatcher = EventDispatcher()
    runtime_store = StrategyRuntimeStore(db_path=db_path)
    
    
    registry = StrategyRegistry(dispatcher=dispatcher, command_queue=command_queue)
    executor = ExecutionService(
        dispatcher=dispatcher,
        command_queue=command_queue,
        runtime_store=runtime_store,
        gamma_client=None
    )
    
    feeder = MarketDataFeeder(
        dispatcher=dispatcher,
        ws_url="wss://ws-subscriptions-clob.polymarket.com/ws/market",
        token_ids=token_ids,
        poll_interval_sec=1.0
    )
    
    notifier = TelegramNotificationListener(
        dispatcher=dispatcher,
        bot_token=bot_token,
        chat_id=chat_id
    )

    def _shutdown_handler() -> None:
        logger.info("Shutdown signal received!")
        asyncio.create_task(shutdown(dispatcher, executor, feeder, notifier, runtime_store))

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown_handler)

    # 2. Register Strategies
    # Here we simulate loading a strategy instance from DB/Config
    instance_id = f"pilot_{args.strategy}_test_001"
    
    # Mark it as running in DB
    runtime_store.upsert_start(
        instance_id=instance_id,
        strategy_key=f"unified_{args.strategy}",
        label=f"Pilot {args.strategy.capitalize()} Testing",
        execution_mode="DRY_RUN",
        market_data_source="PM_WS",
        account_id="dev_01",
        wallet_address="0x0",
        token_ids=token_ids,
        max_position=100.0,
        telegram_enabled=False,
        pid=0,
        log_file="",
        metrics_path="",
        cwd=""
    )

    run_params = {"max_trades": 3, "usdc_balance": 1000.0}
    strategy_instance = None
    
    if args.strategy == "arb":
        strategy_instance = UnifiedArbStrategy()
    elif args.strategy == "weather":
        strategy_instance = UnifiedWeatherEdgeStrategy()
        run_params["weather_no_token_ids"] = token_ids
        run_params["weather_position_pct"] = 0.1
    elif args.strategy == "copy_trade":
        strategy_instance = UnifiedCopyTradingStrategy()
        run_params["target_wallet"] = "0xTargetWallet"

    context = StrategyContext(
        instance_id=instance_id,
        strategy_key=f"unified_{args.strategy}",
        run_params=run_params,
        execution_mode="DRY_RUN",
    )
    
    await registry.register(strategy_instance, context, subscribe_tokens=token_ids)

    # 3. Start the Engine Loop
    logger.info("Starting up the Unified Trading Engine...")
    await dispatcher.start()
    await notifier.start()
    await executor.start()
    await feeder.start()
    
    logger.info("Engine is running. Press Ctrl+C to stop.")
    
    # Keep the main coroutine alive
    while True:
        await asyncio.sleep(3600)


async def shutdown(
    dispatcher: EventDispatcher,
    executor: ExecutionService,
    feeder: MarketDataFeeder,
    notifier: TelegramNotificationListener,
    store: StrategyRuntimeStore
) -> None:
    """Graceful shutdown sequence."""
    logger.info("Shutting down the engine...")
    await feeder.stop()
    await executor.stop()
    await notifier.stop()
    await dispatcher.stop()
    store.close()
    logger.info("Shutdown complete. Exiting.")
    sys.exit(0)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
