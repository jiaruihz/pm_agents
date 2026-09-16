# D1 欧洲 distance=2 单腿 NO：历史盲区间 forward v7

## 结论

欧洲分支有后续，但新数据把原结论拆成了两部分：

- **固定五城 `europe_cloud_break` 没有复现。** discovery ROI 从
  8.32% 降到
  0.42%，不能再把这五城当作已找到的城市池。
- **泛欧洲 `Europe-all` 仍保留正点估计，但证据不足。** 新窗口 ROI
  2.90%，相对 market-only 高
  2.66%，但置信区间都跨 0，且概率评分没有打败市场。
- 因此动作是：**保留 Europe-all 为 frozen research/shadow 方向；五城 family 降级为
  dormant；不改 live，不根据本窗口新增 London/Paris/Milan allowlist。**

这里研究的不是“同时买最低档和最高档 NO”。策略表达是：在距离两端各两档的两个
NO 中，用五模型 bias-corrected 分布只选一腿。两端极值 NO basket 是另一条分支，
不能把这里的欧洲结果移植过去。

## 数据与冻结口径

- discovery：2026-06-17 至 2026-07-07。
- historical blind holdout：2026-07-16 至
  2026-07-23；原始文件先前存在，但旧镜像权限导致未进入 discovery，
  因而这是历史盲区间，不冒充实时 prospective forward。
- 固定 `Europe-all`：Amsterdam、Ankara、Helsinki、Istanbul、London、Madrid、
  Milan、Munich、Paris、Warsaw。
- 固定五城：Amsterdam、Helsinki、Madrid、Munich、Warsaw。
- 每个 city-day 只取 D-1 首个 PIT ladder；所有策略使用完全相同的
  `snapshot_key` 分母；ROI 为 fee-adjusted；CI 按 `target_date` block bootstrap。
- 预注册要求为至少 15 个新 `target_date`；本次只有
  8 天，还差
  7 天。

## 同分母结果

| 固定切片 | discovery baskets / dates | discovery ROI | blind baskets / dates | blind ROI | blind 95% CI |
|---|---:|---:|---:|---:|---:|
| Europe-all | 295 / 19 | 6.35% | 84 / 8 | 2.90% | [-5.44%, 10.52%] |
| 固定五城 | 139 / 19 | 8.32% | 43 / 7 | 0.42% | [-8.84%, 8.39%] |

### 相对基线

| 固定切片 | 基线 | candidate ROI | baseline ROI | Δ ROI | Δ 95% CI |
|---|---|---:|---:|---:|---:|
| Europe-all | market-only 选腿 | 2.90% | 0.23% | 2.66% | [-2.82%, 7.23%] |
| Europe-all | 两腿各半机械组合 | 2.90% | 2.28% | 0.61% | [-4.86%, 5.67%] |
| 固定五城 | market-only 选腿 | 0.42% | -3.30% | 3.71% | [-2.50%, 9.57%] |
| 固定五城 | 两腿各半机械组合 | 0.42% | 0.90% | -0.48% | [-8.39%, 7.34%] |

Europe-all 的正 ROI 主要不是来自可确认的模型选腿优势：相对机械两腿各半只高
0.61%。这更像“欧洲 distance=2 NO 本身仍可能有 carry”，
而不是“五模型已经稳定知道该买哪一端”。

## 概率质量

负 delta 才代表模型优于市场。

| 固定切片 | Brier Δ vs market | 95% CI | Logloss Δ vs market | 95% CI |
|---|---:|---:|---:|---:|
| Europe-all | 0.00827 | [-0.00523, 0.02080] | 0.03536 | [-0.00813, 0.07588] |
| 固定五城 | 0.01535 | [-0.00313, 0.03468] | 0.04496 | [-0.02649, 0.11779] |

两个切片的点估计都比市场差；这就是为什么不能拿正 ROI 直接升级 live。

## 城市变化

固定五城 blind ROI：

| city | baskets | target_dates | fee-adjusted ROI |
|---|---:|---:|---:|
| Helsinki | 8 | 4 | 6.83% |
| Munich | 9 | 5 | 5.75% |
| Warsaw | 9 | 5 | 4.11% |
| Madrid | 9 | 5 | -5.20% |
| Amsterdam | 8 | 4 | -11.90% |

其他欧洲城市 blind ROI：

| city | baskets | target_dates | fee-adjusted ROI |
|---|---:|---:|---:|
| London | 10 | 6 | 16.14% |
| Paris | 7 | 4 | 12.51% |
| Milan | 8 | 5 | 5.06% |
| Istanbul | 7 | 4 | 0.73% |
| Ankara | 9 | 5 | -7.54% |

London/Paris/Milan 的点估计较好，但这是看完 forward 后才观察到的，不能反过来组成
新的“赢家城市池”。它只说明**宽泛 Europe 假设比固定五城故事更值得继续收集**。

## Gate 与后续

- significance gate：FAIL（ROI 与相对基线 CI 均跨 0）。
- baseline gate：FAIL（相对机械组合仅小幅正，概率 proper score 更差）。
- frozen-forward gate：INCOMPLETE（8/15 个新日期）。
- action：Europe-all 保留 frozen collector/shadow；固定五城不再作为主假设；满 15 个
  新日期后按同一代码、同一城市集合、同一分母重跑，期间不换城市、不调阈值。

## Zero-notional shadow contract

用户于 2026-07-28 授权启动独立 zero-notional shadow：

- instance：`europe_d1_distance2_dual_no_shadow_v1`
- universe：冻结的 10 个 `Europe-all` 城市。
- signal：当地 D-1 12:00–24:00 内首次看到的完整 ladder；锁定
  `distance_from_nearest_endpoint=2` 的低侧与高侧 NO，各 50%。
- coverage：首个 ladder 即锁定；任一腿不可执行仍保留
  `paired_book_unexecutable`，不等待更晚、更好看的盘口替换。
- weather：五模型 bias-corrected exact-mass、spread 等只写 telemetry，
  `weather_features_used_for_eligibility=false`。
- execution：`orders_submitted=0`、`actual_notional_usd=0`；runner 不包含
  CLOB 下单入口。
- forward：collector 首轮因当日窗口已开始，标记
  `collector_bootstrap_partial_window=true`，不计入完整 frozen-forward 日期；
  后续城市集、距离和天气 eligibility 均冻结。
