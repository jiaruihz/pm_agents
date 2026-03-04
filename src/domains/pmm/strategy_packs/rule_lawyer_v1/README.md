# rule_lawyer_v1

## 策略说明

- 类型：规则智能（research 域）
- key：`rule_lawyer_v1`
- 特性：基于规则解析和争议风险评估做筛选，不走 PMM 下单循环

## 目录内容

- `package.py`：策略包元信息
- `params.example.json`：参数模板
- `run_paper.sh`：执行脚本（调用 research CLI）

## 快速执行

```bash
bash src/domains/pmm/strategy_packs/rule_lawyer_v1/run_paper.sh
```
