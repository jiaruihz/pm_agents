# 绩效分析：current T1/T2 city contribution replay

> 时间窗：全部已结算镜像样本（北京时间 target_date）
> 策略：snapshot replay / live-like wide capture
> 城市池：current_t1 vs current_t2
> 数据源：镜像 JSON/pm_history（DB/API 无法回答未执行候选 replay）

## 数据快照

| 项目 | 值 |
|---|---|
| 数据源路径 | runtime/weather_edge_v1/market_data/paper_snapshots + cache/pm_history |
| 数据快照时间 | 2026-05-26T23:45:42 |
| replay trades 行数 | 1042 |
| raw candidate rows | 8320 |
| unsettled 占比 | 222 / 1264 |
| missing_bracket 数 | N/A（pm_history 缺失/未结算计入 unsettled） |
| DB/API 降级原因 | DB unavailable: weather.db missing_or_empty; API 无 counterfactual candidate replay endpoint |

## 分析口径

- 当前 T1/T2 归属取最新 snapshot 的 `trading_t1_cities` / `research_t2_cities`。
- 信号规则：`22 <= hours_to_settle <= 28`，`abs(edge) >= 0.10`，`0.25 <= entry_price < 0.75`。
- 去重：每个 `market_id/condition_id + side` 只保留第一次满足条件的候选。
- PnL：10 shares replay；未结算不计入已结算 PnL。

## 总览

| group | trades | wins | win_rate | cost_usd | pnl_usd | roi |
|---|---|---|---|---|---|---|
| all | 1042 | 675 | 64.8% | $5,884.48 | $865.52 | 14.7% |
| current_t1 | 545 | 350 | 64.2% | $2,965.99 | $534.01 | 18.0% |
| current_t2 | 497 | 325 | 65.4% | $2,918.49 | $331.51 | 11.4% |

## 当前 T2 里表现较好的城市（n>=5, pnl>0, roi>=10%）

| city | trades | wins | win_rate | cost_usd | pnl_usd | roi |
|---|---|---|---|---|---|---|
| Amsterdam | 21 | 17 | 81.0% | $111.19 | $58.80 | 52.9% |
| BuenosAires | 18 | 15 | 83.3% | $97.85 | $52.15 | 53.3% |
| Helsinki | 29 | 21 | 72.4% | $179.16 | $30.85 | 17.2% |
| Chengdu | 11 | 10 | 90.9% | $73.85 | $26.15 | 35.4% |
| Milan | 25 | 17 | 68.0% | $144.50 | $25.50 | 17.6% |
| Atlanta | 23 | 15 | 65.2% | $125.50 | $24.50 | 19.5% |
| Manila | 15 | 11 | 73.3% | $87.35 | $22.65 | 25.9% |
| Taipei | 15 | 12 | 80.0% | $97.45 | $22.55 | 23.1% |
| SanFrancisco | 11 | 9 | 81.8% | $67.56 | $22.44 | 33.2% |
| Singapore | 19 | 13 | 68.4% | $112.10 | $17.90 | 16.0% |
| PanamaCity | 9 | 7 | 77.8% | $54.40 | $15.60 | 28.7% |
| Munich | 15 | 10 | 66.7% | $84.95 | $15.05 | 17.7% |
| SaoPaulo | 14 | 11 | 78.6% | $95.50 | $14.50 | 15.2% |
| CapeTown | 19 | 12 | 63.2% | $108.40 | $11.60 | 10.7% |
| Chongqing | 9 | 7 | 77.8% | $62.80 | $7.20 | 11.5% |
| HongKong | 6 | 4 | 66.7% | $36.35 | $3.65 | 10.0% |

## 当前 T1 里表现较弱的城市（n>=5 且 pnl<0 或 roi<5%）

| city | trades | wins | win_rate | cost_usd | pnl_usd | roi |
|---|---|---|---|---|---|---|
| London | 37 | 20 | 54.1% | $202.32 | $-2.33 | -1.1% |
| Miami | 45 | 22 | 48.9% | $218.20 | $1.80 | 0.8% |
| Chicago | 26 | 15 | 57.7% | $145.13 | $4.87 | 3.4% |
| LA | 35 | 19 | 54.3% | $183.30 | $6.70 | 3.7% |

## 切片：by_city（全部）

| city | pool | trades | wins | win_rate | cost_usd | pnl_usd | roi |
|---|---|---|---|---|---|---|---|
| Amsterdam | current_t2 | 21 | 17 | 81.0% | $111.19 | $58.80 | 52.9% |
| Ankara | current_t1 | 24 | 16 | 66.7% | $135.02 | $24.98 | 18.5% |
| Atlanta | current_t2 | 23 | 15 | 65.2% | $125.50 | $24.50 | 19.5% |
| Austin | current_t2 | 26 | 14 | 53.8% | $143.34 | $-3.34 | -2.3% |
| Beijing | current_t2 | 28 | 15 | 53.6% | $176.90 | $-26.90 | -15.2% |
| BuenosAires | current_t2 | 18 | 15 | 83.3% | $97.85 | $52.15 | 53.3% |
| Busan | current_t2 | 15 | 10 | 66.7% | $92.89 | $7.11 | 7.7% |
| CapeTown | current_t2 | 19 | 12 | 63.2% | $108.40 | $11.60 | 10.7% |
| Chengdu | current_t2 | 11 | 10 | 90.9% | $73.85 | $26.15 | 35.4% |
| Chicago | current_t1 | 26 | 15 | 57.7% | $145.13 | $4.87 | 3.4% |
| Chongqing | current_t2 | 9 | 7 | 77.8% | $62.80 | $7.20 | 11.5% |
| Dallas | current_t2 | 9 | 6 | 66.7% | $57.80 | $2.20 | 3.8% |
| Denver | current_t2 | 4 | 3 | 75.0% | $18.60 | $11.40 | 61.3% |
| Guangzhou | current_t1 | 15 | 12 | 80.0% | $102.80 | $17.20 | 16.7% |
| Helsinki | current_t2 | 29 | 21 | 72.4% | $179.16 | $30.85 | 17.2% |
| HongKong | current_t2 | 6 | 4 | 66.7% | $36.35 | $3.65 | 10.0% |
| Houston | current_t2 | 23 | 12 | 52.2% | $118.70 | $1.30 | 1.1% |
| Istanbul | current_t1 | 31 | 19 | 61.3% | $143.70 | $46.30 | 32.2% |
| Jakarta | current_t2 | 9 | 5 | 55.6% | $53.60 | $-3.60 | -6.7% |
| Jeddah | current_t1 | 24 | 19 | 79.2% | $135.75 | $54.25 | 40.0% |
| Karachi | current_t1 | 32 | 20 | 62.5% | $181.90 | $18.10 | 10.0% |
| KualaLumpur | current_t2 | 16 | 8 | 50.0% | $79.10 | $0.90 | 1.1% |
| LA | current_t1 | 35 | 19 | 54.3% | $183.30 | $6.70 | 3.7% |
| Lagos | current_t2 | 4 | 2 | 50.0% | $28.30 | $-8.30 | -29.3% |
| London | current_t1 | 37 | 20 | 54.1% | $202.32 | $-2.33 | -1.1% |
| Lucknow | current_t1 | 21 | 13 | 61.9% | $109.05 | $20.95 | 19.2% |
| Madrid | current_t1 | 36 | 22 | 61.1% | $181.38 | $38.62 | 21.3% |
| Manila | current_t2 | 15 | 11 | 73.3% | $87.35 | $22.65 | 25.9% |
| MexicoCity | current_t2 | 3 | 3 | 100.0% | $14.60 | $15.40 | 105.5% |
| Miami | current_t1 | 45 | 22 | 48.9% | $218.20 | $1.80 | 0.8% |
| Milan | current_t2 | 25 | 17 | 68.0% | $144.50 | $25.50 | 17.6% |
| Moscow | current_t1 | 20 | 15 | 75.0% | $109.12 | $40.88 | 37.5% |
| Munich | current_t2 | 15 | 10 | 66.7% | $84.95 | $15.05 | 17.7% |
| NYC | current_t1 | 45 | 28 | 62.2% | $244.35 | $35.65 | 14.6% |
| PanamaCity | current_t2 | 9 | 7 | 77.8% | $54.40 | $15.60 | 28.7% |
| Paris | current_t1 | 45 | 29 | 64.4% | $255.05 | $34.95 | 13.7% |
| SanFrancisco | current_t2 | 11 | 9 | 81.8% | $67.56 | $22.44 | 33.2% |
| SaoPaulo | current_t2 | 14 | 11 | 78.6% | $95.50 | $14.50 | 15.2% |
| Seattle | current_t1 | 16 | 13 | 81.2% | $98.83 | $31.16 | 31.5% |
| Seoul | current_t2 | 32 | 15 | 46.9% | $184.41 | $-34.41 | -18.7% |
| Shanghai | current_t1 | 19 | 14 | 73.7% | $123.76 | $16.24 | 13.1% |
| Shenzhen | current_t2 | 21 | 12 | 57.1% | $120.45 | $-0.45 | -0.4% |
| Singapore | current_t2 | 19 | 13 | 68.4% | $112.10 | $17.90 | 16.0% |
| Taipei | current_t2 | 15 | 12 | 80.0% | $97.45 | $22.55 | 23.1% |
| TelAviv | current_t2 | 22 | 14 | 63.6% | $129.79 | $10.21 | 7.9% |
| Tokyo | current_t1 | 23 | 17 | 73.9% | $135.30 | $34.70 | 25.6% |
| Warsaw | current_t1 | 51 | 37 | 72.5% | $261.00 | $109.00 | 41.8% |
| Wellington | current_t2 | 15 | 7 | 46.7% | $86.85 | $-16.85 | -19.4% |
| Wuhan | current_t2 | 11 | 8 | 72.7% | $74.25 | $5.75 | 7.7% |

## 切片：by_side

| side | trades | wins | win_rate | cost_usd | pnl_usd | roi |
|---|---|---|---|---|---|---|
| BUY_NO | 827 | 595 | 71.9% | $5,182.06 | $767.94 | 14.8% |
| BUY_YES | 215 | 80 | 37.2% | $702.41 | $97.59 | 13.9% |

## 切片：by_model

| model | trades | wins | win_rate | cost_usd | pnl_usd | roi |
|---|---|---|---|---|---|---|
| ecmwf | 602 | 398 | 66.1% | $3,403.66 | $576.34 | 16.9% |
| gfs | 440 | 277 | 63.0% | $2,480.82 | $289.19 | 11.7% |

## 数据完整性自检

- [x] DB/API 降级原因已注明；本报告为 counterfactual snapshot replay。
- [x] current_t1/current_t2 取 latest snapshot，不混用历史 city_pool 字段。
- [x] unsettled=222，未计入已结算总览。
- [x] 切片限定为 by_city / by_side / by_model / by_pool 派生视图。

## 观察与建议

当前 T2 整体可以继续作为晋升候选池，但应按城市与 side 分层推进；当前 T1 中低 ROI 或负 PnL 城市建议先降 size 或 shadow，再等更多 live fill 样本确认。
