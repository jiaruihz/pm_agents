# Heat-Death 两条表达线 live 晋升判据预注册 v1

Status: current-reference（判据冻结文档;改判据必须新开 v2,不许原地改）
Date: 2026-07-15
Operational update: 2026-07-18（用户授权 H2 增加 5-share maker；只改 probe sizing/execution，不改晋升判据）
Scope: `current_yes_heat_death_physical_v1` 家族的两个入场 regime。判据在读取任何新 forward
结果之前冻结;之后的 forward 数据只做复核,不回来调这里的阈值。

## 0. 为什么要按入场价拆成两条线

同一个 heat-death 确认信号有两个经济上不同的交易头:

| Head | 定义 | 赚什么 | 当前证据状态 |
|---|---|---|---|
| **H1 late-carry** | 确认后以 ask >= 0.95 买 current YES(或 d1 NO) | 晚盘 carry premium,左尾一次亏损抹掉 ~20-50 笔 | 2026-07-15 用干净 single-runs peak clock 重建 factory 重跑([backtest](2026-07-14-current-yes-heat-death-physical-backtest-v1.md)):holdout strong 7 行/6 天 +5.8% CI [+1.2,+18.1]；严格 H1 paired 仅 5 行/5 天，两边全赢，current YES +1.70%、d1 NO +1.37%；significance=FAIL_LOW_SAMPLE，相对同价 base-fade 超额 CI 跨 0 |
| **H2 early dislocation** | 首次确认时 ask <= 0.93 买入(Busan 0.84 形态) | 市场 repricing 行情 | [early replay](2026-07-14-heat-death-early-event-replay-v1.md) 输入扩到 7/07-15，ROI 使用 7/07-14 八个结算日(anchor 剔除)：proxy 同分母 150 行 current YES +0.31%、d1 NO -1.31%；d1 多赢 2.67pp 但平均贵 4.24c，YES-d1 +1.63pp CI[+0.98,+2.37]。广州 forward fill @0.89 用户确认获胜，但 canonical settlement 待落库，仍不足晋升 |

0.93 < ask < 0.95 是预注册的隔离带:两边的主分析都不收这段,防止边界挑选。
tiny-live probe(`current_yes_heat_death_tiny_live_v1`,10 shares,<=3 单/UTC 日,max-ask 0.99)的
fill 按**成交时点的入场价 regime** 归属到 H1/H2,不按事后叙事归属。

## 1. 共同硬门(两条线都要过,任何一条不过不进入单线判据)

1. PIT 完整性:整个 forward 窗口内 shadow/probe 无已知 PIT 破坏;发现破坏则该窗口作废,时钟重启。
2. 数据链:`weather_clob_fill_coverage_gate.py` `gate_pass=true`;probe fill 全部进入
   `fact_trades`,首信号候选全部进入 shadow decision 分母。盘口/结算缺失记 coverage gap,不许当筛选。
3. 概率层先于交易层(ANALYSIS_CONTRACT 既有口径):在 shadow 全候选分母上,
   calibrated `P(win)` 相对 market implied 的 proper score(logloss/Brier)不劣于 market;
   拟合用 expanding window,forward 只复核。
4. 多重检验:每条线预注册一个 primary expression = **current YES**;d1 NO 只作 secondary
   diagnostic,不得因 d1 NO 偶然更好而事后换主表达。

## 2. H1 late-carry 晋升判据(冻结)

分母:shadow 13:00-17:00 base 确认、首次满足 ask >= 0.95 的 city-day expression 行(settled)。

- **样本门**:>= 30 settled 行、>= 12 个独立 target_date、>= 2 个 source region(不全是东亚)。
- **显著性门**:fee-adjusted ROI 的 target_date block bootstrap 95% CI 下界 > 0。
- **尾部门(本线特有,数学如下)**:入场 0.95-0.98 的保本亏损率约 1.9%-4.3%
  (breakeven `p_loss = 1 - cost`,cost = ask + 0.05*ask*(1-ask))。零亏损样本按 rule-of-three,
  95% 上界 ≈ 3/N。因此要求:**观察到亏损并且实测亏损率 binomial 95% 上界 < 该 cohort 平均保本率**,
  或 **>= 120 行零亏损**(3/120 = 2.5% < 0.97 入场保本率 2.85%)。30 行零亏损不够,不许拿去 live。
- **baseline 门**:物理 overlay 相对同价 plain base-fade 的 paired delta CI 若仍跨 0,
  则晋升的是**更简单的 base-fade 规则**,不带未证明的物理过滤;物理特征继续 shadow。
- 通过后动作:走 [weather-strategy-deploy] git-first,T1 上限 = 每 UTC 日总 notional <= $50,
  只 taker,单笔 <= 20 shares。

## 3. H2 early dislocation 晋升判据(冻结)

分母:shadow 首次确认(base 或 strong)时 direct quote pair 成功、ask <= 0.93 的 city-day
expression 行(settled)。probe 在该 regime 的真实 fill 优先计入。

- **样本门**:>= 25 settled 行、>= 10 个独立 target_date、>= 8 个城市。
- **显著性门**:按**记录到的可执行 ask**(不是 price proxy)的 fee-adjusted ROI,
  date block bootstrap 95% CI 下界 > 0。
- **机制门(本线特有)**:entry 后 60 分钟内该 token 中位有利变动 > 0
  (证明赚的是 repricing,不是又一个 carry);以及 entry 时点 top ask depth >= 10 shares
  的行占比 >= 70%(证明 0.8x 价位真实可成交,不是幽灵报价)。
- **反事实门**:同分母下 H2 行如果等到 ask >= 0.95 再进(即退化为 H1),paired delta 必须
  显示 early 入场更优;否则 early 线并入 H1,不单独 live。
- 通过后动作:同 H1 的 deploy 流程;T1 上限相同。

## 4. Probe 部署形态(2026-07-16 用户确认调整)

原单实例 probe(max-ask 0.99)在拆分前成交广州 `30 YES @ 0.89 x 10`,随后退役并拆分为两个独立归属的 head 实例
(git-first:runner/start/register 脚本同 commit 变更):

| 实例 | ask 带 | 其余参数 |
|---|---|---|
| `current_yes_heat_death_tiny_live_h1_late_carry_v1` | [0.95, 0.99] | 每个 city-day 共 10 shares：5 taker + 5 maker-first；maker 每 30s 按 fresh book 追到初始 ask 上限，3m 后满足价格/深度条件则 taker fallback；<=3 个 city-day opportunity/UTC 日；TTL 15m |
| `current_yes_heat_death_tiny_live_h2_early_dislocation_v1` | [0.50, 0.93] | 每个 city-day 共 10 shares：5 taker + 5 maker-first；maker 每 30s 按 fresh book 追价，3m 后仅在 ask<=首次 ask 且 depth>=5 时 taker fallback；<=3 个 city-day opportunity/UTC 日；taker depth>=5；TTL 15m |

- 0.93-0.95 buffer band 两头都不交易(预注册第 0 节)。
- H2 的 0.50 下限是无人值守 live probe 的资金安全边界:首确认后 ask 远低于信号隐含概率,
  大概率是 bracket/数据错配而非免费错价;此类行留给 shadow 记录,不用真钱验证。
- H1/H2 maker 首次在 best bid 上改善一 tick，之后每 30s 读取 fresh book；只在可挂价格提高时撤单重挂，撤单重挂次数不设上限，边界是 3 分钟 chase window 和首次触发 ask price cap。3 分钟后仍未成交时，仅当 fresh ask 不高于首次 ask 且 ask depth>=5，才撤 maker 并对剩余 5 shares taker fallback；否则不为成交而付出比首次直接 taker 更差的价格，原 maker 最迟由 15m TTL 撤销。partial fill 后剩余不足 CLOB 5-share minimum 时只撤不补 dust order。
- 2026-07-18 H2 maker 扩仓是用户授权的 execution probe 变更，不代表 H2 通过样本/显著性/机制/反事实晋升门；新增 maker fill 与 taker fill 必须保留同 signal 的 paired 血缘。
- H1/H2 共享 family city-day 去重：任一 head 首先出现 submitted order 后，另一 head 不再为同一 city-day 下单。
  日上限按 opportunity 而不是 child order 计数，因此 H1 的 5 taker + 5 maker 合计只占 1 次。
- 东亚快源盲窗(hko/jma/amos 未接入 running-high)会推迟首确认时点,对 H2 是入场价劣化;
  该修复属于数据层工作,不影响本判据。

调整原因与影响半径：2026-07-15 Munich 同一 `27 YES` 先由 H1 `10 @0.95`、后由 H2 `10 @0.93`
各成交一次。两单使用同一 14:55:50Z decision snapshot；根因是两个实例各自做 signal/city-day 去重，family 没有共享去重。
双 head 拆分后截至调整时仅 Munich 重叠，造成 1 笔额外 order/fill、10 shares、$9.30 submitted notional（fee 未计）。
修复后反事实为保留首次 H1 opportunity、阻止后续 H2 opportunity。

## 5. 失败与退出条件

- 任一线 forward 窗口内 fee-adjusted ROI 95% CI 上界 < 0(同分母、主表达):该线降级
  `rejected_for_expression`,保留数据与代码,停止该表达的 probe。
- 连续 30 个交易日样本门仍不满足(H2 可执行首信号行 < 25):结论是采集/市场结构问题,
  回数据层解决,不放宽判据。
- probe 出现任何一次超出 H1 10 shares、H2 10 shares或任一 head 每 UTC 日 3 个 city-day opportunity 的执行：
  立即暂停 probe，先审计执行链。

## 6. 当前状态快照(2026-07-16,写入时点)

- shadow v1:运行中,zero-notional,per-snapshot 决策分母 + 候选 token 聚焦盘口刷新(今日 pair 1/1 成功)。
- tiny-live probe:已拆为 H1/H2 双实例(见 §4);拆分前原实例有广州 `30 YES @ 0.89 x 10` 一笔成交。原始 instance 血缘不改写,绩效分析按成交价归入 H2 regime。
- H1 历史证据:干净 PIT 重跑后 holdout 只剩 7 行,carry 点估为正但低于样本地板;物理增量在干净数据上仍不存在;
  严格 H1 价格段 paired 只有 5 行/5 天，两边全赢，current YES 因平均入场更便宜而略优；旧 15 行证据作废。
- canonical 注册:strategy definition、H1/H2 config 与两个 enabled instance 均已落库；H1 新实例当前 0 order/0 fill。
- H2 历史证据:8 个结算日的 proxy current YES 只有 +0.31%，CI 跨 0；d1 NO 的额外 overshoot 胜率不足覆盖溢价。2026-07-18 起用户授权 probe 改为 5 taker + 5 maker，研究 verdict 不变。
- H2 forward:广州 2026-07-15 先在 14:17 PIT strong signal 显示 current 30 indicative 0.84，legacy probe 于 14:24 成交 `30 YES @0.89 x10`，用户确认最终获胜；fact row 已有 fill，但官方/canonical settlement 尚未写入，因此暂不计 realized PnL。原 instance 血缘不改写，分析归 H2 regime。
