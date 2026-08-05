# Amsterdam KNMI → next EHAM METAR cadence v1

Status: `inconclusive_keep_shadow`

## 结论

Amsterdam 确实存在可研究的时间窗，但当前更像“预测下一份 routine METAR”的概率优势，而不是看到 KNMI 明显跨档后再和盘口拼纯速度。

- EHAM routine METAR 的实际报文时钟是每小时 `:25 / :55`，不是 `:00 / :30`。
- KNMI 10-minute interval 位于 `:00/:10/:20/:30/:40/:50`，provider 初版文件通常在 interval end 后约 `3m41s` 创建。
- 因而理论可用窗口分三档：`:00/:30` 约 21.3 分钟，`:10/:40` 约 11.3 分钟，`:20/:50` 约 1.3 分钟。
- `:10/:40` 是当前最有研究价值的 checkpoint：已有两个 10-minute path 点可估计 slope/acceleration，同时通常还剩约 11 分钟供模型、决策和成交。
- 明显 cross 出现后，盘口多数已经很贵或没有 ask；历史 dense-book 窗口里只有 `4/27` 个 first-cross signals 能以 `NO ask <= 0.97` 且 10-share depth 完整执行。因此不能把“数据比 METAR 早”直接等同于“有可交易 alpha”。

## 1. 时钟与实际 lead

2026-07-22 至 2026-07-26 的 714 个 KNMI→next EHAM METAR 对齐行：

| KNMI interval end | 初版文件典型出现 | 下一份 EHAM METAR | provider-created 后剩余时间 p50 | ta 对下一报文整度 exact |
|---|---|---|---:|---:|
| `:00` | `:03:41` | `:25` | 21.32 min | 59.2% |
| `:10` | `:13:41` | `:25` | 11.32 min | 72.5% |
| `:20` | `:23:41` | `:25` | 1.32 min | 93.3% |
| `:30` | `:33:41` | `:55` | 21.32 min | 61.0% |
| `:40` | `:43:41` | `:55` | 11.32 min | 64.4% |
| `:50` | `:53:41` | `:55` | 1.32 min | 89.0% |

这里的 `exact` 只是 rounded temperature 与下一 METAR 相同，不是交易准确率。ta 全体 within 1°C 为 99.2%，说明它适合做下一报文的连续概率特征；越靠近报文 exact 越高，但可交易时间越少。

2026-07-27 至 2026-07-31 的 actual first-seen raw census 为 p50 `3.915 min`、p95 `4.052 min`。这意味着 collector 正常时，`:10/:40` 的实测剩余窗口大约仍有 11 分钟；`:20/:50` 只剩约 1 分钟。最大 83.8 分钟来自 revision/backfill，不能当实时 lead。

## 2. 盘口有没有留空间

有，但稀疏且容易 adverse selection。

2026-06-18 至 2026-06-27 的 dense PIT book 回放，在 ta `+0.5°C`、single-confirm、每个 date/bracket 首次信号的固定分母上：

- first signals: `27`（8 target dates）
- book covered: `27`
- NO ask available: `19`
- 10-share executable 且 ask `<=0.97`: `4`
- `13/27` 已经高于 `0.97`，`8/27` 没有 NO ask

按 cadence 看，`:10/:30/:40` 各出现过少量可执行例子；`:20/:50` 的 median NO ask 都是 `0.999`，基本说明等到最后一个 KNMI 点才反应，通常已经太晚。

一个重要反例是 2026-06-21：KNMI `14:20` interval 的文件在 `14:23:42` 创建，`14:23:49` 的盘口仍有 previous-bracket NO `0.84`，看起来有 7 秒的速度空间；但下一 KNMI 回落、METAR 未确认跨档，最终该交易输。这说明“盘口没动”有时不是免费延迟，而是市场在给 source false-cross 定价。

### 盘口是在 routine METAR 时才被扫空吗

不是单一时点。把上述 27 个 first-cross signals 的同一 previous-bracket NO，在 KNMI decision、METAR 前、METAR 后和 +2 分钟对齐：

| checkpoint | PIT book covered | ask 已扫空或 ≥0.99 | 尚未扫空 |
|---|---:|---:|---:|
| KNMI decision | 26 | 16 | 10 |
| METAR 前 | 27 | 24 | 3 |
| METAR 后 | 25 | 23 | 2 |
| METAR +2m | 27 | 24 | 3 |

在 decision 与 post-METAR 都有覆盖的 25 个事件中，16 个在 KNMI decision 时已经扫空，另有 7 个在报文前后变成扫空，只有 2 个仍未扫空。说明 market 确实会在 routine METAR 附近完成重定价，但多数 first-cross 在此之前已经被定价。该证据不能识别对手到底使用 KNMI、其他机场源还是路径模型，只能证明“只等 routine METAR”的对手并不是全部市场。

尤其是用户提出的 `:20/:50 → :23:40/:53:40` 形态：10 个事件里 9 个最终 previous-bracket NO 获胜，而这 9 个在 decision 时已全部无 ask 或 ask ≥`0.996`；唯一仍便宜的 `0.84` 是 terminal false、最终亏损。当前样本不支持“看到 :20/:50 KNMI 已明确跨档后，在 :23 买入”作为主策略，且呈现明显 adverse selection。

相反，decision 时尚未扫空的 10 个事件主要出现在更早的 `:00/:10/:30/:40`：其中 8 个最终获胜，7 个到 post-METAR 已扫空。真正应检验的是 `:10/:40` 之前的连续路径概率是否能识别这 8 个，而不是把 `:20/:50` 的事后高准确率当成可交易 edge。

## 3. 推荐的模型与交易实验

不要只训练一个“KNMI 是否高于当前官方温度”的硬信号。使用两个独立 probability heads：

1. `P(next routine METAR confirms cross | information as-of KNMI first-seen)`：短周期 repricing head。
2. `P(final settlement crosses / exact bracket | information as-of checkpoint)`：持有到结算 head。

第一个 head 按 `:00/:10/:20` 与 `:30/:40/:50` 分 slot 训练和评测；首要 checkpoint 为 `:10/:40`。输入至少包括 ta、tx、相对上份 METAR/current high 的距离、10/20-minute slope、acceleration、persistence、pullback/rebound、辐射、云雨风湿度、forecast peak clock、距离下一 METAR 的分钟数和 settlement lattice distance。

策略实验必须分开两种 label：

- 短周期：在 KNMI first-seen 时按真实 taker ask 入场，报告下一 METAR 发布前后 `+30s/+2m/+5m` markout，并明确退出成本。
- 结算：使用 final exact-bracket winner、官方 fee、真实 depth/partial fill，报告信号数、胜率、PnL 和 ROI。

两者都要在同一 checkpoint 与 market probability 比 Brier/logloss/calibration；不能用“下一报文预测对了”替代最终结算胜负，也不能把明显 cross 后的高胜率当成可执行收益。

## 4. 当前证据缺口

用户所说“这两天”的 canonical live 对照目前不完整：2026-08-02 Amsterdam collector 存在 `02:54:11Z..12:26:15Z` 缺口，2026-08-03 当前 canonical JRS context 又无法读写、相关 collector/session 缺失。因此本报告没有声称最近两个完整自然日的实时盘口领先结果；上面的时钟来自最近完整 KNMI/METAR 窗口，盘口可执行性来自明确分开的历史 PIT dense-book 窗口。

下一轮 shadow 应补齐每个 KNMI first-seen 周围的 `pre-event/t0/+15/+30/+60/+120/+300s` ladder，并保存下一 METAR first-seen，才能直接回答“盘口在 KNMI 后第几秒完成重定价”。

## 5. 更早 checkpoint 的模型与 ROI 边界

冻结 Amsterdam V7 weather artifact 的 2025 expanding OOF 为 `50,525` checkpoints / `363` dates：checkpoint EOD Brier/logloss=`0.04996/0.17109`，重算方向 accuracy 约 `93.23%`。同一结构中的 next-routine head 在 V5 OOF 报告为 Brier `0.02127`，显著优于 date-equal prior `0.08949`。这说明天气预测层有能力，但不等于打败 market。

困难集中在本地午后：V5 EOD probability 在 local `10–18` Brier/logloss=`0.08300/0.27041`，`13–15` 为 `0.11013/0.34819`；V4 的本地 13/14/15 点方向 accuracy 分别为 `85.17%/83.73%/79.90%`。因此全天 93% 不能代表最有交易价值的午后窗口。

ROI 证据远少于天气标签：五年天气数据没有 PIT historical book，不能计算可执行 ROI。当前两个分开的 smoke 为：

- 简单 ta first-cross、ask≤0.97、10-share depth：更早 `:10/:30/:40` 共 `4` 笔/`4` 日，`4/4`，含费成本 `$37.5205`、PnL `+$2.4795`、ROI `+6.61%`；这不是 V7 selector。
- V4 model×book target-book：`2` signals / `1` date，`2/2`，含费 ROI `+12.35%`；weather enrichment 为 non-PIT，只有一个独立日期，只能验证 plumbing。

因此当前 verdict 是 `weather probability frozen / early market residual inconclusive`：准确度层已值得继续，ROI 层尚未形成足量同分母 frozen forward。

## 产物

- `generated/knmi_next_metar_cadence_v1/cadence_summary.csv`
- `generated/knmi_next_metar_cadence_v1/cross_summary.csv`
- `generated/knmi_next_metar_cadence_v1/market_summary.csv`
- `generated/knmi_next_metar_cadence_v1/report_transition_events.csv`
- `generated/knmi_next_metar_cadence_v1/report_transition_summary.csv`
- `scripts/analysis/forecast_quality/research_knmi_next_metar_cadence_v1.py`
