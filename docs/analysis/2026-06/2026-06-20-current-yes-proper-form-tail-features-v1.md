# Current-YES Proper-Form Tail Features v1

Status: research-only
Date: 2026-06-20
血缘层: [1] 概率/特征层，row grain=`city + target_date + decision snapshot`
Target metric: `current_yes_survive_discrimination`，即区分 survive=0（当前 running-max 档被后续更高温打穿）

## 结论先行

**交易动作：不加尾部新特征，不动模型，不改 live，仍 research。** 这轮测完两个正确形态后，没有任何一个新增尾部特征在 base v9 之上给出 material holdout logloss 改善；因此不把它们加进 `train_current_yes_peak_forming_hazard_v1.py` 的 `NUMERIC_FEATURES`，也不重训/替换模型。

这不是“气象特征都没用”。更准确的合并结论是：**常规 METAR 温湿趋势对 survive 有真实物理判别力，但这部分主要已经被 market/base 吸收；本轮新增的风向/云导数尾部形态没有提供稳定 residual edge。**

`d_sky_3h × 太阳高度角` 是负结果：AUC 0.530，delta logloss -0.00008，近似 0。`风向 × city` 有一点单独判别力（AUC 0.607），但叠到 base v9 后 holdout logloss 明显退化（+0.04971），像过拟合或样本不稳，不加。

进一步诊断显示：`metar_weather_core`（温度、露点、湿度、风速、云、温湿趋势）自己预测 survive 的 holdout AUC 为 0.795；加到 market price 后 logloss 点估从校准 market 的 0.3022 降到 0.2980，delta -0.00423，但 95% target-date bootstrap CI 为 [-0.01588, +0.00079]，跨 0。加到 base v9 后 delta 只有 +0.00015，说明 base v9 已基本吃掉这批常规天气信息。

气压倾向 `d_alti_3h` **blocked**：当前 `theta_no_iem_ext_patch_v6` 只有 `drct/sknt/skyc1`，没有 `alti` 列。本轮没有用近似字段替代；要测它必须先在 N100 IEM fetch 加 `alti` 并重物化 ext cache。

三门 verdict:

```text
significance=FAIL
baseline=FAIL
forward=NA
conclusion=inconclusive / research-only
```

## 数据快照

- 数据源: `runtime/weather.db` + `docs/analysis/2026-06/generated/theta_yes_current_full_replay_v8/feature_rows.csv` + `docs/analysis/2026-06/generated/theta_no_iem_ext_patch_v6/`
- 同步/重建: 已执行 `scripts/ops/sync_weather_remote.sh`；`scripts/weather_dashboard/run_stack.sh` 完成 DB/fact/gate 重算后因前端 5174 端口占用退出，核心 DB 已重建
- DB snapshot: `fact_trades MAX(fact_built_at_utc)=2026-06-19T16:37:17.775637+00:00`
- CLOB fill coverage gate: `gate_pass=true`，DB/live cache/fact live_real fills 均为 855，`db_not_in_cache=0`，`cache_not_in_db=0`
- feature rows: 3,239 rows，`target_date=2026-05-19..2026-06-14`；train 1,527 rows，holdout 1,712 rows / 14 active dates
- unsettled 占比: `fact_trades` 150/4,400 = 3.4%
- missing_bracket 数: 0

5 行 SQL 自检:

| check | result |
|---|---|
| fact build | `fact_trades_max_built_at_utc=2026-06-19T16:37:17.775637+00:00` |
| trade_class | `live_real=855, live_simulated=624, paper=2285, snapshot_replay=636` |
| settlement | `settled=4250, blank/unsettled=150` |
| candidate coverage | `fact_signal_candidates rows=33141, eligible=11643, paper_ordered=4509, live_filled=348` |
| CLOB order/fill join | `submitted orders=961 with_fill=855; error orders=33 with_fill=0` |

Join 自检: `corr(sky_now 原表, ext asof 还原)=1.000`。

## 基准

| baseline | holdout rows | dates | AUC | logloss | Brier |
|---|---:|---:|---:|---:|---:|
| market price as probability | 1,712 | 14 | 0.929 | 0.3042 | 0.0946 |
| base current-YES v9 | 1,712 | 14 | 0.925 | 0.3130 | 0.0954 |

市场价本身仍是最强基准；base v9 稍弱于 market price-as-prob。本轮新特征的 promotion 标准是：在 base v9 logit 之上让 holdout logloss 有 material 改善（本报告把 `abs(delta_logloss)<=0.001` 视为 near-zero）。

## 正确形态结果

| feature family | coverage | feature-only AUC | delta logloss vs base v9 | verdict |
|---|---:|---:|---:|---|
| `d_sky_3h_x_solar_altitude_pos` | 73.9% | 0.530 | -0.00008 | near_zero，不加 |
| `wind_city_interaction`（72 维 city x wind sin/cos） | 92.0% | 0.607 | +0.04971 | no_or_degrades，不加 |
| `d_alti_3h` | NA | NA | NA | blocked，ext cache 无 `alti` |

补充读法：

- `solar_altitude_deg/pos` 单独 AUC 0.713，但 delta logloss 分别是 +0.00051/+0.00049；它更像时段/太阳高度 proxy，base v9 和盘口已经吸收了这部分信息。
- 真正要验证的 `d_sky_3h × solar altitude` 没有判别力，AUC 接近 0.5。
- wind × city 用 one-hot city 交互后没有解决问题，反而使 holdout logloss 从 base 校准后退化到 0.3645。

P0 廉价形态复现:

| feature | coverage | AUC | delta logloss vs base v9 | verdict |
|---|---:|---:|---:|---|
| `wind_dir_sin` | 92.0% | 0.535 | +0.00064 | near_zero |
| `wind_dir_cos` | 92.0% | 0.526 | -0.00041 | near_zero |
| `d_sky_3h` | 73.9% | 0.535 | -0.00016 | near_zero |

参照特征中，`d_tmpf_3h` 单独 AUC 0.776，说明打穿日确实主要由温度趋势区分；但它已在 base v9 里，叠加后 delta logloss 近零。

## 深入诊断：不是气象没用，是 residual edge 变薄

| feature group | conditioning | AUC | logloss | delta logloss | 95% date bootstrap CI |
|---|---|---:|---:|---:|---:|
| `metar_weather_core` | weather only | 0.795 | 0.5082 | NA | NA |
| `metar_weather_core` | plus market price | 0.931 | 0.2980 | -0.00423 | [-0.01588, +0.00079] |
| `metar_weather_core` | plus base v9 | 0.925 | 0.3150 | +0.00015 | [-0.00059, +0.00115] |
| `p0_p1_tail_candidates` | weather only | 0.757 | 0.5689 | NA | NA |
| `p0_p1_tail_candidates` | plus market price | 0.913 | 0.3547 | +0.05243 | [+0.02643, +0.07616] |
| `p0_p1_tail_candidates` | plus base v9 | 0.909 | 0.3653 | +0.05048 | [+0.02574, +0.06458] |
| `metar_plus_tail_candidates` | weather only | 0.832 | 0.4860 | NA | NA |
| `metar_plus_tail_candidates` | plus market price | 0.915 | 0.3540 | +0.05180 | [+0.01449, +0.07427] |
| `metar_plus_tail_candidates` | plus base v9 | 0.908 | 0.3676 | +0.05275 | [+0.02908, +0.06934] |

读法：

- **符合物理常理的部分成立**：温度趋势、湿度趋势、太阳高度这类变量和 survive 标签有明显关系。比如 `d_tmpf_3h` 对 label 的相关为 -0.420，`d_relh_3h` 为 +0.333，`solar_altitude_pos` 为 -0.330。
- **但交易问题问的是 residual**：同一批变量对 `y - market_price`、`y - base_v9` 的相关明显变小。`d_tmpf_3h` 对 market residual 只有 -0.067，对 base residual 只有 +0.021；`d_sky_3h × solar` 对 base residual 只有 +0.002。
- **尾部候选稳定退化**：把 P0/P1 tail candidates 加到 market 或 base 后，日期 bootstrap CI 全在正数侧，说明不是“没明显改善”，而是这组高维/粗糙候选在 holdout 上稳定变差。

客观规律解释：天气当然决定最高温是否继续刷新；但市场价和 base v9 已经把“当前多热、还在升温吗、湿度/露点是否支持继续升温、现在几点”这类一阶信息吃掉了。本轮新增的风向/云导数形态太粗：风向缺上游温度梯度，云导数来自粗 METAR sky code，太阳高度只解释日内时段，三者都没有形成可交易的额外残差。

## 为什么不重训

用户给定流程是“过关的特征加进 `train_current_yes_peak_forming_hazard_v1.py` 的 `NUMERIC_FEATURES` 重训”。本轮过关特征数为 0，所以没有改训练脚本、没有生成新模型、没有跑 `train_selected_grid` ROI/95% 日期 bootstrap CI。这样做是为了避免把 near-zero 或退化特征硬塞进模型。

## 8 环覆盖自检

| 环 | 覆盖情况 |
|---|---|
| 1 描述性绩效切片 | 不适用，本轮不报 live/PnL |
| 2 统计推断 | 部分覆盖：holdout logloss/AUC；未做 ROI bootstrap，因为未触发重训/交易规则 |
| 3 信号判别 | 已覆盖，目标为 survive=0 区分度 |
| 4 概率分布评估 | 已覆盖，base v9 之上增量 logloss/Brier |
| 5 执行微结构 | 不覆盖 |
| 6 容量 | 不覆盖 |
| 7 组合相关性 | 不覆盖 |
| 8 基准/反事实 | 已覆盖 base v9 与 market-price-as-prob |

## 产物

- Script: `scripts/analysis/reheat_risk/validate_reheat_tail_feature_discrimination_v1.py`
- Summary: `docs/analysis/2026-06/generated/current_yes_proper_form_tail_features_v1/summary.json`
- CSV: `docs/analysis/2026-06/generated/current_yes_proper_form_tail_features_v1/proper_form_tail_feature_discrimination.csv`
- Feature-group diagnostics: `docs/analysis/2026-06/generated/current_yes_proper_form_tail_features_v1/feature_group_conditioning_diagnostics.csv`
- Residual diagnostics: `docs/analysis/2026-06/generated/current_yes_proper_form_tail_features_v1/residual_correlation_diagnostics.csv`

## 下一步

后续 A/B 已补：见 [2026-06-20-current-yes-residual-calibrator-alti-v1.md](2026-06-20-current-yes-residual-calibrator-alti-v1.md)。IEM 历史接口可拉到 `alti`；`d_alti_3h` 有小的条件信号，但完整候选模型相对 raw market/base 的日期 bootstrap CI 仍跨 0，不加模型。

若继续完善模型，优先方向不是继续堆这批粗尾部特征，而是扩样后继续让“market price + existing METAR core/alti”的正则 residual calibrator 参赛；当前点估有改善，但日期 bootstrap CI 跨 0，不能升级到 live。HGB/GBDT 可以继续作为 challenger，但本轮 27 日期窗口下明显退化。
