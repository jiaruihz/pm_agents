# Amsterdam KNMI V9：PIT 特征合同修复、冻结评测与策略表达

状态：`CrossNO live不变 / 新增独立market-offset概率策略为zero-notional shadow candidate / 尚未确认稳定alpha`

## CrossNO 之外：独立概率策略（2026-08-12 更新）

用户要求的第二条路线不是给 `.7°C CrossNO` 加模型过滤，而是在每个可交易 KNMI checkpoint 直接估计
`P(final EHAM settlement leaves current exact bracket)`，并同时允许表达 current bracket 的 YES（不离档）和
NO（离档）。模型采用 coefficient-one market prior：

```text
logit(p_post) = logit(p_current_bracket_NO_market)
              + g(KNMI path, forecast peak, remaining heat, market disagreement)
```

这里 market 不是训练标签，也不是让模型“更像市场”；settlement 才是标签，天气/path head 只学习市场 log-odds
之上的 correction。底层 weather probability 使用五年 KNMI 10-minute 数据和 fixed-lead ECMWF previous-day1；
market residual 使用 2026-04-03..07-29 的11,800个 PIT sampled-price checkpoints / 112 dates。开发只在4–5月
训练和选择，6月 validation；最终 residual 仅用截至6月的数据 refit，7月不进训练。

审计时修正了两个会虚高结果的口径 bug：binary current-bracket prior 必须用该合约直接 NO price，不能用
full-ladder 归一化后的 `1-q_current`；6月 validation 必须由只训练到5月的模型评分，不能用含6月标签的 refit
模型回报。修正后 expanding OOF（6月由截至5月模型、7月由截至6月模型）为6,136 rows / 57 dates：posterior
Brier/logloss=`0.04056/0.13588`，market=`0.04718/0.15212`；delta点估为
`-0.00662/-0.01624`，但95% CI上界仍轻微跨0。

固定交易合同为 Amsterdam local 10–16点、KNMI `:10/:40`、side probability≥55%、相对 sampled price +
Weather fee proxy 的edge≥2pp、每 `target_date × expression bracket` 首次进入、5 shares。expanding OOF中98笔/
55 dates、78胜（79.59%），fee proxy后PnL `+$41.3237`、ROI `+11.85%`，target-date bootstrap ROI 95% CI
`[+0.61%,+23.05%]`。同一98个时点只买market favorite为70胜、PnL `-$22.1470`、ROI `-5.95%`；模型相对
market多赚 `$63.4707`，paired target-date bootstrap PnL-delta CI `[$20.7404,$109.1779]`。这组价格是PIT
sampled reference，不是可执行 order book，因此只能证明“值得正式shadow”，不能直接称可兑现收益。

7/30–8/11 collector-exact first-seen t0 book 的seen-window审计覆盖448同盘口checkpoints / 9 dates。posterior
Brier/logloss=`0.01157/0.06502`，market=`0.03362/0.13075`，两项paired date CI均优于market；固定policy为
7笔/6 dates、7胜、5-share taker counterfactual ROI `+31.49%`。但这7笔与同rows market favorite方向完全
相同，故该窄窗只能支持概率校准，不证明额外选向alpha；actual fills仍为0。

artifact `amsterdam_knmi_market_offset_probability_v2` 已冻结，clean-forward boundary 为
`2026-08-12T07:45:13.999634Z`。WCIR adapter/config 已实现但本轮没有重启生产：它保持
`zero_notional_shadow / orders_submitted=0`，与现有 live CrossNO 完全分开。真实sample smoke还发现并修复了旧
Amsterdam adapter 的expression anchor bug：物理running max为18°C而盘口最低档为“23°C or below”时，必须保存
`physical_current=18`、使用`expression_current=23 / hard_floor`构造概率和交易命题；不能拿18去找不存在的合约。
若floor盘口只有单边book，market-offset模型按合同写`market_prior_midpoint_interval_censored` blocker，不伪造概率。

## 最终选定的交易策略

Amsterdam 当前只推进一条交易表达：KNMI first-seen 的 `ta` 首次高于旧 official exact bracket 至少
`0.7°C` 时，买该旧 bracket 的 `NO`；要求 t0 ask≤0.97、10-share 可执行深度，最多10 shares，扣官方
Weather fee 后持有至结算。市场价格只作为真实买入成本，不进入天气模型训练。

独立 `amsterdam_knmi_cross_survival` head 估计
`P(final settlement leaves previous bracket | first ta +0.5°C cross)`，共102个纯天气特征；模型在2024年内
完成选择，2025年为一次性 frozen test。`.7°C` 子集为636 rows/238 dates、626 true / 10 terminal-false，
AUC `0.9452`、Brier `0.01682`、logloss `0.06500`；同 margin 历史常数基准为
`0.02055/0.10314`。paired date-bootstrap 的 logloss delta 为 `-0.03814`，95% CI
`[-0.07316,-0.00948]`；Brier点估改善但CI仍轻微跨0。它已经证明能识别 terminal-false 风险，但尚未证明
相对当时盘口的可交易 residual。

最终 forward 信号合同锁定为：`.7°C first cross` 且
`p_cross_survives - (taker ask + fee) > 1pp`。模型在这里是排雷器，不是市场模仿器；V9 current-bracket
EOD 模型不再承担 CrossNO 选股。2026-07-30..08-11 development PIT replay 中，裸 `.7` 为3笔/3日、3胜、
fee后 ROI `+7.79%`；新 survival 合同选择其中2笔/2日、2胜、ROI `+5.12%`。另一个完整 raw scorecard
还覆盖08-08，因此裸规则为4笔/4日、4胜、ROI `+12.69%`；两套 replay 的差异来自 V9 feature archive
缺08-08 official row，不能把3笔和4笔混成同一分母。

实际资金证据只有一笔：2026-08-11 Amsterdam 21-NO，8 shares @0.97，cost `$7.76`、fee `$0.01164`，
已结算盈利 `$0.22836`，ROI `+2.94%`。这说明链路能成交且语义正确，不说明稳定收益。当前 live runner
仍是用户此前批准的裸 `.7` 小仓规则；本次新增 survival head 已于2026-08-12 03:02 UTC重载进 WCIR
zero-notional。production SHA `70aa30f7…f28bf8c7e`、artifact SHA `b2d5ec1b…854a2`，首轮
`status=ok / errors=0 / orders_submitted=0`。未来把它改成真实资金 gate 仍需要单独生产授权。

## 结论

Amsterdam 旧 V7 不是“效果差一点”，而是 production feature contract 有 bug：artifact 要求 145 个字段，runtime 只真正生成 80 个，65 个 forecast/path forcing 字段长期以 NaN 进入模型；WCIR migration 又没有把 `feature_coverage` 与 `missing_features` 传到公共 lineage。该生产证据不得再用于评价 V7 算法。

修复后的 V9 使用 2024-03-01..2025-12-31 的 92,637 个 10-minute checkpoints、665 个 target dates，输入为 80 个 KNMI/official/solar/path 特征加 20 个固定 `ECMWF previous_day1` forecast-path 特征。训练和 expanding OOF 均不读取 2026-08 数据。

V9 在 2025 expanding OOF 的结果：

| grain | rows / dates | accuracy | Brier | logloss |
|---|---:|---:|---:|---:|
| all checkpoints | 50,525 / 363 | 93.67% | 0.04574 | 0.15819 |
| 12:00–16:00 local | 8,994 / 362 | 85.84% | 0.10258 | 0.32913 |

真正的一次性 8 月 frozen test 是先冻结的 V8，不是后来吸收诊断得到的 V9。V8 在 2026-08-01..11 的 1,101 settled checkpoints / 10 dates 上 accuracy 91.37%、Brier 0.05497、logloss 0.18012；同盘口 361 rows / 8 dates 上 Brier 0.09516，显著差于 market 0.03250，paired date-bootstrap ΔBrier（model-market）为 `+0.06267`，95% CI `[+0.00671,+0.15144]`。

V9 的同一 8 月窗口只能称 post-freeze development audit：补齐 8 月 11 日完整日内 path 后，全 checkpoint 1,125 rows / 10 dates，accuracy 97.51%、Brier 0.02154、logloss 0.08125；同盘口 361 rows / 8 dates上 Brier 0.04252，已把 model-market ΔBrier 收窄到 `+0.01002`，95% CI `[-0.00473,+0.02503]`，但尚未打败 market。

## 策略口径

表达只看 KNMI `:10/:40`（下一份 routine METAR 前约 10 分钟），在同一 current exact bracket 的 YES/NO 中选择模型净 edge 更高的一侧；entry 使用 t0 captured best ask、top ask depth 至少 5 shares、5 shares sizing，并扣 Weather fee。每个 `target_date × current bracket` 只取首次 eligible 信号。

8 月 post-freeze development audit 中，`edge_after_fee >= 2pp`、side probability >=55% 得到 7 笔 / 5 dates，4 胜 3 负，taker cost `$19.5305`、PnL `+$0.4695`、ROI `+2.40%`。但 target-date bootstrap 95% ROI CI 为 `[-100%,+34.46%]`，`P(ROI>0)=52.1%`，与掷硬币没有可区分的稳定性证据。

为避免用不同分母抬高 market baseline，另在这 7 个完全相同的时点上只把方向改为当时 market favorite；7/7 都有 5-share 可执行 ask，结果是 6 胜、PnL `+$4.2346`、ROI `+16.44%`。V9 相对该同分母基准少赚 `$3.7651`，target-date bootstrap 的 model-minus-market PnL CI 为 `[-$16.9621,+$11.2481]`。这说明当前小样本里的正 PnL 主要来自被选中的时点本身，不能证明 V9 的天气方向产生了 alpha。

稳定性切片也不通过：5 个 active dates 只有 2 日盈利、3 日亏损，最大顺序回撤 `$3.5620`；移除 8 月 5 日或 8 月 7 日任一盈利日，剩余 ROI 分别变成 `-17.31%` 和 `-18.67%`。YES 为 3 笔 2 胜、ROI `+42.46%`，NO 为 4 笔 2 胜、ROI `-20.07%`，收益依赖 side。预注册 primary 5pp policy 实际为 6 笔、ROI `-35.28%`；五组 policy 中只有事后锁定的 2pp 版本为正，存在明显参数脆弱性。

因此当前不是“V9 在实际交易中有稳定收益”：它只有 counterfactual taker 回放，actual fills 为 0；显著性、同分母 market baseline、clean frozen forward、真实成交/markout 四项都没有通过。V9 退到 dormant-for-now research comparator，不再作为 Amsterdam 主交易表达。

V9 的 `2pp` policy 是在 post-freeze development 后锁定，配置的最早 clean-forward boundary 为 Amsterdam local 2026-08-12 00:00（UTC 2026-08-11 22:00）；真正样本从 production controller 成功重启后的首个 V9 decision 开始。以后不再用 8 月 1–11 日调该 policy。

## Bug 影响半径与修复

- 受影响窗口：WCIR V7 自 2026-08-03 至 2026-08-11。
- raw 中去重后 538 个 V7 model outputs / 8 dates，产生 10 个 selected zero-notional intents / 8 dates。
- 10 个 intent 的 `requested_size=0`；公共 execution handoff 中 0 条命中；canonical `fact_trades` 为 0 fills、`$0` fill cost。没有真实资金损失，但这段 V7 probability/shadow evidence 标记为 feature-contract polluted。
- 新 adapter 对结构缺列 fail closed；每个 ModelOutput/SignalCandidate 保存 `feature_coverage`、`missing_features`、forecast capture path/line/hash/available-at。
- operational forecast collector 新增 Amsterdam `ECMWF previous_day1` immutable curve；2026-08-12/13 已各生成 24 小时完整曲线。训练历史使用同一参数与固定 lead semantics，避免 current forecast、GFS 与 ECMWF 混用。
- expression 已扩为互补 YES/NO，映射各自 token、ask、mid；仍为 `zero_notional`、requested size 0。
- 2026-08-12 02:02 UTC 的当前 WCIR summary 已加载 V9 artifact SHA `c0bcb0...ecd4d2`，execution mode 仍是 zero-notional；Amsterdam 首个新增 checkpoint 因 `missing_current_bracket_market` 写入 blocker，V9 decision bundle / intent / canonical candidate / fill 均为 0。也就是说 runtime 已加载，但 clean-forward 收益样本尚未开始；不得把“artifact 在进程里”写成 shadow 已有业绩。

## 可重复证据

- V8 frozen reproduction：`/Volumes/jrs/weather_data_feed_service_runtime/research/model_runs/amsterdam_knmi_remaining_heat_v8/frozen_2026_08_reproduced/metrics.json`
- V9 artifact：`/Volumes/jrs/weather_data_feed_service_runtime/research/model_runs/amsterdam_knmi_remaining_heat_v9_ecmwf_day1/run=61b8cde77f5320a4/model_v9.pkl`，SHA-256 `c0bcb0afe1a86da376e86affb411e146b26c292c68bad9c9b6db246acaecd4d2`
- V9 training metrics：同 run 下 `metrics.json`
- V9 development replay：`/Volumes/jrs/weather_data_feed_service_runtime/research/model_runs/amsterdam_knmi_remaining_heat_v9_ecmwf_day1/development_2026_08_run61b8/metrics.json`
- V9 同选中时点 market baseline：同目录 `same_selected_rows_market_favorite_trades.csv`
- Cross-survival artifact：`/Volumes/jrs/weather_data_feed_service_runtime/research/model_runs/amsterdam_knmi_cross_survival/run=53d49e6d7a4c075e/model.pkl`，SHA-256 `b2d5ec1b06cd2c85e764182fbe65ac5a96a438c399016c6107dc80180bd854a2`
- Cross-survival frozen metrics：同 run 下 `metrics.json`；development PIT replay：`/Volumes/jrs/weather_data_feed_service_runtime/research/model_runs/amsterdam_knmi_cross_survival/development_20260730_20260811/metrics.json`
- fixed-lead history：`/Volumes/jrs/weather_data_feed_service_runtime/research/amsterdam_ecmwf_previous_day1_path_v1/forecast_hourly.csv.gz`，SHA-256 `821ae0c4c613a5b863b9c86927dd9f5c02c4cbaecb9d60bd982001fd96c57b42`
- market-offset v2 artifact：`/Volumes/jrs/weather_data_feed_service_runtime/research/model_runs/amsterdam_knmi_market_offset_probability/run=development_20260812_direct_contract_v3/market_offset.pkl`，SHA-256 `a3c69c23964a9344f8f75311af99116b3aa7963087cb2878c4296c08d56e4822`
- market-offset training/expanding OOF：同目录 `market_offset_metrics.json`
- collector-exact t0 replay：`/Volumes/jrs/weather_data_feed_service_runtime/research/model_runs/amsterdam_knmi_market_offset_probability/collector_replay_20260730_20260811_v3/metrics.json`

后续主 scorecard 只看 `cross07_survival_edge01` 在 2026-08-12 03:00 UTC 后新增的 settled target dates；
固定同时报告裸 `.7`、模型合同和同 checkpoint market favorite，不再用新增标签改 margin、edge 或退出方式。

## 2026-08-12 独立红队与 market-offset v3（覆盖此前 v2 升级判断）

独立红队确认 v2 的历史执行回放存在 market clock 错位：11,800 个 market reference 全部不晚于
KNMI observation time，中位还早 55 秒；真实 collector 中 KNMI first-seen 相对 observation 的中位延迟为
222 秒。因此旧 `+11.85%` 不能叫可执行 ROI，它实际混合了 first-seen 前市场价和之后才能得知的 KNMI 值。
这不污染 probability fit 的因果性——pre-event market 本来就可以作为 prior——但污染了 entry cost。

v3 把两个时钟拆开：模型输入固定为 strictly-pre-first-seen market prior，入场与 market baseline 固定为
first-seen t0 ask/mid。历史 OOF 也改为 nested expanding：六月的超参数只用五月选择并训练至五月，七月的
超参数只用六月选择并训练至六月。6–7月共6,136 checkpoints / 57 dates，posterior Brier/logloss
`0.04246/0.14154`，market 为 `0.04718/0.15212`；delta 点估为 `-0.00472/-0.01057`，但95% CI
`[-0.01091,+0.00162] / [-0.02733,+0.00780]`，仍未过显著性门。

用 `observation+240s` 的 sampled prices-history 做 repricing 压力测试时，七月固定策略得到42笔/27日、
34胜，fee proxy ROI `+24.04%`，date-bootstrap CI `[+3.84%,+45.58%]`；同42行 post-240 market
favorite 为29胜、ROI `-6.33%`，model-minus-market PnL CI `[$6.55,$79.50]`。这是方向值得继续的最强
历史证据，但该 API 没有 bid/ask/spread/depth，所以仍不是 executable evidence。

collector-exact clock-parity replay 覆盖443个同分母 checkpoints / 9 dates：posterior Brier/logloss
`0.02205/0.09975`，t0 market 为 `0.03403/0.13224`；delta 为 `-0.01198/-0.03250`，95% CI
上界分别 `+0.00083/+0.00073`，都仍轻微跨0。19个方向分歧/4日中模型正确14次、market正确5次，
说明存在可能的纠偏能力；但transition/state-entry只有21行/7日且0次方向分歧。固定`:10/:40`、2pp、
5-share表达为9笔/6日、9胜、taker counterfactual ROI `+27.22%`，可这9笔与market favorite方向全部相同，
paired uplift严格为0，actual fills也仍为0。

红队同时发现并已修复两处 runtime parity bug：Amsterdam adapter 现在把YES/NO完整ask ladder传给公共
5-share sweep，避免`execution_cost=None → would_enter=false`；position scope 改为每
`target_date×bracket×model`只允许首个最佳方向，不能翻边重复发。未训练的hard floor/ceiling和
`or higher`表达 fail closed；`or below`只有物理current与边界相等时才允许。checkpoint预测表现在保留
pre-event prior、t0 execution book、transition/state-entry和完整posterior，便于逐行复核。

最终 artifact 为 `amsterdam_knmi_market_offset_probability_v3`：11,800 rows / 112 dates，训练至
2026-07-29，部署选择为`weather_path_mechanism + L2=0.01`，clean-forward start
`2026-08-12T08:24:21.919654Z`。SHA-256
`93b525fd752bc5bbb1068469c6d8e428de176c89114b3e3d2efc335e44534881`。

结论不是放弃，而是 **shadow candidate / not live**。significance、selected-row market uplift、untouched
forward、actual fill/capacity 四门仍未同时通过。继续冻结收集，至少达到30个新settled executable signals、
10个active dates，并且proper-score delta CI全负、fee-adjusted paired uplift CI为正且包含足够真实方向分歧，
才进入tiny-live评审；在此之前不碰真钱，也不影响既有CrossNO。

新增证据：

- v3 training/nested OOF：`/Volumes/jrs/weather_data_feed_service_runtime/research/model_runs/amsterdam_knmi_market_offset_probability/run=development_20260812_v5_post_firstseen_stress/market_offset_metrics.json`
- v3 artifact：同目录 `market_offset.pkl`
- final clock-parity collector replay：`/Volumes/jrs/weather_data_feed_service_runtime/research/model_runs/amsterdam_knmi_market_offset_probability/collector_replay_20260730_20260811_v8_final_review/metrics.json`
- 逐checkpoint审计：同目录 `checkpoint_predictions.csv.gz`
