# Tokyo JMA → RJTT METAR Historical Alignment v1

Status: `single_city_evidence`; `research_only`; no live change

## 结论

Tokyo 的长历史数据可以找到，而且 JMA Haneda 10-minute 与 RJTT METAR 足以训练一个城市独立的 source→METAR probability head。

最直接的第一版目标是：

```text
P(next routine RJTT METAR confirms the JMA integer-temperature lattice
  within 30/60/120m | JMA path + strictly-prior METAR state)
```

这不是 final exact-bracket 概率，也还没有证明相对 market 的 residual；因此动作仍是 research/collector，不改 live。

## 数据与时钟

- window: `2024-04-30` 至 `2026-06-10`，`772` 个 Tokyo local dates；
- JMA Haneda 10-minute: `111,144` rows；
- RJTT IEM/METAR: `38,558` rows；
- JMA→下一份 routine METAR（60m 内）配对：`107,913`。

历史 archive 只有 observation clock，不是 first-seen clock。对齐时：

1. state 中的 METAR 必须 `metar_observation_ts < jma_observation_ts`；
2. 与 JMA 相同 observation timestamp 的 METAR 一律视为顺序未知，不进特征；
3. 后续 METAR 只作 label；
4. 历史数据只训练物理 source→METAR relation；live decision 仍必须用 collector `source_first_seen_at_utc`。

## Source basis

下一份 routine METAR 相对 JMA 温度：

- median basis: `+0.1°C`；
- MAE: `0.391°C`；
- `77.2%` 在 `±0.5°C` 内；
- `95.4%` 在 `±1.0°C` 内。

## JMA lead → future METAR

白天 `06:00–18:00`，JMA lead 定义为：

- 本次 JMA integer lattice 相对前一份 JMA 发生变化；
- 相对严格更早的最近 METAR 高至少 `1°C`。

| horizon | 任意 checkpoint 后 METAR 升温 | JMA lead 后 METAR 升温 | JMA lattice 被确认 | 首次 city-day lead 确认 | unique date×lattice 确认 |
|---:|---:|---:|---:|---:|---:|
| 30m | 28.9% | 77.0% | 75.6% | 85.7% | 78.7% |
| 60m | 42.6% | 87.3% | 86.6% | 95.2% | 90.7% |
| 120m | 54.6% | 90.7% | 90.2% | 97.8% | 94.8% |

60m event-level confirmation 在 `2024/2025/2026` 分别为 `85.2%/87.1%/87.5%`，方向跨年份稳定。

## Negative control

JMA lead 不是确定性 cross：

- unique date×lattice leads: `4,200`；
- 当日后续 METAR 最终确认: `96.1%`；
- `terminal_false_cross`: `163`，占 `3.9%`。

这 163 个 source cross 必须保留为 probability model 的负例。JMA→METAR 的高确认率不能替代 METAR/WU→settlement basis，也不能直接解释成 exact YES 胜率。

## 策略计划

1. Historical pretrain：在上述 772 日期训练 `P(next METAR confirm in 30/60/120m)`，按 target_date 做 expanding/OOF。
2. Exact calibration：用实时 collector 的 JMA/METAR first-seen 事件校准 publication delay、同 timestamp source ordering 和特征可见性。
3. Remaining-heat fusion：接入 PIT forecast peak clock、remaining heating window、云雨、风、湿度和 QNH。
4. Final-exact bridge：从 source→METAR cross probability 映射到 settlement full-ladder distribution，显式建模 overshoot 与 source basis。
5. Market residual：同 checkpoint 比较 `P(final bracket)` 与 normalized market ladder；没有同分母 market baseline 前不进入交易层。
6. Forward telemetry：完整写入 zero-notional `fact_signal_candidates v2`，单列 `terminal_false_cross`，不接 plan/order/fill/exit。

## Reproduction

```bash
PYTHONPATH=. .venv/bin/python \
  scripts/analysis/market_structure_edge/research_tokyo_jma_metar_history_alignment_v1.py
```

Generated output:
`docs/analysis/2026-07/generated/tokyo_jma_metar_history_alignment_v1/`
