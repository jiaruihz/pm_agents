# Helsinki FMI 三年短期跨档模型 v1

## 数据与冻结口径

- FMI Helsinki-Vantaa 站号 `100968`，官方 weather+radiation WFS。
- UTC 查询截至 `2026-07-30`；Helsinki 本地实际覆盖 `2023-07-31..2026-07-30`：`1096` 天、`157,781` 个 10 分钟 feature rows（窗口首日 `2023-07-30` 无 FMI rows）。
- train：`2023-07-31..2025-12-31`；frozen audit：`2026-01-01..2026-07-30`，`211` 个 target dates。
- 历史 WFS 只有 observation clock，不含 first-seen clock；这里只验证天气路径概率，不验证盘口 residual、执行或 ROI。

## Frozen 2026 结果

相对只看温度路径的 `temp_path_logit`，完整 FMI 多变量模型的 date-block bootstrap Brier delta：

| Horizon | Multivariate logit | Multivariate HGB |
|---|---:|---:|
| 30m | `-0.00202` CI `[-0.00269,-0.00134]` | `-0.00232` CI `[-0.00337,-0.00133]` |
| 60m | `-0.00262` CI `[-0.00374,-0.00147]` | `-0.00270` CI `[-0.00443,-0.00105]` |
| 120m | `-0.00275` CI `[-0.00471,-0.00063]` | `-0.00702` CI `[-0.01035,-0.00364]` |

三档均在 frozen 2026 上改善 proper score，说明天气/辐射特征对短期跨档概率有稳定增量；仍不授权 live，后续必须接 first-seen event、同分母 PIT book 和 fee-adjusted expression。

## 产物

目录：`generated/helsinki_fmi_multivariate_short_term_3y_v1/`

- `fmi_10m_feature_rows.csv.gz`
- `scores.csv`
- `brier_deltas_vs_temp_path.csv`
- `logit_coefficients.csv`
- `download_audit.csv`
- `summary.json`
