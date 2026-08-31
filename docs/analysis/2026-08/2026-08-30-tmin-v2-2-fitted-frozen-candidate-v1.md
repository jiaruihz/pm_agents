# Tmin V2.2 fitted frozen candidate（2026-08-30）

结论：`FREEZE_V2_2_PROBABILITY_CHALLENGER`。模型规格和参数 artifact 已冻结；
2026-08-31 是 no-backfill 的最早允许 target date，但 zero-notional forward runtime
consumer 尚未部署，当前状态为 `PREREGISTERED_NOT_STARTED`。操作状态仍是
`KEEP_V1_FORWARD_ONLY`，`tiny_live_eligible=false`。

本轮没有改变模型公式、route、selector、threshold、price cap、size 或 execution。
修复的是研究 gate：非 exact settlement-source rows 从 primary forecast-error history
逐行排除并保留 all-source sensitivity，不再把全部合法 P0 rows 强制退回 market。
排除后仍有 226 independent city-days、45 next-colder event city-days，native
available-at coverage 100%；全源 truth 仍原样报告为 226/230=98.26%。

固定 V1 P0 为 2026-08-12—08-26 的 168 rows/15 dates。V2.2 只支持 Seoul/Tokyo
06:00/09:00 active route，共 50 rows/13 dates；现有 fold gate 使 08-23—08-26 的
16 rows/4 dates 真正 prior-date OOF 拟合。该切片相对 raw market 的 date-equal
ΔLogLoss=`-0.00296845`、95% CI `[-0.01290120,+0.00719918]`；ΔBrier=
`-0.00005911`、95% CI `[-0.00248702,+0.00255904]`。两项点估改善，但区间均跨 0。

在冻结 V1 direct-ask/fee/selector replay 上，V2.2 为 1 signal、1 trade/1 date、
1 win，5-share cost `$4.7141`、fee-adjusted PnL `+$0.2859`、ROI `6.06%`。
对照 V1 incumbent 为 30 signals→19 trades/12 dates、19/19、PnL `+$5.14114675`；
V1 alpha=.10 routed 为 2 trades/2 dates、2/2、PnL `+$0.619625`。PnL 只作业务
读数，没有参与 alpha 或 frozen arm 选择。V1 的 19 笔中仅 9 笔落在 V2.2 的
supported active route，所以 V2.2 不是 V1 的全窗口替代。

轻量机器证据、逐 row probability/trade、portable frozen inputs、source snapshots
和空目录逐文件一致重放位于
`reviews/tmin_v2_2_frozen_candidate_readout_v1_r5/`。正式数字以该目录为准。
