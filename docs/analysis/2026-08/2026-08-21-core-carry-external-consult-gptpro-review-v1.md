# 外部高级模型咨询意见:core carry 研究路线审阅(GPT Pro,2026-08-21)

> 来源:用户将 [研究现状+咨询提示词](2026-08-20-core-carry-research-status-and-consult-prompt-v1.md)(含本地数据包)提交外部高级模型获得的审阅意见,原文完整保存、未改动。
> 本地消化与采纳决策另行记录;本文仅持久化原文。
> 快速要点:首要问题是停止时点(first-positive)的逆向选择而非概率精度;建议保留冻结 logistic、新建"下一报文破档 hazard"与"逆向选择状态"两个辅助读数;立即停默认 maker 第二腿;保险腿按 q↑/α1 选择性使用;先修 as-of 数据契约再谈新模型。

---

先给结论：**这套策略现在的首要问题已经不是“再找一个天气特征，把 94.5% 提高到 95.5%”，而是停止时点上的逆向选择。**你面对的 5% 尾部并非随机抽到的普通天气误差，而是“价格刚被知情流压低 → first-positive 触发 → 你接走风险”的条件分布。

因此，最优研究路线不是在 79 个 signal 上继续换模型，而是拆成三层：

1. 保留冻结的 5 特征 logistic，暂不重训。
2. 新建两个**辅助风险读数**：

   * 用大量非交易样本训练的“下一份/未来数份 METAR 破档 hazard”；
   * 用全 ladder、成交流和触发路径构建的“逆向选择状态”。
3. 不立即砍掉全部信号，而是先把改动放到目前已经负贡献的 maker 第二 sleeve，以及选择性保险腿上。

先提醒两个数据口径问题：你粘贴的明细我解析到的是 **84 行：76W、7L、1 个未定**，不是 86 行；另外 Chengdu 亏损行的 `p=0.989`，和“7/7 亏损的 p 都在 0.871–0.917”冲突。只有 7 个尾部事件时，一个字段版本或时间戳错位足以改变整个结论，后面任何规则冻结前必须先统一 canonical dataset。

---

# 1. 概率层：不要直接预测 7 个 loss，要预测“下一阶段物理 hazard”和“知情流状态”

## 1.1 统计约束决定了什么能做、什么不能做

按 7/83 计算，loss rate 约 8.4%，抽样标准误约 3 个百分点，95% 不确定性量级约 ±6 个百分点。即使按早期 5/79 计算，区间也仍然远大于 0.5–1pp。

所以：

* 在 79 个 signal 上验证 0.5–1pp 的增量，统计上基本不可辨识。
* 4700 个 checkpoint 不能当成 4700 个独立样本，因为同一城市日的 checkpoint 共用同一个最终结果。
* GBM、随机森林、复杂 survival、城市交互模型在 signal 层都会学到这 7 个 loss 的具体形状。
* 新模型必须在**大量 METAR 报文间隔、历史城市日或市场微观事件**上训练，再冻结成一个风险分数，最后只在 79 个交易信号上做策略回放。

从绝对金额看也很明确：20 股信号中，真实概率提升 1pp 只增加约 `$0.20/signal`；80 个信号约 `$16`。而成功处理一个尾部亏损就是 `$8–17`。所以现阶段**尾部管理的研究价值明显高于再挤 0.5pp 的全局预测精度**。

## 1.2 值得研究的特征族

| 特征族                                    | 判断              | 与已证伪版本的关键差异                                                                     |
| -------------------------------------- | --------------- | ------------------------------------------------------------------------------- |
| 全 ladder 上方概率质量的短时迁移、跨档成交流             | **立即值得做，最高优先级** | 不是已经失败的静态 full-ladder prior，而是研究 `当前档下跌 + 上方档同步上涨` 的概率质量迁移，以及主动卖当前/主动买上方的流      |
| first-positive 的“触发原因分解”               | **立即值得做**       | 不是增加价格或 p 阈值，而是判断 edge 是天气/时间改善产生，还是卖一价突然下压产生                                   |
| 距下一份预计 METAR 的时间、报文节奏相位                | **立即值得做**       | `obs_age` 是距离上一份观测多久，和“距离下一份预计发布多久”不是同一个变量；知情对手恰好在报文前 5–19 分钟行动                 |
| 历史 station-level late-high climatology | **值得做**         | 不是用 79 个 signal 拟合 survival；而是用多年 METAR，估计某站点、季节、太阳时、天气状态下“当前高点还能否保持”的经验 hazard |
| NWP 实时偏差修正后的剩余路径分歧                     | **先在密集物理样本上验证** | 不是 forecast revision 或简单跨档；而是用当前实际−预报误差修正 ECMWF/GFS 的未来路径，再计算剩余峰值分布             |
| 附近机场/上风向站点的观测变化                        | **先采数据**        | 不是目标站自身的 1/2/3 小时斜率，而是空间上领先目标站的气团、云带和升温变化                                       |
| 定量短波辐射/clear-sky index、卫星云图运动          | **当前规模不建议优先**   | 它与已失败的 METAR 云雨语义不同，但工程成本相对于 0.5–1pp、约 `$8–16/25天` 的价值太高                        |
| 在 79 个 signal 上直接上 GBM/神经网络            | **不建议**         | 没有足够尾部事件，换算法不会创造信息                                                              |

### 最值得建设的物理模型

建立**离散报文 hazard 模型**，但不是你已经证伪的 market cumulative hazard survival：

[
h_k=P(M_{k+1}>U\mid \mathcal F_k)
]

其中 (U) 是当前档上沿，(M_{k+1}) 是下一份 METAR。进一步：

[
P(\text{hold to close})\approx \prod_{j=k}^{K-1}(1-h_j)
]

关键差异是：

* 训练标签来自所有历史 METAR 报文间隔，不是 79 个被价格筛选后的交易；
* 输入是物理状态、报文时间相位、附近观测、偏差修正后的预报；
* 输出先作为 veto/保险触发器，不直接取代当前 logistic；
* 必须按 city-day 或日期块做交叉验证，不能随机拆 checkpoint。

模型族建议：

* 当前 5 特征 logistic：保留。
* report hazard：先用 ridge logistic、GAM 或简单离散时间 logistic。
* 贝叶斯层次：只用于 station/climate partial pooling 和校准，不要用来拟合几十个 signal 特征。
* GBM：只有当 report-level 样本达到数万量级后，才作为 challenger。

## 1.3 一个特征至少需要多强，才值得用来砍仓

在价格 (c) 买 YES、单股手续费 (f) 时：

[
EV=p-c-f=1-r-c-f
]

其中 (r) 是 loss rate。以 (c=0.85)、(f\approx0.0034) 为例，break-even loss rate 是：

[
r^*=1-0.85-0.0034=14.66%
]

你当前基础 loss rate 约 8.4%。因此一个用于“跳过或减半”的风险 flag，至少要把 loss rate 从约 8.4% 富集到约 14.7%，对应正似然比大约需要 **接近 2×**。

这解释了为什么 1.2–1.3× 的 METAR slope 没有实用价值；而 `forecast peak passed ∧ actual fresh-high` 的 2.8× lift 虽然 n=8 很小，却至少达到了经济上值得继续观察的数量级。它现在适合进入**二次确认或保险状态**，不适合单独封禁信号。

---

# 2. 触发条件重构：从 first-positive 改成 candidate → core → confirm/hedge

单纯提高 p、价格或 edge 阈值不对。真正应该利用的是**阈值是怎么被穿过的**。

## 2.1 对 edge 变化做因果分解

设上一个仍未触发的 checkpoint 为 (t^-)，当前为 (t)：

[
e_t=p(m_t,x_t)-a_t-f_t
]

其中 (m) 是市场中价特征，(x) 是天气/时间特征，(a) 是卖一价。将 edge 变化分成：

[
\Delta e =
\underbrace{p(m_t,x_t)-p(m_{t^-},x_t)}*{\text{市场特征贡献}}
+
\underbrace{p(m*{t^-},x_t)-p(m_{t^-},x_{t^-})}_{\text{天气/时间贡献}}
---------------------------------------------------------------

\underbrace{(a_t-a_{t^-})}_{\text{卖一变化贡献}}
]

然后定义两个反事实 edge：

[
e^{\text{old ask}}*t=p_t-a*{t^-}-f_t
]

[
e^{\text{old market}}*t=p(m*{t^-},x_t)-a_t-f_t
]

如果：

* 当前 (e_t>0)；
* 但 (e^{\text{old ask}}_t\le0)；
* 且卖一在短时间内明显下跌；

那么这不是“模型终于看到了便宜货”，而是**这个信号完全由卖压创造**，应标为 `quote_driven_candidate`。

这和提高价格阈值的区别非常大：同样是 `p=.88, ask=.85`，天气状态逐步改善导致的 `.85`，和对手突然砸到 `.85`，不再被当成同一种信号。

## 2.2 全 ladder 不看静态价格，主要看概率质量迁移

对同一城市所有 exact brackets 的 midpoint 做带 spread 权重的 simplex projection：

[
\hat \pi=
\arg\min_{\pi_i\ge0,\sum\pi_i=1}
\sum_i\frac{(\pi_i-m_i)^2}{s_i^2+\epsilon}
]

定义：

[
q_{\uparrow,t}=\sum_{i>\text{current}}\hat\pi_{i,t}
]

[
q_{1,t}=\hat\pi_{\text{next},t}
]

[
\alpha_{1,t}=\frac{q_{1,t}}{q_{\uparrow,t}}
]

重点特征不是静态 (q_\uparrow)，而是：

* (\Delta q_\uparrow)：上方总概率质量是否快速增加；
* (\Delta q_1)：下一档是否快速增加；
* 当前档主动卖出量；
* 上方档主动买入量；
* 当前档下跌是否被上方概率质量近似承接；
* 同一 pseudonym 是否在多个城市重复出现；
* quote 更新后是否迅速恢复，还是持续恶化。

静态 full ladder prior 已经失败，不代表**短时跨档概率质量迁移**失败。这是两个不同假设。

## 2.3 推荐的生产状态机

第一阶段不改变 10 股 taker core，所有实验都改造当前负贡献的 maker sleeve：

```text
BASE_CANDIDATE:
    raw_p >= 0.80
    raw_edge > 0

计算:
    quote_driven
    upper_tail_migration
    adverse_flow
    forecast_actual_divergence
    report_phase_risk

若不是 quote_driven:
    10 股 taker core
    第二 sleeve 仅在 robust edge 仍为正、upper tail 稳定时考虑

若 quote_driven 但 upper tail 稳定、无 adverse flow:
    10 股 taker core
    禁止默认 maker
    将第二 sleeve 置为 armed，等待独立确认

若 quote_driven 且出现以下任一项:
    - q_up 快速增加
    - 当前档主动卖 + 上方档主动买
    - 已知高风险参与者出现
    - forecast-actual divergence 同时成立
则:
    保留最多 5–10 股 current core
    禁止 current maker
    根据 q1/q_up 决定买下一档保险，或不加第二腿
```

确认不要采用“所有信号统一等 5 分钟”，因为你已经证明等待成本过高。确认只用于第二 sleeve，且应是 event-time：

* 下一次独立成交/盘口更新后；
* 至少间隔 30–120 秒；
* (q_\uparrow) 未继续上涨；
* 当前卖一没有继续恶化；
* 按实际新价格计算的 edge 仍为正。

当前 5 分钟盘口只能做初步回放；要验证 30–120 秒确认，需要 tape 和更细盘口。

## 2.4 本地小时怎么用

你给出的 14–18 时段是 40W/7L，loss rate 约 14.9%。这不适合直接封禁，因为承载了大量赢单；但它有非常清晰的经济含义：

* 在 `ask=.85` 时，计入手续费后的 break-even loss rate 约 14.7%；
* 14–18 时段实际描述性 loss rate 约 14.9%；
* 因此这个时段的 `.85` 左右低价信号，整体已经接近零 edge。

所以本地小时不应再加一个自由 spline 去拟合 7 个 loss，而应成为**风险状态的放大器**：

```text
late_band AND quote_driven
```

或者：

```text
late_band AND forecast_actual_divergence
```

只用 conjunction，不用宽泛的 OR，才能保留赢单。

更进一步，应把 civil local hour 换成：

* 太阳时相对 solar noon；
* 距预计气候峰值的分钟数；
* 距下一份 METAR 的分钟数；
* 当日还剩多少个预期报文。

## 2.5 离线验证方法

用 4700 checkpoint 做严格的 chronological replay：

1. 每个城市日只允许第一个满足新策略的时点进场。
2. 所有变量必须取当时已经收到的数据。
3. 模拟实际 ask、手续费、延迟和 maker 撤单。
4. bootstrap 单位必须是 city-day，而不是 checkpoint。
5. 阈值只允许一个很小的预注册网格，例如：

   * ask shock：1c/2c/3c；
   * (q_\uparrow) shock：1c/2c/3c；
   * 参考窗口：5m/10m；
   * 确认窗口：下一事件/2m/5m。
6. 首要指标不是 accuracy，而是：

[
\text{Tail Efficiency}
======================

\frac{\text{减少的 loss 美元}}
{\text{牺牲的 winner 利润}+\text{新增费用}}
]

建议的 go/no-go 标准：

* 保留至少 95% 的 signal count；
* 保留至少 90% 的 taker winner gross profit；
* 减少至少 25–30% 的 loss dollars；
* 尾部效率大于 2；
* leave-one-loss/day 回放中不能只靠某一个 Warsaw 或 Amsterdam 案例。

描述性地，`p 0.87–0.92 × ask≤0.90` 在粘贴表中是 23 个信号、6 个 loss，但这是事后区域，不能再直接做矩形阈值。应该把它转化为上面的**触发路径 + 上方概率质量迁移**规则。

---

# 4. maker 与上一档保险

## 4.1 高价 maker 不值得作为生产 alpha sleeve

在 maker 成交价 0.95 时，零手续费下也要求胜率超过 95%，即 loss rate 小于 5%。

你有 9/9 胜，但在 0 次 loss、n=9 下，loss rate 的 95% 单侧上界仍约 28%。要在“零次 loss”的情况下把这个上界压到 5% 以下，约需要 **59 次连续无 loss 的独立 fill**。

所以：

* `0.95+、9/9` 不能证明 maker 正 EV；
* 当前测得 EV≈0；
* 10 股 maker 赢一次最多约 `$0.50`，一次 `.95` 的 loss 擦掉约 19 次赢单；
* 在你的规模下，不值得占用生产风险预算。

**立即建议：取消默认 maker 第二腿。**
需要继续研究队列和反向选择时，只保留 1–2 股 probe order，并设固定日度数据采集预算，不把它算成策略收益腿。

这和已证伪的 maker 替代方案没有冲突：结论不是再尝试一种 maker，而是承认它目前没有经济价值，只作为微观结构采样工具。

## 4.2 保险腿的精确收益结构

当前档买 (x) 股，价格 (c)；下一档买 (h) 股，价格 (q)。设：

* (p=P(\text{当前档最终赢}))
* (r_1=P(\text{最终恰好落在下一档}))
* 其余是越过两档或更多

忽略费用时：

[
E[\Pi]=x(p-c)+h(r_1-q)
]

三种结果的 PnL：

[
\Pi_{\text{hold}}=x(1-c)-hq
]

[
\Pi_{\text{one-step}}=-xc+h(1-q)
]

[
\Pi_{\text{multi-step}}=-xc-hq
]

以 `x=10, c=.85, q=.10` 为例：

| 保险股数 |  当前档保持 |  恰好升一档 |  升两档以上 |
| ---: | -----: | -----: | -----: |
|    0 | +$1.50 | -$8.50 | -$8.50 |
|    3 | +$1.20 | -$5.80 | -$8.80 |
|    5 | +$1.00 | -$4.00 | -$9.00 |
|   10 | +$0.50 | +$0.50 | -$9.50 |

因此 5 股保险：

* 将 one-step loss 大约减半；
* 但把正常赢单利润从 `$1.50` 降到 `$1.00`，损失 33%；
* 如果 q=.15，正常利润只剩 `$0.75`，减少 50%；
* 对 multi-step overshoot 不仅没保护，还会略微增加损失。

所以“保险只占主腿本金的 7%”不是正确经济口径。保险成本应该相对于**赢单利润和预期 edge**衡量，而不是相对于名义本金。

## 4.3 下一档价格不是完整的 overshoot 概率

这是非常关键的一点：

[
q_1=P(\text{最终恰好下一档})
]

而不是：

[
P(\text{任何 upward overshoot})
]

真正的 overshoot 市场读数是：

[
q_\uparrow=\sum_{\text{所有更高档}}q_i
]

下一档保险的覆盖率则是：

[
\alpha_1=\frac{q_1}{q_\uparrow}
]

只有当 (\alpha_1) 很高时，下一档才是良好保险。如果极端升温时上方概率质量同时扩散到 22°C、23°C，买 21°C 反而会出现 current 与 insurance 双输。

因此：

* **作为触发信号**：主要使用 (q_\uparrow)、(\Delta q_\uparrow) 和跨档 flow。
* **作为保险选择**：使用 (\alpha_1=q_1/q_\uparrow) 判断下一档能覆盖多少 overshoot。
* **作为模型特征**：暂时不要把 raw q1 丢进复杂模型；它和当前 mid 高度共线。先使用动态创新和 compositional residual。
* **作为交易腿**：只有在 (r_1>q_1+\text{fee}) 时才是正 EV；否则只是付费转移风险。

## 4.4 保险比例如何算

若希望把 one-step overshoot 的损失压到原来的比例 (\rho)，忽略手续费：

[
h_\rho=\frac{(1-\rho)xc}{1-q}
]

例如希望损失减半，(\rho=0.5)：

[
h=\frac{0.5\times10\times0.85}{0.90}=4.72
]

也就是约 5 股。

同时设一个最大预期拖累预算 (B)：

[
h\cdot\max(0,q+f_q-\hat r_1)\le B
]

建议把 (B) 定为该 signal 预期 base edge 的 25%–50%，而不是主腿本金的 7%。

最终：

[
h^*=
\min\left(
h_\rho,,
\frac{B}{\max(q+f_q-\hat r_1,\epsilon)},,
\text{available depth}
\right)
]

由于规模很小，生产实现直接枚举 `h ∈ {0,1,2,3,4,5}`，计算每个 outcome 的 EV、最坏损失和 CVaR 即可，不需要复杂优化器。

## 4.5 动态时是退出 current，还是买 next

获得新的不利信息后：

* 当前档可卖 bid 为 (b)；
* 更新后的真实 hold 概率为 (\hat p)；
* 下一档真实概率为 (\hat r_1)。

已经买入价格是 sunk cost，此时：

[
\text{卖 current 更优}\iff b-\text{sell fee}>\hat p
]

[
\text{买 next 更优}\iff \hat r_1>q_1+\text{buy fee}
]

决策结构：

* (q_\uparrow) 上升、且 (\alpha_1) 很高：优先买 next 小额保险；
* (q_\uparrow) 上升、但 (\alpha_1) 很低：说明上方分布变宽，next 保险失效，优先减 current 或不加第二 sleeve；
* 只有 current ask 被砸、但 (q_\uparrow) 没变：更像流动性折价，可以维持 core。

这正是 Warsaw 上方档从 0.08→0.14 最值得研究的部分。

## 4.6 “盘口健康 maker”有没有生存空间

只能先采数据，不能现在投入正常仓位。至少需要：

* 当前档最近无主动卖；
* 上方档最近无主动买；
* (q_\uparrow) 稳定或下降；
* 我方价位下方支撑深度至少为订单的 3 倍；
* 支撑订单已经存活一定时间，而不是刚挂出的可撤单深度；
* cancellation-adjusted OFI 非负；
* 无历史高风险 actor 出现；
* 距下一份预计报文不处于知情流高发窗口。

但即使满足，也只能解决一部分可见微观结构，不能解决对手拥有私有机场源的问题。我的判断仍是：**maker 最多有执行成本改善价值，没有证据表明它能成为独立 alpha。**

---

# 3. 阿姆斯特丹高频 nowcasting

## 3.1 不要直接做“下一份会不会创新高”的 19 样本分类器

最稳妥的是连续值建模，再转换成 crossing probability。

设：

* (K_t)：KNMI 10 分钟温度；
* (M_k)：结算用 METAR；
* (\tau_{k+1})：下一份 METAR 的预计观测时刻；
* (U)：当前档上沿。

### 第一阶段：预测下一 METAR 时刻的 KNMI/潜在温度

用简单的 local-linear state space，443 对齐样本不适合 GBM：

[
\begin{bmatrix}
T_{n+1}\v_{n+1}
\end{bmatrix}
=============

\begin{bmatrix}
1&1\0&1
\end{bmatrix}
\begin{bmatrix}
T_n\v_n
\end{bmatrix}
+Bz_n+\eta_n
]

其中 (z_n) 可以限于：

* 最近 3 个 10 分钟读数；
* 一阶趋势和轻微二阶变化；
* 露点差；
* 风速；
* ECMWF/GFS 当前 residual 和下一时段变化；
* 距下一 METAR 的分钟数；
* 太阳时。

实现上，ridge regression 或 Kalman filter 都够。

### 第二阶段：显式建模 KNMI→METAR 源差

先将 KNMI 插值到 METAR 的实际观测时间：

[
D_k=M_k-K_{\text{interp}}(\tau_k)
]

然后建模：

[
D_{k+1}
=======

\mu+\phi(D_k-\mu)+\gamma^\top z_k+\epsilon_k
]

只保留少数有物理意义的变量：

* 最近一份配对偏差；
* 时段；
* 趋势方向；
* 风速/风向 regime；
* 两站实际时间偏移。

如果 METAR/WU 最终有离散化或取整，使用 rounded-normal 或 ordered-probit 观测层：

[
M_{k+1}=Q(T_{\tau_{k+1}}+D_{k+1}+\epsilon_M)
]

最终：

[
P_{\text{cross,next}}
=====================

P(M_{k+1}>U)
]

不要只输出点预测。

## 3.2 延迟必须按 receive time 回测

每条数据至少需要三套时间：

* observation time；
* source publication time；
* 你的 receive time。

在历史回测时，只能使用：

[
\text{receive time}\le\text{decision time}
]

不能因为 KNMI 读数的观测时间早于决策，就默认当时已经拿到。11.3 分钟延迟应进入：

* latest KNMI observation age；
* 距下一 METAR 的有效 lead；
* 模型预测方差。

lead 越短，置信区间应该自动变宽。

## 3.3 14/19 应如何解释

14/19 在“双方方向分歧”子集中是正面证据，但还不是交易证据，因为：

* 这是筛选后的 disagreement 子集；
* direction correct 不等于 crossing probability 校准；
* 也不等于在当时的 q1、bid/ask 下有正 EV；
* 如果模型或分歧定义在同一 443 样本上调过，会有选择偏差。

需要看：

* 全部 443 样本的 RMSE/CRPS；
* crossing 的 Brier/logloss；
* 按日期 block 的 OOF；
* 在真实 q1/当前 bid 下，hedge/exit 的交易 EV。

## 3.4 先算 oracle value，再决定工程投入

在所有 KNMI 到达时点，允许 oracle 从以下动作中选最优：

* 不动；
* 卖 current；
* 买 1–5 股 next；
* 不加第二 sleeve。

[
V^{oracle}
==========

\sum_j
\left[
\max_a\Pi_{j,a}
---------------

\Pi_{j,\text{baseline}}
\right]
]

如果完美知道未来的 oracle，在这 25 天也只能增加几美元，就不要继续建模。

按目前数据，Amsterdam 只覆盖 7 个 loss 中的 1 个，因此历史绝对上限大致是一笔 tail：

* 仅 10 股 taker：数量级约 `$8–9`；
* 如果旧策略两条 10 股 sleeve 都暴露：上限约 `$15–17`；
* 再减掉 false positives、spread 和保险成本，真实价值更低。

所以这条路线对当前 PnL 的直接价值有限，但它是一个很好的**通用下一报文 nowcast 架构验证场**。

## 3.5 如何泛化到其他城市

采用同一个 latent target temperature 架构：

* 目标机场 METAR：低频、高权重观测；
* 附近机场、气象局、mesonet/PWS：高频、带 station bias 的辅助观测；
* NWP：低频外生路径；
* 历史 station hazard：先验。

没有高频辅助源时：

* 用目标机场多年报文转移规律预测下一 METAR；
* 加入附近机场和上风向站点；
* 使用偏差修正后的 NWP remaining path；
* 模型方差自动放大。

这比试图从目标站自身 1/2/3 小时 slope 中再挖信息更有希望，因为它加入的是空间领先信息和长历史先验。

---

# 5. 选择效应下的校准

## 5.1 对当前策略，应该校准 selected population

你真正关心的是：

[
P(Y=1\mid p_\tau,\ S_{\pi}=1)
]

其中 (\tau) 是 first-positive 停止时点，(S_\pi) 表示当前策略确实入场。

因此，当前策略下直接在已选择 signal 上校准并不是错误；这就是运营目标人群。错误的是：

* 把 4700 个 checkpoint 都当独立校准样本；
* 在当前策略选择的样本上校准后，直接用于完全不同的新触发策略；
* 同时拟合 intercept、slope 和分段曲线。

## 5.2 目前只做 intercept-only logit calibration

[
p_{\text{cal}}
==============

\sigma(\logit(p)+\alpha)
]

固定 slope=1，只估计 (\alpha)，并加较强收缩先验，例如：

[
\alpha\sim N(0,0.5^2)
]

按粘贴表中 83 个 W/L 粗算，不加先验的 intercept shift 约为：

[
\hat\alpha\approx-0.37
]

但标准误也约 0.40，非常不精确。这个量级会将：

* p=.88 调整到约 .835；
* p=.90 调整到约 .86；
* p=.99 只调整到约 .986。

这比全局直接减 5pp 合理，因为校准偏差不应在接近 1 时仍机械减 5pp。

但如果把这个无收缩校准直接当硬 gate，粘贴样本中正 edge 信号只剩大约 18/83，显然过度收缩。所以建议：

* 不用它立即取消所有 taker core；
* 用于第二 sleeve 是否加仓；
* 用于保险预算；
* 用于 `quote-driven + late-band` 风险状态的额外 margin；
* 新策略必须重新 replay 后做 policy-conditional calibration。

更稳健的触发条件不是固定 `edge>2c`，而是：

[
P(p_{\text{true}}>a+f\mid D)>\eta
]

其中：

* core sleeve 可暂取较低 (\eta)；
* incremental sleeve 取 0.75–0.80；
* adverse state 再提高。

---

# 6. 数据投资优先级

按“每 MB 的研究价值”和当前策略绝对规模排序：

| 顺位 | 数据                                  | 能解锁的分析                                                                               |
| -: | ----------------------------------- | ------------------------------------------------------------------------------------ |
|  1 | **过滤后的 token-level tape**           | 判断 first-positive 是谁推动的；当前档主动卖/上方档主动买；actor recurrence；成交后价格影响与持续性；maker 被谁、在什么状态下吃掉 |
|  2 | **KNMI–METAR 配对及三套时间戳**             | 源差分布、动态 bias、as-received nowcast、下一报文 crossing probability；数据量极小，但仅覆盖 Amsterdam      |
|  3 | **触发窗口内的全 ladder event-level book** | (q_\uparrow)、概率质量迁移、OFI、支撑存活、撤单率、quote resilience；不要全日高频存，先存触发前后各 20 分钟和预计报文前窗口      |
|  4 | **持续保存 checkpoint 评分历史**            | 触发政策 replay、edge 原因分解、counterfactual first-positive；这是基础设施，但你已有 4700 个，新增边际信息较低      |

实际工程上，第 1 和第 3 应作为一个 package：只有 tape 没有 book，无法区分成交冲击和盘口撤单；只有 book 没有 tape，又无法识别主动方向和参与者。

建议 schema 至少包括：

```text
market_id / city / date / bracket
exchange_event_time / block_time / receive_time
trade side / price / size / maker-taker / wallet or pseudonym
all-bracket top 3 bid-ask / depth / quote age
expected_next_report_time
weather_obs_time / source_publish_time / receive_time
forecast_issue_time / valid_time / receive_time
model_score / each feature / current policy state
```

单纯把源站轮询从 5 分钟压到 1 分钟可以做，但不是首要研究投资。它只能减少可控的约 2 分钟平均延迟，追不上提前 5–19 分钟行动的私有源对手。

---

# 7. 最可能忽略的系统性问题

我认为最大盲区是：**缺少严格、单一版本的 as-of event contract，导致模型评分、天气观测、预报 valid time、市场盘口和本地小时可能不是同一个决策时点的真实信息集。**

理由很具体：

* 7 个 loss 中已经有 1 个与 forecast peak 时钟错误有关；
* 粘贴数据存在 84/86 行差异；
* Chengdu p 值与总结区间冲突；
* 大量 local hour 缺失；
* 高频源有 observation time、publication time、receive time 三个不同概念；
* first-positive 是一个停止时点，任何 1 个 checkpoint 的时间错位都会改变入场原因；
* 只有 7 个尾部事件，一次数据版本错误就是 14% 的 loss 样本。

在研究新特征前，先为每个 signal 生成一份不可变的 `decision packet`：

```text
decision timestamp
last non-positive checkpoint
first-positive checkpoint
all raw feature bytes available then
forecast issue/valid/receive timestamps
weather observation/publish/receive timestamps
book and tape snapshot
model version/hash
timezone/DST conversion
actual order and fill timestamps
eventual overshoot magnitude and first-cross time
```

所有回测都只从这个 packet 重建。否则再精细的模型比较也可能是在比较时间戳和版本差异。

---

# 最终执行顺序

**立即值得做**

1. 停止默认 10 股 maker sleeve；保留 10 股 taker core。
2. 修复 canonical dataset 和 as-of 时间契约。
3. 从现有 checkpoint 重建 first-positive 的 edge 原因分解。
4. 建立全 ladder 的 (q_\uparrow)、(\Delta q_\uparrow)、(\alpha_1)。
5. 回放三个预注册策略：

   * taker core only；
   * taker core + quote-driven 二次确认第二 sleeve；
   * taker core + adverse-state 3–5 股 next 保险。
6. 做 intercept-only、policy-conditional 校准。
7. 回放“日内第一次确认 overshoot 后，关闭后续高风险第二 sleeve”的组合止损。07-27 的 Singapore 破档在 Chengdu 入场前已经可见，这条规则至少存在一个真实可验证案例，但只能作为风险控制，不能先声称提高 EV。

**先采数据再决定**

1. token tape + 触发窗口全 ladder book。
2. actor shrinkage score 和 adverse-flow score。
3. Amsterdam state-space nowcast。
4. 多年 station-level late-high hazard。
5. 附近机场/上风向站点的传感器融合。

**不建议**

1. 在 79 个 signal 上上 GBM、随机森林、复杂生存或更多交互。
2. 继续寻找宽泛的天气语义 challenger。
3. 再做静态 p/price/hour 矩形阈值。
4. 无条件买下一档保险。
5. 把 0.95+ maker 的 9/9 当成可生产的正 EV 证据。
6. 当前规模下投入昂贵卫星或辐射数据，除非这些数据还能复用于更大规模策略。

**置信度**

* 默认 maker 应退出生产资金、GBM 不适合 n≈79、应采用停止时点/微观结构重构：高。
* 动态全 ladder 迁移能否稳定捕捉尾部：中等，需要 tape 和 forward 验证。
* 3–5 股 selective next hedge 能改善风险调整收益：中等，核心取决于 one-step overshoot 占比 (\alpha_1)。
* Amsterdam nowcast 能否直接显著改善全组合 PnL：中低；它更像可泛化的研究原型，而不是当前规模下的主要利润来源。
