import asyncio

from src.domains.pmm.config import PMMConfig
from src.domains.pmm.engine import TickEngine


def main() -> None:
    config = PMMConfig.from_env()
    asyncio.run(TickEngine(config).run())


if __name__ == "__main__":
    main()
