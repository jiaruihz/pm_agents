# WCIR Book Validity / Liveness / Freshness Contract V1

本合同是 rev2 的独立 corrigendum；不改写 `stage_02_rev2` 的任何文件或 headline。

一个 checkpoint 必须分别记录：`reconstruction_valid`、`identity_valid`、`clock_valid`、`gap_free`、`subscription_expected`、`subscription_requested`、`subscription_acknowledged`、`subscription_active_at_checkpoint`、`connection_liveness_proven`、`baseline_received_for_active_epoch`、`book_state_age_seconds`、`freshness_policy_id`、`freshness_eligible`、`side_available`、`sweep_1_feasible`、`sweep_5_feasible`、`sweep_10_feasible`、`execution_eligible`。未知值保留 `null`，不得当作 `false` 或健康。

`reconstruction_valid` 表示：condition/token identity 正确、receive clock 可比较、没有 open gap，并且当前 connection/epoch 已有 verified WS baseline。它不要求最近出现 delta。`freshness_eligible` 是额外 fail-closed 策略口径：本次诊断固定 `wcir_book_freshness_120s_v1`，除上述条件外还必须有 active epoch、该 epoch 的 baseline、`connection_liveness_proven=true` 且 book age 不超过 120 秒；未知 liveness 不能进入。`execution_eligible` 还要求所需 side 和 size 的 fee-aware sweep 可执行。

旧 `STALE_BOOK` 只保留为 immutable historical label。新诊断按以下互斥优先级拆分：

1. `TOKEN_NOT_ACTIVE_AT_CHECKPOINT`：checkpoint 时没有 active epoch；
2. `CONNECTION_LIVENESS_UNPROVEN`：有 epoch/baseline，但 frozen evidence 没有 heartbeat/connection liveness；
3. `ACTIVE_CONNECTION_NO_RECENT_DELTA`：连接健康且 active，但 book age 超 freshness policy；
4. `FRESHNESS_POLICY_EXCEEDED`：其余明确超龄情况。

active subscription + healthy connection + no delta 可以保持 reconstruction-valid；但超过 policy age 时 freshness-ineligible。rev2 没有冻结 requested/acknowledged、connection_id、heartbeat，因此本轮不能把 stale 行声称为“健康连接无 delta”，必须 fail closed 为 `CONNECTION_LIVENESS_UNPROVEN`。

Transport quality 分四层报告：connection-day、event-token-window、city-target-date、controlled reconnect recovery。全 archive 任一 token 曾有 blocker 不再把整天一票否决；每层都保留 numerator、denominator 和不可识别原因。REST 仅作 parity evidence，永不修补或改变 WS state。
