# Helsinki/FMI pre-cross path atlas v1

Status: `descriptive all-observation PIT atlas / zero-notional / no live change`

## 结论

这份输出首次把 x.7 前的 FMI states 与严格更早的 full-ladder book 对齐；它能回答覆盖到的时点是否已经锁价，
但 book capture 是间隔采样，不能把两个 snapshot 之间的变化误报成精确重定价秒数。
`p_path_expanding` 仅为按距离 bucket 的 expanding baseline，不能作为 live selector；Head A/B 的正式模型仍需
source path、remaining heat 和 frozen forward。

## Head A 同刻概率基线

- settled + same-state NO-mid score rows: `347` / `9` dates.
- path-bucket logloss `0.5825` vs market `0.1986`; path-minus-market `0.3838` CI [`0.1931`, `0.5471`].
- prototype pre-x7 positive-edge selections `4`; existing x.7 executable comparator `2`. These are displayed-book counterfactuals, not fills.

## 盘口在 x.5 / x.7 landmark 的状态

| marker | states | ask≥0.90 | ask≥0.95 | ask≥0.99 | 5-share VWAP missing |
|---|---:|---:|---:|---:|---:|
| x5 | 44 | 13 | 12 | 9 | 29 |
| x7 | 21 | 5 | 5 | 3 | 16 |

## Head B 实时完整分布

- 有 normalized market full distribution 的 state：`440`；完整 YES ladder：`204`。
- 当前只有有限 settled target dates，且 source first-seen physical feature frame 尚未冻结；本轮只固化 state/market distribution，未拟合 M2/M3，避免把少量日期训练成策略。

## 漏斗与产物

见 `funnels.csv`（signal/evidence 分开）、`all_observation_states.csv`（固定 PIT parent denominator）、
`threshold_book_lock_atlas.csv`（x.5/x.7 的 book-lock 状态）、两份 policy replay CSV。
