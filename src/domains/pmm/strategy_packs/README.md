# PMM Strategy Packs

这一层用于把策略组织成类似 skill 的“可管理单元”，每个策略一个目录，至少包含：

- 描述文档（`README.md`）
- 执行脚本（`run_paper.sh`）
- 参数模板（`params.example.json`）
- 包描述类（`package.py`）

当前已覆盖：

- `single_level_v1`
- `multi_level_v1`
- `rule_lawyer_v1`（research 域）
- `smart_money_follow_v1`
- `weather_theta_no_v1`

## 统一管理脚本

```bash
python scripts/python/pmm_strategy_packs.py list
python scripts/python/pmm_strategy_packs.py validate
python scripts/python/pmm_strategy_packs.py show --key weather_theta_no_v1
```
