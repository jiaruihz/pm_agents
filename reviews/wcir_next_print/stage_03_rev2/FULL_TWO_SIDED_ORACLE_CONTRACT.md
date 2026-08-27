# WCIR Stage 3 rev2 — Full two-sided executable oracle contract

## Scope

Primary oracle 是“如果在 source first-seen 时已知道实际 next official print，最多能否在冻结执行合同下赚到钱”的上限诊断，不是可部署模型，也不是 Stage 4 训练证据。

对每条 causal event，REST 冻结 exact condition 的 YES/NO complementary identities、actual-next-print bracket 与相邻 settlement-relevant brackets；WS 独立决定这些 token 在每个 checkpoint 是否可重建、单/双边和 1/5/10-share 可执行。

## Primary action

固定：

- size：5 shares；
- entry：`source_first_seen + global empirical pipeline latency p95`；
- exit：`official_first_seen + 30s`；
- entry price：fee-aware ask sweep；
- exit price：fee-aware bid sweep；
- 两边均收 Weather taker fee；
- 若 actual next print 未抬高 running max，或没有可执行 semantic expression，允许 `NO_TRADE`。

当 next print 抬高 running max 时，候选表达只包括：prior exact bracket 的 NO 与 actual-next-print bracket 的 YES。两者均可执行时，仅按 t0 effective entry cost 与稳定 token-id tie-break 选择。Primary action 不读取 future exit price、future markout、size 或 horizon 表现。删除所有 future-price columns 后 action 必须逐 row 不变。

`effective_lead = official_first_seen - source_first_seen - execution_latency`。若不为正，该 row 不进入 paired executable denominator。

## Ex-post envelope

可以在同一冻结 universe 中另算未来最优 contract 的 envelope，但必须标记：

`UNATTAINABLE_EX_POST_BEST_CONTRACT_ENVELOPE_NOT_FOR_PROMOTION`

它不能进入 primary CI、city gate、horizon/size 选择或任何 alpha claim。

## Baselines 与统计

Persistence、recent slope、PIT forecast-only 与 blocked-OOF market-only 必须使用同一 executable row intersection/hash，不得各自删除坏 row。Outer split 为 `target_date`，inner grouping 至少为 `official_print_id`。报告 raw N、unique print N、date N 与 effective N。

Primary inference 以 target-date block 为单位，保存 source-time-shift placebo、pre-source→t0、post-latency→pre-official、official fixed horizons、best-date removal 和 date concentration。覆盖不足时结论只能是 `CONTINUE_COLLECTION_WITHOUT_MODELING`，不得进入 substantive Stage 4。
