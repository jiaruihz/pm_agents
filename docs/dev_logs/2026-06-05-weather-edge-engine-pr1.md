# Weather Edge Engine — PR1 开发日志

> 关联设计：[WEATHER_CITY_BLEND_MODEL_IMPLEMENTATION_PLAN_2026-06-05.md](../WEATHER_CITY_BLEND_MODEL_IMPLEMENTATION_PLAN_2026-06-05.md)
> 范围：Step 1 / PR 1 — 本机纯函数骨架（blender + city_day_basket + tests）
> 状态：进行中

## 0. PR1 范围

只在本机加纯函数 + 单测，不接 N100、不改生产。

新增文件：

```text
weather_dashboard/blend/__init__.py
weather_dashboard/blend/blender.py
weather_dashboard/blend/city_blend_config.json
weather_dashboard/basket/__init__.py
weather_dashboard/basket/city_day_basket.py
tests/blend/__init__.py
tests/blend/test_blender.py
tests/basket/__init__.py
tests/basket/test_city_day_basket.py
```

## 1. 现有代码摸底

- `weather_dashboard/` 下既有 `metrics/`、`ingest/`、`api/` 这类模块，新模块走 `weather_dashboard/blend/`、`weather_dashboard/basket/`，与现有约定一致。
- 测试根目录是 `tests/`，已有 `tests/weather_dashboard/` 把绝大多数测试摊平在那里。计划文档指定 `tests/blend/`、`tests/basket/`，遵守文档。
- 现有模块多用 `from weather_dashboard.<module> import ...` 的绝对导入。沿用。
- `conftest.py` 在 `tests/weather_dashboard/conftest.py`，PR1 两个模块是**无 DB 的纯函数**，不需要 fixture，所以不依赖现有 conftest。

## 2. blender 实现要点

### 2.1 输入 / 输出契约

输入：
- `city: str`
- `model_p_yes_raw: float | None`
- `market_implied_p_yes: float | None`
- `config: BlendConfig`（带 `global_alpha`、`global_beta`、`blacklist_alpha`、`raw_model_blacklist`、`clip_lo`、`clip_hi`）

输出（dataclass `BlendResult`）：
- `p_yes_used: float`
- `blend_alpha: float`
- `blend_beta: float`
- `blend_mode: str`  — `global` | `blacklist` | `market_only` | `raw_only`
- `reason: str`

### 2.2 决策矩阵

| raw | market | mode | 公式 |
|---|---|---|---|
| 有 | 有，城市 ∈ 黑名单 | `blacklist` | `α=0.10, β=0.90` |
| 有 | 有，城市 ∉ 黑名单 | `global` | `α=0.30, β=0.70` |
| 有 | 缺 | `raw_only` | `p = raw` |
| 缺 | 有 | `market_only` | `p = market` |
| 缺 | 缺 | 抛 `ValueError` | — |

最终 clip 到 `[clip_lo, clip_hi]`。

### 2.3 配置 JSON

放 `weather_dashboard/blend/city_blend_config.json`：

```json
{
  "global_alpha": 0.30,
  "global_beta": 0.70,
  "blacklist_alpha": 0.10,
  "clip_lo": 0.001,
  "clip_hi": 0.999,
  "raw_model_blacklist": ["Milan", "Lucknow", "Austin", "Beijing"]
}
```

- 文件即 source of truth。代码里只兜底默认值。
- 城市名匹配区分大小写——与现有 N100 city naming 保持一致（city_pools 配置里也是 PascalCase）。

## 3. city_day_basket 实现要点

### 3.1 输入 / 输出契约

输入：`BasketInput`，含
- `city`, `target_date`, `snapshot_ts`
- `candidates: list[BracketCandidate]`，每条 candidate 含 bracket id、bracket label、`p_yes_used`、`yes_best_ask`、`no_best_ask`、稳定性字段、`existing_city_day_exposure`
- `config: BasketConfig`

输出：`BasketPlan`，含 `basket_id`、`selected_legs`、`payoff_by_final_temp`、`worst_case_loss`、`expected_value`、`max_notional`、`decision`、`reason_codes`。

### 3.2 决策流程

1. **过滤掉 forecast_jump 大、当日 side flip 的 candidate** → 进 shadow_legs。
2. **算可执行 edge**：
   - `edge_yes = p_yes_used - yes_best_ask`
   - `edge_no  = (1 - p_yes_used) - no_best_ask`
3. **挑 side**：同 bracket 上同时有 yes/no edge 时按 EV 取一边，不允许两边都打。
4. **BUY_NO 优先**：所有 candidate 整理出 NO leg 列表，按 `edge_no` 排序，取前 `max_no_legs_per_city_day` 条。
5. **YES leg**：剩余 candidate 里挑 `edge_yes` 最高的一条作为可选 hedge（v1 简化：YES、NO 不混打，按整篮子 EV 选）。
6. **算 payoff matrix**：枚举 `final_temp ∈ buckets`，每个温度算各腿 PnL，汇总成 `payoff_by_final_temp`。
7. **风险检查**：`worst_case_loss > city_day_notional_cap → SHADOW` 或按比例缩 size 到 cap 以内。
8. **paper-only edge**（candidate 的 `yes_best_ask`、`no_best_ask` 为 `None`）整 basket → `SKIP`。

### 3.3 payoff 公式

- `BUY_YES` 单腿 notional=$n、entry=p、最终命中 bracket B：
  - `T == B → +n * (1 - p)/p`
  - `T != B → −n`
- `BUY_NO` 单腿 notional=$n、entry=q、最终命中 bracket B：
  - `T == B → −n`
  - `T != B → +n * (1 - q)/q`

把所有腿的 vector 相加得到 basket 的 `payoff_by_final_temp`。

### 3.4 决策枚举

- `TRADE`：existing exposure + new legs 在 cap 内、有正 EV、最坏 loss 可接受。
- `REDUCE`：worst_case_loss 超 cap，按比例缩到 cap 内。
- `SHADOW`：稳定性差 / paper-only edge / 城市日 exposure 已超 cap。
- `SKIP`：无可执行 candidate。

## 4. 开发顺序

1. ✅ 摸底现有代码风格
2. ✅ 写 dev log（本文档）
3. ✅ 写 `blender.py` + `city_blend_config.json` + `__init__.py`
4. ✅ 写 `test_blender.py` — 12 tests
5. ✅ blender pytest 全绿（12/12）
6. ✅ 写 `city_day_basket.py` + `__init__.py`
7. ✅ 写 `test_city_day_basket.py` — 16 tests
8. ✅ 全部 pytest 绿（PR1 28/28，叠加现有 weather_dashboard 共 127/127）
9. ✅ 收尾：本日志补完 deviation 与下一步

```bash
.venv/bin/python -m pytest tests/blend tests/basket -v
# 28 passed

.venv/bin/python -m pytest tests/weather_dashboard tests/blend tests/basket
# 127 passed in 2.17s
```

## 5. 偏离设计文档的地方

- **`BasketInput.possible_final_temps` 默认值口径**
  设计文档说 basket 「按可能 final temp 枚举 payoff」，但没说默认温度集合是什么。本实现里如果 caller 不传 `possible_final_temps`，默认用所有 candidates 的 `final_temp_key` 集合。
  影响：只有一条 candidate 时，payoff 只在那一个温度上枚举，导致 EV ≈ p_yes × (−n)，结果一定为负，basket 会被判 SHADOW（`non_positive_ev`）。
  做法：单 candidate 的单测显式传完整 final temp 集合；正式 caller（PR2 离线回放、PR3 shadow 双写）必须传 city-day 的完整 bracket 集合作为 `possible_final_temps`，不能依赖 candidate 默认。
  **TODO**：在 README/docstring 中明确「可执行 basket 要求 caller 提供完整 final temp 集合」，避免未来调用方踩坑。

- **YES/NO 同 bracket 冲突解析放在 basket builder，而不是上游**
  设计文档说「同 bracket YES/NO 同时有 edge → 按 basket payoff 整体选稳的一组」。v1 简化为「同 bracket 上 edge 大的一边胜」，没有真正按 basket payoff 重算两套方案。
  影响：v1 BUY_NO 优先 + 单 bracket 冲突取大 edge，足够保守。
  改进窗口：PR2 离线评估如果出现明显 YES 优势的案例，再引入 dual-side 比较。

- **EV 计算用 candidate p_yes_used 而不是显式 prior**
  v1 用每个 candidate 的 `p_yes_used` 作为「最终 temp == 该 bracket」的概率，剩余概率均分给没有 candidate 的可能温度。这对 ranking 够用，但**不是校准过的 EV**。设计文档里 §2.3 没指定 EV 怎么算，本实现选了最简口径，并在 docstring 里注明。
  改进窗口：v1.5 引入 seasonal residual / forecast distribution 后，可以换成更扎实的 prior。

- **shadow legs 暴露 list 不在设计文档 fields 列表里**
  `BasketPlan` 多了一个 `shadow_legs: list[SelectedLeg]`。设计文档 §3.3 列了 basket 字段，但没包含 shadow legs。
  动机：dashboard 双写需要把「本来想 trade 但因稳定性被降级的腿」存下来，否则归因看不到。
  做法：作为 plan 的非持久化字段先留着，在 PR3 shadow 双写时再决定怎么落 DB。

- **测试目录路径**
  设计文档写的是 `tests/blend/` 和 `tests/basket/`。仓库现状里大多数 weather_dashboard 测试摊平在 `tests/weather_dashboard/`。本 PR 遵守设计文档，新建 `tests/blend/` 和 `tests/basket/` 平级目录。
  长期一致性：后续 PR 如果加更多 weather_edge_engine 子模块测试，建议统一收口到 `tests/weather_edge_engine/` 或并入 `tests/weather_dashboard/`，避免 tests 根目录越来越散。

## 6. 下一步（PR2 预告）

PR1 跑通后进入：
- `scripts/analysis/eval_city_day_basket.py` — 离线回放 raw vs blended single-leg vs basket。
- `scripts/analysis/recalibrate_blend.py` — 周度 sklearn isotonic + logistic 重新校准 alpha。
- 评估文档 `docs/analysis/2026-06/2026-06-XX-city-day-basket-eval.md`。

## 7. 文件变更清单（PR1）

```text
A  weather_dashboard/blend/__init__.py
A  weather_dashboard/blend/blender.py
A  weather_dashboard/blend/city_blend_config.json
A  weather_dashboard/basket/__init__.py
A  weather_dashboard/basket/city_day_basket.py
A  tests/blend/__init__.py
A  tests/blend/test_blender.py
A  tests/basket/__init__.py
A  tests/basket/test_city_day_basket.py
A  docs/dev_logs/2026-06-05-weather-edge-engine-pr1.md
```

不修改任何现有文件。生产、N100、CI 行为不变。

## 6. 下一步（PR2 预告）

PR1 跑通后进入：
- `scripts/analysis/eval_city_day_basket.py` — 离线回放 raw vs blended single-leg vs basket。
- `scripts/analysis/recalibrate_blend.py` — 周度 sklearn isotonic + logistic 重新校准 alpha。
- 评估文档 `docs/analysis/2026-06/2026-06-XX-city-day-basket-eval.md`。
