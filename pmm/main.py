import asyncio

from pmm.config import PMMConfig
from pmm.tick_loop import tick_loop


def main() -> None:
    config = PMMConfig.from_env()
    asyncio.run(tick_loop(config))


if __name__ == "__main__":
    main()
