# Polymarket Research Architecture

## 目标

本轮重构把 Polymarket 研究能力统一收口到 `src/strategies/rule_lawyer`，并与仓库主架构保持一致：

- 业务核心在 `src/strategies/`
- 公共 HTTP/数据访问能力在 `src/platform/`
- 跨域编排在 `src/workflows/`
- CLI 和 skill 只做薄入口

## 分层

### 1. Platform Clients

位置：`src/platform/clients/`

本次新增：

- `polymarket_data.py`
- `polymarket_comments.py`
- `polymarket_profiles.py`

职责：

- 访问 `data-api.polymarket.com`
- 访问 `gamma-api.polymarket.com/comments`
- 访问 Polymarket profile 页面与 profile API
- 统一分页、重试、基础返回归一化

禁止事项：

- 不在 client 层做胜率判断
- 不在 client 层拼业务报告
- 不在 client 层输出结论

### 2. Strategy Services

位置：`src/strategies/rule_lawyer/services/`

本次新增：

- `common.py`
- `market_resolver.py`
- `profile_audit.py`
- `comment_intel.py`
- `smart_wallets.py`
- `market_rule_audit.py`
- `market_analysis.py`
- `reporting.py`

职责：

- 承载研究核心逻辑
- 提供可复用服务函数
- 给 workflow、CLI、skill 提供统一 contract

### 3. Strategy Adapters

位置：`src/strategies/rule_lawyer/adapters/`

本次新增：

- `rule_analysis_adapter.py`

职责：

- 把现有 Rule Lawyer 规则解析能力包装成 research summary
- 隔离旧 parser/pipeline 与新 research workflow 的耦合

### 4. Workflows

位置：`src/workflows/research/`

本次新增：

- `profile_audit_workflow.py`
- `market_rule_audit_workflow.py`
- `market_intel_workflow.py`
- `market_analysis_workflow.py`

职责：

- 编排 service 调用
- 组织输出目录
- 写入 `summary.json` / `report.md` / 中间产物

### 5. Interfaces

位置：

- `scripts/ops/`
- `scripts/python/`
- `skills/`

职责：

- 解析参数
- 调 workflow/service
- 打印简短摘要

约束：

- 不允许在入口脚本中保留第二套业务实现
- 不允许 skill 调 skill 作为核心依赖

## 能力拆分

### 市场规则审计

入口：

- `scripts/ops/polymarket_market_rule_audit.py`
- `skills/polymarket-market-rule-audit/`

职责：

- 解析 market URL / slug / condition id
- 提取市场基础信息
- 生成规则摘要、结算条件摘要、清晰度与争议风险

### 账户历史审计

入口：

- `scripts/ops/polymarket_profile_audit.py`
- `skills/polymarket-profile-audit/`

职责：

- 输入用户名、主页 URL 或钱包地址
- 审计 trades / positions / closed positions / PnL 轨迹
- 输出胜率、回撤和样本内结论

### 市场情报

入口：

- `scripts/ops/polymarket_market_intel.py`
- `skills/polymarket-market-intel/`

职责：

- 评论区信息抓取与打分
- top holders / smart wallets 发现
- 关键钱包历史审计

### 完整市场分析

入口：

- `scripts/ops/rule_lawyer_market_analysis.py`
- `skills/polymarket-research-orchestrator/`

职责：

- 规则审计先行
- 再叠加市场情报
- 最终由规则风险门控综合结论

## 关键依赖方向

固定为：

- `skills/*/scripts/*.py` -> `src/workflows/research/*`
- `scripts/ops/*.py` -> `src/workflows/research/*`
- `scripts/python/*.py` -> `src/strategies/rule_lawyer/services/*`
- `src/workflows/research/*` -> `src/strategies/rule_lawyer/services/*`
- `src/strategies/rule_lawyer/services/*` -> `src/platform/clients/*`

禁止：

- skill -> skill
- wrapper -> wrapper 作为主依赖
- service 中直接拼 CLI 输出

## 数据模型

统一模型在 `src/strategies/rule_lawyer/models_research.py`：

- `MarketTarget`
- `ResolvedMarket`
- `ProfileTarget`
- `ProfileSummary`
- `CommentRecord`
- `WalletSignal`
- `EvidenceRecord`
- `RiskFlag`
- `RuleAuditSummary`
- `MarketIntelSummary`
- `MarketAnalysisSummary`

目标：

- 避免各脚本随手拼 dict
- 保持 workflow、report、tests 共用同一套 schema

## 规则风险门控

完整分析里的总结论不是直接看评论，也不是直接看钱包偏向。

顺序固定为：

1. 先跑规则审计
2. 如果 `resolution_risk` 高，或者 `rule_clarity_score` 低：
   - 最终结果降级到 `high_rule_risk` 或弱结论
3. 规则风险可接受时，再结合：
   - wallet bias
   - comment bias

这保证了“规则有坑但评论很热”的市场不会被误判成高置信度机会。

## 输出规范

### 账户审计

- `runtime/profile_audits/<target>-<timestamp>/summary.json`
- `runtime/profile_audits/<target>-<timestamp>/report.md`
- `runtime/profile_audits/<target>-<timestamp>/raw/...`

### 市场规则审计

- `runtime/market_rule_audits/<slug>-<timestamp>/summary.json`
- `runtime/market_rule_audits/<slug>-<timestamp>/report.md`
- `runtime/market_rule_audits/<slug>-<timestamp>/market_snapshot.json`

### 市场情报

- `runtime/market_intel/<slug>-<timestamp>/summary.json`
- `runtime/market_intel/<slug>-<timestamp>/report.md`
- `runtime/market_intel/<slug>-<timestamp>/comments.json`
- `runtime/market_intel/<slug>-<timestamp>/smart_wallets.json`
- `runtime/market_intel/<slug>-<timestamp>/wallet_audits/...`

### 完整市场分析

- `runtime/market_analysis/<slug>-<timestamp>/summary.json`
- `runtime/market_analysis/<slug>-<timestamp>/report.md`
- `runtime/market_analysis/<slug>-<timestamp>/rule_analysis.json`
- `runtime/market_analysis/<slug>-<timestamp>/comments.json`
- `runtime/market_analysis/<slug>-<timestamp>/smart_wallets.json`
- `runtime/market_analysis/<slug>-<timestamp>/wallet_audits/...`

## 后续扩展

后续如果要继续扩展：

- 更细的评论来源适配器
- LLM 二次中文总结
- Rule Lawyer 更深层 parser / scoring 整合
- Web/BFF 直接调用 workflow

都应该优先加在 service 或 workflow 层，而不是继续堆在脚本和 skill 里。
