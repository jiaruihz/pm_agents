# Weather 研究：日内 forecast 曲线形态与 peak-clock alias v1

## 数据快照

| 项目 | 值 |
|---|---|
| historical source | `docs/analysis/2026-07/generated/current_yes_core_carry_overshoot_missing_mechanisms_v2/feature_ledger.csv` |
| historical coverage | 2026-06-02..2026-07-08；1349 state rows / 772 city-days / 31 target dates |
| forward raw source | `runtime/weather_edge_v1/current_yes_core_carry_tiny_live_v2/pre_live_scores.jsonl` |
| forward coverage | 2026-07-24..2026-07-28；591 checkpoints / 5 target dates |
| DB snapshot mtime UTC | 2026-07-28T13:02:49.181237+00:00 |
| sync / rebuild | 未执行；历史 parent 已覆盖目标窗，7/27 案例直接读 Mac raw，settlement 只读 canonical DB |
| unsettled / missing bracket | 历史 parent 0 / 0；forward 按 settled 子集单列，不把未结算当策略筛除 |

## 结论与动作

**动作：把 `peak-clock alias / future local heat lobe` 作为共享连续风险特征和 zero-notional collector；不改 live，不把“双峰”直接做成交易 gate。**

成都 7/27 证明这个状态可在事前识别：17:30 当地时间，旧模型只看 00:00 global argmax，得到 `peak passed 17.5h` 和 `p_hold=98.85%`；但同一条 PIT 曲线的未来热峰仍到达 current 29 档的 upward-exit boundary。盘口同时给出 29 NO 和 30 YES 约 `9c` 的互补价格，最终 canonical settlement 为 30。

历史 31 日同分母上，新增 boundary-relative morphology 相对 frozen core 的 Brier Δ `0.000127`（95% CI `[-0.0013376749786077305, 0.001689888654552386]`），logloss Δ `0.001209`（95% CI `[-0.004379557696800124, 0.0073559472543067595]`）；负值才是改善。当前没有通过 proper-score baseline，因此它还不是独立 alpha。

但 forward raw 的风险标签很清楚：首个 alias 共 35 city-days / 5 dates，其中 settled 33 city-days / 4 dates，current exact loss 32，loss rate 97.0%。fresh complementary quote 只有 current NO 4 city-days、d1 YES 1 city-days，无法把风险识别包装成可执行策略。

结论等级：`inconclusive_feature_value / zero_notional_collector_candidate`；significance=`FAIL`，baseline=`FAIL`，forward=`NA`（规则由 7/27 案例提出，7/29 起才是真 frozen forward）。

## Target

```text
估计 P(current exact bracket upward-exit | PIT forecast curve morphology,
      boundary-relative future local heat lobes, observed path, source basis)，
并检验它相对同一时点 market/core probability 的 residual。
```

- physical target：未来任一局部热峰是否越过 current exact bracket 的 settlement-native 上沿；不是“forecast 有没有双峰”。
- grain：历史为 carry checkpoint state，评分按 city-day 等权；forward signal 为 first `(city,target_date)` alias checkpoint。
- decision timestamp：历史 `decision_snapshot_ts_utc`；forward raw checkpoint 的 immutable hourly curve。
- label：current exact hold/leave；exact bracket 语义，30 被触及后 29 YES 输、29 NO 赢。
- expressions：risk overlay 主对象是 current YES；独立交易诊断为 current NO / d1 YES，必须用 direct complementary quote、depth 和 Weather fee。
- primary metric：Brier/logloss vs same-row frozen core 与 market；交易 ROI 只作 coverage 足够后的第二层。

## 形态定义

形态名只用于解释，模型吃连续量：global peak clock 的 sin/cos、afternoon-lobe gap、inter-lobe valley depth、prominent peak count、near-max width、peak separation，再加 future exit margin / hours / heat area。

| 名称 | 解释 |
|---|---|
| `canonical_afternoon_single` | 12–17 点单峰 |
| `broad_plateau` | 至少 4 个小时位于全日峰值 1°F 内 |
| `overnight_peak_afternoon_lobe` | 0–6 点 global peak，下午 lobe 距峰≤2°F，且中间 valley≥3°F |
| `morning_peak_afternoon_reheat` | 7–11 点主峰后，下午再次接近主峰 |
| `late_peak_or_advection` | 18 点以后主峰，常对应平流/晚清 |
| `multi_peak_other` | 两个相隔≥4h 的近峰 |
| `peak-clock alias` | global peak 已过>2h，但未来 lobe 仍达 current upward-exit boundary |

## 成都 2026-07-27 PIT 时间线

| local_time | checkpoint_key | curve_shape | global_peak_hour | future_curve_max_native | current_exit_threshold_native | peak_clock_alias | current_bracket | current_no_ask_proxy | d1_bracket | d1_yes_ask_proxy | model_probability_hold | current_final_yes | d1_final_yes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-07-27 13:42 | Chengdu\|2026-07-27\|13 | multi_peak_other | 17 | 30.666666666666668 | 29.5 | False | 29 | 0.2 | 30 | 0.16 | 0.7257275219801212 | 0.0 | 1.0 |
| 2026-07-27 14:41 | Chengdu\|2026-07-27\|14 | multi_peak_other | 17 | 30.666666666666668 | 29.5 | False | 29 | 0.36 | 30 | 0.26 | 0.6128299577165406 | 0.0 | 1.0 |
| 2026-07-27 15:42 | Chengdu\|2026-07-27\|15 | multi_peak_other | 17 | 30.666666666666668 | 29.5 | False | 29 | 0.83 | 30 | 0.64 | 0.22924279971239817 | 0.0 | 1.0 |
| 2026-07-27 16:44 | Chengdu\|2026-07-27\|16 | overnight_peak_afternoon_lobe | 0 | 29.77777777777778 | 29.5 | True | 29 | 0.27 | 30 | 0.31 | 0.9627708987953272 | 0.0 | 1.0 |
| 2026-07-27 17:30 | Chengdu\|2026-07-27\|17 | overnight_peak_afternoon_lobe | 0 | 29.77777777777778 | 29.5 | True | 29 | 0.09 | 30 | 0.09 | 0.9885015083661685 | 0.0 | 1.0 |

17 点 checkpoint：shape=`overnight_peak_afternoon_lobe`，global peak=0h，future max=29.78，29 档 exit threshold=29.50；current NO / 30 YES 的互补 ask proxy 均约 `0.09` / `0.09`。这是事前可见 residual；后到的 18:00 METAR 和 settlement 只作 label。

异常形态其实在 13:42 已出现：当时 00:00 与 17:00 是两个相隔 17h 的近峰，分类为 `multi_peak_other`；16:44 forecast vintage 把 global argmax 从 17:00 切到 00:00，但未来 17:00 lobe 仍越过 29 档上沿。真正的危险是旧模型概率从 15:42 的 22.9% 反跳到 16:44 的 96.3%，不是形态突然消失。

## Wide-denominator sanity

| curve_shape | city_days | dates | overshoots | overshoot_rate | mean_core_risk | mean_market_risk | delta_vs_canonical | ci95 |
|---|---|---|---|---|---|---|---|---|
| canonical_afternoon_single | 370 | 31 | 34 | 9.2% | 0.10855669776487539 | 0.08815270270270267 | 0.0% | [0.0, 0.0] |
| broad_plateau | 338 | 30 | 30 | 8.9% | 0.09836054194488417 | 0.09028994082840233 | -0.3% | [-0.0463673606691866, 0.03875460474837321] |
| multi_peak_other | 23 | 16 | 0 | 0.0% | 0.11668767286061428 | 0.10073913043478258 | -9.2% | [-0.125, -0.06353252822137666] |
| irregular_other | 16 | 12 | 1 | 6.2% | 0.04619473597533492 | 0.07065624999999995 | -2.9% | [-0.11458407951289398, 0.1310545129579982] |
| late_peak_or_advection | 16 | 12 | 3 | 18.8% | 0.17792035207298862 | 0.10549999999999998 | 9.6% | [-0.08684496567505721, 0.2862634302027431] |
| morning_peak_afternoon_reheat | 7 | 6 | 0 | 0.0% | 0.05230851602513297 | 0.07721428571428565 | -9.2% | [-0.125, -0.0635054611747099] |
| overnight_peak_afternoon_lobe | 2 | 1 | 0 | 0.0% | 0.015085126126333951 | 0.14 | -9.2% | [-0.12311557788944724, -0.06390988372093025] |

命名形态没有一个可凭历史点估直接成为 gate。尤其 D-1 historical alias 只有 8 city-days / 7 dates，upward exit 0；这和短 forward 的 97.0% loss 形成强烈 vintage/denominator 差异，说明必须校准 curve issue/run、source basis 和 decision-relative boundary，不能用一个布尔“双峰”外推。

Forward negative control 是 Karachi 7/27：future curve 只刚好到 34 档 exit boundary `34.5°C`，current 34 最终仍 hold。它是 33 个 settled first-alias city-day 里唯一 false positive，说明 `forecast reaches boundary` 不能当确定性标签，也不能事后把 `>=` 改成 `>` 来追样本。

Frozen selector 的形态分布：

| curve_shape | city_days | dates | overshoots | overshoot_rate | mean_core_risk | mean_market_risk |
|---|---|---|---|---|---|---|
| broad_plateau | 75 | 28 | 4 | 5.3% | 0.06734484643636876 | 0.11550666666666662 |
| canonical_afternoon_single | 21 | 16 | 1 | 4.8% | 0.08004804206044021 | 0.11811904761904758 |
| irregular_other | 8 | 7 | 0 | 0.0% | 0.027832373732864495 | 0.07243749999999996 |
| multi_peak_other | 3 | 3 | 0 | 0.0% | 0.08980748444211263 | 0.13899999999999998 |
| morning_peak_afternoon_reheat | 2 | 2 | 0 | 0.0% | 0.05508391349019475 | 0.12249999999999994 |
| overnight_peak_afternoon_lobe | 2 | 1 | 0 | 0.0% | 0.015085126126333951 | 0.14 |
| late_peak_or_advection | 1 | 1 | 0 | 0.0% | 0.1050160131792308 | 0.135 |

## Model / residual

| model | city_days | dates | Brier | logloss | AUC | ΔBrier vs core | Δlogloss vs core | Brier CI |
|---|---|---|---|---|---|---|---|---|
| p_over_core | 538 | 23 | 0.0744 | 0.2643 | 0.7546 | NA | NA | NA |
| p_over_market | 538 | 23 | 0.0769 | 0.2760 | 0.7032 | NA | NA | NA |
| p_over_morphology | 538 | 23 | 0.0749 | 0.2661 | 0.7528 | 0.0001 | 0.0005 | [-0.001156592576680566, 0.0014378372988948628] |
| p_over_boundary_morphology | 538 | 23 | 0.0751 | 0.2677 | 0.7428 | 0.0001 | 0.0012 | [-0.0013376749786077305, 0.001689888654552386] |

`morphology` 只加日形连续量；`boundary_morphology` 再加 relative-to-exit future heat budget。两者都是 expanding OOF，训练只用更早 target dates；但 feature family 是看过 7/27 案例后定义的，因此这轮 OOF 只能作历史 sanity，不冒充真正 frozen forward。

## Expression / execution

历史 first-checkpoint d1 YES price-only diagnostic（`YES ask proxy = 1 - d1 NO bid`，官方 taker fee；bid depth 缺失，所以不称 executable）：

| curve_shape | quoted_city_days | dates | wins | mean_yes_ask_proxy | fee_adjusted_roi | depth_status |
|---|---|---|---|---|---|---|
| canonical_afternoon_single | 370 | 31 | 32 | 0.1042891891891892 | -20.3% | missing_bid_depth_not_executable_claim |
| broad_plateau | 338 | 30 | 28 | 0.1067189349112426 | -25.5% | missing_bid_depth_not_executable_claim |
| multi_peak_other | 23 | 16 | 0 | 0.1233913043478261 | -100.0% | missing_bid_depth_not_executable_claim |
| irregular_other | 16 | 12 | 1 | 0.07250000000000001 | -17.4% | missing_bid_depth_not_executable_claim |
| late_peak_or_advection | 16 | 12 | 2 | 0.11693750000000001 | 2.6% | missing_bid_depth_not_executable_claim |
| morning_peak_afternoon_reheat | 7 | 6 | 0 | 0.08 | -100.0% | missing_bid_depth_not_executable_claim |
| overnight_peak_afternoon_lobe | 2 | 1 | 0 | 0.15000000000000002 | -100.0% | missing_bid_depth_not_executable_claim |

Forward alias first-city-day expression：

| expression | quoted_settled_city_days | dates | wins | mean_ask_proxy | fee_adjusted_roi | depth_status |
|---|---|---|---|---|---|---|
| current_no | 2 | 1 | 1 | 0.15000000000000002 | 221.2% | bid_depth_not_stored_in_pre_live_score |
| d1_yes | 1 | 1 | 1 | 0.31000000000000005 | 211.8% | bid_depth_not_stored_in_pre_live_score |

成都的单笔价格很漂亮，但 d1 YES evidence funnel 只有 1 个 quoted settled city-day；current NO 也只有 2 个，其中 Karachi false positive 亏损。历史 D-1 early-peak/double-lobe 同样没有稳定收益。因此独立策略只保留为 expression hypothesis：先估 `p_upward_exit`，再在 current NO / d1 YES / higher YES 中按 fresh full-ladder EV 选表达。

## Signal funnel

| 层 | grain | rows | dates |
|---|---|---:|---:|
| historical raw carry parent | checkpoint state | 1349 | 31 |
| historical first city-day shape | city-day | 772 | 31 |
| historical peak-clock alias | city-day | 8 | 7 |
| forward raw checkpoints | checkpoint | 591 | 5 |
| forward first alias | city-day | 35 | 5 |

## Evidence funnel

| stage | rows | city_days | dates | settled_rows | settled_city_days | current_exact_losses | current_no_quote_rows | d1_yes_quote_rows |
|---|---|---|---|---|---|---|---|---|
| all_forward_checkpoints | 591 | 162 | 5 | 527 | 137 | 319 | 355 | 125 |
| alias_checkpoints | 67 | 35 | 5 | 64 | 33 | 63 | 6 | 3 |
| first_alias_city_day | 35 | 35 | 5 | 33 | 33 | 32 | 4 | 1 |

- PIT curve：historical 用固定 previous-run Single Runs cache；forward 用 raw checkpoint hourly curve。两者不能混成一个 vintage。
- source first-seen / settlement basis：historical parent 没有完整 first-seen source，属 coverage gap；forward observation/source 字段存在但本轮未把后到 source 当特征。
- book：forward alias 大多缺 direct complementary quote/depth，coverage gap 不能当策略筛选。
- settlement：canonical DB 已确认成都 final bracket 30；未结算 7/28 rows 不进入命中率。
- fill：0；本轮不声称真实 fill 或 realized PnL。

## Frozen forward

- prereg SHA256：`cc47c8585b713594a955c70f969749c527fa2e649108b213c9f08f92e39200c9`。
- start：target_date 2026-07-29；沿用现有 core-carry full-denominator raw checkpoints，不新开生产进程。
- primary：continuous boundary morphology vs same-row market/core Brier+logloss；15 个独立 target dates 后复核。
- expression：current NO / d1 YES 分开记；fresh direct quote + bid depth coverage≥90%，官方 fee，不能用 future touch 冒充 fill。
- promotion：proper-score delta 的 target-date block CI <0；交易层另需 fee-adjusted ROI 与 market excess CI >0。否则保持 feature/collector。
- multiple testing：7 个 named shapes 只作 diagnostic；primary 只有 continuous boundary-relative head。

## 8 环与血缘放置

- 描述性 / 判别 / 概率 / 基准：已覆盖。
- 统计：target-date block bootstrap 已覆盖，但真正 frozen forward 尚未发生。
- 执行：PARTIAL，仅 complementary price proxy；fresh depth/fill 缺失。
- 容量 / 真实 fill：未覆盖。
- 共享数据逻辑：最终应进入 `weather_feature_layer` 的 curve morphology / future-local-peak feature，不进某个 selector 私有脚本。
- canonical candidate：未来按 first checkpoint 写 `state_checkpoint_id + feature_row_id + market_evidence_status`，不另建平行事实表。
- live：本轮不改 selector、sizing、runner 或 deployment。

## 复现

```bash
.venv/bin/python scripts/analysis/reheat_risk/research_intraday_forecast_curve_morphology_v1.py
```

- report: `docs/analysis/2026-07/2026-07-28-intraday-forecast-curve-morphology-v1.md`
- result: `docs/analysis/2026-07/2026-07-28-intraday-forecast-curve-morphology-v1.json`
- generated: `docs/analysis/2026-07/generated/intraday_forecast_curve_morphology_v1/`
