import asyncio

from src.strategies.arb.arb_engine import ArbEngine
from src.strategies.arb.config import ArbConfig


def main() -> None:
    config = ArbConfig.from_env()
    engine = ArbEngine(config)
    asyncio.run(engine.run())


if __name__ == "__main__":
    main()
