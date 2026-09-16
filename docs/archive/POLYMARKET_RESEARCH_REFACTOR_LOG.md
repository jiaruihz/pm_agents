# Polymarket Research Refactor Log

## 本次改造结果

### 新增 platform clients

- `src/platform/clients/polymarket_data.py`
- `src/platform/clients/polymarket_comments.py`
- `src/platform/clients/polymarket_profiles.py`

### 新增 strategy research models / services / adapters

- `src/strategies/rule_lawyer/models_research.py`
- `src/strategies/rule_lawyer/services/common.py`
- `src/strategies/rule_lawyer/services/market_resolver.py`
- `src/strategies/rule_lawyer/services/profile_audit.py`
- `src/strategies/rule_lawyer/services/comment_intel.py`
- `src/strategies/rule_lawyer/services/smart_wallets.py`
- `src/strategies/rule_lawyer/services/market_rule_audit.py`
- `src/strategies/rule_lawyer/services/market_analysis.py`
- `src/strategies/rule_lawyer/services/reporting.py`
- `src/strategies/rule_lawyer/adapters/rule_analysis_adapter.py`

### 新增 workflows

- `src/workflows/research/profile_audit_workflow.py`
- `src/workflows/research/market_rule_audit_workflow.py`
- `src/workflows/research/market_intel_workflow.py`
- `src/workflows/research/market_analysis_workflow.py`

### 新增 / 改造 CLI

- 改造：`scripts/ops/polymarket_profile_audit.py`
- 改造：`scripts/python/pmm_find_smart_wallets.py`
- 新增：`scripts/ops/polymarket_market_rule_audit.py`
- 新增：`scripts/ops/polymarket_market_comments.py`
- 新增：`scripts/ops/polymarket_market_intel.py`
- 新增：`scripts/ops/rule_lawyer_market_analysis.py`

### 新增 / 改造 skills

- 改造：`skills/polymarket-profile-audit/`
- 新增：`skills/polymarket-market-rule-audit/`
- 新增：`skills/polymarket-market-intel/`
- 新增：`skills/polymarket-research-orchestrator/`

## 抽掉的重复逻辑

本次显式抽掉了这些重复源：

1. target 解析
- 用户目标解析收口到 `profile_audit.py`
- 市场目标解析收口到 `market_resolver.py`

2. HTTP / 分页
- 用户 trades / positions / closed positions 收口到 `polymarket_data.py`
- profile 页面和 userData 收口到 `polymarket_profiles.py`
- comments 抓取收口到 `polymarket_comments.py`

3. 通用工具函数
- `to_float`
- `normalize_json_list`
- `safe/path/slug` 类工具
- 输出目录命名

4. 报告模板
- 统一收口到 `services/reporting.py`

## 架构变化

### 之前

- 业务逻辑散在 `scripts/`
- skill 通过 subprocess 调脚本
- 缺少统一模型和 workflow

### 现在

- 业务核心在 `src/strategies/rule_lawyer`
- workflow 在 `src/workflows/research`
- CLI 和 skill 只做薄入口
- 输出目录规范统一

## 已知局限

- `market_intel` 当前仍然是同步串行跑关键钱包审计，后续可并发化
- comments 抓取仍依赖公开接口 best-effort
- Rule Lawyer adapter 目前只接了规则解析摘要，没有把旧 pipeline 全量迁入
- smoke 回归主要覆盖新 workflow / CLI 路径，不包含完整线上稳定性验证

## 后续建议

1. 给 `market_intel_workflow` 加并发 profile audit
2. 为 comments / market resolver 增加更多 mock 测试
3. 把 Web/BFF 直接接到 `src/workflows/research`
4. 评估是否把部分 research CLI 收敛到 `src/interfaces/cli`
