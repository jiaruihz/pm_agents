from src.domains.pmm.strategy_packs.base import StrategyPackSpec

PACK_SPEC = StrategyPackSpec(
    key="smart_money_follow_v1",
    name="聪明钱跟随策略包",
    strategy_module="src.domains.pmm.strategies.smart_money_follow_v1:SmartMoneyFollowV1Strategy",
    runner_module="src.domains.pmm.main",
    pack_dir="src/domains/pmm/strategy_packs/smart_money_follow_v1",
    description="基于钱包与 token 信号的方向性倾斜策略。",
)
