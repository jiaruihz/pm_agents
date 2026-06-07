# 2026-06-07 CLOB partial-fill recovery and performance recalc

## 结论先行

- 历史 CLOB fill 底表已按修正口径重建完成；本报告早期重建快照是 `fact_trades live_real=1246`、raw `clob_fills.jsonl=1246`、fill_id 差异 0。
- 2026-06-07T05:48Z 当前本机快照已继续刷新到 `fact_trades live_real=1302`、raw `clob_fills.jsonl=1302`、raw cost `$3457.59`，coverage gate 仍通过。行数会随新增真实成交变化，最终以 gate 为准，不以某个历史行数为准。
- 新增 fill coverage gate 已通过：`missing_order_rows=0`、`over_order_keys=0`、`fact_trades` 成本与 `fills` 成本差 0。当前 DB/fill 内部口径可以作为策略 PnL 基础。
- partial-fill bug 和旧 public fallback 过量分配都确认存在：旧 DB 是 `1316` rows / `$3499.01`，其中有 `2` 行 order_id mismatch、`53` 个 order 超 cap；新 DB 是 `1246` rows / `$3348.78`。
- 外部账户 coverage 仍未完全闭环：`2026-05-31..2026-06-06 UTC` 本地 rebuilt CLOB BUY 成本 `$1532.75`，Polymarket public activity BUY `$1818.70`，净差 `$285.95`。这属于 Poly public activity 与本地订单级 fills 的外部对账差额，不再混入策略 PnL。
- 逐笔 match 仍不是单纯“本地少 `$285.95`”：当前有 unmatched Poly BUY `$378.36`，也有 unmatched local `$92.41`，说明仍有一部分 trade-grain 匹配、时间窗或账户 activity 归属问题。
- `2026-06-06` 上海 26°C 市场的约 `$15` 损失可以解释：当时不是两个实例，而是三个实例都成交了 `BUY_NO 26`，合计成本 `$14.89`。`mid_price_core_v2_25_75` 现在已停，但 2026-06-05 UTC 仍留下过上海 6/6 的历史成交。
- 重建后最近 7 天按 `target_date` 做策略归因，活跃三实例已结算 PnL 合计 `-$232.74`，settled cost `$1454.68`，open cost `$186.55`；live 总体含 legacy 为 `-$225.14`。

## 数据快照

### Current autogate snapshot

| item | value |
|---|---:|
| `MAX(fact_built_at_utc)` | `2026-06-07T05:48:57.787507+00:00` |
| `fact_trades live_real rows` | `1302` |
| `fact_trades live_real distinct fill_id` | `1302` |
| `raw clob_fills rows` | `1302` |
| raw CLOB cost | `$3457.59` |
| DB/fill/cache/fact coverage gate | `PASS`: `missing_order_rows=0`, `over_order_keys=0`, `db_not_in_cache=0`, `cache_not_in_db=0`, `db_fill_cost_minus_fact_cost=0` |

### Initial rebuilt snapshot

| item | value |
|---|---:|
| `MAX(fact_built_at_utc)` | `2026-06-06T18:29:44.862646+00:00` |
| `fact_trades live_real rows` | `1246` |
| `fact_trades live_real distinct fill_id` | `1246` |
| `raw clob_fills rows` | `1246` |
| `raw CLOB vs DB fill_id` | `db_not_in_raw=0`, `raw_not_in_db=0` |
| DB/fill internal coverage gate | `PASS`: `missing_order_rows=0`, `over_order_keys=0` |
| window | `2026-05-31..2026-06-06 UTC/BJ reports` |

## Fill coverage gate

新增脚本：`scripts/analysis/weather_clob_fill_coverage_gate.py`。

| source | rows | cost | missing/mismatched order rows | over-order keys | gate |
|---|---:|---:|---:|---:|---|
| rebuilt DB `fills` | `1246` | `$3348.78` | `0` | `0` | `PASS` |
| rebuilt `clob_fills.jsonl` | `1246` | `$3348.78` | `0` | `0` | `PASS` |
| old DB/cache before rebuild | `1316` | `$3499.01` | `2` / `$7.42` | `53` / excess `$57.13` | `FAIL` |

Root cause refinement:

- 旧 public fallback 会把 Polymarket account-level activity 的一整笔 trade 归到单个 submitted child order；split taker/maker 场景里这会超过 child order 的真实 `makingAmount/takingAmount`。
- `exchange_response.place.status='matched'` 的订单本地已有精确 `makingAmount/takingAmount`，现在 `clob_fill_sync.py` 和 rebuilt cache 脚本都优先使用这个来源，再回退到 public activity。
- 旧 DB 的 `fact_trades` 和 `fills` 一致，只能说明 fact 表吃到了 fills；不能说明 fills 本身是权威正确的。
- 当前 rebuilt DB 已通过 fill/order 内部 gate，因此策略 PnL 可从 `fact_trades` 读取；外部 Poly public BUY 差额单独作为账户对账未闭环项。

## Account coverage gate

| metric | value |
|---|---:|
| raw remote live posted notional, BJ created date | `$1909.81` |
| rebuilt local raw CLOB fill cost, UTC fill date | `$1532.75` |
| Polymarket public activity TRADE:BUY | `$1818.70` |
| Poly BUY - rebuilt local raw CLOB | `$285.95` |
| matched local/poly BUY | `$1440.35` |
| unmatched local | `$92.41` |
| unmatched Poly BUY | `$378.36` |
| unmatched Poly weather-like | `$378.36` |
| unmatched Poly known local token | `$348.67` |
| unmatched Poly unknown local token | `$29.68` |

Asset-day aggregate diagnostic:

| metric | value |
|---|---:|
| asset+UTC-day keys | `246` |
| keys with both local and Poly BUY | `230` |
| local total cost | `$1532.75` |
| Poly total BUY | `$1818.70` |
| net Poly - local | `$285.95` |
| sum absolute asset-day gaps | `$285.99` |
| Poly-only asset-day keys | `8` |
| Poly-only money | `$35.37` |
| Poly-only known-local-token money | `$5.68` |
| local-only money | `$0.00` |

Interpretation:

- `DB raw CLOB fills == fact_trades live_real` 已通过，这是底表内部一致性 gate。
- `local raw CLOB BUY ~= Poly BUY` 还没通过；但这是外部账户 activity 对账问题，不再阻止 `fact_trades` 作为成交后策略 PnL 底表。
- 未匹配 Poly BUY 主要仍是 weather-like，且大部分 token 已在本地见过，下一步应继续查 matching/分配，而不是先假设是非 weather 手工单。
- asset-day 聚合后仍有 `$285.95` 净缺口，说明问题不只是严格逐笔匹配窗口过窄；top gaps 分散在多个 known-local-token，仍需要按 orderID/transactionHash/账户 activity 继续查。

## Shanghai 2026-06-06 lineage check

口径：`city='Shanghai' AND target_date='2026-06-06'`，真实成交只看 `fact_trades.trade_class='live_real'`。本地缺 `Shanghai_2026-06-06` pm_history，所以下表只解释下单/成交成本，不把 UI 结算亏损重新写入 DB PnL。

| strategy instance | policy | entry window | bracket | side | submitted orders | submitted notional | real fills | real fill cost |
|---|---|---|---|---|---:|---:|---:|---:|
| `mid_price_core_v1_side_band` | `mid_price_core_v1` | `0.35-0.65` | `26` | `BUY_NO` | `1` | `$5.00` | `1` | `$5.00` |
| `mid_price_core_v1_25_75` | `mid_price_core_v1` | `0.25-0.75` | `26` | `BUY_NO` | `1` | `$5.00` | `1` | `$5.00` |
| `mid_price_core_v2_25_75` | `mid_price_core_v2` | `0.25-0.75` | `26` | `BUY_NO` | `1` | `$5.00` | `1` | `$4.90` |
| `mid_price_core_v1_25_75` | `mid_price_core_v1` | `0.25-0.75` | `27` | `BUY_NO` | `1` | `$5.00` | `2 partial fills` | `$4.79` |
| `mid_price_core_v2_25_75` | `mid_price_core_v2` | `0.25-0.75` | `27` | `BUY_NO` | `1` | `$3.65` | `1` | `$3.50` |

Key points:

- 上海 26°C 这一腿不是 `$10` 上限，而是 `$5 + $5 + $4.90 = $14.89`，因为 v2 历史实例也成交了。
- 上海 6/6 全部真实成交成本合计 `$23.18`，其中 v2 贡献 `$8.40`。
- N100 当前启动脚本已写明 `mid_price_core_v2_25_75` 默认停用，只有 `START_MID_PRICE_CORE_V2_25_75=1` 才会启动；当前进程环境也只看到 `mid_price_core_v1_25_75` 和 `mid_price_core_v1_side_band` 两个 loop。

## Strategy performance after history rebuild

以下数字按 `target_date`，最近 7 天 `2026-05-31..2026-06-06`，来自已通过 fill coverage gate 的 rebuilt DB。

| strategy_instance | settled PnL | settled cost | open cost | settled ROI |
|---|---:|---:|---:|---:|
| `mid_price_core_v1_25_75` | `-$163.91` | `$898.28` | `$143.16` | `-18.2%` |
| `mid_price_core_v1_side_band` | `+$24.76` | `$241.72` | `$34.99` | `+10.2%` |
| `mid_price_core_v2_25_75` | `-$93.59` | `$314.68` | `$8.40` | `-29.7%` |
| active 3 instances | `-$232.74` | `$1454.68` | `$186.55` | `-16.0%` |
| live total including legacy | `-$225.14` | `$1464.07` | `$186.54` | `-15.4%` |

按 `fill_date_bj`，最近 7 天钱包现金流：

| metric | value |
|---|---:|
| fill cash cost | `$1642.72` |
| settled realized PnL | `-$205.51` |
| open cost | `$248.45` |
| available mid MTM on open subset | `+$7.72` |
| open rows missing mid valuation | `44` |

## Files produced

- `docs/analysis/2026-06/2026-06-07-account-equity-replay-after-fill-fix.json`
- `docs/analysis/2026-06/2026-06-07-account-equity-replay-after-history-rebuild.json`
- `docs/analysis/2026-06/2026-06-07-live-account-reconcile-after-fill-fix.json`
- `docs/analysis/2026-06/2026-06-07-live-account-reconcile-after-history-rebuild.json`
- `docs/analysis/2026-06/2026-06-07-live-strategy-period-slice-after-fill-fix.md`
- `docs/analysis/2026-06/2026-06-07-live-strategy-period-slice-after-fill-fix.json`
- `docs/analysis/2026-06/2026-06-07-live-strategy-period-slice-after-history-rebuild.md`
- `docs/analysis/2026-06/2026-06-07-live-strategy-period-slice-after-history-rebuild.json`
- `docs/analysis/2026-06/2026-06-07-clob-fill-coverage-gate.json`
- `docs/analysis/2026-06/2026-06-07-clob-fill-coverage-gate-after-rebuild.json`
- `runtime/weather_edge_v1/clob_fills.rebuilt.jsonl`

## Verification

- `pytest tests/weather_dashboard/test_clob_fill_sync.py -q` -> `6 passed`
- `scripts/weather_dashboard/run_stack.sh` rebuilt DB/fact tables; it exited after API start failed because port `8000` was already in use, but DB rebuild completed before that failure.
- `weather_clob_fill_coverage_gate.py` after rebuild -> `gate_pass=true`
- `py_compile` passed for:
  - `weather_dashboard/ingest/clob_fill_sync.py`
  - `scripts/analysis/weather_account_equity_replay.py`
  - `scripts/analysis/weather_clob_fill_coverage_gate.py`
  - `scripts/analysis/weather_live_strategy_period_slice.py`
  - `scripts/ops/rebuild_clob_fill_cache_from_activity.py`

## Remaining work

1. Continue external account activity reconciliation: rebuilt local CLOB BUY is `$1532.75`, Poly public BUY is `$1818.70`, leaving `$285.95` to classify.
2. Use rebuilt `runtime/weather.db` for strategy PnL reports; keep coverage gate in every future report so old failed DB/cache cannot silently come back.
3. Do not run `sync_weather_remote.sh` blindly until N100/raw cache handling is updated, otherwise the rebuilt local `clob_fills.jsonl` may be overwritten by older cache state.
