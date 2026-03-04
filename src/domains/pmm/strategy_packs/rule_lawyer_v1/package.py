from src.domains.pmm.strategy_packs.base import StrategyPackSpec

PACK_SPEC = StrategyPackSpec(
    key="rule_lawyer_v1",
    name="规则律师策略包",
    strategy_module="src.domains.research.pipeline:run_single_market_flow",
    runner_module="src.domains.research.cli",
    pack_dir="src/domains/pmm/strategy_packs/rule_lawyer_v1",
    description="规则解析与争议风险驱动策略（research 域，不接 PMM 下单循环）。",
)
