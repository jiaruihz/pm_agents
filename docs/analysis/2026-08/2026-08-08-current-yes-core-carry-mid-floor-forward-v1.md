# Current-YES Core Carry：0.80 market-mid gate forward 复核 v1

Status: `keep live floor / floor is not model boundary / lower band remains shadow-only`

## 结论

`market mid >= 0.80` **不是模型有效性的自然卡点，也不是训练边界**；它是 live 资金域。本轮最新 8 个 target dates（entry label 全覆盖，probability 分母仅 1 row 缺口）没有证明把它降到 0.50/0.60/0.70/0.75 会改善收益，所以当前 live 仍保留 0.80。但 0.80 以下不是模型不能算：应继续完整评分并作为独立 shadow domain，而不是在模型层删除。

最宽的 floor 0.50 得到 `26` 笔，10 股 PnL `$+0.54`、ROI `+0.24%`；当前 floor 0.80 得到 `21` 笔，PnL `$-2.21`、ROI `-1.15%`。两者 paired PnL 差 `$+2.74`，95% target-date block CI `[$-20.64, $+24.63]`。

## 只改变 floor 的 forward 回放

| floor | entries | W-L | avg mid | avg cost | PnL (10sh) | ROI | date-block 95% CI | vs 0.80 PnL |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.50 | 26 | 22-4 | +82.28% | +84.41% | $+0.54 | +0.24% | [-8.71%, +14.53%] | $+2.74 |
| 0.60 | 26 | 22-4 | +83.73% | +85.76% | $-2.97 | -1.33% | [-8.91%, +11.40%] | $-0.76 |
| 0.70 | 23 | 21-2 | +88.02% | +89.67% | $+3.77 | +1.83% | [-7.87%, +12.08%] | $+5.97 |
| 0.75 | 22 | 20-2 | +89.15% | +90.78% | $+0.29 | +0.15% | [-10.08%, +10.09%] | $+2.50 |
| 0.80 | 21 | 19-2 | +90.02% | +91.53% | $-2.21 | -1.15% | [-11.48%, +8.09%] | $+0.00 |
| 0.85 | 15 | 14-1 | +92.99% | +94.32% | $-1.48 | -1.05% | [-9.19%, +6.50%] | $+0.73 |
| 0.90 | 12 | 12-0 | +94.20% | +95.24% | $+5.71 | +5.00% | [+2.32%, +6.89%] | $+7.92 |

所有策略均固定：deployed v3、首次成功小时 checkpoint、fresh observation、训练 support、exact bracket、10-share full ask ladder + 官方 fee、首个正 net-EV 锁 city-day。
这些是 signal-level 全部成交反事实，不是实际 live fill PnL。

事后最好的 floor 是 `0.90`，相对 0.80 多 `$+7.92`；但同时试了 6 个 floor 后，exact target-date sign-flip family-wise p=`0.547`，不能把这 8 天的赢家阈值当新规则。floor 0.70 的改善只来自 2 个新增 winner 和 Ankara/Wellington 两个同档更早、更便宜的 winner，没有形成独立样本规模。

## 概率层：0.80 以下是否属于域外

| mid band | rows / city-days | actual | model p | market | model-market Brier | model-market logloss |
|---|---:|---:|---:|---:|---:|---:|
| [0.50,0.6000) | 25 / 23 | +52.17% | +49.22% | +54.88% | -0.01851 | -0.03713 |
| [0.60,0.7000) | 43 / 41 | +56.10% | +61.19% | +64.68% | +0.00180 | +0.00651 |
| [0.70,0.7500) | 17 / 15 | +73.33% | +68.01% | +72.44% | +0.00876 | +0.01818 |
| [0.75,0.8000) | 21 / 20 | +80.00% | +73.07% | +76.96% | +0.01065 | +0.02134 |
| [0.80,0.9000) | 87 / 76 | +85.53% | +83.02% | +85.37% | +0.00470 | +0.01585 |
| [0.90,0.9895] | 190 / 143 | +97.55% | +94.83% | +95.71% | +0.00045 | +0.00436 |

负的 proper-score delta 才表示模型优于同 rows 的 market mid。forward 只有 8 个 date blocks，六个 band 的 Brier/logloss target-date CI 均跨 0，因此只把方向当新增证据，不据此重新选阈值。

历史 OOF（32 dates）同样说明 0.80 不是能力边界：
`0.50–0.80` 有 603 states，model-market Brier `-0.00614`，首个正 taker-EV ROI `+8.03%`，但 CI `[-0.04352905561964825, 0.20252437858010675]` 跨 0。反而 `0.90–0.9895` 历史 ROI 为 `-1.65%`。

## 被 0.80 排除的正 net-EV 机会

本窗 `<0.80` 有 `10` 个首个正 net-EV city-day，`7` 胜 `3` 负，10 股 PnL `$-1.55`、ROI `-2.17%`。其中相对当前 0.80 policy：新增 city-day `5`，同 city-day 提前/换档 `5`。

这些是研究候选，不应再叠天气模式 hard gate。城市/天气 regime 只作为诊断，完整逐条 ledger 在归档产物中。

## 双漏斗与数据修复

- signal funnel：stable raw `966` → 成功 score `773` → 去 restart 重复后 `773` → 同支持域 probability rows `577` → 10-share executable `576`。
- evidence funnel：settlement 在研究前 8/2–8/7 只覆盖 2–7 城；本轮按 canonical bounded backfill 补到每日 47 城，新增 2,783 条 `settlement_outcomes`。另有 San Francisco 8/1 一条旧 append-only row 仍是 0.9825/missing_bracket；当前 Gamma 已刷新为 1.0。该 row 只从 probability score 分母显式排除，且其 model p 低于 10-share cost，不影响任何 floor 的 entry/PnL。
- `2026-07-31`: 87/87 (100%)
- `2026-08-01`: 81/82 (99%)
- `2026-08-02`: 58/58 (100%)
- `2026-08-03`: 36/36 (100%)
- `2026-08-04`: 95/95 (100%)
- `2026-08-05`: 84/84 (100%)
- `2026-08-06`: 51/51 (100%)
- `2026-08-07`: 85/85 (100%)

数据快照：artifact `40af251fa48d827da294febc5854bde1bfde3f0e82efe93b7119d9886aa2d411`；probability denominator SHA `300027d1c4b489639a33dfc048bd19c396e87d2696159762058088dbd5cae4d9`；canonical DB inode `54444`。

## 决策

- live：保持 `market_mid_floor=0.80`，不因本轮改生产。
- 语义：把 0.80 明确称为 `live_authorized_market_mid_floor`；不要称为 model support floor。
- research/shadow：继续记录 `0.50 <= mid < 0.80 AND p_model > 10-share taker cost`；累计至少 30 个新 settled target dates 后，按同一预注册 selector 做一次 frozen review。
- 不新增 price bucket/weather regime gate；最终候选应是连续 net-EV expression，floor 只负责 live 风险授权。

## Reproduce

```bash
.venv/bin/python scripts/analysis/reheat_risk/research_current_yes_core_carry_europe_mid_replay_v1.py --study global-live-forward
```
