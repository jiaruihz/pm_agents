# Chain.Love bounty Harness profile

实施合同：`chain-love/harness-runs/GLM_AUTOMATION_IMPLEMENTATION_BRIEF.md`（P0 已落地）。

本 profile 保留 Terra 语义裁决、Luna 有界扫描、Sol 脚本/构建分工，并把全部关键门换成
可信 COMMAND verifier（`scripts/ops/chainlove_verify.py`），发布阶段独立为
PRODUCTION_CHANGE 风险、显式 submit-grant 放行。

## 生成运行配置

```bash
.venv/bin/python scripts/ops/chainlove_bounty_harness.py \
  --run-id chainlove-eligible-services-YYYYMMDD-vN \
  --repo-path /absolute/path/to/chain-love-repo \
  --output-dir /absolute/path/to/run-config \
  --reward-address 0xEXPLICITLY_CONFIRMED_ADDRESS \
  --submission-mode review_required
```

- 默认 `review_required`：终态到 `READY_TO_SUBMIT` 为止，不 push 不开 PR。
- `auto` 必须同时给 `--grant <submit-grant.json>`（字段：grant_id / action=submit_pr /
  repo / github_user / reward_address / max_prs / expires_at_utc / run_id）；
  过期、错绑、缺字段由 submit-grant 门 fail-closed。
- `--legacy` 输出旧五节点 DAG（仅用于回放旧 run 的产物结构）。

## DAG（默认 extended）

```text
preflight -> freeze_inputs
  -> candidate_mcp / candidate_services / stale_delta / deferred_recheck
  -> evidence_review
       zero candidates -> noop_verification                 => NOOP_VERIFIED
       approved        -> implementation_worker (唯一 workspace-write, 仅 run worktree)
          -> deterministic_validation + adversarial_review（并行，均对新 SHA 重跑）
          -> submission_bundle                               => READY_TO_SUBMIT
          -> publish [submit-grant] -> post_publish_verification -> ci_monitor => SUBMITTED
```

终态合同：`NOOP_VERIFIED | READY_TO_SUBMIT | SUBMITTED | DEFERRED | REJECTED | BLOCKED | FAILED`
（`terminal_outcomes.json`；由 outcome-contract 门按分支强制）。

## freeze_inputs 执行体

```bash
.venv/bin/python scripts/ops/chainlove_freeze.py \
  --repo-path /absolute/repo --run-dir /absolute/run-config \
  --context precedents=<state/reviewer_precedents.md> \
  --context deferred=<state/deferred_candidates.json> \
  --context stale=<state/stale_inventory.json> \
  --category-modifiers '{"security":1.5}'
```

顺序固定：fetch → base SHA → open-PR 快照 → 触碰目标路径 PR 的 diff → header-safe
claimed 索引（排除 `slug` 表头/空 cell，解析失败计数且拒绝冻结）→ context 哈希 →
manifest（fail-fast：repo 缺失、base 漂移、快照/索引空损、hash 不符一律失败）。

## 可信门要点

- `final-collision` 按 PR diff 新增行的 slug cell / `!offer:<slug>` 判碰撞（非文件路径）。
- `ci-head`：skipped / action_required（fork 审批门）= BLOCKED，不算 pass。
- `outcome-contract`：三分支条件强制；非成功终态必须带 reason。
- JSON 证据读取全部 fail-closed（缺证据 = FAIL，不崩溃）。
- 角色权限：builder 是唯一 writer（仅 run worktree）；scanner/reviewer/adversarial
  联网但文件系统只读；publish 为 PRODUCTION_CHANGE。

## 状态与遥测

state/（precedents、deferred queue、stale inventory、payout ledger）+ 各 run 的
append-only `decision_ledger.jsonl`（fsync 原子追加，并发不丢行）。所有角色
`require_usage=true`；无遥测即不满足验收。
