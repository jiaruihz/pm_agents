# External Weather Market References

Status: current-reference
Created: 2026-06-17

这份文档保存外部 weather / prediction-market 资料，并把它们翻译成
`pm_agent` weather 研究可执行的改进方向。它不替代本项目事实口径：
策略绩效仍以 `runtime/weather.db`、`fact_signal_candidates`、`fact_trades`
和 `docs/WEATHER_ANALYSIS_CONTRACT.md` 为准。

## How To Use

外部资料只给研究假设和验证清单，不直接给 live 结论。

默认读取顺序：

1. 先看 "Actionable Lessons"。
2. 再按当前分支选择资料：`pre_predict`、`reheat_risk`、execution、source-basis。
3. 真正改 live/N100 前，仍走 `weather-strategy-deploy` 的 git-first 流程。

## Core External Papers And Notes

### Weather betting autopsy

- Andreas Wenth, "Four Strategies, 562 Trades, Zero Edge: A Forensic Autopsy of Algorithmic Weather Betting", Zenodo DOI: https://doi.org/10.5281/zenodo.19337464
  - Public metadata / abstract mirror: https://api.datacite.org/dois/10.5281/zenodo.19337464
  - Why it matters: live weather strategies can fail even when paper/backtest variants look profitable. The useful takeaway is not "weather has no edge"; it is the checklist: forecast error vs bucket width, paper/live fill gap, exit policy, copy-trade latency, and market crowding.

### Polymarket contracts, CLOB, and execution

- Polymarket API overview: https://docs.polymarket.com/api-reference/introduction
- Polymarket market data overview: https://docs.polymarket.com/market-data/overview
- Polymarket market concepts: https://docs.polymarket.com/concepts/markets-events
- Polymarket order lifecycle: https://docs.polymarket.com/concepts/order-lifecycle
- Polymarket resolution process: https://docs.polymarket.com/concepts/resolution
- Polymarket fee documentation: https://docs.polymarket.com/trading/fees
- Polymarket maker rebates: https://docs.polymarket.com/market-makers/maker-rebates
- Polymarket rate limits: https://docs.polymarket.com/api-reference/rate-limits
- Kalshi weather markets overview: https://help.kalshi.com/en/articles/13823837-weather-markets

Project use:

- Model edge must be evaluated against executable ask/bid, fees, queue, and available size.
- Public activity is not an authoritative per-order fill source; keep authenticated CLOB/order-response recovery as the live-real standard.
- Weather-category fee and rebate settings should be explicit in execution simulations, especially for taker-like fills.

### Weather forecast and observation sources

- NOAA National Blend of Models: https://vlab.noaa.gov/web/mdl/nbm
- NOAA NBM on AWS Open Data: https://registry.opendata.aws/noaa-nbm/
- NOAA NBM dashboard: https://blend.mdl.nws.noaa.gov/nbm-dashboard
- NOAA/NWS web API: https://www.weather.gov/documentation/services-web-api
- NOAA Aviation Weather data API: https://aviationweather.gov/data/api/
- Iowa Environmental Mesonet ASOS/AWOS/METAR download: https://mesonet.agron.iastate.edu/request/download.phtml
- IEM API documentation: https://mesonet.agron.iastate.edu/api/
- Open-Meteo Historical Forecast API: https://open-meteo.com/en/docs/historical-forecast-api
- Open-Meteo Ensemble API: https://open-meteo.com/en/docs/ensemble-api
- Open-Meteo Previous Runs API: https://open-meteo.com/en/docs/previous-runs-api

Project use:

- Separate "forecasting the true max" from "forecasting the market's settlement source".
- Keep forecast issue time, model run, source station, local timezone, and ingestion delay as first-class fields.
- Use historical forecast archives to measure point-in-time skill; do not backfill with later forecasts unless the report labels it as lookahead-contaminated.

### Weather and climate prediction markets

- Roulston and Kaivanto, "Can Expert Prediction Markets Forecast Climate-Related Risks?", BAMS: https://journals.ametsoc.org/view/journals/bams/105/10/BAMS-D-24-0135.1.xml
- Roulston and Kaivanto, "Joint-outcome prediction markets for climate risks", PLOS ONE: https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0309164
- Roulston et al., "Prediction-market innovations can improve climate-risk forecasts", Nature Climate Change: https://www.nature.com/articles/s41558-022-01467-6
- CRUCIAL publication/project index: https://www.crucialab.net/

Project use:

- Temperature markets are ordinal and often joint-outcome problems, not isolated binary coins.
- Range/basket expressions should be evaluated as a coherent distribution over mutually exclusive buckets.
- CRPS / log-loss / reliability / PIT-style coverage are better model diagnostics than only per-bracket ROI slices.

### Prediction-market microstructure and semantics

- Gebele and Matthes, "Semantic Non-Fungibility and Violations of the Law of One Price in Prediction Markets": https://arxiv.org/abs/2601.01706
- Dubach, "The Anatomy of a Decentralized Prediction Market: Microstructure Evidence from the Polymarket Order Book": https://arxiv.org/abs/2604.24366
- Tsang and Yang, "The Anatomy of Polymarket: Evidence from the 2024 Presidential Election": https://arxiv.org/abs/2603.03136
- Cheng, Liu, and Long, "PolyBench: Benchmarking LLM Forecasting and Trading Capabilities on Live Prediction Market Data": https://arxiv.org/abs/2604.14199
- Wolfers and Zitzewitz, "Prediction Markets": https://www.aeaweb.org/articles?id=10.1257/0895330041371321
- Hanson, "Logarithmic Market Scoring Rules": https://mason.gmu.edu/~rhanson/mktscore.pdf
- Gneiting and Raftery, "Strictly Proper Scoring Rules, Prediction, and Estimation": https://sites.stat.washington.edu/raftery/Research/PDF/Gneiting2007jasa.pdf

Project use:

- Resolution wording, station identity, time window, and oracle procedure are part of the asset definition.
- Cross-market or cross-source "same event" comparisons need semantic alignment before claiming arbitrage.
- Point-in-time orderbook snapshots are mandatory for any publishable executable-edge claim.

### Weather derivatives and temperature risk

- Zeng, "Weather Derivatives and Weather Insurance": https://journals.ametsoc.org/view/journals/bams/81/9/1520-0477_2000_081_2075_wdawic_2_3_co_2.xml
- Cao and Wei, "Weather Derivatives Valuation and Market Price of Weather Risk": https://onlinelibrary.wiley.com/doi/abs/10.1002/fut.20122
- Alaton, Djehiche, and Stillberger, "On modelling and pricing weather derivatives": https://www.tandfonline.com/doi/abs/10.1080/13504860210132897
- Jewson, "Introduction to Weather Derivative Pricing": https://papers.ssrn.com/sol3/papers.cfm?abstract_id=557831

Project use:

- Treat city-day temperature bets as weather-risk instruments with basis risk, not only as generic prediction-market tickets.
- Portfolio sizing should account for spatial and date-level correlation; many city bets can be one macro-weather factor in disguise.

## Actionable Lessons For `pm_agent`

### 1. Add a forecast precision gate before expression tuning

Target metric:

```text
forecast_error_vs_bucket_width =
  MAE(forecast_max_source_adjusted, official_settlement_max) / bracket_width
```

Interpretation:

- `< 0.5`: forecast source may support directional or range edge.
- `0.5-1.0`: prefer range/basket or source-basis expressions over single-bracket bets.
- `>= 1.0`: single-bracket model edge should be treated as mostly noise unless orderbook mispricing is independently proven.

Best local home: `pre_predict.md`, `model_vs_market.md`, and source-aware Range RV reports.

### 2. Promote settlement-source basis to a first-class feature

External lesson: a weather contract settles against a source/station/procedure, not against "weather" in the abstract.

Local action:

- Keep `source_system + city + target_date + bracket` settlement grain.
- Maintain station registry and source aliases in the shared fact layer.
- For each city, track default-WU vs official/NWS/IEM/HKO station basis and whether the basis is stable enough to trade.

Best local home: `data_integrity.md`, `reheat_risk.md`, official observation feed design, and station-basis reports.

### 3. Make point-in-time replay the default standard

External lesson: live forecasting benchmarks and CLOB studies require timestamp-locked market state.

Local action:

- Every replay row should know `decision_snapshot_ts_utc`, forecast run timestamp, orderbook snapshot age, and settlement label timestamp.
- Reports should explicitly label whether they are opportunity research, decision proxy, time-aligned orderbook replay, paper/shadow telemetry, or true live fills.
- Avoid promoting any strategy based only on "best available later data".

Best local home: `WEATHER_ANALYSIS_CONTRACT.md`, `execution_quality.md`, and `entry_timing.md`.

### 4. Put execution microstructure ahead of more slicing

External lesson: paper/backtest edge can disappear through spread, fees, queue, thin top-of-book size, and adverse selection.

Local action:

- For each candidate family, report executable ask/bid edge, spread, available size at top-of-book, fill rate, and fill-vs-unfill PnL.
- Stress every apparent positive ROI with taker-like and maker-only assumptions.
- Treat low-price YES and high-ask NO as separate execution regimes; convexity and fee impact differ.

Best local home: `execution_quality.md` and `_ANALYSIS_COVERAGE_MAP.md` ring 5/6.

### 5. Replace naive stop-profit rules with payoff-state analysis

External lesson: binary contracts can make early profit-taking look safe while destroying expected value.

Local action:

- For each expression, compare hold-to-settlement, fixed exit, and sibling-expression alternatives.
- Decompose payoff states: current-hit, d1-hit, d2-hit, skip-over, miss.
- Do not call a mark-to-market gain "edge" unless exit liquidity and fill probability are observed.

Best local home: `reheat_risk.md`, current YES / higher NO sibling selectors, and low-price YES reheat reversal.

### 6. Downgrade copy-trade and wallet signals unless latency is measured

External lesson: smart-wallet edge may be latency, privileged source access, or market-making inventory rather than forecast skill.

Local action:

- If wallet signals are used, require signal arrival lag, same-price capacity, and post-signal slippage reports.
- Do not mix wallet-following evidence with forecast-model evidence.

Best local home: future market-structure or execution-quality work, not current live policy.

### 7. Evaluate distributions, not only independent binary legs

External lesson: temperature buckets are mutually exclusive and ordered.

Local action:

- Add CRPS/log-loss/reliability/PIT-style diagnostics for full city-day distributions.
- For basket/range strategies, score the whole bracket distribution and then choose expression; avoid optimizing one leg at a time.
- Track monotonicity around adjacent buckets; a model that predicts 25C should not assign incoherent mass to far buckets.

Best local home: `model_vs_market.md`, `pre_predict.md`, and `_ANALYSIS_COVERAGE_MAP.md` ring 3/4.

### 8. Add portfolio correlation before scaling

External lesson: weather outcomes are spatially and temporally correlated.

Local action:

- Compute city/date outcome and PnL correlation by region.
- Convert raw trade count into effective independent sample count for confidence intervals.
- Cap daily exposure by correlated weather factor, not only by per-order notional.

Best local home: `city_selection.md`, `sizing_entry_band.md`, and future portfolio-risk work.

## Immediate Backlog Candidates

These are local research tasks suggested by the external material. They are analysis-only unless a later report explicitly moves them to shadow/paper/tiny-live.

1. `forecast_error_vs_bucket_width_v0`: city x source x lead MAE, bias, and bracket-width ratio.
2. `source_basis_registry_v0`: current city -> settlement source/station/procedure table with observed basis stability.
3. `execution_microstructure_gate_v0`: spread, fee, top-of-book capacity, fill-vs-unfill, and adverse-selection report for candidate families.
4. `hold_vs_exit_binary_payoff_v0`: compare hold-to-settlement, fixed profit-taking, and sibling-expression exits.
5. `distribution_scorecard_v0`: CRPS/log-loss/reliability for full city-day temperature distributions.
6. `weather_portfolio_correlation_v0`: effective independent bets, region/date factor exposure, and daily exposure caps.

## Non-Conclusions

- The Wenth autopsy does not prove our current strategy has no edge; it proves we need stricter live-replay and execution gates.
- Climate prediction-market papers do not imply retail Polymarket weather markets are efficient; they imply market design and outcome semantics matter.
- Forecast model accuracy alone is not a strategy. The tradable object is forecast skill minus settlement-basis error minus execution cost minus selection bias.
