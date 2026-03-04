from src.domains.pmm.strategy_packs.base import StrategyPackSpec

PACK_SPEC = StrategyPackSpec(
    key="weather_theta_no_v1",
    name="天气 Theta No 策略包",
    strategy_module="src.domains.pmm.strategies.weather_theta_no_v1:WeatherThetaNoV1Strategy",
    runner_module="src.domains.pmm.main",
    pack_dir="src/domains/pmm/strategy_packs/weather_theta_no_v1",
    description="天气市场 NO 侧 carry，支持时间离场、止盈止损与冷却重入。",
)
