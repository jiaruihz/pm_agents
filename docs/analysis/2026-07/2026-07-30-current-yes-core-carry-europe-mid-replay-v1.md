# Current-YES Core Carry：欧洲 mid-price 回放 v1

Status: `inconclusive / research replay / no live change`

## 结论

欧洲并非没有 carry。把 floor 从 0.80 降到 0.70，历史 first-positive-EV city-days 增加 `11`；但是否值得取决于 lower-floor 的 fee ROI、model-vs-market proper score 与最后 10 日 forward 是否同时成立。Istanbul 有历史分母但当前 live source 未放行；Moscow 连当前 observation cache 和历史 OOF 分母都不足，二者不能一起直接加进 live。

## 欧洲 floor 回放

| mid floor | city-days | dates | win rate | fee ROI | date-block 95% CI | forward ROI |
|---:|---:|---:|---:|---:|---:|---:|
| 0.50 | 44 | 25 | +72.73% | -5.86% | [-20.88%, +8.69%] | -5.82% |
| 0.60 | 41 | 23 | +75.61% | -5.59% | [-19.57%, +8.71%] | -5.82% |
| 0.70 | 38 | 22 | +78.95% | -6.15% | [-17.72%, +5.91%] | +0.79% |
| 0.75 | 32 | 21 | +90.62% | +3.44% | [-8.71%, +12.61%] | +12.98% |
| 0.80 | 27 | 20 | +92.59% | +3.50% | [-9.54%, +11.95%] | +11.16% |

Train/forward 切点固定为 `2026-06-23`；最后 10 个 target dates 只作 frozen direction check。

### 0.75 相对 0.80 的实际增量

- 触发数增加 `18.5%`，win rate 变化 `-2.0%`，整体 ROI 变化 `-0.07%`。
- 0.75 独有的 `5` 个 city-day 为 `4` 胜 `1` 负，单独 fee ROI `-0.92%`；因此增加触发不等于增加 alpha。
- Istanbul：floor 0.80 为 `12` 笔、`11` 胜 `1` 负、ROI `+3.98%`；floor 0.75 为 `14` 笔、`13` 胜 `1` 负、ROI `+6.69%`。样本仍低于城市级门槛。

## 概率层：同一 mid band 与市场比较

| mid band | rows | actual hold | model−market Brier | model−market logloss |
|---|---:|---:|---:|---:|
| [0.00,0.5) | 228 | +15.32% | +0.00549 | +0.01302 |
| [0.50,0.6) | 29 | +53.85% | -0.00507 | -0.01080 |
| [0.60,0.7) | 41 | +64.71% | -0.02036 | -0.04447 |
| [0.70,0.8) | 60 | +69.61% | +0.01179 | +0.03185 |
| [0.75,0.8) | 34 | +79.03% | -0.00565 | -0.02200 |
| [0.80,0.9) | 66 | +85.00% | -0.00149 | -0.00665 |
| [0.90,0.9895] | 182 | +95.83% | -0.00024 | +0.00132 |

负数表示 v3 优于 raw market mid；正数表示更差。该表固定同 rows/labels，不使用 selected trades 代替概率分母。

## 当前 live signal funnel

- raw Europe score checkpoints：`243`，覆盖 `9` 城 / `6` 个 target dates。
- mid 0.80–0.9895：`33`；positive taker EV：`1`。
- 当前 raw 是触发覆盖，不含完整 settlement，因此不与历史 realized ROI 混算。

## Istanbul / Moscow source coverage

- Istanbul: historical OOF rows `109`；current cache station `LTFM` present=`false`；source profile live_eligible=`false`。
- Moscow: historical OOF rows `0`；current cache station `UUWW` present=`false`；source profile live_eligible=`false`。

Istanbul 有历史 PIT/settlement 分母，可加入 collector 和 zero-notional Core Carry shadow；Moscow 当前历史 OOF 与 live observation cache 都不足，先补 source collector，不能直接加入真实执行。

## 双漏斗与验证边界

- signal funnel：欧洲 historical OOF `606` rows → 5-share executable `606` → exact bounded `606` → floor-specific first positive-EV city-days（见表）。
- evidence funnel：`31` target dates，PIT book、settlement、5-share ladder 与官方 fee 已覆盖；Istanbul/Moscow 当前 source coverage 另列，maker queue/fill 不属于本次 taker-expression 回放。
- 本轮同时查看 5 个 floor，未做多重检验校正；lower-floor 只可作为 shadow hypothesis。

## 三门

- significance：按各 floor 的 target-date block CI。
- baseline：看同 band 的 model−market proper score；不能只看交易 ROI。
- forward：最后 10 个日期只复核方向，样本不足则 FAIL/NA。
- conclusion：`inconclusive`；不修改现有 0.80 live floor，不扩大城市 live allowlist。

## Reproduce

```bash
.venv/bin/python scripts/analysis/reheat_risk/research_current_yes_core_carry_europe_mid_replay_v1.py
```
