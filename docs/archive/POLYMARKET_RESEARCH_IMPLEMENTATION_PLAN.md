# Polymarket Research Implementation Plan

## 本次实现范围

已按以下方向实施：

1. 核心研究逻辑下沉到 `src/strategies/rule_lawyer`
2. 公共 HTTP client 下沉到 `src/platform/clients`
3. workflow 下沉到 `src/workflows/research`
4. 旧 CLI 改成薄壳
5. 新增市场规则审计、市场情报、完整分析入口
6. skill 改为直连 workflow

## 实施顺序

### 第一阶段

建立基础骨架：

- `models_research.py`
- `services/`
- `adapters/`
- `src/workflows/research/`
- `src/platform/clients/polymarket_*`

### 第二阶段

先完成市场规则审计：

- `market_resolver.py`
- `rule_analysis_adapter.py`
- `market_rule_audit.py`
- `market_rule_audit_workflow.py`
- 对应 CLI / skill

### 第三阶段

迁移账户审计：

- `profile_audit.py`
- `profile_audit_workflow.py`
- 改造旧 profile audit 脚本与 skill

### 第四阶段

迁移 smart wallet 和评论：

- `smart_wallets.py`
- `comment_intel.py`
- 对应 CLI

### 第五阶段

完成市场情报与总分析：

- `market_analysis.py`
- `market_intel_workflow.py`
- `market_analysis_workflow.py`
- 对应 CLI / skill

### 第六阶段

补文档与 README 对齐。

## 当前保留的兼容策略

### CLI 兼容

保留了这些高频参数：

- `polymarket_profile_audit.py`
  - `--target`
  - `--fetch-all`
  - `--max-trades`
  - `--resolve-trade-outcomes`
  - `--max-resolve-markets`
  - `--out-file`
  - `--raw-dir`

- `pmm_find_smart_wallets.py`
  - `--url`
  - `--slug`
  - `--condition-id`
  - `--target-market`
  - `--score-mode`
  - `--top-wallets`
  - `--out-file`

### 输出兼容

`profile audit` 的 `summary.json` 继续保留：

- `profile`
- `trade_summary`
- `position_score`
- `closed_position_score`
- `portfolio_pnl_analysis`
- `verdict`
- `caveat`

## 未纳入本期的内容

- 更深层的 Rule Lawyer pipeline 重构
- 评论来源的浏览器级抓取
- Web UI 接入
- 自动交易执行
- Telegram 通知整合到新 workflow
