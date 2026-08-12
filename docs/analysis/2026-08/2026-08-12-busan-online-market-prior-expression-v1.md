# Busan online market-prior expression v1

## 结论

Busan 得到了第一套**点估为正、可重复离线评估、可运行 zero-notional shadow**的 market-prior
候选：`busan_intraday_exact_no_online_market_prior_residual`。

它不再让 weather-only 模型直接和 ask 比较，而是把同一 PIT checkpoint 的盘口
当作 prior，只允许天气模型在 logit 空间做受限修正：

```text
logit(P_post) = logit(P_market)
              + weather_weight * (logit(P_weather) - logit(P_market))
```

`weather_weight` 每天只用**此前已经结算的 target dates**按 date-equal logloss
选择；当天 label 不参与当天权重。8/04–11 expanding-date OOF 的权重路径为
`0, 0, 0, 0, 0, 0.125, 0.25, 0.25`，因此模型只在最后三天认为天气信息有
足够增量。

同盘口 65 checkpoints / 8 target dates 上：

| 口径 | Market | Online posterior | 差值（candidate-market） |
|---|---:|---:|---:|
| date-equal logloss | 0.29021 | **0.26818** | **-0.02203**，date-block CI95 `[-0.04821,0.00000]` |
| date-equal Brier | 0.09324 | **0.08353** | **-0.00971**，date-block CI95 `[-0.02301,0.00000]` |

fee-adjusted taker replay 为 **6 单 / 3 天 / 5 胜，PnL +$5.6138，ROI
+28.96%，date-block CI95 `[+15.54%,+40.48%]`**。但有效天气创新只有 3 个
独立日期，而且 online family 是看过 8/04–11 后才确定的，所以状态仍是
`zero-notional shadow candidate / clean forward pending`，不是 live alpha。原报告把数值误差造成的
`-1e-17` 当成了严格负上界，从而误判为 CI 胜过市场；当前已修正为
上界 0，因此不具备统计显著性。

## 固定分母

### Signal funnel

```text
8/04–11 Busan exact-NO checkpoint identity       106
同 checkpoint 有 causal PIT book                   72
settled + weather/market probability 同时有效       65
online weight > 0 的独立日期                         3
first fee-positive date-rung expression              6
```

概率评分不使用 price、edge 或结算结果筛行。交易表达才使用第一笔
`P_post > no_ask + official fee`，每个 `target_date × routine rung` 去重，最多
5 shares。

### Evidence funnel

- 开发输入原有 96 rows；自动剔除与评估窗重叠的 8/04 共 10 rows。
- 非重叠开发窗为 7/29–8/03；其中同盘口概率分母是 14 rows / 5 dates。
- OOF 评估窗为 8/04–11，65 rows / 8 dates；所有日期已结算。
- 8/12 当前 market proxy `127.0.0.1:7896` 拒绝连接，实时盘口链降级；因此
  8/12 残缺数据没有进入训练或评分。

## 逐日因果权重

| Test date | Train end | Train dates | Weather weight |
|---|---|---:|---:|
| 8/04 | 8/03 | 5 | 0 |
| 8/05 | 8/04 | 6 | 0 |
| 8/06 | 8/05 | 7 | 0 |
| 8/07 | 8/06 | 8 | 0 |
| 8/08 | 8/07 | 9 | 0 |
| 8/09 | 8/08 | 10 | 0.125 |
| 8/10 | 8/09 | 11 | 0.25 |
| 8/11 | 8/10 | 12 | 0.25 |

加入 8/11 结算后，下一日期的 frozen state 是 `weather_weight=0.25`，训练证据
覆盖 13 dates / 79 同盘口 rows，trained through 8/11。

## 逐单 replay

| Date / rung | P_market | P_weather | Weight | P_post | Ask | Edge/share | Label | PnL |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 8/09 / 31 | .640 | .971 | .125 | .720 | .68 | +.0290 | win | +$1.5456 |
| 8/09 / 32 | .015 | .371 | .125 | .023 | .02 | +.0025 | loss | -$0.1049 |
| 8/10 / 29 | .945 | .989 | .25 | .963 | .96 | +.0007 | win | +$0.1904 |
| 8/10 / 31 | .425 | .966 | .25 | .648 | .46 | +.1754 | win | +$2.6379 |
| 8/11 / 29 | .945 | .991 | .25 | .965 | .96 | +.0030 | win | +$0.1904 |
| 8/11 / 30 | .735 | .968 | .25 | .834 | .76 | +.0649 | win | +$1.1544 |

每日 PnL 分别为 `+$1.4407 / +$2.8283 / +$1.3448`。这解释了为何 3-day
date bootstrap 没有跨零，也同时说明置信区间由很少的独立日支撑，不能按普通
“8 天策略”理解。

## 稳健性与执行表达

不是只在一个网格上碰巧为正：

| Variant | OOF weight pattern after 8/08 | Logloss delta | Trades | ROI |
|---|---|---:|---:|---:|
| primary logloss | .125/.25/.25 | -.02203 | 6 | +28.96% |
| primary Brier | .125/.25/.25 | -.02203 | 6 | +28.96% |
| coarse grid | .25/.25/.25 | -.02685 | 7 | +23.99% |
| decimal grid | .20/.20/.30 | -.02323 | 6 | +28.96% |

交易成本缓冲只作执行敏感性，不反向改 eligibility：

| Min edge/share | Orders/dates | PnL | ROI |
|---:|---:|---:|---:|
| 0 | 6/3 | +$5.6138 | +28.96% |
| 0.5c | 3/3 | +$5.3379 | +55.25% |
| 1–2c | 3/3 | +$5.3379 | +55.25% |
| 3–5c | 2/2 | +$3.7923 | +61.09% |

这说明正收益不依赖两笔贴成本线的 29 NO；但 buffer 切片同样只有 2–3 天，不能
据此新增 live gate。若进入 shadow forward，应同时记录 raw-positive-edge 与
`+0.5c` execution-quality 两个表达，使用同一 probability denominator。

## WS 的真实贡献与缺口

Busan WS 从 8/09 07:59Z 后才开始，8/09 两笔模型信号发生在 02:22Z 与
06:27Z，均早于覆盖；8/10–11 订阅又是间歇性的。当前只有 2 个独立 settled
target dates、约 12 个潜在同 checkpoint exact-rung rows 可用于 WS 增量诊断，
不足以训练 settlement probability head。

因此本候选的入参只有 `P_market(NO)` 与 frozen weather probability；WS 暂只用于
spread/depth/短时 adverse-selection 诊断。不得把 frames 当独立样本，也不得把
generic hot-strip 的 60s markout 当本模型收益。

## Shadow 部署审计与修复

审计发现的阻断项已按同一模型合同修复：

- 在线 adapter 只读未结算 Korea checkpoint，不读取 `label_no/settled`；注入或翻转
  label 不改变输出。
- 冻结的是历史真实使用的 18 特征 RF confirmation head，而不是拿 19 特征新 ontology
  冒充原模型；底层 physical artifact 仍为 train-through 7/21 的四特征模型。
- 序列化 composite artifact 在 8/04–11 的 106 条 frozen rows 上逐行复现：RF confirmation
  与 composed weather probability 的最大绝对误差均为 `1.11e-16`。
- feature book response 上限为 10 秒，runtime execution book age 上限为 90 秒；历史
  177.6 秒异常行会明确输出 `not_scorable`。
- 真实 8/11 case 已穿过 `CityScore → ModelOutput → SignalCandidate → TradeIntent`；
  token/condition/outcome/book identity 完整，TradeIntent 为 `mode=zero_notional`、
  `requested_size=0`，不会创建真实订单。
- 6 笔 replay 的 raw checkpoint、rung、ask/depth、fee 和 settlement 可回连，但其中两笔
  edge 不足 0.3c，且 6 笔只来自 3 个 active dates，不足以支持显著性。
- 8/12 早先的 proxy 与 canonical refresh 阻断已经恢复；部署前 strict manifest、JRS、
  market-books、Korea collector、city runtime 均为 healthy。

## 决策与下一步

1. 保留 weather-only physical head，market 只在独立 expression head 中作 prior；
   不把盘口混入物理特征。
2. 保留 `weight=0.25, trained_through=8/11` 作为**待验证**的下一日状态；
   8/12 残缺数据不得补入。
3. 已固化并 parity-lock composite artifact，也已实现不读 label 的 Busan WCIR adapter；
   clean forward 从 `2026-08-12T03:00:00Z` 起 append，部署前的当日数据不回填冒充 forward。
4. 至少新增 5 个 `weight>0` 的独立日期并出现可执行信号后，再做一次固定分母
   admission；当前只有 3 天，且 clean forward 为 0 天。
5. WS 等覆盖至少跨多个独立 settlement dates 后，只做增量 A/B：
   `weather+market level` vs `weather+market level+WS dynamics`。

## 可复现入口与产物

入口：

```text
python -m weather_model_evaluation.cli busan-market-prior \
  --development-input <expanding-oof.csv.gz> \
  --evaluation-input <fixed-forward.csv.gz> \
  --weather-column p_factorized_random_forest_full_weather \
  --weather-weight 0.125 \
  --freeze-cutoff 2026-08-11 \
  --forward-start 2026-08-12 \
  --bootstrap-draws 10000 \
  --runtime-state-input <frozen-pending-state.csv.gz> \
  --physical-artifact <physical-v7.joblib> \
  --runtime-artifact-output <busan-online-market-prior.joblib> \
  --output-dir <artifact-dir>
```

当前修正后 durable output：
`/Volumes/jrs-archive/weather_data_feed_service_runtime/research/model_runs/busan_online_market_prior/run=20260812_010000z/evaluation/summary.json`；
生产可读的 parity-locked artifact 位于
`/Volumes/jrs/weather_data_feed_service_runtime/model_artifacts/city_probability_runtime_v3/busan_online_market_prior_v1.joblib`。
其中包含 prediction、逐单、daily weight history、weight-grid、robustness 和
execution-buffer sensitivity 的完整路径与 SHA/build identity。
