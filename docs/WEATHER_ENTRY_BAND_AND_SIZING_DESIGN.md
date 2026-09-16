# Weather 策略 入场区间 × 仓位 Sizing 设计

Status: design-draft
Updated: 2026-06-09 metadata pass; preserve content dates below
Source of truth: no
Superseded by / Used by: WEATHER_DOCS_INDEX.md; draft/design reference, not current production fact

> 主题研究文档（功能待实施）。把「入场价带调参」和「仓位 sizing」作为一个整体设计。
> 证据基础（时间点快照）：
> - [2026-05-30-performance-entry-band-research.md](archive/analysis/2026-05/2026-05-30-performance-entry-band-research.md)（side×价位桶 EV 结构）
> - [2026-05-30-performance-sizing-and-band-distribution.md](archive/analysis/2026-05/2026-05-30-performance-sizing-and-band-distribution.md)（**收益分布 + 反过拟合扫描**，§7 结论以此为准）
> 可复跑脚本：`scripts/analysis/sizing_entry_band/weather_entry_band_research.py`、`scripts/analysis/sizing_entry_band/weather_sizing_band_study.py`
> 口径来源：[WEATHER_ANALYSIS_CONTRACT.md](WEATHER_ANALYSIS_CONTRACT.md)（PnL 唯一源 `fact_trades.pnl_usd_at_fill`）

## 0. 当前状态与问题

- **现状 sizing**：等额 $5/笔，不分 side、不分价位、不看 edge。
- **现状入场带**：统一 `0.25-0.75`（`fact_trades.entry_price_window`），不分 side。
- **问题**：把统一价带当成唯一闸门，导致 live_real 整体打平（200 笔 / 成本 $696.9 / PnL ≈ -$1.6 / ROI ≈ -0.2%）。真正的 alpha 来自 side×价位的结构,被等额 sizing 抹平了。

## 1. 三层证据（决定三个 sizing 维度）

> 数据快照：`runtime/weather.db`（mtime 2026-05-29 19:45 +08:00，未重新同步 N100）。live 结论用 `live_real`，paper 仅作大样本结构对照（时间窗与城市池均不重叠，不可相加）。

### 1.1 方向不对称：BUY_NO >> BUY_YES

同价位下 BUY_NO 普遍强于 BUY_YES。BUY_YES 仅在 [25-40) 边际正（paper +13% / live +8%），其余区间弱或负（[10-25) paper -23%）。
→ **决定「做不做 / 基础档位」**。

### 1.2 价位桶 EV（Kelly 验证）

| side × 桶 | paper ROI | 候选 win | 按桶中点 full Kelly | 结论 |
|---|---|---|---|---|
| BUY_NO [40-55) | +34% | 0.54 | ~37% 本金 | 核心 +EV |
| BUY_NO [55-70) | +17% | 0.69 | ~31% 本金 | 核心 +EV |
| BUY_NO [70-85) | -0% | 0.75 | **负**（-EV） | 死钱,降仓 |
| BUY_NO [85-100] | +4% | 0.80 | 负 | 关 |
| BUY_YES [25-40) | +13% | 0.36 | ~0% | 试探小仓 |

> full Kelly 仅用于判方向与相对强弱；**禁止当成实际仓位**（单仓 30%+ 本金不可接受）。实际取重度分数 Kelly（≈1/10）。
→ **决定「主仓 / 降仓 / 关」**。

### 1.3 模型 edge 校准

`fact_trades.abs_edge` 在大样本上单调（paper）：

| abs_edge | paper ROI | paper win |
|---|---|---|
| [10-15%) | +2% | 0.53 |
| [15-20%) | +4% | 0.54 |
| [20-30%) | +10% | 0.61 |
| [30%+) | **+45%** | 0.66 |

`abs_edge < 10%` 区间在 paper/live 都没有回报地板。**live 小样本上 edge 不单调（噪声）**,故 edge 缩放为次级、可关、需先 shadow 验证。
→ **决定「同档位内的微调」**。

## 2. Sizing 设计：三层乘数 + 硬上限

锚定现在的 $5 为基准单位 `U`，最终单笔 = `U × 档位乘数 × edge乘数`，再过硬上限。

### 第一层：side × 价位桶档位（主乘数）

| side × 桶 | 档位 | 乘数 | 单笔 $（U=5） |
|---|---|---|---|
| BUY_NO [40-55) | 核心 A | 1.5× | 7.5 |
| BUY_NO [55-70) | 核心 A | 1.5× | 7.5 |
| BUY_NO [25-40) | 核心 B | 1.0× | 5 |
| BUY_NO [70-85) | 降仓 | 0.4× | 2 |
| BUY_NO [85-100] | 关 | 0 | — |
| BUY_YES [25-40) | 试探 | 0.5× | 2.5 |
| BUY_YES 其他 | 关 | 0 | — |
| 低价 YES [05-20) | 彩票（可选,独立预算） | 封顶 $1 | 1 |

### 第二层：edge 缩放（次乘数,默认先 shadow）

- `abs_edge < 0.10` → **不下单**
- `0.10–0.20` → ×1.0
- `0.20–0.30` → ×1.25
- `≥0.30` → ×1.5

### 第三层：硬上限（不可越过）

- 单笔 ≤ **$12**（≈2.4×U；核心 1.5 × edge 1.5 ≈ $11 已压在线内）。
- **单 city-day 总 notional 上限**（关键,见 §4 风险）：建议 ≤ $20。
- 每日总 notional 上限（按 §3 资金参数定）。
- 单仓 ≤ 本金的 2–3%（≈1/10 Kelly,对比 full Kelly 30%）。

## 3. 待定资金参数（需用户拍板）

相对乘数已由数据确定,绝对上限需以下输入:

| 参数 | 用途 | 当前值 |
|---|---|---|
| 总交易本金 | 定 2–3% 单仓上限绝对值 | 待定 |
| 每日预算 / 同时最多几个 city-day | 定每日总 notional 上限 | 待定 |

定下后可把乘数表落成「city-day → 具体下单金额」配置,并定位 N100 下单脚本 sizing 改点。

## 4. 预期收益与残余风险

### 同样本回测（强假设,非未来预测）

把建议权重套回同一批已成交样本（脚本 `STRATEGY COMPARE` 段）:

| 样本 | 方案 | 成本 | PnL | ROI |
|---|---|---|---|---|
| paper | 现状 | $5271 | +$599 | +11.4% |
| paper | 建议(过滤/降size) | $3740 | +$590 | +15.8% |
| paper | 建议+省下资金回投核心 | $5271 | +$908 | +17.2% |
| live_real | 现状 | $697 | -$1.5 | -0.2% |
| live_real | 建议(过滤/降size) | $540 | -$0.5 | -0.1% |
| live_real | 建议+回投核心 | $697 | -$8.0 | -1.1% |

**读法**：结构样本(paper)上约 +4~6pp ROI / 同资金多赚约 50%（资金效率 +40%）。**但近期 live 上几乎无改善甚至略差** —— 因为 5 月那笔亏损是核心桶 BUY_NO [55-70) 在 5/27–28 两天的方差,而该桶建议保留,加仓反而放大。这套改法买的是「长期资金效率 + 方向集中」,不是「救坏 streak」。

### 残余风险

- **加仓 = 放大同源方差**：核心加仓会放大 5/27–28 那类集中亏损 → 必须配单 city-day 上限,且核心加仓先 shadow。
- **样本量**：live_real settled 仅 200 笔,bucket×side 后多格 n<30;paper 大但时间窗/池与 live 不重叠。
- **edge 缩放未在 live 复现**：第二层默认 shadow,确认后再放开。
- **回投核心假设线性吸收 size**,忽略冲击成本。
- **数据未同步**：用 5/29 缓存。落地前必须 `sync_weather_remote.sh` + 重建底表复跑。

## 5. 落地节奏

1. **立即可上(纯减风险,不加方差)**：BUY_NO [70-85)→0.4×、[85-100]→0、弱 BUY_YES→0、`abs_edge<10%`→不下单。
2. **核心加仓(1.0×→1.5×)+ edge 缩放**：先 shadow / 小步,live 多 1–2 周确认 paper 结构在 live 成立后再放开。
3. 第一层(side×桶)先上,第二层(edge)后上。

## 6. 后续 TODO

- [ ] 用户给资金参数(§3),落「city-day → 金额」配置表。
- [ ] 定位并改 N100 下单脚本 sizing 入口。
- [ ] 同步 N100 + 重建底表,在更长样本上复跑 `STRATEGY COMPARE` 确认。
- [ ] 上线第 1 步(减风险),shadow 第 2 步(加仓)。
- [ ] 1–2 周后用新 live 数据复盘 edge 缩放是否在 live 复现。
- [ ] 排查 execution size 一致性(见 §7：实成 $3.48≠$5、亏损单偏大)。

## 7. 分布研究修订结论（反过拟合，覆盖前文乐观处）

> 来源：[2026-05-30-performance-sizing-and-band-distribution.md](archive/analysis/2026-05/2026-05-30-performance-sizing-and-band-distribution.md)。
> 方法：sizing 权重从 paper 推、live 上做样本外检验；band 整片扫描；日级 bootstrap CI。
> **以下结论优先级高于 §2 的具体乘数**——§2 是 EV 方向，§7 是稳健性裁剪。

1. **入场带（两个 grain 交叉，机会全集优先）**：
   - **BUY_NO 上界 0.75 → ~0.65**：成交样本(fact_trades)与机会全集(fact_signal_candidates)都指向收紧；**机会全集定位最优在 0.65**，成交样本显示的 0.60 是「成交选择偏差」(砍 0.60 会丢掉优质的 0.60–0.65 NO)。强稳健。
   - **BUY_NO 下界 ~0.25 → ~0.35–0.40（中等置信）**：机会全集显示下界抬高单调改善(NO 太便宜=YES 太贵时机会差)，但成交样本看不出、且本表无 ROI、win_rate 随价升有机械成分 → 小步抬 + shadow 验证。
   - **BUY_YES 不是 1D 价带，是 edge 驱动的二维门**（2026-05-30 重研，推翻之前「[0.25–0.30) 窄带」——那是桶边界 + 0.25 下限挤堆的假结构；滑窗连续曲线在 0.25 无断点）：
     - 先卡 `abs_edge ≥ ~20%`（<20% edge 整体 -4%，白做；edge 20-30%→+15%、30-45%→+34%、45%+→+85%，win 率几乎不随 edge 变 ~23%，ROI 随 edge 单调升）。
     - 价位用**宽带 ~0.20–0.45**（两表稳健重叠 0.22–0.33，成交样本到 0.45 仍正），避开 0.07–0.20 死区(低 edge 时 -21~-57%)与 0.47+。
     - **低价 YES(<0.20)只在高 edge(≥30%)做**（彩票尾本质 = 便宜 + 大 edge，不是便宜就买）。
   - 别追 live 的 (0.30,0.60) +20% 窄尖峰(n≈60，过拟合诱饵)。

2. **差异化 sizing 是「杠杆」不是「Sharpe 改进」**。EV-tier 把 live ROI +8%→+15% 但 Sharpe 不变(0.37→0.38)，回撤同比例放大。→ 按**风险预算**定档位上限，别指望风险调整后白赚。

3. **拒绝 Kelly / 过度集中**。quarter-Kelly 在 paper 最优(Sharpe 0.45、maxDD -16)，一到 live 尾部炸裂(最差日 -$77)——过拟合活教材。§2 的 1.5× 上限保留，**不得再往 Kelly 方向加码**。

4. **一阶问题：当前根本不是干净 flat $5**。实成均值 $3.48(min $0.12)、**亏损单平均多吃 9% 资金**；理想 flat $5 在 live 是 +8.2% 而实际 -0.2%。**先修执行 size 一致性，可能比桶权重更值钱**。

5. **统计显著性免责**：live 仅 11 天，所有 sizing 方案 ROI 90% CI 跨 0。一切结论靠「paper 稳健 + live 同向」，不是 live 单独证明 → 改动先小步/shadow。
