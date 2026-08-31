# Legacy mixed test root

Status: `maintained-mixed-root / migration-pending`

这个目录名是历史遗留，已经不等于“PMM 测试”。当前包含 200+ 个测试文件：

- legacy PMM 与通用 quote runtime；
- weather 数据、策略、执行与生产合同；
- 少量历史研究 runner 的回归测试。

不能因为目录名是 `pmm_tests` 就整目录归档或删除。迁移时按被测模块逐文件移动，
并保持 `scripts/ops/verify_repo.py` 的 maintained suite 覆盖不下降。

真正直接覆盖 `src.strategies.pmm` / `src.platform.quote_runtime` 的核心文件包括：

- `test_config.py`
- `test_live_broker.py`
- `test_order_manager.py`
- `test_pricing_sizing.py`
- `test_safety_guard.py`
- `test_signals.py`
- `test_strategies.py`
- `test_target_order_set.py`
- `test_tick_engine_maker_only.py`

其余文件按 import/被测入口判断归属，不能按文件名猜测。

## 运行

```bash
source .venv/bin/activate
python -m pytest -q tests/pmm_tests
```

## 说明

- maintained 总入口是 `python scripts/ops/verify_repo.py --profile maintained`。
- `tests/research_tests/` 仍是单独的 full/task-scoped surface；不要把它与本目录混并。
