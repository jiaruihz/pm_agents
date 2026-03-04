from src.domains.pmm.strategy_packs.base import StrategyPackSpec

PACK_SPEC = StrategyPackSpec(
    key="multi_level_v1",
    name="多层做市策略包",
    strategy_module="src.domains.pmm.strategies.multi_level_v1:MultiLevelV1Strategy",
    runner_module="src.domains.pmm.main",
    pack_dir="src/domains/pmm/strategy_packs/multi_level_v1",
    description="多档 PMM 做市策略，支持分层报价与层间 size 衰减。",
)
