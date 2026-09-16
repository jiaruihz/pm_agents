# 2026-06-06 三策略实例 near-binary 勘误后重算

## 结论先行

- `mid_price_core_v2_25_75` 的 live 亏损结论成立：最近目标日 `2026-05-31..2026-06-06` 已结算 PnL `-$68.43`，ROI `-18.70%`；全实例样本也是 `-$47.51`，ROI `-10.00%`。继续保持 stopped / shadow，不恢复 normal live。
- `mid_price_core_v1_25_75` 不是“已经整体亏坏”，但最近确实回撤：最近目标日已结算 PnL `-$46.62`，ROI `-8.46%`；全实例样本仍是 `+$34.41`，ROI `+2.58%`。动作是保留主路径，但继续收紧坏城市/坏 side，不加仓。
- `mid_price_core_v1_side_band` 修正 YES 侧 `0.20-0.45` 映射后仍是三者里最好：最近目标日已结算 PnL `+$25.27`，ROI `+18.76%`；未结算 open cost `$48.92`，mid MTM `-$1.65`。动作是保留，但样本还小，维持小 size。
- 本轮重算后 `missing_bracket=0`，旧报告里的 `missing_bracket=725/734/28` 已过时；DB 与 raw CLOB fill 对齐为 `855/855`，差异 `0/0`。

## 数据快照

| 字段 | 值 |
| --- | --- |
| 数据源 | `runtime/weather.db` 的 `fact_trades` / `fact_signal_candidates`；账户对账脚本 `scripts/analysis/weather_live_account_reconcile.py` |
| 本轮刷新 | 已跑 `bash scripts/ops/sync_weather_remote.sh` + `bash scripts/weather_dashboard/run_stack.sh` |
| DB build | `fact_built_at_utc=2026-06-06T03:54:29.726938+00:00` |
| 最新 order / fill | `max_order_ts_utc=2026-06-06T03:54:04Z`; `max_fill_ts_utc=2026-06-06T03:06:15+00:00` |
| 估值快照 | `max_val_snapshot_ts_utc=2026-06-06T03:30:53Z` |
| run_stack 状态 | DB/fact 表已完成；API 启动因 `8000` 端口占用失败，不影响本次 DB 分析 |

## 强制自检

| check | result |
| --- | --- |
| `MAX(fact_built_at_utc)` | `2026-06-06T03:54:29.726938+00:00` |
| `fact_trades.trade_class` | `live_real=855`, `live_simulated=1032`, `paper=2285`, `snapshot_replay=636` |
| `fact_trades.settlement_status` | `settled=4487`, `null/unsettled=321`, `missing_bracket=0` |
| `fact_signal_candidates` | `rows=21845`, `eligible=6974`, `paper_ordered=2621`, `live_filled=464`, `decision_window_missing=9362` |
| `orders` / `fills` join | `submitted orders=964 with_fill=855`; `error orders=151 with_fill=0` |
| fill ID reconciliation | `db_live_real_distinct_fills=855`, `raw_clob_distinct_fills=855`, `db_not_in_raw=0`, `raw_not_in_db=0` |

## 账户现金流口径

窗口 `2026-05-31..2026-06-06`，date lens = `fill_date_bj`。`cash_cost_usd` 是真实成交花掉的现金，不是亏损；`open_cost_usd` 是未结算成本。

| strategy_instance | fills | cash_cost | settled_cost | realized_pnl | ROI settled | open_cost | mid MTM | bid MTM | val_ts |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `mid_price_core_v1_25_75` | 208 | 675.31 | 505.80 | -35.35 | -6.99% | 169.51 | +1.22 | +2.65 | `2026-06-06T03:30:53Z` |
| `mid_price_core_v2_25_75` | 144 | 401.29 | 356.86 | -68.30 | -19.14% | 44.42 | -1.57 | -1.37 | `2026-06-06T03:30:53Z` |
| `mid_price_core_v1_side_band` | 61 | 183.62 | 134.70 | +25.27 | +18.76% | 48.92 | -1.65 | -1.15 | `2026-06-06T03:30:53Z` |

Raw live order files 同窗口：submitted `$1809.00`，posted `$1794.96`；raw CLOB fill cost `$1160.65`。这些解释钱包现金流和订单占用，不等于 realized PnL。

## 目标日绩效口径

窗口 `target_date=2026-05-31..2026-06-06`，只把 `settlement_status='settled'` 计入 realized PnL；`2026-06-05/06` 大部分还是 open。

| strategy_instance | fills | settled | open | settled_cost | realized_pnl | ROI settled | open_cost | mid MTM |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `mid_price_core_v1_25_75` | 218 | 163 | 55 | 551.31 | -46.62 | -8.46% | 169.51 | +1.22 |
| `mid_price_core_v2_25_75` | 148 | 134 | 14 | 365.96 | -68.43 | -18.70% | 44.42 | -1.57 |
| `mid_price_core_v1_side_band` | 61 | 48 | 13 | 134.70 | +25.27 | +18.76% | 48.92 | -1.65 |

### Target-date 日拆

| target_date | v1_25_75 PnL | v2_25_75 PnL | side_band PnL | note |
| --- | ---: | ---: | ---: | --- |
| 2026-05-31 | -55.47 | -61.20 | - | 两个 25-75 分支共同大亏 |
| 2026-06-01 | +23.63 | +3.77 | +34.46 | side-band 主要盈利日 |
| 2026-06-02 | -7.03 | +0.91 | +7.42 | 小幅分化 |
| 2026-06-03 | +37.58 | +35.54 | +7.68 | 三者均正 |
| 2026-06-04 | -45.33 | -47.44 | -24.29 | 三者共同亏损，近期最大问题日 |
| 2026-06-05 | open | open | open | 未结算，不进 realized |
| 2026-06-06 | open | open | open | 未结算，不进 realized |

## 全实例样本

这里看当前实例自出现以来的全部 `live_real`，仍只用已结算 PnL。

| strategy_instance | fills | settled | open | settled_cost | realized_pnl | ROI settled | win_rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `mid_price_core_v1_25_75` | 454 | 382 | 72 | 1331.82 | +34.41 | +2.58% | 57.6% |
| `mid_price_core_v2_25_75` | 190 | 176 | 14 | 474.84 | -47.51 | -10.00% | 44.3% |
| `mid_price_core_v1_side_band` | 61 | 48 | 13 | 134.70 | +25.27 | +18.76% | 60.4% |

同城 core-9、同目标日 `2026-05-29..2026-06-01` 的 V1/V2 对比不是 V2 的亏损来源：V1 `+$20.01`，V2 `+$24.51`。V2 亏损主要来自 expanded / new T1 城市：

| cohort | v1 PnL | v2 PnL |
| --- | ---: | ---: |
| `new_t1_v2_2026_05_26` | +10.16 | -35.14 |
| `new_t1_v3_2026_05_27` | -35.77 | -34.10 |

## 机会层 sanity check

`fact_signal_candidates` 是机会粒度，不是三实例成交绩效。它不能替代 `fact_trades` 的实例 PnL；这里只用来确认 recent opportunity alpha 是否和成交结论同向。

| scope | side | usable opp | cf_pnl | win_rate | live_filled |
| --- | --- | ---: | ---: | ---: | ---: |
| all dates | BUY_NO | 492 | +16.07 | 67.3% | 271 |
| all dates | BUY_YES | 288 | +77.66 | 26.0% | 83 |
| `2026-05-31..2026-06-06` | BUY_NO | 129 | +0.75 | 65.1% | 106 |
| `2026-05-31..2026-06-06` | BUY_YES | 56 | -22.24 | 28.6% | 36 |

近期机会层显示 BUY_YES 变差，和 live recent drawdown 一致；但 all-date BUY_YES 仍为正，说明不能简单永久禁全局 BUY_YES，应该按 `city×side×instance` gate。

## 代码/文档勘误

- 已修 `scripts/analysis/weather_three_strategy_overlap_analysis.py`：`mid_price_core_v1_side_band` 必须同时包含 YES 侧 `entry_price_window='0.20-0.45'` 和 NO 侧 `0.35-0.65`。旧脚本只认 `0.35-0.65`，会把 side-band 从 `61/48 settled` 低估成 `42/32 settled`。
- 旧报告 `2026-06-03-performance-three-strategy-instances.md` 头部已标记 near-binary 口径过时；还要同时按本报告的 side-band 映射勘误看待。

## 交易动作

1. `mid_price_core_v2_25_75`：维持停止 live。若要继续研究，只能 shadow / paper，并优先拆 new T1 city cohort，不要按 core-9 overlap 的正收益误判它可恢复。
2. `mid_price_core_v1_25_75`：保留主路径，但不要加仓；把近期亏损集中城市/side 纳入 city-side gate，尤其 `NYC BUY_YES`、新增城市 BUY_YES、以及 6/4 共同亏损城市。
3. `mid_price_core_v1_side_band`：保留小 size。它的 realized 表现最好，但样本只有 `48` settled fills，且 `2026-06-04` 亏 `-$24.29`，不能直接放大。
4. 对外口径：不要再引用旧 `missing_bracket=725/734/28` 的 ROI/win rate；本轮正确基线是 `missing_bracket=0`、fill 对账 `855/855`。
