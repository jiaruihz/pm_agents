import asyncio

from pmm.config import PMMConfig
from pmm.engine import TickEngine


def main() -> None:
    config = PMMConfig.from_env()
    asyncio.run(TickEngine(config).run())


if __name__ == "__main__":
    main()
