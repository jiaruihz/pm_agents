# PMM Skill 拆分与 Schema 设计

## 1. 目标

把当前项目里的高频能力抽成可组合 Skill，让 AI 通过自然语言触发稳定流程，而不是每次临时拼命令。

原则：

1. Skill 之间低耦合、高内聚。
2. 每个 Skill 有稳定输入/输出 schema。
3. 支持链式编排（上游输出直接作为下游输入）。
4. 先定义接口，再迭代实现。

---

## 2. 推荐 Skill 拆分

### 2.1 `pmm-scenario-generator`

职责：
- 从 catalog 或参数生成回测场景文件。
- 支持按“用户诉求”自动生成不同参数配置（例如基准概率区间、波动形态、流动性强弱、是否突发事件）。
- 可输出初始状态和样本 tick 快照。

映射当前能力：
- `pmm/backtest/scenario_generator.py`
- `scripts/python/generate_backtest_scenarios.py`

### 2.2 `pmm-backtest-runner`

职责：
- 跑单场景、批量场景。
- 产出 `metrics/actions/summary`。
- 可选同时出图（将原 `pmm-plot-reporter` 能力合并）。

映射当前能力：
- `pmm/backtest/replay_runner.py`
- `scripts/python/pmm_backtest.py run/run-all`
- `pmm/backtest/plotter.py`

### 2.3 `pmm-backtest-compare`

职责：
- 同场景多 profile 对比。
- 输出 compare 汇总和排名。

映射当前能力：
- `scripts/python/pmm_backtest.py compare`

### 2.4 `pmm-fill-model-evaluator`

职责：
- 同一批场景同时跑 `conservative/optimistic`。
- 输出统一榜单（txt/csv/json）。

映射当前能力：
- `scripts/python/pmm_backtest.py run-all-fill-models`

### 2.5 `pmm-market-data-fetcher`

职责：
- 输入 Polymarket market/event URL。
- 自动解析并抓取：标题、规则（rule/description）、相关 token_id、实时 orderbook。
- 产出标准化 market snapshot，供 PMM 或 Arb 直接消费。

映射当前能力（部分）：
- `scripts/python/server.py` HTTP API + PM 连接层

---

## 3. 统一 Skill I/O Envelope（建议）

所有 Skill 共用统一外层结构，避免每个 Skill 风格不一致。

### 3.1 Request Envelope

```json
{
  "skill": "pmm-backtest-runner",
  "version": "v1",
  "request_id": "uuid",
  "dry_run": false,
  "params": {}
}
```

字段说明：
- `skill`: skill key
- `version`: schema 版本
- `request_id`: 调用链追踪 id
- `dry_run`: 是否只演练不落盘
- `params`: skill 特定参数

### 3.2 Response Envelope

```json
{
  "skill": "pmm-backtest-runner",
  "version": "v1",
  "request_id": "uuid",
  "status": "ok",
  "summary": {},
  "artifacts": [],
  "warnings": [],
  "error": null
}
```

字段说明：
- `status`: `ok | partial | error`
- `summary`: 核心统计
- `artifacts`: 产物路径列表
- `warnings`: 非阻塞告警
- `error`: 失败时错误对象

### 3.3 Error Schema

```json
{
  "code": "INVALID_PARAMS",
  "message": "fill_models must be conservative/optimistic",
  "detail": {}
}
```

---

## 4. 各 Skill 参数 Schema（v1）

## 4.1 `pmm-scenario-generator`

`params`（两种模式）：

1) catalog 模式（兼容当前）：
```json
{
  "mode": "catalog",
  "catalog": "pmm/backtest/case_catalog.json",
  "out_dir": "pmm/backtest/scenarios",
  "seed": 42,
  "show_initial": true,
  "show_sample": "b30_event_spike_then_revert"
}
```

2) intent 模式（按诉求出参数）：
```json
{
  "mode": "intent",
  "out_dir": "pmm/backtest/scenarios",
  "seed": 42,
  "request_profile": {
    "base_zone": "around_30",
    "market_regime": "single_side_up",
    "liquidity": "medium",
    "fill_expectation": "partial_fill",
    "shock_event": "spike_then_revert",
    "horizon_ticks": 600,
    "count": 20
  }
}
```

`request_profile` 关键字段建议：
- `base_zone`: `around_30 | around_50 | around_70`
- `market_regime`: `stable | oscillating | single_side_up | single_side_down | whipsaw`
- `liquidity`: `low | medium | high`
- `fill_expectation`: `mostly_fill | partial_fill | mostly_no_fill`
- `shock_event`: `none | spike_up | spike_down | spike_then_revert | drop_then_revert`
- `horizon_ticks`: 回测长度
- `count`: 生成样本数量

`summary`:
- `count`
- `mode`
- `out_dir`

`artifacts`:
- `scenarios/*.json`

## 4.2 `pmm-backtest-runner`

`params`:

```json
{
  "mode": "single",
  "scenario": "pmm/backtest/scenarios/b50_oscillating_fill.json",
  "scenarios_dir": "pmm/backtest/scenarios",
  "out_dir": "pmm/backtest/results",
  "with_plots": true,
  "plot_mode": "single",
  "plot_formats": ["png"]
}
```

约束：
- `mode=single` 时必须有 `scenario`
- `mode=all` 时必须有 `scenarios_dir`
- `with_plots=true` 时，自动调用 plotter 并写 `plot_manifest.json`

`summary`:
- `pnl_end`
- `total_fills`
- `total_placed`
- `max_drawdown`

`artifacts`:
- `metrics.jsonl`
- `actions.jsonl`
- `summary.json` 或 `summary_all.json`
- （可选）`plots/*.png`
- （可选）`plots/plot_manifest.json`

## 4.3 `pmm-backtest-compare`

`params`:

```json
{
  "scenario": "pmm/backtest/scenarios/b50_oscillating_fill.json",
  "profiles": "pmm/backtest/compare_profiles_all_strategies.json",
  "out_dir": "pmm/backtest/results_compare"
}
```

`summary`:
- `count`
- `best_by_pnl`
- `worst_by_pnl`

`artifacts`:
- `compare_summary.json`

## 4.4 `pmm-fill-model-evaluator`

`params`:

```json
{
  "scenarios_dir": "pmm/backtest/scenarios",
  "fill_models": ["conservative", "optimistic"],
  "out_dir": "pmm/backtest/results_fill_models"
}
```

`summary`:
- `count`
- `fill_models`
- `leaderboard_rows`

`artifacts`:
- `summary_all_fill_models.json`
- `summary_all_fill_models_table.txt`
- `summary_all_fill_models_table.csv`

## 4.5 `pmm-market-data-fetcher`

`params`:

```json
{
  "market_url": "https://polymarket.com/event/natural-disaster-in-2026",
  "include_orderbook": true,
  "include_event_detail": true,
  "include_rules": true,
  "snapshot_out": "local_market_collection/market_snapshot_natural_disaster_2026.json"
}
```

`summary`:
- `market_url`
- `event_title`
- `token_count`
- `snapshot_time`

`artifacts`:
- `market_snapshot_*.json`

建议返回字段（标准化）：
- `event`: `event_id`, `title`, `slug`, `description`, `rules`
- `markets`: `market_id`, `token_id`, `outcome`, `active`, `close_time`
- `orderbooks`: 每个 `token_id` 的 `best_bid`, `best_ask`, `bids`, `asks`

---

## 5. Skill 之间的编排关系

建议编排链路：

1. `pmm-market-data-fetcher`（可选）
2. `pmm-scenario-generator`
3. `pmm-backtest-runner` / `pmm-fill-model-evaluator`
4. `pmm-backtest-compare`（可选）

标准传递字段：
- `scenario_paths`
- `results_dir`
- `summary_path`
- `plot_manifest_path`（如果开启绘图）

---

## 6. 版本与兼容策略

1. 每个 Skill 的 envelope 带 `version`，从 `v1` 开始。
2. 新增字段只能“向后兼容添加”，不要删旧字段。
3. 产物 JSON 建议加 `schema_version`。
4. 重要路径字段保持稳定命名（便于外部 AI 自动消费）。

---

## 7. 落地优先级

1. 先把已有 CLI 封成 5 个 Skill（最小改造）。
2. `pmm-scenario-generator` 先支持 `catalog`，再补 `intent` 模式。
3. `pmm-backtest-runner` 增加 `with_plots` 可选能力（不再独立 plot skill）。
4. 最后接编排器（按 `skill` key 路由，支持链式运行）。

---

## 8. 你后续改逻辑的方式

1. 先改 Skill 内部实现（算法/策略/细节）。
2. 尽量不改 Skill 输入输出 schema。
3. 如必须改 schema，升 `version` 并保留旧版本兼容期。

这能保证：你可以高频迭代策略，而不把上层 AI 调用全打崩。
