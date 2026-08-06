# D-1 nonlinear residual 与 forecast-repricing 研究 v1

weather-only:
significance=FAIL_on_reconstructed_OOF
calibration=legacy_robust_tail_reference
pooled_baseline=retained_negative_control
forward=clean_exact_run_accumulating

market residual:
baseline=contemporaneous_normalized_market
forward=V05_to_V10_failed_to_improve_on_17_date_fixed_denominator
execution=not_run_no_probability_or_repricing_gate

production:
live_action=none
orders_changed=0

## 结论

V04 不是最终模型，只是线性 market-offset baseline。本轮已把候选扩到 V10：piecewise GAM、revision/spread gate、固定 nonlinear hidden layer、hybrid nonlinear、shallow gradient boosting 和 interaction gradient boosting。

在当前可直接读取的 168 states / 17 OOF dates 上：

- market logloss=1.560207；V04=1.560129，仅改善约 0.000077，CI 仍跨 0；
- V06 曾在 5 个 states 偏离 market 超过 2pp，最大 8.56pp，但 logloss=1.561013，反而差约 0.000806；
- V05/V07/V08 被 inner OOF 全部收缩回 market；
- V09/V10 是真正的 gradient-boosted nonlinear interaction model，但 17/17 folds 都选择 `alpha=0`，严格回到 market。

这否定的是“用同一 reconstructed checkpoint 的 forecast level/revision 去修最终 settlement 概率”这一表达，不是否定天气交易。能制造大偏离的 V06 已经实际验证：没有更好的事件信息时，大偏离只会放大错误。

## 盈利目标的转向

下一主目标不是继续在 terminal probability 上堆模型，而是：

```text
真实 provider run first-seen
  → consensus / assigned forecast revision
  → 当时尚未完全更新的完整 ladder
  → 5 / 10 / 30 / 60 / 90m signed market repricing
  → direct ask→future bid 与 maker queue 分开验证
```

已经把 `directional_repricing_summary` 接入稳定 runner：它分别统计 consensus/assigned revision 与 immediate、5/10/30/60/90m ladder mean shift 的方向一致率、平均 directional shift 和每 1°F revision 的市场位移。该目标直接对应 LMVM 短持策略，不再拿最终结算 logloss 代替短期可交易 markout。

旧 LMVM archive 的 6,357 snapshots / 12,237 paired update events 只能作反例：旧 `model_prob` 明显坏于 market（logloss 2.7019 vs 1.4148）；D-1 late holdout 60m taker ask→future bid ROI=-13.74%，最高 innovation decile 假设 maker entry、taker exit 后仍约 -1.34%。因此不能复用旧 probability，也不能用“假设挂到 maker”挽救负 taker edge。

## 扩展历史重建

本轮本地重新完成：

- 4,086 snapshot jobs / 56 target dates / 48 cities；
- 17,750 forecast rows，五模型各 3,550；
- 2026-06-11..16 的 536 jobs 明确记 `known unavailable`，没有拿更旧 daily cache 或估计 run 代替；
- Gamma settlement 文件 3,036 个，另 84 个 event `not_found` 作为 coverage gap。

数量与此前 JRS 上的 expanded forecast artifact 一致。当前无法在本地重物化 571-state full-ladder denominator，原因不是 forecast 或 settlement 缺失，而是历史逐档 snapshot 只在 JRS，当前 canonical tmux permission host 的 read/write probe 失败；直接读返回 `Operation not permitted`。已有 expanded V04 结果仍有效：571 OOF states / 39 dates，market=1.460752、V04=1.459326、delta=-0.001426，95% CI [-0.004173,+0.001028]。

## 状态与动作

- V04：保留为 baseline，不 freeze、不 shadow；
- V05–V10：保留为 negative controls；
- terminal probability 路线：不再盲调参数，等待 clean exact-run forward 后再训练；
- repricing 路线：runner 已实现连续 signed markout；JRS 恢复后直接重跑当前 exact-run events；
- execution：只有 signed repricing 在 target-date OOF/forward 为正，才加入 direct ask、fee、slippage、depth；maker queue 单独 A/B。

测试：相关 10 tests 全部通过。没有修改 live strategy、city pool、sizing、execution policy 或订单。

## 市场基准的正确含义

同一 checkpoint 的 weather probability `p_t` 与 market distribution `q_t` 并不是互相拟合，而是分别对最终 winner `Y` 评分：

```text
weather logloss = -log p_t(Y)
market logloss  = -log q_t(Y)
delta           = weather logloss - market logloss
```

因此，市场当时定价错误并不会把 weather 模型判错；只要 weather 给最终赢家的概率更合理，它就会得到更低的 loss。V05–V10 失败的原因不是“偏离市场”，而是这些偏离在后来的真实 winner 上没有稳定改善。市场是同时间、同信息集的强基线，settlement 才是概率准确度的真值。

短持 repricing 是另一项任务，不能与 terminal probability 混算。该任务使用事件对齐的四个时点：

```text
first-seen 前最后一份完整 ladder = pre-event market state
first-seen 后第一份新鲜完整 ladder = entry / immediate response
+5/+10/+30/+60/+90m ladder          = future repricing markout
最终 settlement                     = terminal accuracy label
```

未来市场也不是真值，它只回答“其他参与者随后是否向 forecast revision 的方向调整”；是否真有盈利，还必须使用 entry ask、future bid、fee、slippage 和 depth 单独验收。

## 2026-08-06 首轮 repricing smoke

当前最后一份可读的 exact-run artifact 只有 2026-08-05 一个 target date、68 个 revision rows；其中 34 个是 existing-run bootstrap，34 个是 partial batch 完成，尚无 `forward_new_complete_run`。因此只作机制 smoke，不作 alpha 结论：

| horizon | scoreable events | revision 与 ladder shift 同向率 | mean signed rung shift |
|---|---:|---:|---:|
| immediate | 13 | 69.23% | +0.02004 |
| 5m | 16 | 0.00% | 0.00000 |
| 10m | 16 | 0.00% | 0.00000 |
| 30m | 14 | 21.43% | -0.00251 |
| 60m | 14 | 42.86% | -0.00594 |
| 90m | 14 | 57.14% | -0.00764 |

immediate 的正方向反应可能是真实首跳，也可能混有同批采集/partial-completion timing；5/10m 完全不动显示现有 book cadence 或 freshness 仍不足；30–90m 没有稳定延续。当前结果不支持交易，但支持继续积累真正的 new-run first-seen forward 后再判断。

2026-08-06 当时生产检查为 `CRITICAL`：canonical JRS permission host 的真实 read/write probe 失败，forecast-run collector 与 full-ladder feed stale，无法生成新的 clean forward event。该状态已由下方 2026-08-07 迁移后审计取代。

## 2026-08-07 迁移后重跑与根因修复

迁移后的 production manifest 已恢复 `healthy`，canonical DB 解析为 `/Volumes/jrs/pm_agents/runtime/weather.db`（device `16777247`、inode `54444`）。研究 runner 不再把单一热盘目录当作完整历史：它从 production contract 同时解析 hot 与 archive snapshot roots。旧 hot-only 路由只读到 68 个 checkpoint；修复后同一研究分母恢复到 2,243 个，即旧结果漏掉 2,175 个（97.0%）。

collector 原始 append-only journal 的实际污染窗口为 `2026-08-05T07:15:57.703070Z..2026-08-06T16:55:51.268043Z`：

- 56,236 raw rows 折叠为 3,454 个 provider-run keys；3,420 个多次投递 run 的 `first_seen_at_utc` 全部随轮询漂移，共 52,782 条重复 delivery；
- 1,840 条被标为 same-run content revision 的记录全部 `content_revision_delta_f=0`；根因是把含动态 provider 元数据的 raw payload hash 当作 forecast content identity；
- 旧 5m/10m markout 分别把 317/316 个事件当作 scoreable；修复“post checkpoint 晚于 horizon 仍自比”后变成 0/7，即 5m 有317个、10m 有309个伪 scoreable；
- 该 collector 只产研究 forecast rows，没有生成 SignalCandidate、TradeIntent、order 或 fill，因此交易影响为 0 单。

v3 合同把 `provider-run first_seen` 与 `same-run content first_seen` 分开；逻辑 content hash 只覆盖 target-date 时间温度序列，raw payload hash 继续 append-only 保存。旧 journal 不重写，只用每个 run 的 earliest-observed clock 进入 development；只有部署 v3 后新产生的 `collector_exact` rows 才能进入 formal forward。

真实 one-shot sample 已用 `icon_seamless` 同一 run 连续请求两次验证：D-1/D-2 共 4 rows，run first-seen 与 content first-seen 均稳定，两个不同 raw payload hashes 只产生一个 logical content hash，伪 content revision=0。GFS 同次探针由 provider 返回 HTTP 400，已作为结构化 source blocker 保留，未冒充成功。

迁移后 legacy-development 分母为 34 城、3 target dates、1,224 个 D-1 provider-run transitions；其中 262 个落在 D-1 18–24 主窗口。市场证据为 2,243 checkpoints、452 complete；immediate/5m/10m/30m/60m/90m scoreable 分别为259/0/7/156/210/205。`model_revision_f` 的 immediate 同向率为59.0%（249 events/3 dates），30m 49.7%，60m 53.5%，90m 61.2%；日期只有2–3天且时钟只是 legacy earliest-observed，所以结论仍是 `inconclusive`，不能计算或宣称交易 alpha。

下一步不是拿未来 market 当“天气真值”：最终 settlement 继续裁决概率准确度；future 5–90m ladder 只检验 forecast revision 是否领先市场 repricing；只有 formal forward 的 signed repricing 成立后，才用 entry ask→future bid、official fee、slippage 与 depth 检验能否赚钱。当前 `live_action=none`、`orders_changed=0`；collector v3 代码与测试已完成，生产重启仍需显式部署确认。

### 盘口变化主口径修正

raw provider-event 不能直接当独立盘口变化：259 条 immediate scoreable event rows 只对应 134 个 pre→post book transitions；32 个 transition 前出现多条 provider updates，最多8条，24个 transition 同时包含上调和下调。runner 已改为在两个盘口 checkpoint 之间汇总 rolling consensus mean 的净 revision，并将同一次盘口变化只评分一次。

去除净 revision=0 的 interval 后，legacy development 的独立盘口变化为：

| horizon | event rows | independent transitions | single-event | conflicting directions | dates | direction agreement | mean signed rung shift |
|---|---:|---:|---:|---:|---:|---:|---:|
| immediate | 246 | 128 | 97 | 23 | 3 | 59.38% | +0.01661 |
| 5m | 0 | 0 | 0 | 0 | 0 | NA | NA |
| 10m | 6 | 6 | 6 | 0 | 1 | 33.33% | -0.02762 |
| 30m | 145 | 91 | 79 | 7 | 3 | 50.55% | +0.00310 |
| 60m | 199 | 123 | 104 | 13 | 2 | 53.66% | +0.00661 |
| 90m | 193 | 111 | 92 | 14 | 2 | 62.16% | +0.01736 |

这里最重要的不是90m点估为正，而是时钟覆盖：事件后的下一份 book checkpoint 延迟中位28.8分钟，book真正 `available_at` 延迟中位43.0分钟；1,224个事件中0个在5分钟内可用、0个在10分钟内可用，只有34个在30分钟内可用。因此当前 archive 无法回答“forecast 到来后5–10分钟能否抢先”，30–90m点估也只有2–3个日期，不能视为 alpha。正式研究必须先部署v3 collector，让 forecast first-seen 与更高频完整 ladder 同时运行；然后按独立book interval评估，最后再接 ask→future bid。
