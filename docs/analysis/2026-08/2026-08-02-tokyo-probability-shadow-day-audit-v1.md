# Tokyo probability shadow 首日审计（2026-08-01）

## 结论

Tokyo v6 在 2026-08-01 **实际产生了 zero-notional forward shadow 记录**。当天白天仍由
`city_probability_shadow_v1` 写 journal，之后 runtime contract migration 才把输出目录
切到 `city_probability_shadow_v2`。只检查 v2 会错误得到 Tokyo `0 evaluation / 0 intent`。

v1 原始 journal 中有 100 条 Tokyo evaluation（YES/NO 成对，即 50 个实际决策
checkpoint）和 4 个首次 `target_date × bracket × model` paper intent，但 `06:00–09:02
JST` 存在 official journal physical-shard coverage gap。最终 winning
bracket 为 35°C，因此 4 笔为 1 胜 3 负，胜率 `25%`；假设每笔 5 shares，成本
`$6.82245`、PnL `-$1.82245`、ROI `-26.71%`。实际 forward checkpoint 上 model
accuracy `92%`、Brier `0.06331`；同分母 market accuracy `100%`、Brier `0.01712`。

## 输出目录切换时间线

- Tokyo v1 evaluation：`2026-08-01T00:03:04Z` 至 `08:58:15Z`，即 Tokyo
  `09:03–17:58 JST`，覆盖当天主要评分窗口。
- v2 `evaluations.jsonl` 创建于 Beijing `2026-08-01 18:44:52`，即 Tokyo
  `19:44:52 JST`，已经晚于 Tokyo `18:00 JST` 评分截止。
- 所以 v2 当天只有仍处于日内窗口的 Helsinki 数据；Tokyo 当天数据完整留在 v1，
  没有迁移或聚合进 v2。
- v1 在 Tokyo 评分窗口内另有 254 个 error polls，其中 171 个发生于 `06:08–09:02
  JST`，根因是旧 consumer 按 target date 猜物理 shard，直到 UTC 0 点才找到
  `observations/2026-08-01/observations.jsonl`。其余主要是 one-sided book 或
  official/book bracket mismatch。poll 次数不是缺失 checkpoint 数，但证明 full-window
  coverage 不完整。v2 的 `InputCatalog` 已按 decision clock 搜索跨日 physical shards，
  修复了这类 target-date path guess。
- v2 后续出现的 61 条 Tokyo stale-book error 位于 `22:02–23:06 JST`，属于窗口外
  freshness-check 顺序问题，不代表白天 shadow 漏跑。当前 off-hours 修复版本
  `81a7ca8d46de` 已运行，定向测试 `10 passed`。

### 统一后的读取契约

不能把 v1 rows 直接追加进 v2：两者 schema 不兼容，v1 缺少的 loaded runtime identity
也不能事后伪造。当前本地修复是保留两份不可变 physical journal，但在 v2 config 建立唯一
`journal_catalog`，current v2 永远排第一、legacy v1 只读排第二。runtime 的 evaluation
和 position 去重都从这个 catalog 读取，日审计也只接受该统一入口。这样既不破坏原始
lineage，也不会再因只查 current 目录漏掉 migration-day 数据。代码与20项定向测试已
通过；production checkout `60f5e58c` 已于 Tokyo 凌晨窗口外重载，PID `43518`，首轮
`errors=0 / orders_submitted=0 / new_evaluations=0 / new_paper_intents=0`。

## Actual forward 结果

| 指标 | model | market |
|---|---:|---:|
| expression rows | 100 | 100 |
| unique decision checkpoints | 50 | 50 |
| accuracy | 92.00% | 100.00% |
| Brier | 0.06331 | 0.01712 |

这里的 accuracy 由大量 exact-bracket 负标签抬高；正确比较应看同分母 Brier。该日
model Brier 比 market 差 `+0.04619`，不能解释为模型胜过盘口。

| JST 决策时刻 | bracket / side | model P | market P | YES ask | edge after fee | 结果 |
|---|---|---:|---:|---:|---:|---|
| 11:42 | 32 YES | 6.64% | 2.10% | 3¢ | 3.49pp | 输 |
| 13:07 | 33 YES | 42.79% | 14.50% | 16¢ | 26.12pp | 输 |
| 14:10 | 34 YES | 51.73% | 23.50% | 29¢ | 21.70pp | 输 |
| 15:08 | 35 YES | 94.12% | 82.50% | 86¢ | 7.52pp | 赢 |

真实 paper intents 为 4；execution mode 为 `zero_notional_shadow`，真实 order/fill 均为
0。上述成本、PnL、ROI 是按 journal 的 ask、官方 fee 和固定 5 shares 计算的模拟结果。

## 为什么 4 笔只赢 1 笔

这不是 settlement label 写反。4 笔是在同一个 exact-bracket event 升温过程中依次买
`32/33/34/35 YES`；最终最高温是 35°C，因此它们结构上最多只能有一个赢家。第一版
“每档首次 edge”会把互斥档位连续加入组合，headline win rate 本来就不应当被理解成
4 次独立天气预测。

更关键的是概率层确实错得很有方向性。模型相对 market 给四档分别增加约
`+1.20/+1.48/+1.25/+1.22` logit，将 33 YES 从 14.5% 抬到 42.8%，34 YES 从
23.5% 抬到 51.7%。四笔 correction 全为正，主要来自：

- base weather head 对 current-stay 的估计过高：32/33/34/35 分别约
  `27.2%/84.8%/69.5%/96.1%`；
- residual 的 `weather_market_logit_gap` 系数继续信任该天气 head；
- 太阳高度与 remaining-to-18h 在持续升温日继续给正 correction；
- 模型是逐档 binary current-stay，不约束连续持有多个互斥 exact brackets 的组合风险。

因此第一天暴露的是“ongoing climb 下系统性低估 overshoot、连续追当前档”的结构错误，
不是简单的随机 1/4。单日不足以重新训练，但同分母 Brier 已明确判负；在 joint-ladder
coherence、overshoot hazard 与 position-aware portfolio 表达修复前，只保留
zero-notional telemetry，不新增事后价格或时钟 hard filter。

## 保存数据覆盖审计

另用当天保存的 JMA exact first-seen 与 direct active-ladder book 运行 deployed adapter
PIT replay，得到 60 个可评分 checkpoint、156 个 YES/NO expression rows、4 个首次
paper intent；4 个 intent 与 actual journal 的时刻、概率、价格逐项一致。replay 比 actual
多 10 个 checkpoint，是对已保存 capture cycles 的覆盖重放，不替换 actual-forward
分母。replay model/market Brier 为 `0.05247/0.01434`，方向与 actual 一致：市场更好。

## Gate 与链路状态

```text
significance=NA          # 只有 1 个 settled target date
baseline=FAIL            # actual 与 replay 同分母 Brier 均未打败 market
forward_runtime=PARTIAL  # v1 有实际结果，但06:00–09:02 JST缺official shard
decision_chain=PASS      # exact source → PIT book → model → edge → intent
exchange_chain=NA        # zero-notional，不覆盖 submit/queue/fill/slippage
conclusion=inconclusive_forward_day_1
deployment=zero-notional_only
journal_catalog_fix=PRODUCTION_PASS
```

因此数据与决策 shadow 链已经在 09:03 JST 后真实跑通，但首日没有覆盖完整评分窗口；也
不能说完整 live execution 已验收，后者尚未模拟 exchange submit、排队、成交和滑点，
更不应因为单日结果修改 live。

复现保存数据覆盖审计：

```bash
PYTHONPATH=/Users/deepsleep/projects/pm_agents_city_runtime_v2_tokyo_fix \
  /Users/deepsleep/projects/pm_agents_prod/.venv/bin/python \
  scripts/analysis/market_structure_edge/audit_tokyo_probability_shadow_day_v1.py \
  --config /Users/deepsleep/projects/pm_agents_city_runtime_v2_tokyo_fix/configs/weather/city_probability_shadow_v2.json \
  --target-date 2026-08-01 --winning-bracket 35 \
  --output docs/analysis/2026-08/generated/tokyo_probability_shadow_day_audit_v1/2026-08-01.json
```
