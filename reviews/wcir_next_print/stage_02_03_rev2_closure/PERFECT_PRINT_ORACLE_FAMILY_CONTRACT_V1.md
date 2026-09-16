# WCIR Perfect-Print Oracle Family Contract V1

本合同只定义 measurement harness；Stage 2 data gate 未关闭，因此不授权选择 city、horizon、size、模型或交易 policy。

## 两套 entry clock

- `FIRST_VALID_FRESH_EXECUTABLE_AFTER_SOURCE`：从 `source_first_seen` 起，选择第一份 identity/clock/gap/liveness 合格且所需 ask sweep 可执行的 WS book。
- `FROZEN_OPERATIONAL_LATENCY_SLO`：`source_first_seen + city/source/collector_epoch` 预先冻结的 latency SLO 后，选择第一份合格 book。

Latency 从 source 的 host receive first-seen wall clock 开始，到 runner decision-ready（含 fresh-book fetch）host receive wall clock结束。wall 与 monotonic 不可混算；缺 clock、clock regression 或不可比较 domain fail closed。按 city/source/collector epoch 报 observed/missing N 与 nearest-rank p50/p90/p95；不删 outlier、不 winsorize。旧 heterogeneous global pooled p95 只保留 diagnostic，不能作为新的唯一 primary。

## 固定测量矩阵

- sizes：1、5 shares；
- exits：official first_seen 后 +5、+15、+30、+60、+120 秒的第一份合格 book；
- entry：fee-aware ask sweep；exit：fee-aware bid sweep；
- fee：`src/platform/market_data/executable_book_truth.py` 的 `weather_taker_fee`，版本 `weather_executable_book_truth_v1`，逐 level 计算 `shares * 0.05 * price * (1-price)`，不另行 cents rounding；
- stable tie-break：先最低 t0 effective entry cost，再 token_id 字典序；
- 不读取 future exit、markout、best horizon 或 best size来选 action。

## Policy family

旧 primary action 正式命名为 `CROSS_ONLY_SEMANTIC_ORACLE`：仅在 actual next print 高于 prior running max 时，在 prior/current exact bracket NO 与 actual-next-print bracket YES 中按 t0 fee-aware cost选一个。

完整 family 独立测量：

- `PRIOR_CURRENT_EXACT_NO_ON_UPWARD_CROSS`；
- `ACTUAL_NEXT_PRINT_BRACKET_YES`；
- `LOWER_IMPOSSIBLE_BRACKETS_NO_ON_MULTI_TICK_CROSS`；
- `CURRENT_BRACKET_YES_NO_NEW_MAX_DIAGNOSTIC`；
- `NO_TRADE`（abstain，PnL=0，仍在共同 denominator）。

每个 policy 只可读取 actual next print、prior running state、frozen event market universe、t0 executable cost 与固定 tie-break。若语义不适用或所需 token/book 不存在，输出结构化 abstain 原因。`UNATTAINABLE_EX_POST_BEST_CONTRACT_ENVELOPE` 永远单列为 ex-post diagnostic，不进入 promotion 或 policy selection。
