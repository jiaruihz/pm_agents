# Weather Strategy Research Window Handoff

> generated_at_bj: `2026-06-09`
> scope: 本窗口总结、当前进度、下一窗口提示词。
> no live/N100 behavior changed in this window.

## 当前判决

旧主线不能再当作已验证策略：

- H_A：全局 `model_p_yes - market` 单腿 alpha 不成立，不能继续用“模型全局比市场准”作为 live 加仓 thesis。
- H_B：model-free 市场结构偏差第一版没有过三门，不能作为 live 扩仓依据。
- H_C：`city x model_version x side` 条件子池没有过三门，GFS 子池不能直接上 live。
- Step2B：time-aligned raw orderbook 后，taker edge 很薄且 CI 跨 0；maker proxy 看起来更好但没有证明成交概率，也没有证明无逆向选择。

当前 live 动作结论：

- 不加仓。
- 不扩城市池。
- 不按近期赢家或 GFS 子池改 live。
- 若要改 N100 生产行为，必须另走 `weather-strategy-deploy` git-first 流程。

## 本窗口已完成

| Commit | 内容 |
|---|---|
| `76c29ad` | 保存并审计外部草稿，新增 H_B/Step2 脚本第一版和报告产物。 |
| `769471e` | 新增 H_C `city x model x side` 条件优势脚本与报告。 |
| `5e031f9` | 将 Step2B raw orderbook 从 fail-closed 改为 time-aligned replay。 |

关键产物：

| 文件 | 结论 |
|---|---|
| `docs/analysis/2026-06/2026-06-08-decisive-experiment-scripts-audit-and-handoff.md` | 外部草稿中可用/不可用结论的本机审计版。 |
| `docs/analysis/2026-06/2026-06-08-market-structural-edge.md` | H_B：`significance=FAIL`, `baseline=FAIL`, `forward=PASS`, `verdict=inconclusive`。 |
| `docs/analysis/2026-06/2026-06-08-city-model-conditional-edge.md` | H_C：`significance=FAIL`, `baseline=FAIL`, `forward=FAIL`, `verdict=inconclusive`。 |
| `docs/analysis/2026-06/2026-06-08-executable-edge.md` | Step2B：taker ROI `+2.6%`, CI `[-6.0%, +11.1%]`; maker proxy ROI `+8.8%`, CI `[-0.3%, +18.1%]`; verdict `inconclusive`。 |

## 当前研究进度

完成：

1. 把外部审计草稿保存为本机可追溯文档，并标废弃提示。
2. 用 `fact_trades` / `fact_signal_candidates` 重新校验核心绩效与机会层。
3. 落库并运行 H_B、H_C、Step2B 三个决定性实验脚本。
4. 用 time-aligned orderbook 修掉“最新盘口未来信息污染”的 Step2B 硬伤。

未完成但不应在旧主线里继续挖：

1. H_A living doc 当前有草稿 `docs/analysis/model_vs_market.md`，但它不是 live 动作依据。
2. `decision_window_missing` 仍然很高，约 44%，继续做过细切片前应改善 capture 覆盖。
3. maker proxy 尚未变成成交策略；需要单独建 fill probability / adverse selection 研究。

## 下一步研究方向

用户提出的修正是对的：不要做“全档 NO basket”，而要把区间套利和相邻 bracket relative value 合并成一个新策略族。

新 thesis：

> 在某个 `city + event_date` 的温度分布上，市场给某一段温度区间的总价格/形状定错了；用一组相邻 bracket 的 YES/NO 组合表达区间观点，而不是单腿押某一档。

下一窗口优先做 `Range RV Scanner v0`：

- grain：`city + event_date`
- 输入：`fact_signal_candidates` + time-aligned raw orderbook
- 枚举：
  - 单档
  - 相邻 2 档
  - 相邻 3 档
  - below-tail
  - above-tail
- 指标：
  - `market_prob_sum`
  - `model_prob_sum`
  - `range_edge = model_prob_sum - market_prob_sum`
  - `taker_cost`
  - `maker_proxy_cost`
  - `settled_pnl`
  - 相对 matched baseline 的 excess ROI
- 验证：
  - train 选规则
  - holdout 复核
  - target_date cluster bootstrap
  - 三门标签 `significance / baseline / forward`

## 新窗口提示词

```text
我们在 /home/rui/projects/pm_agent 继续天气策略研究。请先遵守 AGENTS.md 和 weather-strategy-performance skill，分析只用 runtime/weather.db 的 fact_trades / fact_signal_candidates；涉及 live_real 结论前跑 weather_clob_fill_coverage_gate.py；不要改 N100/live 配置。

当前结论：旧 H_A/H_B/H_C 都没有过三门，不加仓、不扩池。最新提交包括：
- 76c29ad Add weather edge audit scripts
- 769471e Add city model conditional edge research
- 5e031f9 Add time aligned orderbook executable edge

关键报告：
- docs/analysis/2026-06/2026-06-08-market-structural-edge.md
- docs/analysis/2026-06/2026-06-08-city-model-conditional-edge.md
- docs/analysis/2026-06/2026-06-08-executable-edge.md
- docs/analysis/2026-06/2026-06-09-weather-strategy-research-window-handoff.md

现在不要继续在单腿 model edge 里挖。请新建并运行 Range RV Scanner v0：把 city-day 区间套利和相邻 bracket relative value 合并成一个策略研究。核心 thesis 是：市场在某个温度区间/分布形状上定价错误，我们用相邻 bracket 的 YES/NO 组合表达，而不是单腿押某一档。

要求：
1. 先锁 target metric：city-day range relative value alpha。
2. 数据源优先用 fact_signal_candidates；需要可成交价格时复用或提取 scripts/analysis/research_executable_edge.py 里 time-aligned orderbook 逻辑，必须满足 orderbook_snapshot_ts <= decision_snapshot_ts_utc。
3. 枚举单档、相邻2档、相邻3档、below-tail、above-tail。
4. 对每个 range 计算 market_prob_sum、model_prob_sum、range_edge、taker_cost、maker_proxy_cost、settled_pnl、baseline/excess ROI。
5. 按 event_date 做 train/holdout，train 选规则，holdout 复核；bootstrap 按 target_date/event_date cluster。
6. 报告三门：significance/baseline/forward。任何门不过只能 inconclusive，禁止给 live 动作。
7. 生成脚本、JSON、Markdown 报告，并维护 docs/WEATHER_DOCS_INDEX.md。只做 scoped commit，不提交无关脏改动。
```

## Git 状态提醒

本窗口结束时，工作区仍有大量既有未提交改动和未跟踪文件。下一窗口开始前先跑：

```bash
git status --short
```

不要把无关脏改动混进 Range RV Scanner 的提交。
