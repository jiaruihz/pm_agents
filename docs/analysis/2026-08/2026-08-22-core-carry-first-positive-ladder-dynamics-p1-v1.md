# Core Carry first-positive 与 ladder dynamics P1（2026-08-22）

## 结论

P1 已按冻结的 92 条 Core Carry signal 分母完成，但结论是 **机制仍未确认，不产生新 gate、模型或 live 变更**。

1. 92/92 trigger 可精确回连 `pre_live_scores`；61 条有同 artifact、可执行盘口的上一条 non-eligible checkpoint。严格反事实定义下，只有 4/61 是“旧 cost 下 edge 仍不为正、cost 下移后才转正”的 `quote_driven` candidate，4 条均有 settlement label、0 loss；其余 57 条有 6 loss。这个分组不能推翻 adverse-selection 假设，因为上一 checkpoint 最短也距 trigger 42.35 分钟，中位 59.27 分钟，0 条在 30 分钟内；Warsaw 与 Amsterdam 分别相隔 63.45/73.68 分钟。它是小时级诊断，不是触发前 tape 的因果识别。
2. REST full ladder 可在 prior/trigger 两端评分 59 条；按两端 snapshot lag 都不超过 7 分钟的 primary 只剩 31 条/12 target dates，其中 27 条已结算、3 loss。`Δq_up` 均值/中位数为 `-0.1561/-0.1102`，loss−win 点估 `+0.0490`，target-date block bootstrap 95% CI `[-0.1726,+0.3332]`，没有可用方向性证据。
3. 历史 WS epoch 覆盖 39 条 trigger，但 current token 当时实际被订阅只有 2 条，完整 YES ladder 订阅 0 条。P0 的 Core full-ladder demand 仍只在 worktree、尚未部署，所以不存在 clean frozen-forward slice，也无法验证 30–120 秒 `q_up / q1 / alpha1` 迁移、主动成交方向或 book reconstruction parity。

因此 P1 的有效收口是：**粗 checkpoint 没有支持“多数 first-positive 由 cost 下压单独创造”的说法，但证据分辨率不足，不能据此否定 Warsaw/Amsterdam 已由链上逐笔确认的接刀机制；动态 ladder 也尚未显示可交易区分度。下一步必须先形成 P0 forward tape，再做预注册的 P2 replay。**

## 冻结范围与数据身份

- signal denominator：`docs/analysis/2026-08/generated/decision_packet_v1/signal_ledger.csv`，92 rows，through `2026-08-20`，SHA256 `bc24db013813aabc8da05b1208764d16dad7162a6258e03ffeb6b4e471c0f761`。
- score prefix：5,065 complete JSONL rows，最后时间 `2026-08-22T04:53:13Z`，prefix SHA256 `f6c4f5b4a15dc86a6839be749709b525cdd0e83d18d80e0b42d8a3a1c43ebf63`。92 条 signal 之外的新 trigger 不进入分母。
- model lineage：逐行使用记录中的 artifact hash；3 个历史 artifact 从 immutable git object 恢复，当前 3 个从 config 读取。61 条可评分记录的重算误差最大为 `0`，edge 分解残差最大为 `1.11e-16`。
- settlement：canonical DB identity 为 `/Volumes/jrs/pm_agents/runtime/weather.db`、device `16777244`、inode `54444`；85/92 有 label（78 win、7 loss），Warsaw/Amsterdam 使用已有 external on-chain confirmed loss evidence，7 条显式 pending/unavailable。
- REST ladder：扫描 4,118 个 batch files，得到 674 个相关 city-date batches；只使用 checkpoint 当时或之前可见的 snapshot。
- WS：读取现有 subscription epochs 与 raw public frames；rollout metadata SHA256 `8b26ae0c1d4ff7c61201c7e3f772a78591ff6088a906968606154f36ef2b55df`。
- 机器产物：`/Volumes/jrs-archive/pm_agents/research/artifact_store/current_yes_core_carry_p1_stop_time_dynamics/run=signal_ledger_92_through_20260820/p1_result.json`，SHA256 `80bdd1b82ef7d835c1edf6275ac6ab238786e31c88e7ea9b8d397a97699075e2`。

## P1-A：first-positive edge 原因分解

### 预注册定义

对每条 trigger 取同 city/date、同 artifact 的最后一条 earlier non-eligible checkpoint，且要求 model probability、双边 current-token quote 与 full taker cost 都可用。固定顺序分解：

`edge change = market-feature contribution + weather/time contribution + taker-cost-drop contribution`

Primary `quote_driven` 定义为：

`trigger_edge > 0 AND p_trigger - prior_cost <= 0 AND trigger_cost < prior_cost`

这不是按结果挑阈值；其经济含义是保持 trigger probability 不变，若仍使用 prior executable cost 就不会过零。

### 双漏斗与结果

| 漏斗 | 数量 |
|---|---:|
| frozen signals | 92 |
| exact trigger match | 92 |
| informative prior | 61 |
| strict quote-driven candidates | 4 |
| settlement-labelled | 85 |

61 条粗分解中，market feature/weather-time/cost-drop 为正的数量分别为 `57/53/5`；按绝对贡献最大项分组为 market feature `17`、weather/time `3`、taker cost `41`。taker cost 在 41 条中绝对值最大，主要因为约一小时前低价 checkpoint 到 trigger 时 cost 已显著上升；其平均 contribution 为负，不代表触发前瞬时 quote 没有下压。另有 28/61 的 prior edge 本已为正、只是被 `outside_carry_market_mid_domain` 等资格条件挡住，更说明“上一 non-eligible checkpoint”不能等同于真实 first-positive crossing 的临近状态。

quote-driven 4 条的 labelled loss rate 是 `0/4`；non-quote-driven 是 `6/53=11.32%`。虽 target-date bootstrap 的点估方向不支持“quote-driven 更差”，但 4 个独立日期、且 prior cadence 全部超过 42 分钟，远不足以把这个结果解释成机制证伪。

## P1-B：`q_up / q1 / alpha1` 动态诊断

对完整 YES exact-bracket ladder 先按 spread 加权投影到 non-negative unit simplex；单边 rung 使用 spread `1` 降权。定义：

- `q_up`：current bracket 以上全部 probability mass；
- `q1`：紧邻上方一档 probability mass；
- `alpha1 = q1 / q_up`（`q_up=0` 时缺失）。

Primary 只纳入 prior 与 trigger 两端最新 PIT REST ladder lag 都不超过 7 分钟的记录。敏感性覆盖为：5m `30`、7m `31`、10m `35`、15m `42`。7m primary 的 31 条中：

| 指标 | 均值 | 中位数 |
|---|---:|---:|
| `Δq_up` | -0.1561 | -0.1102 |
| `Δq1` | -0.1137 | -0.0913 |
| `Δalpha1` | +0.0235 | 0.0000 |

3 个 primary loss 的 `Δq_up` 分别是 Busan `-0.3127`、Warsaw `+0.1318`、Amsterdam `-0.1650`，方向不一致。loss−win `Δq_up` 的 date-block CI 跨 0；当前样本不支持把 `q_up` 上升、下一档集中度或其简单组合直接变成 confirm/hedge gate。

REST 是约 5 分钟 snapshot，只能回答低频 ladder state 是否有粗关联，不能回答外部审阅提出的 30–120 秒主动卖出、相邻上档主动买入、冲击持续性与恢复速度。历史 WS 的 current-token coverage `2/92`、full-ladder coverage `0/92`，该层明确记为 blocked。

## Readiness 与后续执行合同

| 检查 | 状态 |
|---|---|
| PIT clocks / append-only score prefix | ready with historical gaps |
| exact model artifact replay | ready；重算误差 0 |
| canonical DB identity | route healthy；production overall health 另有 unrelated critical，未做 canonical-wide refresh |
| first-positive causal attribution | blocked：无 sub-minute pre-trigger tape |
| REST ladder diagnostic | ready only for 5–7m coarse diagnostic |
| WS full-ladder forward | blocked：P0 未部署，历史 0 full-ladder subscription |
| clean frozen forward | blocked：尚未形成 P0 后的新 target dates |

### 2026-08-23 production follow-up

P0 collector 已部署：Core release `24099162`、WS release `6d4676d0`，唯一 WS owner 使用 selector v7、Core shared-demand path 与 24-token atomic full-ladder budget。新 snapshot 35/35 有完整 ladder lineage；历史回填 96 行因缺 full-ladder tokens 全部显式 fail closed。12 分钟验收窗内没有新 first-positive，因此真实 Core demand、atomic resolution 与可冻结 forward target-date slice 仍为 0。上述表格中的“P0 未部署”是 2026-08-22 研究运行时状态；当前状态是 collector active、forward evidence pending。

随后为解决 `t<0` 不可识别问题，生产升级到 Core `c6d9027e` 与 WS `34eb44c7`：只对 frozen selector 中唯一 blocker 为 `non_positive_taker_ev` 的有效 candidate 声明 30 分钟 research-only WS demand，最多 8 个 current token，并只给最高 edge 且有完整 lineage 的候选申请 atomic full ladder。selector v8 按 P0→P1 与新鲜度分配 24-token budget，first-positive trigger 不会被旧 candidate window 饿死。部署时没有仍在有效期内的新 candidate，因此 active demand 仍为 0；这不是 coverage 成功样本，必须等待下一条 clean forward。共享 maker 同时改成 `10 taker + 5 shared maker`，staged 撤单确认后 pullback 才能 handoff，单 signal 最大 15 shares；这只修 exposure stacking，不代表 market-state router 已验证。

P2 不应立即拟合新模型。P0 部署后先冻结首批 forward slice，并预注册：

1. 以 trigger 为 `t=0`，需要 current token 至少 `t-15m..t+30m` 的连续 book/trade tape；若只能从 trigger 后开始 capture，则 first-positive 原因分解仍不可识别，必须在 candidate 阶段建立 bounded rolling pre-trigger coverage。
2. full ladder 至少覆盖 `t-5m..t+30m`，固定报告 `30/60/120/300/900/1800s` 的 `q_up/q1/alpha1`、spread、depth、主动成交与 reconstruction parity。
3. 固定 signal/evidence 双漏斗、target-date block、同一 92-row historical diagnostic 与新 forward 分开；不把 Warsaw/Amsterdam anchor 混入调参后验证。
4. 只有在 clean forward 上先证明机制方向与经济量级，再 replay candidate→core→confirm/hedge；当前不增加二次确认、保险腿或 live threshold。

## 可复现命令

```bash
.venv/bin/python scripts/analysis/reheat_risk/core_carry_p1_stop_time_dynamics.py
.venv/bin/python -m pytest -q tests/pmm_tests/test_core_carry_p1_stop_time_dynamics.py
```

本研究只新增 runner、focused tests、research artifact 与文档路由；没有部署、重启、下单、写 canonical 或改变 Core Carry selector。
