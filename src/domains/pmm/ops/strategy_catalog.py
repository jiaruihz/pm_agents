"""Built-in PMM strategy catalog entries for ops database."""

from __future__ import annotations

from typing import Any, Dict, List


BUILTIN_STRATEGIES: List[Dict[str, Any]] = [
    {
        "strategy_key": "multi_level_v1",
        "strategy_name": "做市策略",
        "strategy_group": "market_making",
        "strategy_family": "maker",
        "domain": "pmm",
        "is_active": True,
        "runner_module": "src.domains.pmm.main",
        "description": "多档 PMM 做市策略（库存+波动+盘口信号）。",
        "meta": {
            "variant": "multi-level",
            "order_style": "maker",
            "pack_dir": "src/domains/pmm/strategy_packs/multi_level_v1",
        },
    },
    {
        "strategy_key": "single_level_v1",
        "strategy_name": "做市策略（单层）",
        "strategy_group": "market_making",
        "strategy_family": "maker",
        "domain": "pmm",
        "is_active": True,
        "runner_module": "src.domains.pmm.main",
        "description": "单档 PMM 做市策略，执行路径更简洁。",
        "meta": {
            "variant": "single-level",
            "order_style": "maker",
            "pack_dir": "src/domains/pmm/strategy_packs/single_level_v1",
        },
    },
    {
        "strategy_key": "rule_lawyer_v1",
        "strategy_name": "规则律师策略",
        "strategy_group": "rule_intelligence",
        "strategy_family": "advisory",
        "domain": "research",
        "is_active": True,
        "runner_module": "src.domains.research.pipeline",
        "description": "规则解析/争议风险驱动的策略框架（当前为策略目录建模，未接入 PMM 下单循环）。",
        "meta": {
            "execution": "off-engine",
            "status": "catalog_only",
            "pack_dir": "src/domains/pmm/strategy_packs/rule_lawyer_v1",
        },
    },
    {
        "strategy_key": "weather_theta_no_v1",
        "strategy_name": "天气策略",
        "strategy_group": "weather",
        "strategy_family": "directional",
        "domain": "pmm",
        "is_active": True,
        "runner_module": "src.domains.pmm.main",
        "description": "天气市场 NO 侧 carry 与定时平仓策略。",
        "meta": {
            "side_bias": "NO",
            "style": "carry",
            "pack_dir": "src/domains/pmm/strategy_packs/weather_theta_no_v1",
        },
    },
    {
        "strategy_key": "smart_money_follow_v1",
        "strategy_name": "跟随聪明钱策略",
        "strategy_group": "smart_money",
        "strategy_family": "directional",
        "domain": "pmm",
        "is_active": True,
        "runner_module": "src.domains.pmm.main",
        "description": "基于高胜率钱包信号的报价倾斜与仓位倾斜策略。",
        "meta": {
            "signal_source": "wallets+token_signals",
            "style": "follow",
            "pack_dir": "src/domains/pmm/strategy_packs/smart_money_follow_v1",
        },
    },
]
