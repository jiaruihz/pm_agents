# reheat-risk Tail-NO Retail Diagnosis v0

Status: snapshot
Updated: 2026-06-11
Source of truth: no
Superseded by / Used by: WEATHER_DOCS_INDEX.md; analysis/reheat_risk.md

## 问题

用户提出的 reheat-risk 表达是：当天下午/傍晚，在「物理上不支持二次升温」的城市，
买入**高于当前 observed running max 的高温尾部 bracket 的 NO**，做一个
类 theta（收剩余不确定性溢价）的策略，并要求评估散户可行性。

注意这与仓库此前回测的两个表达不同：

| 表达 | bracket 相对 running max | 性质 |
|---|---|---|
| `below_running_max_buy_no`（已回测，负） | 低于 | 逻辑死档，纯结算基差 |
| `observed_bucket_buy_yes`（已回测，负） | 等于 | 集中买当前档 |
| **tail NO（本报告）** | **高于** | **真 theta：赌不再升温** |

## 数据与脚本

全部输入为已提交 artifacts，不依赖镜像，任何 checkout 可复现：

```bash
.venv/bin/python scripts/analysis/observed_max/research_m3_tail_no_diagnosis.py
```

产物：`docs/analysis/2026-06/generated/m3_tail_no_diagnosis_v0/`

| input | 覆盖 |
|---|---|
| settlement alignment v1 city-days | 1,403 valid single-winner city-days |
| observed max residual detail v1 | 49 城 × 18-21h × 2024-04-30..2026-06-10，150,203 rows |
| orderbook best-ask joined v1 | 2026-05-19..06-09，20/21h，417 quote rows |

Contract status：

```text
significance=NA（样本过小，无统计 gate）
baseline=official pm_history settlement
forward=NA
conclusion=negative（20/21h 窗口下 tail NO 无可执行正边际）
```

本报告禁止导出 live 动作。

## 发现 1：settlement mismatch 是按城市集中的站点错位，不是 rounding

C 市场 85.5% 的 round 对齐率拆开看是双峰分布：

- **36 城白名单 100% 对齐**（≥20 valid days，含全部 F 城除 Chicago）：
  Tokyo / Beijing / Shanghai / Madrid / Warsaw / Helsinki / Amsterdam /
  Munich / Singapore / NYC / LA / Miami ... 完整名单见 manifest。
- **错位集中在少数城市**：Shenzhen 7%、Jakarta 13%、Milan 22%、London 26%、
  KualaLumpur 33%、HongKong 44%、Paris 46%、PanamaCity 63%、Chicago(F) 64%。

错位方向每城系统化但**不稳定**：

| city | raw match | modal offset | 修正后 match | 月度漂移 |
|---|---:|---:|---:|---|
| Paris | 0.46 | +1 | 0.49 | 2026-05:+1 → 2026-06:0 |
| Milan | 0.19 | -1 | 0.33 | -1 → -2 |
| Shenzhen | 0.07 | +1 | 0.32 | +1 → +2 |
| HongKong | 0.30 | -1 | 0.48 | -1 → 0 |

**常数偏移修正不可行**（修正后仍只有 0.3-0.6）。日期错位假设同样排除
（D±1 shift 只解释 ~17% mismatch，接近随机）。结论：这些城市 Polymarket
官方结算用的是**另一个站点/数据源**，其逐日读数与我们的 WU/IEM ICAO 站
逐日不同。唯一修法是找到每城官方 resolution source 并重建 observed max。

## 发现 2：白名单内不存在 below-running-max 的"捡漏" NO

整个 orderbook 窗口内，below-running-max NO 卖单 **100% 出现在错位城市**
（Milan 8 / London 7 / Shenzhen 5 / Moscow 3），白名单城市 0 条。

v1 官方结算下这 23 笔 win rate 13%、ROI -12.6%：那些 0.05-0.15 的"便宜
NO"不是做市商挂漏了，而是盯着官方源的对手在卖**结算基差**——官方站读数
比 WU/IEM 低 1-2°C，该 bracket 在官方口径下还活着甚至大概率赢。
观察到的 1300% observed-payout ROI 全部是这个陷阱的镜像。

## 发现 3：物理精算表支持 theta 前提，但尾部不为零

`round(final) − round(running)` 穿档概率（白名单 36 城，27,565 city-days/小时）：

| local hour | P(jump≥1) | P(jump≥2) |
|---:|---:|---:|
| 18 | 1.23% | 0.29% |
| 19 | 0.80% | 0.22% |
| 20 | 0.60% | 0.17% |
| 21 | 0.45% | 0.10% |

20h 后按城市分层差异巨大（这就是「支持/不支持二次升温」的数据版）：

- **接近零穿档**：SaoPaulo / Chengdu / Chongqing / Wuhan / Beijing /
  TelAviv / CapeTown（0.00%），Singapore / Manila / Jeddah / Miami / LA /
  Austin / Denver（~0.13%）
- **危险城市**：Helsinki 3.6%、Amsterdam 3.4%、NYC 2.6%、Warsaw 1.7%
  （高纬/海洋性，夜间锋面平流可以抬最高温）

实例：Wellington 2026-06-04，20:00 running max 17.2°C → final 17.8°C，
官方 winner 18，正是夜间二次升温吃掉 theta 卖方。

## 发现 4：20/21h 尾部 NO 在官方结算下无可执行边际

对 417 条 20/21h 报价中全部「bracket > round(running max) 的 NO」叠加
官方 winner（36 条可结算，白名单 11 条）：

| scope | ask≤0.97 trades | win rate | official ROI |
|---|---:|---:|---:|
| whitelist | 5 | 40% | **-27.8%** |
| non-whitelist | 16 | 0% | -100% |

机制完全一致且残酷：

1. **便宜的尾部 NO，市场是对的**。Lucknow 2026-05-27 bracket 40 的 NO
   ask 0.007：我们的 running max 才 39.x，官方 winner = 40。市场在 20h
   已经领先于我们的 WU running max。
2. **有溢价（0.90-0.97）的尾部 NO，全部带真实二次升温风险**。
   Wellington bracket 18 NO @0.938 → 输。
3. 余下的 0.97-0.99 ask，对应 ~0.5-1% 真实穿档率 + spread，约为公允价。

**20/21h 的市场已经有效**：theta 溢价在这个时点要么不存在，要么是对
真实尾部风险的公允补偿。

## 发现 5：散户容量在 20/21h 窗口约等于零

22 天窗口 × 36 个白名单城市，全部 premium≥2c 的 theta 类报价（含 YES 侧）
top-of-book 合计：**名义 ~$211，最大总利润 ~$11**，其中还包含会输的
Wellington。即使方向有边际，绝对收益也是美分级。

## 结论

1. `tail NO theta` 的**物理前提成立**（白名单+干净城市 20h 后穿档率
   ≈0-0.1%），但 **20/21h 市场定价已经反映了它**——无错价、无容量。
2. 此前所有 observed-payout 正收益均为结算基差伪影，维持
   `settlement_blocked` 与「禁止 shadow/paper/live」不变。
3. 常数偏移不能修复错位城市，必须找官方 resolution source。

## 下一步（按优先级）

1. **官方源识别（解锁一切的前置）**：从 Polymarket 市场 rules 文本抓每城
   resolution source/station，逐城用 1,403 city-days 验证候选站
   round(max) 与 winner 的对齐率，要求 ~100%。
2. **把研究窗口前移到 14-17h**：本报告只覆盖 18-21h（residual detail 的
   采样窗口）。真正可能有溢价的时段是午后峰值刚过、市场还没收敛时。
   需要在有 wu_obs 镜像的机器重跑
   `research_m3_observed_max_residual.py`（扩 hours）并用全量 orderbook
   snapshot 重建 14-17h 报价（当前 joined 只筛了 20/21h）。
3. **角色互换评估**：taker 没空间不代表 maker 没空间。在零穿档城市
   20h 后**挂 YES 卖单/NO 买单提供流动性**收 spread，承担的尾部风险
   有精算表定价。这是数据已支持、还没评估的方向。
4. 若 14-17h 仍无边际：reheat-risk 作为独立策略关闭，把穿档精算表降级为现有
   策略的**风控/退出模块**（持仓的 bracket 被 running max 穿越后立即
   止损卖出，而不是等结算）。
