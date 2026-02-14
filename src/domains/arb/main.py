import asyncio

from src.domains.arb.arb_engine import ArbEngine
from src.domains.arb.config import ArbConfig


def main() -> None:
    config = ArbConfig.from_env()
    engine = ArbEngine(config)
    asyncio.run(engine.run())


if __name__ == "__main__":
    main()
