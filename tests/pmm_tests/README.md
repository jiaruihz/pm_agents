# PMM Tests

当前测试集仅覆盖 `src.strategies.pmm` 核心模块，不再包含旧 `agents` 组件。

## 文件

- `tests/pmm_tests/test_config.py`
- `tests/pmm_tests/test_signals.py`
- `tests/pmm_tests/test_strategies.py`
- `tests/pmm_tests/test_order_manager.py`
- `tests/pmm_tests/test_data.py`

## 运行

```bash
source .venv/bin/activate
pytest tests/pmm_tests -q
```

## 说明

- 测试以单元测试为主，默认不依赖真实外部 API。
- 若修改 `src/strategies/pmm/config.py` 或策略参数路由，建议优先补充 `test_config.py` 与 `test_strategies.py`。
