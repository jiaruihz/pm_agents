# Core Carry near-Core maker H0/H1/E0 v1

Status: `snapshot / implementation verified / production default-off`

## 结论

历史 H0/H1 不足以冻结 WS economic-cancel policy：1,503 个 frozen candidate/checkpoint 中没有 GOLD 或 SILVER，1,479 个 BRONZE、24 个 UNUSABLE。因此下一阶段只能是 amendment 定义的 WS-1：真实 5-share fixed-rest baseline 收集 private lifecycle，WS action 继续 shadow；不能把历史 quote crossing 当 fill，也不能直接进入 WS-2。

E0 已在当前生产 Core runtime SHA `b39f234ef3117f9f4c7c680d5074cfbebde7fa59` 上实现，但默认关闭、未部署、未重启、未下真实单。现有 Core 路由在没有 near-Core exposure 时保持原 10 taker + 5 shared maker 行为。

## H0 历史 inventory

- grain：`source_sleeve × frozen candidate/checkpoint`
- 覆盖：2026-07-25..2026-08-28，35 个 target dates，41 个城市
- 总计：1,503 rows
- existing Core maker：89（87 BRONZE，2 UNUSABLE）
- near-Core sole-blocker candidates：1,414（1,392 BRONZE，22 UNUSABLE）
- deterministic policy replay ready：0
- full ladder lifecycle research ready：0

两类 sleeve 保留独立 `source_sleeve`，PnL 和 eligible denominator 不合并。inventory 只接受 exact one-to-one PIT join；不使用 later score、later book 或 settlement 反修 action-time state。

## H1 frozen replay

冻结输入重放发现 193 个事件、185 个 decision、555 个 maker policy actions。185 个 decision 全部 `reconstruction_complete=false`：103 candidate、63 near_core_candidate、19 first_positive，根因均为 pre/acute subscription 或 epoch continuity gap；promotion gate 为 false，evidence coverage 0.0。该结果证明缺口在 raw WS/sequence/private lifecycle，而不是 cache 或 inventory join。

## E0 实现合同

- 独立 feature flag：默认关闭；真实单除通用 `--live --confirm-live` 外，还必须显式 `--confirm-near-core-maker-probe-live`。
- selector：existing Core 不 actionable，且 frozen score 的唯一 blocker 精确等于 `non_positive_taker_ev`。
- fixed 5 shares；独立 strategy instance、config、experiment ID、`pmc_ccnc_` client-order prefix、daily city-day/cost risk budget。
- WS-1 只 fixed rest；仅 safety cancel（feature disabled、共同 deadline、weather state 变化、fresh two-sided book 缺失、Core supersession），没有 economic WS cancel/reprice。
- Core 优先：near-Core resting 时若 Core 重新 actionable，当轮只 cancel near-Core，Core submit 延后一轮，等待 cancel terminal。
- city×target_date dedupe；near-Core 已成交数量从现有 Core shared maker 5-share allowance 中扣除。
- 独立 append-only ledger、manifest、report、promotion gate；promotion gate 固定 fail，直到 frozen policy、held-out actual-order evidence 和 WS-2 授权齐备。

## 证据

- 内容寻址 artifact：`/Volumes/jrs-archive/pm_agents/research/artifact_store/core_carry_historical_ws_inventory_v1/0dfee647caa0e7e569a6076a3822b693433fdc471e35e39e3025c948d265eb7c`
- artifact/input seal：`EVIDENCE_MANIFEST.json`
- inventory：`historical_ws_inventory.json`
- H1 replay：`market_state_decisions.jsonl`、`maker_policy_actions.jsonl`、`promotion_gate.json`
- canonical DB seal：device `16777244`、inode `54444`、size `16274857984`；`fact_trades` max build `2026-08-28T15:37:35.127917+00:00`

复现 inventory：

```bash
.venv/bin/python scripts/analysis/execution_quality/core_carry_historical_ws_inventory_v1.py \
  --output /tmp/core_carry_historical_ws_inventory.json
```

## 下一道授权边界

E1 production collector/control smoke、启用 near-Core feature、runner restart 与 WS-1 真实 5-share order 都是生产行为变更，必须走 production manifest + git-first deploy，并由用户显式确认。当前实现不构成 live 授权。
