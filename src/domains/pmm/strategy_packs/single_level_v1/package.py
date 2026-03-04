from src.domains.pmm.strategy_packs.base import StrategyPackSpec

PACK_SPEC = StrategyPackSpec(
    key="single_level_v1",
    name="单层做市策略包",
    strategy_module="src.domains.pmm.strategies.single_level_v1:SingleLevelV1Strategy",
    runner_module="src.domains.pmm.main",
    pack_dir="src/domains/pmm/strategy_packs/single_level_v1",
    description="单档 PMM 做市策略，执行路径简洁，适合基线与小步调参。",
)
