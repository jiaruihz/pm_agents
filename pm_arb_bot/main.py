import asyncio

from pm_arb_bot.arb_engine import ArbEngine
from pm_arb_bot.config import ArbConfig


def main() -> None:
    config = ArbConfig.from_env()
    engine = ArbEngine(config)
    asyncio.run(engine.run())


if __name__ == "__main__":
    main()
