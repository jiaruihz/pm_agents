import asyncio
import logging
import os

from src.strategies.pmm.config import PMMConfig
from src.strategies.pmm.engine import TickEngine


def _setup_logging() -> None:
    level_name = os.getenv("PMM_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format="%(message)s")


def main() -> None:
    _setup_logging()
    config = PMMConfig.from_env()
    asyncio.run(TickEngine(config).run())


if __name__ == "__main__":
    main()
