# M3 Orderbook Best-Ask Backtest v0

Status: superseded
Updated: 2026-06-10
Source of truth: no
Superseded by / Used by: 2026-06-11-m3-settlement-alignment-v1.md; WEATHER_DOCS_INDEX.md; analysis/observed_max_m3.md

## 2026-06-11 勘误

本报告的 observed-payout ROI 不得再引用为收益结论。

后续 settlement alignment 发现，WU/IEM observed final max 与 `pm_history` 官方
winning bracket 不稳定一致。用官方 winner label 重算同一方向后，M3 v1 best-ask
收益转负。当前结论改读：

```text
docs/analysis/2026-06/2026-06-11-m3-settlement-alignment-v1.md
```

## 结论先行

这次补全后，M3 已经从 paper price proxy 接到历史 orderbook best ask。

但这里的收益是 **historical orderbook best-ask backtest**，不是 live PnL、不是
CLOB fill ROI，也没有模拟 queue position / latency / partial fill。

当前最强的表达是：

```text
below_running_max_buy_no
= 当地 20/21 点 observed running max 已经高于某个 Celsius bracket 时，
  买该 lower bracket 的 NO token，入场成本用 orderbook raw.asks best ask。
```

修正 Celsius 档位口径后，结果仍明显为正：

| strategy | hour | trades | city_days | cities | cost | pnl | roi | win_rate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| below_running_max_buy_no | 20 | 48 | 45 | 21 | 3.89 | 44.11 | 1133.30% | 100.00% |
| below_running_max_buy_no | 21 | 26 | 25 | 17 | 1.41 | 24.59 | 1747.90% | 100.00% |
| below_running_max_buy_no | ALL | 74 | 49 | 23 | 5.30 | 68.70 | 1296.49% | 100.00% |
| observed_bucket_buy_yes | 20 | 6 | 6 | 5 | 3.04 | 2.96 | 97.56% | 100.00% |
| observed_bucket_buy_yes | 21 | 1 | 1 | 1 | 0.01 | 0.98 | 6566.67% | 100.00% |
| observed_bucket_buy_yes | ALL | 7 | 6 | 5 | 3.05 | 3.95 | 129.36% | 100.00% |

Action:

```text
M3 可以继续推进到 shadow/paper 设计，但还不能直接上 live。
优先方向是 below_running_max_buy_no，而不是 observed_bucket_buy_yes。
```

## WU/IEM Cache 补全

用户指出本机 WU observed cache 只到 2026-05-12。排查结论：

```text
不是本机 sync 漏了；N100 weather-predict 生产 cache 本身也停在旧窗口。
```

已在 N100 执行全池补全：

```bash
cd /home/jiarui/projects/weather-predict
python3 scripts/ops/fill_t2_weather_cache.py \
  --pool all \
  --start 2024-04-30 \
  --end 2026-06-10 \
  --force \
  --sleep 0.2 \
  --summary output/research/m3_weather_cache_fill_20260610_summary.json
```

随后本机执行：

```bash
cd /home/rui/projects/pm_agent
scripts/ops/sync_weather_remote.sh
```

本机校验：

| cache | files | rows | min date | max date |
|---|---:|---:|---|---|
| `cache/wu_obs/*.csv` | 52 | 1,470,936 | 2024-04-29 | 2026-06-10 |
| `cache/iem/iem_v2_*_2024-04-30_2026-06-10.csv` | 52 | 1,471,124 | 2024-04-30 | 2026-06-09 |

说明：

- WU proxy 用 `date_local`，可覆盖到本地日期 2026-06-10。
- IEM raw 用 UTC `valid`，max 为 2026-06-09 是时间口径差异，不表示 WU 本地日缺 6/10。
- 同步后的 summary 位于
  `runtime/weather_edge_v1/market_data/research/m3_weather_cache_fill_20260610_summary.json`。

## 物理层 v1

补全后重跑 M3 observed running max residual：

```bash
.venv/bin/python scripts/analysis/observed_max/research_m3_observed_max_residual.py \
  --output-dir docs/analysis/2026-06/generated/m3_observed_max_v1

.venv/bin/python scripts/analysis/observed_max/research_m3_observed_max_residual.py \
  --core-only \
  --output-dir docs/analysis/2026-06/generated/m3_observed_max_core9_v1
```

产物：

```text
docs/analysis/2026-06/generated/m3_observed_max_v1/
docs/analysis/2026-06/generated/m3_observed_max_core9_v1/
```

补全后物理结果仍支持 20/21 点窗口：

| universe | cities | detail rows | hour | p95_residual_c |
|---|---:|---:|---:|---:|
| all | 49 | 150,203 | 18 | 0.0 |
| all | 49 | 150,203 | 19 | 0.0 |
| all | 49 | 150,203 | 20 | 0.0 |
| all | 49 | 150,203 | 21 | 0.0 |
| core9 | 9 | 27,764 | 18 | 0.0 |
| core9 | 9 | 27,764 | 19 | 0.0 |
| core9 | 9 | 27,764 | 20 | 0.0 |
| core9 | 9 | 27,764 | 21 | 0.0 |

## Orderbook Best-Ask Backtest

脚本：

```text
scripts/analysis/observed_max/research_m3_orderbook_best_ask_backtest.py
```

命令：

```bash
.venv/bin/python scripts/analysis/observed_max/research_m3_orderbook_best_ask_backtest.py \
  --output-dir docs/analysis/2026-06/generated/m3_orderbook_best_ask_v0
```

产物：

```text
docs/analysis/2026-06/generated/m3_orderbook_best_ask_v0/
```

数据口径：

| item | value |
|---|---:|
| orderbook files scanned | 1,098 |
| orderbook records scanned | 1,379,825 |
| rows kept after city-local target-day 20/21 + best ask filter | 645 |
| final quote rows after last quote per local hour | 417 |
| joined rows with observed v1 | 417 |
| trade rows | 81 |
| quote target dates | 2026-05-19 to 2026-06-09 |
| trade target dates | 2026-05-20 to 2026-06-09 |
| trade cities | 23 |
| trade city-days | 52 |

重要修正：

```text
Polymarket weather bracket label 是 Celsius 档位。
脚本使用 observed 的 running_max_c / final_max_c 对齐 bracket。
不要使用旧 paper proxy 脚本里的 Fahrenheit bracket 比较逻辑解释本次结果。
```

价格口径：

| side | cost |
|---|---|
| BUY_YES | YES token `raw.asks` best ask |
| BUY_NO | NO token `raw.asks` best ask |

收益口径：

```text
payout = 1 if observed final max falls on the bought side else 0
pnl = payout - entry_cost
roi = pnl / entry_cost
```

## Interpretation

`below_running_max_buy_no` 的逻辑强，是因为如果本地观测最高温已经超过某个 lower Celsius bracket，该 bracket 后续不应再成为最终最高温档位。市场上仍出现低价 NO ask 时，历史 best-ask 回测就接近确定性收敛。

这更像一个 **late-day stale lower-bracket NO** 的执行机会，而不是传统 forecast alpha。

但是当前还缺三件事，不能把它直接当 live 收益：

1. 这里用 WU/IEM observed final max 判定 payout，尚未逐 city-date 对齐 Polymarket `pm_history` 官方 settlement bracket。
2. best ask 不是 fill；还没有 queue position、latency、partial fill、size cap 和下单后盘口消失检查。
3. 还没有 matched baseline，例如同城同日同小时同 price 的普通 low-ask NO，或者 lower-bracket NO 是否只是机械套利/数据延迟。

## Next Plan

1. 做 settlement alignment gate：
   `observed final max -> Celsius bracket -> pm_history winning bracket`，按 city/date 统计 mismatch。
2. 对 `below_running_max_buy_no` 加 capacity cap：
   用 best ask size、max notional、每 city-day 去重规则重算 capped pnl。
3. 做 matched baseline：
   同 city/date/hour、同 side、同 price bucket 的 lower-bracket NO 与非 lower-bracket NO 对比。
4. 生成 shadow journal：
   每 20/21 点本地记录 would-trade，不下真钱，先验证 N100 实时观测和 orderbook 捕获是否能稳定同窗。
5. 只有 settlement alignment + capacity + shadow journal 都过，再进入 paper/live 部署评审。
