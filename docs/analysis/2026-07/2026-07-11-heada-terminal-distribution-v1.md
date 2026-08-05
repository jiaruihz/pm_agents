# HeadA 终值分布 kernel v1 — 结果与方向更正

> authored_at_bj: `2026-07-11` · status: `research / 阴性主结论，方向更正`
> 起点：[终值分布重建设计](2026-07-11-low-price-yes-heada-terminal-distribution-rebuild-design-v1.md)
> （主轴：把静态点估 p_yes 换成物理终值分布）。
> 代码：kernel `src/strategies/weather_edge_v1/tools/heada_terminal_distribution.py` ·
> 验证 `scripts/analysis/forecast_quality/research_heada_terminal_distribution_v1.py`
> 生成 `generated/heada_terminal_distribution_v1/scored_rows.csv` · 指标 `2026-07-11-heada-terminal-distribution-v1.json`

## 结论（先给动作）

**证伪了本设计的核心前提**：造一个更好的**独立**终值分布不是正确目标——
**市场报价对这些尾部的定价比我们任何模型都准**（含新建的物理 kernel）。
`edge = model_p_yes − market` 是模型高估产生的噪声，不是模型发现的价值。

- **动作 1**：暂停「独立分布 → edge → 选票 / sizing」这条线。现行 `edge≥0.2` 选票信号在纠正后是
  在**和一个更强的预报者（市场）对赌**，方向逆选择。
- **动作 2**：若要继续找边，只有两条可辩护路径——(a) **market-anchored 残差**：从市场隐含分布出发
  （它是当前最好的先验），只在**前向验证过的条件切片**（source-quality / mechanism_good_source /
  path-state）里偏离市场；(b) 接受这是**市场公允的薄凸性彩票**，只做风险/sizing 纪律，不追概率 alpha。
- **动作 3**：新 kernel 保留为**诊断工具 + market-anchored 方向的 base 先验之一**，不作为 live 选择器
  （它排序力比现行 baseline 还差，见下）。

## 证据

### 1. 校准：kernel 略优于 baseline，但两者都远差于常数

全候选宇宙 `base_rows.csv`（476 行，`raw_dist_br ∈ [−2.56,+2.56]` 跨 below-forecast 到 far-tail，
base win rate 13.7%）：

| 模型 | Brier↓ | logloss↓ | ECE↓ | mean_pred |
|---|---|---|---|---|
| PIT-bias kernel | 0.1890 | 0.5643 | 0.2343 | 0.362 |
| 现行 `model_p_yes` | 0.1941 | 0.5832 | 0.2635 | 0.400 |
| **常数 base-rate(0.137)** | **0.1179** | — | — | 0.137 |

kernel 绝对校准比 baseline 稍好，但**「永远预测 13.7%」的常数预测器 Brier 0.118 碾压两者**——
说明两个模型的概率都是**系统性高估的净噪声**（mean_pred 0.36/0.40 vs 实际 0.137）。
reliability 曲线上，两模型的实际命中在 predicted 0.1→0.7 全程**平在 ~13-15%**，概率无判别力。

### 2. 排序力（AUC）：市场碾压所有模型

- **选择样本**（476，已 edge≥0.2/dist>0 过滤）：kernel **0.454**（比随机差）、baseline 0.550、
  **市场 ask 0.653**、「便宜票更易中」0.347（→ 越贵越易中，买便宜不是边）。
- **大清洁宇宙**（`fact_signal_candidates` 3,239 个已结算低价 3–20c YES，winners 9.3%）：
  - AUC(`model_p_yes`) = **0.631**
  - AUC(`market_yes_price`) = **0.686** ← 市场更好
  - 两者都 >0.5（模型有信号），但**市场支配模型**。新 kernel 在选择样本上 0.454，不进入大宇宙评测。

### 3. edge 是幻觉：实际命中贴市场价、不贴模型

大宇宙按 `edge = model − market` 分桶：

```text
edge 桶           n     实际命中   avg市场价  avg模型p
[-1.0,+0.1)     1663    7.9%      0.091     0.131
[+0.1,+0.2)      838    9.1%      0.100     0.246
[+0.2,+0.3)      473   14.2%      0.092     0.336
[+0.3,+1.0)      265    9.8%      0.084     0.483
```

**每一桶实际命中 ≈ 市场价，且远低于模型 p。** edge 越大 = 模型越高估，实际命中不跟模型跟市场。
`[+0.3,+1.0)` 桶最刺眼：模型 48% / 市场 8.4% / 真相 9.8%——市场对，模型 5x 高估。
这从概率层坐实审计 F2（model p 越高实际越低）与 F3（被 gate 挡=市场已 reprice=赢家）。

### 4. kernel 自身的具体失效：低估 overshoot

hot 切片（dist>0, n=333）kernel 三态 vs 经验（overshoot-exit 文档）：

```text
              below   win(exact)  overshoot
kernel        0.53     0.32        0.15
经验(文档)     0.48     0.15        0.37
```

kernel 把 P(exact win) 高估到 0.32（实际 0.15），overshoot 低估到 0.15（实际 0.37）——
**as-of PIT bias 分位数刻画的日内实现 spread 太窄**：真实终值上尾（冲过档）远比历史 bias p90 暗示的胖。
这说明即便走 market-anchored 路线，分布 width 也必须 regime/path 条件化（设计 §4.2），静态 bias 矩不够。

## 与设计的对账（更正）

- 设计主轴「重建独立终值分布修 F2/F3/F4」——**F2/F3/F4 的病根不是分布不够好，是我们在和一个更准的市场对赌**。
  独立分布再精，也先天落后市场。设计 Phase 1 的诊断出口因此得到一个**决定性阴性**：不复用、不自建独立分布当选择器。
- **设计需改向**：Phase 2/3 从「给独立分布加 conditioning + 真边 sizing」改为
  「market-anchored 残差 + 前向验证的条件信任门」。kernel/物理分布退为 base 先验与诊断，不是 live 选择器。
- 已有研究一致：`mechanism_good_source`（forecast-error-direction）与 source-quality 都指向
  「只在特定条件下才该偏离基线」，而非「全局用模型 p 选票」——与本结论合流。

## 局限

- base win rate 低（9–14%），winners 少（大宇宙 301、hot 切片 50），单桶差异含 variance；
  结论用 AUC/校准/分桶三重印证而非单点，但 market-anchored 条件门仍需**真前向**验证，不可用本观测窗定 live。
- `base_rows.csv` 是 HeadA 候选（受 model_p_yes 选择影响），故其上 model AUC 被 range-restriction 压低；
  大宇宙（未受 HeadA 选择）的 0.631/0.686 是更干净口径，市场支配结论在两口径下都成立。
- kernel 无拟合参数（PIT 矩），不存在过拟合；其 shadow 化不需 train/test 切分。
