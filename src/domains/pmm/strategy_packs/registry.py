from __future__ import annotations

from src.domains.pmm.strategy_packs.base import StrategyPackSpec
from src.domains.pmm.strategy_packs.multi_level_v1 import PACK_SPEC as MULTI_LEVEL_V1_PACK
from src.domains.pmm.strategy_packs.rule_lawyer_v1 import PACK_SPEC as RULE_LAWYER_V1_PACK
from src.domains.pmm.strategy_packs.single_level_v1 import PACK_SPEC as SINGLE_LEVEL_V1_PACK
from src.domains.pmm.strategy_packs.smart_money_follow_v1 import PACK_SPEC as SMART_MONEY_FOLLOW_V1_PACK
from src.domains.pmm.strategy_packs.weather_theta_no_v1 import PACK_SPEC as WEATHER_THETA_NO_V1_PACK


STRATEGY_PACKS: list[StrategyPackSpec] = [
    SINGLE_LEVEL_V1_PACK,
    MULTI_LEVEL_V1_PACK,
    RULE_LAWYER_V1_PACK,
    SMART_MONEY_FOLLOW_V1_PACK,
    WEATHER_THETA_NO_V1_PACK,
]


def strategy_pack_map() -> dict[str, StrategyPackSpec]:
    return {pack.key: pack for pack in STRATEGY_PACKS}
