---
name: polymarket-profile-audit
description: 审计一个 Polymarket 账户在公开端点可见窗口内的交易、持仓、已平仓表现、PnL 与回撤，输入支持 @username、主页 URL 或钱包地址。不得把公开分页称为完整私有历史，也不用于 weather live-account 对账。
---

# Polymarket Profile Audit

适用于这些请求：

- “查一下这个 Polymarket 用户是不是全对”
- “给这个地址拉历史并分析胜率”
- “看看这个主页对应的人是不是异常稳定”
- “把原始数据拉下来，再给我一个人能看懂的总结”

## Inputs

- `target`（必填）
  - `@username`
  - `https://polymarket.com/@username`
  - `https://polymarket.com/profile/%40username`
  - `0x...` wallet
- `fetch_all`（可选）
  - 在公开端点和当前代码上限内尽量分页；不是无条件完整账户历史
- `focus`（可选）
  - `accuracy | pnl | drawdown | suspiciousness | all`
- `report_style`（可选）
  - `brief | analyst | risk`
- `out_file`（可选）
- `raw_dir`（可选）
- `max_trades`（可选）
- `resolve_trade_outcomes`（可选）
- `max_resolve_markets`（可选）

## Run

```bash
.venv/bin/python skills/polymarket-profile-audit/scripts/run_profile_audit.py \
  --target "@cqk"
```

全量模式示例：

```bash
.venv/bin/python skills/polymarket-profile-audit/scripts/run_profile_audit.py \
  --target "https://polymarket.com/@cqk" \
  --fetch-all \
  --report-style analyst
```

## Output

默认输出到 `runtime/profile_audits/<target>-<timestamp>/`：

- `summary.json`
- `report.md`
- `raw/`（可选）

## Rules

- 先交代样本量，再下结论。
- `fetch_all` 必须同时报告返回数量、最早/最晚时间、端点/分页上限和已知缺口，不得简称“全部历史”。
- 已平仓结果和未平仓浮盈亏必须分开写。
- 回撤不是可选项，必须写。
- 不能基于公开数据直接指控内幕交易。
