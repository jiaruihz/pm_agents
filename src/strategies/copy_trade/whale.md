# Polymarket 鲸鱼筛选与跟单策略 — 方法论总结

## 一、地址池构建：怎么找到符合要求的地址

### 1.1 数据源

| 来源 | 方式 | 覆盖 | 限制 |
|------|------|------|------|
| Polymarket Leaderboard API | GET /v1/leaderboard?category=XX&timePeriod=ALL&limit=50&offset=N | 6大类 × 最多1000名 | 每次最多50条，offset上限1000 |
| Polymarket Positions API | 已知地址 → /positions?user={addr} | 单地址当前持仓 | 最多200条 |
| Polymarket Closed-Positions API | /closed-positions?user={addr}&limit=50&offset=N | 单地址历史结算仓 | 每页最多50条，但分页可用，需offset步进50 |
| Polymarket Trades API | /trades?user={addr}&limit=50&offset=N | 单地址交易记录 | 最多约1000条 |
| Arkham Intelligence | Web UI，无公开API | 钱包标签、实体聚类、胜率 | 无API，只能手动查 |
| Bitquery | GraphQL API，需API Key | 链上settlement事件可算胜率 | 需要付费key |
| HyperSync | 需API Key | 链上原始交易 | 返回401，需要认证 |

**实际可行路径**：Polymarket Data API 是唯一免费、无需认证、可批量拉取的数据源。

### 1.2 Leaderboard 拉取策略

6个category: OVERALL, POLITICS, CRYPTO, SPORTS, ECONOMICS, TECH
4个timePeriod: DAY, WEEK, MONTH, ALL

→ 优先拉 ALL，每个category拉到 offset=950（上限）
→ 6个category去重后约 377 个唯一地址

**关键发现：**
- OVERALL 包含所有类别的top用户，和其他category有大量重叠
- SPORTS 类别用户量最大，但大部分是体育博彩bot/套利者
- POLITICS 和 CRYPTO 的用户质量最高（非bot比例高）
- timePeriod=ALL 的用户最稳定，DAY/WEEK 的可能是一时运气

### 1.3 Bot 过滤（最关键的一步）

从377个候选地址中，73%是bot或高频套利者，必须过滤掉。

**Bot识别信号**

| 信号 | 阈值 | 含义 |
|------|------|------|
| sports_ratio > 80% | 超过80%仓位是体育 | 体育单场套利/做市 |
| trade_freq > 50/h | 每小时交易超50次 | 高频算法交易 |
| vol_pnl_ratio > 15 | 交易量/PnL > 15 | 做市商，靠spread不是靠预测 |
| buy_sell_ratio < 2 | 买卖比接近1 | 做市/套利，不是方向性下注 |
| outcome_type = spread | 只买盘口(非Yes/No) | 体育做市 |
| entry_price集中在0.45-0.55 | 74%仓位在0.40-0.60 | 做市/套利特征 |
| 当前持仓全部归零 | open_positions全curPrice=0 | 可能已不活跃 |

**"Slow Whale"画像（我们要找的人）**
- 非体育类（sports_ratio < 50%）
- V/P比 < 10（不是做市商）
- 交易频率 < 5次/小时
- 有活跃持仓（open_positions > 0）
- 分类排名靠前（至少在一个category上榜）
- 买卖比 > 3（方向性下注，不是做市）

### 1.4 评分维度

```
score = pnl_score(0-4) + category_diversity(0-3) + vp_efficiency(0-2)
      + active_positions(0-2) + market_diversity(0-2)
      + low_sports_bonus(0-1) + category_rank_bonus(0-2)
```

**权重逻辑：**
- PnL是基础，但不是唯一维度（大选赢家PnL高但不可复制）
- 分类多样性加分（在多个category都有表现说明不是一次运气）
- V/P效率加分（用更少的交易量赚同样的钱=更聪明）
- 低体育比加分（体育套利者不是我们的目标）

---

## 二、数据获取与分析框架

### 2.1 可获取的数据维度

| API端点 | 返回内容 | 关键字段 | 限制 |
|---------|----------|----------|------|
| /closed-positions | 已结算仓位 | realizedPnl, avgPrice, curPrice, outcome, title | 每页最多50条，limit参数被忽略，但offset分页可用，步进50 |
| /positions | 当前持仓 | currentValue, initialValue, cashPnl, percentPnl, avgPrice, curPrice | 最多200条 |
| /trades | 交易记录 | side, size, price, timestamp, conditionId | 最多约1000条 |
| /activity | 活动流 | type(TRADE/MAKER_REBATE/YIELD), title | 最多100条 |
| Gamma API /markets | 市场元数据 | closed, resolved, outcomePrices | closed字段不可靠 |
| CLOB API /prices | token当前价格 | price | 已结算的旧token返回空 |

### 2.2 关键发现：closed-positions 分页规则

这是整个项目中最关键的API发现，直接决定了胜率计算的准确性。

**错误认知（踩坑过程）**
- 最初认为 /closed-positions 只返回盈利仓 → 因为前50条恰好全是正PnL
- 后来发现确实包含亏损仓（realizedPnl可以为负），但认为有50条硬限制
- 最终验证：每页最多返回50条，但offset分页完全可用

**正确的分页方式**

```python
# ❌ 错误写法（我们之前的bug）
for offset in range(0, 2000, 100):  # 步进100
    data = fetch(f"...&limit=100&offset={offset}")  # limit=100被忽略，只返回50
    if len(data) < 100:  # 50 < 100，第一页就break了！
        break

# ✅ 正确写法
for offset in range(0, 10000, 50):  # 步进50
    data = fetch(f"...&limit=50&offset={offset}")  # 每页50条
    if len(data) < 50:  # 不足50条说明到末尾了
        break
```

**实测数据量对比**

| 地址 | 错误方式(只拿50条) | 正确方式(全量分页) | 差异 |
|------|--------------------|--------------------|------|
| tdrhrhhd | 50条 | 57条 | 低频交易者，差异小 |
| zerosmart | 50条 | 150条 | 3倍！ |
| Sandra.rachkova | 50条 | 101条 | 2倍 |
| 0xafEe | 50条 | 95条 | 近2倍 |
| Melody626 | 50条 | 891条 | 18倍！ |
| Dropper | 50条 | 2122条 | 42倍！ |
| eCash | 50条 | 432条 | 8倍！ |

**结论**：对于高频交易者，50条只覆盖了5-10%的历史仓位，胜率严重失真。全量分页后数据完全不同。

**真实胜率 vs 错误胜率对比**

| 名称 | 错误胜率(50条) | 真实胜率(全量) | 总仓位 | 差异原因 |
|------|---------------|--------------|--------|----------|
| zerosmart | 98% | 46% | 150 | 50条后全是亏损仓 |
| 0xafEe | 98% | 70% | 95 | 50条后有28个亏损仓 |
| Magamyman | 91% | 69% | 94 | 50条后有26个亏损仓 |
| eCash | 98% | 65% | 432 | 50条后有147个亏损仓 |
| ScarletRot | 75% | 69% | 185 | 差异不大，亏损均匀分布 |
| Dropper | 69% | 66% | 2122 | 高频交易者，50条严重不足 |
| Melody626 | 35% | 58% | 891 | 50条时亏损占比过高 |

### 2.3 分析维度与方法

#### A. 分类画像（Category Profile）

对每个closed position的title做关键词匹配：
- Politics: trump, biden, election, president, senate...
- Crypto: bitcoin, ethereum, crypto, token, defi...
- Sports: " vs ", nba-, mlb-, nfl-...
- Economics: fed, interest rate, inflation, gdp, tariff...
- Geopolitics: war, ukraine, iran, russia, nato...
- Tech: ai, openai, google, tesla, nvidia...
- Culture: movie, oscar, grammy, celebrity...
- Other: 不匹配以上任何类别的

输出：每个类别的仓位数量、PnL、平均入场价。

核心判断：是否有明确的主攻领域？分散 vs 专注？

#### B. 风险画像（Risk Profile）

入场价分布：
- < 0.30: 逆向/长shot（赌冷门）
- 0.30-0.70: 平衡（不确定的市场）
- > 0.70: 保守/热门（买确定性）

核心判断：
- 逆向型（avg entry < 0.35）：高赔率低胜率，如tdrhrhhd、Sandra.rachkova
- 平衡型（avg entry 0.35-0.55）：Dropper、ScarletRot
- 保守型（avg entry > 0.55）：zerosmart、eCash、Magamyman

#### C. 退出风格（Exit Style）

- curPrice >= 0.99: 持有到期并赢了
- 0.01 < curPrice < 0.99: 提前卖出（盈利或止损）
- curPrice <= 0.01: 持有到期但输了（或卖了但API标记为0）

#### D. 赚钱模式识别（修正后的真实数据）

**模式A：高胜率 + 高盈亏比（最理想）**

| 名称 | 真实胜率 | 盈亏比 | 均赢 | 均亏 | 净PnL | 模式 |
|------|---------|--------|------|------|-------|------|
| Michie | 69% | 89.7x | $285K | $9K | $3.1M | 大选赢家 |
| GCottrell93 | 58% | 22.2x | $319K | $31K | $4.6M | 大选赢家 |
| tdrhrhhd | 37% | 15.0x | $114K | $6K | $2.4M | 逆向长shot |
| edenmoon | 71% | 12.0x | $127K | $26K | $1.7M | 保守型 |
| Magamyman | 69% | 6.0x | $14K | $6K | $806K | 伊朗地缘 |
| bizyugo | 77% | 5.7x | $81K | $83K | $1.5M | 大选+crypto |

**模式B：高胜率 + 低盈亏比（靠频率赚钱）**

| 名称 | 真实胜率 | 盈亏比 | 均赢 | 均亏 | 净PnL | 模式 |
|------|---------|--------|------|------|-------|------|
| ScarletRot | 69% | 1.7x | $5.8K | $10K | $336K | 保守热门 |
| Dropper | 66% | 1.6x | $3.5K | $4.3K | $1.9M | 高频分散 |
| Melody626 | 58% | 1.4x | $5.0K | $6.3K | $814K | 超高频分散 |
| eCash | 65% | 1.1x | $2.9K | $4.8K | $98K | 伊朗地缘 |

**模式C：低胜率 + 高赔率（幸存者偏差型）**

| 名称 | 真实胜率 | 盈亏比 | 均赢 | 均亏 | 净PnL | 模式 |
|------|---------|--------|------|------|-------|------|
| zerosmart | 46% | 4.7x | $16K | $3K | $878K | 平衡型 |
| ro0k | 42% | 1.2x | $21K | $15K | $327K | 逆向型 |
| Sandra.rachkova | 21% | 0.9x | $62K | $22K | -$208K | 净亏损！ |

**关键发现**：
- Sandra.rachkova 之前看起来PnL 1.5M，实际是**净亏损−208K**！50条限制只显示了盈利仓
- eCash 的净PnL只有 $98K，之前以为有 $682K（也是50条限制的假象）
- 真正赚钱且可持续的：tdrhrhhd（37%胜率但15x盈亏比，净赚2.4M）、**Magamyman**（69%胜率6x盈亏比，净赚806K）

#### E. 大选依赖过滤（Critical Filter）

**问题**：2024美国大选是一次性事件，很多鲸鱼的PnL来自Trump胜选，不可复制。

**方法**：对每个closed position的title做关键词匹配，识别大选相关仓位。

**结论**：
- Michie: 99% PnL来自大选 → 不可复制
- RepTrump: 100% PnL来自大选 → 不可复制
- GCottrell93: 98% PnL来自大选 → 不可复制（虽然盈亏比22x，但全是赌Trump赢）
- tdrhrhhd: 73%来自大选，但非大选PnL仍有$685K → 有价值
- Magamyman: 0%来自大选 → 完全可复制

#### F. 当前持仓验证（Forward Validation）

核心问题：历史PnL不代表未来，当前持仓是否在赚钱？

open_positions:
- total_invested vs total_value → unrealized PnL
- unrealized ROI = (value - invested) / invested

最佳信号：当前持仓+30%浮盈（如tdrhrhhd），说明这个人的判断正在被市场验证。

#### G. 体育博彩 vs 预测市场洞察

体育鲸鱼的三种模式：

**盘口做市商（如JPMorgan101）**
- 100%买Spread/盘口（Hornets -6.5, Over 217.5）
- 买/卖比 ≈ 1.0（频繁买卖）
- 入场价0.59（买热门方）
- 本质：做市/套利，不是预测

**足球冷门猎手（如Supah9ga）**
- 96%买No（赌强队不会赢）
- 入场价0.37（赌冷门）
- 买/卖比12.7（只买不卖）
- 本质：赌强队翻车，赔率被低估时买入No

**大额单场（如KeyTransporter）**
- 只下注11场，每场400K−2.5M
- 买/卖比181:0（只买不卖）
- 本质：对特定比赛有强判断，重仓下注

**体育鲸鱼不适合跟单的原因：**
- 体育是零和博弈，信息优势来自实时数据（伤停/阵容/天气）
- 做市商型需要极低延迟，个人无法复制
- 冷门型的盈亏比差（亏了直接归零）
- 单场流动性差，跟单时价格已变

---

## 三、踩过的坑

### 3.1 API 坑

| 坑 | 现象 | 根因 | 解决方案 |
|----|------|------|----------|
| closed-positions 分页bug | 只拿到50条数据 | limit参数被忽略（始终返回≤50条），且分页步进用了100导致第一页就break | 用offset步进50，判断len(data) < 50时停止 |
| /closed-positions limit被忽略 | limit=100或limit=500都只返回50条 | API服务端硬编码每页上限50 | 接受50条/页，用offset翻页 |
| /leaderboard 路径 | /leaderboard 返回404 | 正确路径是 /v1/leaderboard | 加 /v1 前缀 |
| Gamma API closed 字段 | 已结算的市场仍显示 closed=False | Gamma API的状态更新延迟或不一致 | 不要用这个字段判断市场是否结算 |
| CLOB API 旧token价格 | 已结算token返回空 | 已结算的token不再有价格数据 | 无法用token价格判断历史输赢 |
| 所有API需要代理 | 在中国直连超时 | 需要翻墙 | 配置HTTP代理 http://127.0.0.1:13659 |
| curPrice=0 在open positions | 不一定是亏损 | 可能是已结算但API未迁移到closed | curPrice<0.05的open仓大概率是亏损 |

### 3.2 数据理解坑

| 坑 | 错误理解 | 正确理解 | 影响 |
|----|----------|----------|------|
| /closed-positions 只返回盈利仓 | 早期判断：胜率100%不对 | 包含亏损仓（realizedPnl可以为负） | 胜率可以计算，但需要全量分页 |
| 50条数据够用 | 低频交易者够，高频不够 | Melody626有891条，Dropper有2122条 | 高频交易者必须全量分页 |
| 大PnL = 好鲸鱼 | 很多大选赢家PnL超高 | 必须过滤大选PnL，看非大选的持续盈利能力 | Sandra.rachkova看似1.5M盈利，实际净亏208K |
| 高胜率 = 好跟单标的 | zerosmart 98%胜率 | 全量分页后只有46%，50条只看到了盈利页 | 胜率必须基于全量数据 |
| Leaderboard排名 = 跟单价值 | 排名高应该跟 | 排名高可能是一次性事件（大选），或体育套利 | 需要多维度过滤 |

### 3.3 方法论坑

| 坑 | 说明 | 修正 |
|----|------|------|
| 胜率从50条数据计算 | 之前所有胜率数据都是错的 | 必须用offset=50步进全量分页 |
| "当前浮盈"可能是假信号 | 如果只下了1-2个仓位且恰好对了 | 需要看仓位数量和多样性 |
| 非大选PnL也可能不可复制 | Sandra.rachkova的非大选PnL来自"美国打击伊朗"系列 | 需要看盈利来源是否是单一事件 |
| 盈亏比比胜率更重要 | 37%胜率但15x盈亏比的tdrhrhhd净赚$2.4M | 评估鲸鱼时盈亏比>胜率 |
| 净PnL才是硬指标 | Sandra.rachkova看似盈利，实际净亏 | 必须计算 total_win_pnl + total_loss_pnl |

---

## 四、后续需要展开的方向

### 4.1 必须解决

- **全量胜率计算纳入筛选流程**：当前筛选脚本还在用50条分页，需要更新为offset=50步进
  - 对所有候选地址全量分页拉取closed-positions
  - 重新计算真实胜率、盈亏比、净PnL
  - 用净PnL而非总PnL作为筛选指标

- **实时监控**：当前是手动跑脚本，需要7×24轮询
  - 定时轮询 /positions?user={addr} 检测新仓位
  - 对比前后差异，发现新买入/卖出
  - 推送通知（Telegram/Discord webhook）

- **跟单执行**：发现鲸鱼买入后，如何在Polymarket上复制
  - CLOB API下单
  - 滑点控制
  - 仓位比例

### 4.2 值得深入

- **鲸鱼聚类**：把相似风格的鲸鱼分组
  - 地缘政治派（Magamyman, eCash, flydartball → 都赌伊朗方向）
  - 美联储派（tdrhrhhd → 赌加息/降息）
  - 科技/文化派（0xafEe → Google热搜，Kendrick Lamar等）
  - 当同一cluster的多个鲸鱼同时买入同一方向，信号更强

- **时间序列分析**：鲸鱼买入时机 vs 市场价格变动
  - 鲸鱼买入后，该市场的价格是否上涨？
  - 如果是，说明鲸鱼有信息优势
  - 如果不是，说明鲸鱼可能只是运气好

- **链上身份关联**：
  - Arkham Intelligence 可以把钱包地址关联到Twitter/Discord
  - 知道鲸鱼是谁，可以跟踪他们的公开观点
  - 但Arkham没有公开API

- **跨平台对冲检测**：
  - 鲸鱼是否同时在Polymarket和其他预测市场（Kalshi, Metaculus）下注？
  - 如果是，可能是跨平台套利，不是预测能力

### 4.3 核心结论（全量扫描修正版，2025-05）

**全量扫描关键发现**

对 Top 20 非bot候选地址进行了全量分页扫描（offset步进50），以下是最重要的新发现：

**发现一：排行榜前列 80% 是大选一次性赢家**

| 名称 | 净PnL | 大选PnL占比 | 非大选PnL | 活跃持仓 | 结论 |
|------|-------|-----------|----------|----------|------|
| Theo4 | $22M | 100% | -$19 | 0 | 不可复制 |
| Fredi9999 | $16.6M | 102% | -$329K | 0 | 不可复制，大选外亏损 |
| Len9311238 | $8.7M | 100% | $0 | 0 | 不可复制 |
| RepTrump | $7.5M | 100% | $0 | 0 | 不可复制 |
| PrincessCaro | $6.1M | 100% | $1K | 0 | 不可复制 |
| BetTom42 | $5.6M | 100% | $0 | 0 | 不可复制 |
| mikatrade77 | $5.1M | 100% | $0 | 0 | 不可复制 |
| alexmulti | $4.8M | 100% | $0 | 0 | 不可复制 |
| GCottrell93 | $4.6M | 98% | $102K | 9仓(-45%) | 大选外微利 |

**发现二：zerosmart 非大选PnL为负**

之前认为 zerosmart 是"平衡型"好鲸鱼（4.7x盈亏比），但全量数据显示 104% 的PnL来自大选，非大选PnL为 -$39K。大选赢家伪装成了可持续盈利者。

**发现三：KimballDavies 是单事件鲸鱼**

$567K净利润，10仓全胜，但100%来自 Solomon 公募一个事件。当前持仓全部归零。不可复制。

**发现四：ro0k 的对冲模式**

最大赢利和最大亏损来自同一个市场（泽连斯基穿西装 +540K/−465K，波兰总统选举 +302K/−240K），说明他在同一事件上反复下注/对冲，不是纯方向性判断。

**发现五：Dropper 的韩国政治配对交易**

最大赢利 +2.2M和最大亏损−1.1M 都来自韩国执政党候选人市场（金文洙 vs 韩德洙），这是配对对冲型操作，不是单向预测。

**发现六：Sandra.rachkova 的真实画像**

23.8%胜率、0.9x利润因子、净亏208K。但加密货币市场亏损 -$771K。本质是"赌黑天鹅"，不是可持续策略。

**值得跟单的鲸鱼特征（修正）**

- 净PnL为正（total_wins_pnl + total_losses_pnl > 0，不是只看盈利页）
- 利润因子 > 2x（赢的总金额 / 亏的总金额 > 2）
- 非大选PnL > $100K（证明不是一次性运气）
- 非体育类（体育是零和博弈，信息优势不可复制）
- 当前有活跃持仓且浮盈（判断正在被市场验证）
- 有明确的主攻领域（不是到处撒网）
- 买卖比 > 3（方向性下注，不是做市）
- 非单事件依赖（利润不集中在单一市场/事件）

**最终推荐跟单地址（全量数据修正版）**

| 排名 | 名称 | 胜率 | 利润因子 | 净PnL | 非大选PnL | 模式 | 持仓 | 跟单价值 |
|------|------|------|----------|-------|----------|------|------|----------|
| 1 | tdrhrhhd | 40% | 15.0x | $2.4M | $655K | 逆向高赔率+宏观 | 14仓+29% | 最高 |
| 2 | Magamyman | 72% | 6.0x | $806K | $811K | 伊朗地缘专注 | 14仓+11% | 高 |
| 3 | 0xafEe | 71% | 2.7x | $941K | $824K | 科技/文化/体育混合 | 1仓(-100%) | 中（不活跃） |
| 4 | Dropper | 65% | 1.6x | $1.9M | $1.6M | 超高频分散+韩国政治 | 44仓-37% | 中（盈亏比低） |
| 5 | Melody626 | 64% | 1.4x | $814K | $646K | 高频分散 | 145仓-26% | 中低 |
| 6 | ScarletRot | 75% | 1.7x | $336K | $554K | 保守热门 | 20仓-49% | 中低 |

**不建议跟的**

| 名称 | 原因 | 之前误判 |
|------|------|----------|
| Sandra.rachkova | 净亏$208K，0.9x利润因子，赌黑天鹅 | 50条限制时看似盈利$1.5M |
| zerosmart | 非大选PnL为-$39K，大选依赖104% | 之前认为"平衡型"好鲸鱼 |
| eCash | 净PnL仅$98K，1.1x利润因子 | 之前以为$682K |
| KimballDavies | 10仓全来自Solomon公募，单事件 | 看似100%胜率好鲸鱼 |
| ro0k | 1.2x利润因子，同市场对冲模式 | 看似$327K盈利 |
| 大选赢家 | Theo4/Fredi9999/RepTrump等，100%大选PnL | 排行榜前列但不可复制 |
| 体育鲸鱼 | 做市/套利，不可复制 | — |
| 当前持仓全归零 | 已不活跃或判断全错 | — |

---

## 五、跟单策略开源项目分析

### 5.1 项目概览

| 项目 | 语言 | 定位 | 延迟 | 复杂度 |
|------|------|------|------|--------|
| gamma-trade-lab/polymarket-copy-trading-bot | Rust | 高性能实时跟单执行引擎 | 亚秒级 | 高 |
| enviodev/poly-whale-tracker | TypeScript/Bun | 链上大单实时监控 TUI | ~1s | 低 |
| enviodev/polymarket-v2-indexer | TypeScript | Polymarket V2 全量链上索引器 | 分钟级 | 中 |
| GiordanoSouza/polymarket-copy-trading-bot | Python | Data API 轮询 + py-clob-client 下单 | 15-60s | 低 |
| Dadel-1/Polymarket-CopyTrading | Python | 自动鲸鱼发现 + 跟单 | 15-60s | 低 |
| Quicknode Copy Trading Guide | TypeScript | 教程级实现 | 中等 | 低 |

### 5.2 gamma-trade-lab/polymarket-copy-trading-bot（Rust）

仓库: https://github.com/gamma-trade-lab/polymarket-copy-trading-bot

目前最完善的开源跟单实现。生产级 Rust 代码，双信号源（链上确认 + Mempool 预确认），完整的风控和执行链路。

**检测机制**

双信号源架构:
- 链上确认 WebSocket（main.rs）: 订阅 Alchemy/Chainstack WSS 的 eth_subscribe("logs", ...)，过滤 OrderFilled 事件 + 目标鲸鱼地址（topics[2]），延迟 2-4s
- Mempool 预确认（mempool_monitor.rs）: 订阅 alchemy_pendingTransactions，用 SIMD 加速的 memchr::memmem 扫描交易 calldata 中的鲸鱼地址字节，延迟 <1s

事件解析（main.rs:1231-1286）:
```c
// makerAssetId == 0 → maker 支付 USDC → BUY
// takerAssetId == 0 → taker 支付 USDC → SELL（标记为 SELL_FILL）
price = usd / shares;
```

合约地址（settings.rs:34-38）:
- CTF Exchange V1: 0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E
- Conditional Tokens: 0x4d97dcd97ec945f40cf65f87097ace5ea0476045
- Neg Risk CTF Exchange V1: 0xC5d563A36AE78145C45a50134d48A1215220f80a

**注意**: 仅覆盖 V1 合约，V2 合约地址未更新。

**跟单策略**

| 策略 | COPY_SIZE 含义 | 计算方式 |
|------|---------------|----------|
| PERCENTAGE（默认） | 百分比 | whale_shares * (COPY_SIZE% / 100) * TRADE_MULTIPLIER |
| FIXED | 固定金额 | 每笔 COPY_SIZE USD |
| ADAPTIVE | 基础百分比 | 鲸鱼交易越大，跟单比例越小（min~max 范围） |

**阶梯执行（settings.rs:126-180）**:

| 鲸鱼份额 | 价格缓冲 | 订单类型 | 仓位乘数 | 最大重试 | 最大追价 |
|----------|---------|----------|---------|----------|----------|
| >= 4000 | +0.01 | FAK | 1.25x | 5 | +0.01 |
| >= 2000 | +0.01 | FAK | 1.0x | 4 | +0.00 |
| >= 1000 | +0.00 | FAK | 1.0x | 4 | +0.00 |
| < 1000 | +0.00 | FAK | 1.0x | 4 | +0.00 |
| 卖出 | +0.00 | GTD | 1.0x | — | — |

重试策略: FAK 失败 → 阶梯追价（仅大单第1次+0.01）→ 最终兜底 GTD（实时市场61s，非实时30min）

**风控**

熔断器（risk_guard.rs，代码未提交但 API 可推断）:

| 参数 | 默认值 | 说明 |
|------|--------|------|
| CB_LARGE_TRADE_SHARES | 1500 | 大单阈值 |
| CB_CONSECUTIVE_TRIGGER | 2 | 连续大单触发数 |
| CB_SEQUENCE_WINDOW_SECS | 30 | 时间窗口 |
| CB_MIN_DEPTH_USD | 200 | 最小订单簿深度 |
| CB_TRIP_DURATION_SECS | 120 | 熔断持续时间 |

两阶段检查: check_fast() 内存快速判断 → 若需验证则 fetch_book_depth() 查订单簿 → 深度不足则 trip() 触发熔断

其他保护: MAX_ORDER_SIZE_USD（100）、MIN_ORDER_SIZE_USD（1）、MOCK_TRADING 干跑模式、余额不足立即中止重试

**执行层**

双路径下单:
- 官方 SDK（orders.rs）: L1 ECDSA 签名，支持 EOA + Gnosis Safe，FOK/GTC/GTD
- 自建 CLOB Client（lib.rs）: L2 HMAC 认证（API Key + Secret + Passphrase），仅 EOA，连接池预热

精度处理: FAK 订单 USDC 2位小数 + shares 4位；限价单 USDC 4位 + shares 2位

**可借鉴点**

| 借鉴点 | 位置 | 说明 |
|--------|------|------|
| 阶梯执行 + FAK/GTD 重试链 | main.rs:766-943 | 大单先 FAK 快速成交，失败后阶梯追价，最终 GTD 兜底 |
| 两阶段风控 | main.rs:266-285 | 先内存检查（零延迟），再查订单簿深度（500ms超时） |
| Mempool 预确认 | mempool_monitor.rs:40-75 | SIMD 字节搜索 + pending tx 解析，亚秒级检测 |
| Token ID 缓存 | main.rs:42-46 | Thread-local HashMap<[u8;32], Arc<str>> 避免重复 U256→decimal |
| Hex nibble LUT | main.rs:1331-1355 | 查表法加速 hex 解码，2-3x 提速 |
| 体育市场额外缓冲 | settings.rs + tennis_markets.rs | 网球/足球市场额外 +0.01 缓冲对抗滑点 |
| 连接预热 | lib.rs:458-478 | 启动时 3 个并行 /time 请求预热 HTTP 连接池 |
| neg_risk 三级缓存 | lib.rs:331-349 | 全局 OnceLock → 本地 RwLock → API 查询 |

**局限**
- 仅单地址跟单: 每个实例只能跟踪一个鲸鱼，多鲸鱼需多实例
- V1 合约未更新: 未覆盖 V2 CTFExchange 地址
- 无持仓跟踪: 不维护累计仓位和 P&L，只有 CSV 日志
- 无鲸鱼评分: 完全依赖手动配置 TARGET_WHALE_ADDRESS
- strategy/risk_guard 模块源码缺失: 核心策略和风控逻辑未提交

---

### 5.3 enviodev/poly-whale-tracker（TypeScript/Bun）

仓库: https://github.com/enviodev/poly-whale-tracker

轻量级链上大单监控 TUI，~300 行核心代码，纯展示不执行。

**检测机制**

- 数据源: Envio HyperSync（https://polygon.hypersync.xyz），比 JSON-RPC 快 2000x
- 查询方式: 轮询 client.get(query)，从上一个已处理块继续推进
- 合约: V1 CTFExchange + NegRiskCTFExchange（与 Rust 版相同两个地址）
- 事件: OrderFilled，使用 Decoder.fromSignatures() 自动 ABI 解码

**过滤逻辑**

```javascript
// 仅显示 BUY 方向 + 金额 > 阈值 + 可选地址过滤
if (trade.direction !== "BUY") return false;
if (trade.usdc <= threshold) return false;             // 默认 $100
if (watchAddresses.length > 0 && !matchesAny(trade)) return false;
```

去重 key: `${txHash}-${orderHash}-${maker}-${makerAssetId}-${makerAmountFilled}-${takerAssetId}-${takerAmountFilled}`

**可借鉴点**

| 借鉴点 | 说明 |
|--------|------|
| HyperSync 集成模式 | 比 WebSocket 更适合历史回填，API 简洁 |
| ABI 自动解码 | Decoder.fromSignatures() 比 Rust 版手动 hex 解码更安全 |
| 去重 key 设计 | 多字段组合避免重复计数 |
| CLI 交互式阈值 | TUI 快捷键实时调整阈值和地址过滤 |

**局限**
- 纯展示，无执行能力
- 仅显示 BUY 方向，忽略 SELL
- 无价格/市场元数据（只有 token ID 和金额）
- 无持久化（重启丢失）
- V1 合约地址

---

### 5.4 enviodev/polymarket-v2-indexer（TypeScript）

仓库: https://github.com/enviodev/polymarket-v2-indexer

全量链上数据索引器，PostgreSQL + GraphQL API。不是跟单工具，而是数据基础设施——适合构建鲸鱼评分和历史回测。

**覆盖的 V2 合约**

| 合约 | 地址 | 事件 |
|------|------|------|
| CTFExchangeV2 #1 | 0xe111180000d2663c0091e4f400237545b87b996b | OrderFilled, OrdersMatched, FeeCharged |
| CTFExchangeV2 #2 | 0xe2222d279d744050d28e00520010520000310f59 | 同上 |
| CTFExchangeV2 #3 | 0xe2222d002000ba0053cef3375333610f64600036 | 同上 |
| PolyUSD | 0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb | Transfer, Wrapped, Unwrapped |
| Rewards | 0xdd8db71ce3be8d71ff148b2163d64da181a29e8b | DistributedRewards, MarketCreated, Sponsored |

**对鲸鱼跟踪的查询能力**

| 查询 | GraphQL 示例 |
|------|-------------|
| 某地址所有交易 | OrderFills(where: {maker: "0x..."}) |
| 大单过滤 | OrderFills(where: {makerAmountFilled_gt: 100000000}) |
| pUSD 余额排名 | PolyUSDAccounts(orderBy: balance, orderDirection: desc) |
| Builder 来源分析 | OrderFills(where: {builder_not: "0x00..."}) |

**可借鉴点**

| 借鉴点 | 说明 |
|--------|------|
| V2 合约地址清单 | 唯一覆盖 V2 的开源项目，3个 CTFExchangeV2 地址 |
| Gamma API 元数据富化 + Effect 缓存 | 每个 tokenId 只获取一次市场信息，280 req/10s 限速 |
| GraphQL 标准查询接口 | 方便上层策略做回测和鲸鱼发现 |
| PolyUSD 流向追踪 | 预判市场资金方向 |
| Builder Code 追踪 | V2 新增字段，分析订单来源 |

**局限**
- 批量索引非实时（分钟级延迟），不适合跟单信号检测
- Gamma API 1s 超时可能导致 market_id 为 null
- 不支持按地址聚合交易量（需新增 TraderStats 实体）
- 无鲸鱼评分、地址标签

---

### 5.5 GiordanoSouza/polymarket-copy-trading-bot（Python）

仓库: https://github.com/GiordanoSouza/polymarket-copy-trading-bot

Python 实现的跟单 bot。使用 Supabase 存储鲸鱼白名单和交易历史，MongoDB 记录跟单执行。通过 Data API 轮询鲸鱼持仓变化，检测到新仓位后通过 py-clob-client 下单。

**可借鉴点**

| 借鉴点 | 说明 |
|--------|------|
| Data API 轮询模式 | 最简单的检测方式，适合慢鲸鱼策略（24h+ 持仓） |
| py-clob-client 集成 | 官方 Python SDK 的实际使用示例 |
| Supabase 白名单管理 | 鲸鱼地址的增删改查，可扩展为评分系统 |
| MongoDB 执行记录 | 跟单历史、滑点、成交率的持久化 |

**局限**
- 延迟 15-60s（Data API 轮询间隔）
- 无链上事件监听
- 无风控/熔断机制
- 无自适应仓位计算

---

### 5.6 Dadel-1/Polymarket-CopyTrading（Python）

仓库: https://github.com/Dadel-1/Polymarket-CopyTrading

Python 跟单 bot。特色是自动鲸鱼发现——从 Data API leaderboard 动态获取高胜率交易者列表，然后按比例复制其仓位。

**可借鉴点**

| 借鉴点 | 说明 |
|--------|------|
| 自动鲸鱼发现 | 从 leaderboard 动态获取 Top Trader，不需手动配置地址 |
| 胜率+收益筛选 | 多维度过滤（但维度较少，只有胜率和总收益） |
| 比例复制仓位 | 按用户资金比例复制鲸鱼仓位，而非固定金额 |

**局限**
- 筛选维度简单（无利润因子、非大选PnL过滤、bot检测等）
- 无实时链上监控
- 无风控机制

---

### 5.7 Quicknode Copy Trading Bot 教程

URL: https://www.quicknode.com/guides/defi/polymarket-copy-trading-bot

TypeScript 教程级实现。Data API（REST 轮询）+ WebSocket（实时价格）+ CLOB Client（执行）。

**可借鉴点**

| 借鉴点 | 说明 |
|--------|------|
| 完整端到端流程 | 从检测到下单的完整代码示例 |
| Data API + CLOB 结合 | 轮询持仓变化 + 下单执行的参考实现 |
| 限价单策略 | 使用 GTC 限价单而非市价单，控制滑点 |

**局限**
- 仅 BUY 方向演示
- 无风控
- 教程级代码，不适合生产

---

### 5.8 开源项目 vs 我们的需求对比

| 能力 | Rust 跟单 bot | Whale Tracker | V2 Indexer | Python 跟单 | Dadel 跟单 | 我们的需求 |
|------|--------------|---------------|------------|-------------|------------|-----------|
| 实时链上检测 | WSS+Mempool | HyperSync | 批量索引 | Data API 轮询 | Data API 轮询 | Data API 轮询（慢鲸鱼够用） |
| 鲸鱼评分 | 无 | 无 | 无 | 无 | 简单胜率 | 多维评分（利润因子/非大选PnL/bot检测） |
| 跟单执行 | 完整 | 无 | 无 | 基础 | 基础 | py-clob-client |
| 风控/熔断 | 完整 | 无 | 无 | 无 | 无 | 必须 |
| 多鲸鱼并行 | 多实例 | 地址列表 | N/A | 白名单 | 动态列表 | 动态评分+白名单 |
| 持仓跟踪 | 无 | 无 | 无 | 无 | 无 | 必须（计算当前风险敞口） |
| V2 合约 | 未更新 | V1 | V2 | N/A | N/A | 需覆盖 V1+V2 |
| 历史回测 | 无 | 无 | GraphQL | 无 | 无 | 必须（验证策略有效性） |

### 5.9 我们应借鉴的架构

```
┌─────────────────────────────────────────────────────────────┐
│                    鲸鱼筛选层（我们已有）                       │
│  Data API leaderboard → 评分 → 过滤bot/大选 → 推荐地址列表     │
│  [pm_whale_screen.py + pm_full_scan.py]                     │
├─────────────────────────────────────────────────────────────┤
│                    实时监控层（待实现）                         │
│  方案A: Data API 轮询 /positions?user={addr}  (60s间隔)       │
│  方案B: HyperSync OrderFilled 事件流 (1-2s延迟)               │
│  方案C: Alchemy WSS eth_subscribe (亚秒级)                    │
│  → 检测新仓位/仓位变化 → 推送信号                              │
├─────────────────────────────────────────────────────────────┤
│                    风控层（借鉴 Rust bot）                     │
│  两阶段检查: 内存快速判断 → 订单簿深度验证                      │
│  熔断器: 连续大单触发 → 暂停交易                               │
│  仓位限制: 单市场/总敞口/日亏损上限                             │
│  市场过滤: 流动性阈值、体育市场排除、大选相关排除               │
├─────────────────────────────────────────────────────────────┤
│                    执行层（借鉴 Rust bot + py-clob-client）    │
│  限价单为主（GTC/GTD），避免滑点                               │
│  阶梯追价: FAK → +0.01 → +0.02 → GTD兜底                     │
│  体育市场额外缓冲 (+0.01)                                     │
│  neg_risk 缓存（避免每次查询）                                 │
├─────────────────────────────────────────────────────────────┤
│                    持仓跟踪层（开源项目均缺失，需自建）          │
│  维护每个鲸鱼和自身的持仓快照                                  │
│  计算未实现PnL、总敞口、市场集中度                              │
│  定期与 Data API /positions 同步                               │
└─────────────────────────────────────────────────────────────┘
```

**关键差异**：开源项目都没有"鲸鱼评分"这一层，它们假设你已经知道跟谁。我们的核心价值在于筛选出真正值得跟的鲸鱼（利润因子 > 2x、非大选PnL > $100K、非bot、活跃持仓），这是现有开源生态的空白。

---

## 六、从“找鲸鱼”到“可交易信号”

### 6.1 策略核心假设

跟单策略不是简单复制所有买入，而是验证一个更窄的假设：

> 少数低频、方向性、非体育、非一次性事件依赖的钱包，在特定主题上拥有信息优势；当这些钱包新建仓或明显加仓时，市场价格尚未完全反映该信息，跟随者可以在可控滑点内获取正期望。

这个假设拆成四个可检验条件：

| 条件 | 验证方式 | 失败信号 |
|------|----------|----------|
| 鲸鱼有真实 edge | 全量 closed positions 的非大选净PnL、利润因子、主题稳定性 | 非大选PnL接近0或来自单一事件 |
| 信号有前瞻性 | 买入后 1h/6h/24h/到期价格或结算结果优于基准 | 买入后价格无方向性，或只在成交瞬间跳价 |
| 跟单可成交 | 下单时盘口深度足够，预估滑点低 | best ask 被打穿，实际成交价明显劣化 |
| 风险可控 | 单市场、单主题、单鲸鱼、日亏损上限可执行 | 同一事件多鲸鱼同源重仓导致集中爆仓 |

### 6.2 信号定义

Data API 轮询模式下，不直接看到原始成交，只能通过持仓快照差分推断。

**快照字段**

| 字段 | 来源 | 用途 |
|------|------|------|
| wallet | 目标钱包 | 归因 |
| token_id / asset | positions | 市场与 outcome 标识 |
| condition_id | positions / market metadata | 同一市场聚合 |
| title / slug | positions | 主题分类、过滤体育/大选 |
| outcome | positions | YES/NO 或具体温度/候选人 |
| size | positions | 当前持仓份额 |
| avg_price | positions | 鲸鱼成本 |
| cur_price | positions | 当前价格 |
| initial_value | positions | 初始投入 |
| current_value | positions | 当前价值 |
| timestamp | 本地采集时间 | 差分排序 |

**差分规则**

| 快照变化 | 信号 | 解释 |
|----------|------|------|
| token 从无到有，size > min_size | OPEN_BUY | 新建仓，最高价值 |
| size 增加超过 max(绝对阈值, 百分比阈值) | ADD_BUY | 加仓，次高价值 |
| size 减少但仍 > 0 | REDUCE_SELL | 减仓，不一定要跟 |
| token 消失或 size≈0 | EXIT_SELL | 清仓，可作为退出信号 |
| cur_price 大幅下跌但 size 不变 | HOLD_DRAWDOWN | 不交易，只记录信念 |
| cur_price 大幅上涨但 size 不变 | HOLD_PROFIT | 不追涨，除非多鲸鱼共振 |

**建议初始阈值**

| 参数 | 初始值 | 说明 |
|------|--------|------|
| min_whale_position_usd | $500 | 小仓位不跟，可能是测试单 |
| min_delta_usd | $250 | 差分投入低于该值忽略 |
| min_delta_pct | 20% | 加仓幅度阈值 |
| max_signal_age_sec | 180 | 超过3分钟的信号不追 |
| max_price_chase | 0.03 | 当前价比推断成交价高3c以上不追 |
| min_market_liquidity_usd | $1,000 | 盘口深度不足不下单 |

### 6.3 信号分级

| 等级 | 条件 | 动作 |
|------|------|------|
| A | 白名单鲸鱼 + OPEN/ADD + 非体育 + 非大选 + 主题匹配 + 当前价未劣化 | 可 paper / 小额实盘 |
| B | 白名单鲸鱼 + OPEN/ADD，但主题不在优势圈或价格已劣化 | 只 paper，观察 |
| C | 候选鲸鱼但未通过完整评分 | 只记录，不下单 |
| D | 体育/大选/单事件/高频做市/低流动性 | 丢弃 |

实际执行时只允许 A 级进入真实下单；B/C 级用于扩充研究样本。

### 6.4 主题匹配比“全市场跟随”更重要

每个鲸鱼只在自己的优势主题内跟。

| 鲸鱼类型 | 可跟主题 | 禁跟主题 |
|----------|----------|----------|
| tdrhrhhd | 宏观、利率、少量政治长shot | 体育、娱乐、明显大选复刻 |
| Magamyman | 伊朗/中东/地缘政治 | crypto 短线、体育 |
| 0xafEe | 科技/文化事件、小众信息市场 | 当前不活跃时不跟 |
| Dropper | 高频政治事件可观察 | 直接实盘慎用，利润因子偏低 |
| ScarletRot | 保守热门可观察 | 当前持仓浮亏大时降权 |

策略不应该把“好钱包”理解成所有市场都好。更合理的表达是：

```
signal_score = wallet_quality * topic_fit * signal_freshness * liquidity_fit * price_quality
```

---

## 七、回测设计

### 7.1 回测目标

回测要回答的问题不是“这个鲸鱼历史赚了多少钱”，而是：

1. 如果我们在观测到鲸鱼建仓后延迟 X 秒/分钟买入，是否还能赚钱？
2. 最优跟单窗口是 OPEN_BUY 还是 ADD_BUY？
3. 跟到哪个价格就应该放弃？
4. 是否应该跟卖出信号？
5. 多鲸鱼共振是否优于单鲸鱼信号？

### 7.2 事件回放框架

理想数据源是链上 OrderFilled；如果暂时没有，就用 Data API 快照近似。

**数据层**

| 数据 | 最低可用方案 | 更好方案 |
|------|--------------|----------|
| 鲸鱼交易时间 | positions 快照差分时间 | 链上 OrderFilled block timestamp |
| 鲸鱼成交价 | avg_price 差分估算 | OrderFilled maker/taker amount |
| 市场后续价格 | 定期 CLOB price snapshot | orderbook mid / last trade |
| 结算结果 | closed-positions / Gamma resolution | 链上 redeem / final payout |

**回放步骤**

```text
1. 构建 wallet_snapshot 表，每 60s 采集目标钱包 positions
2. 对相邻快照做 diff，生成 whale_signal
3. 对每个 OPEN/ADD 信号，查当时市场盘口或最近价格
4. 模拟延迟：0s / 30s / 60s / 180s / 600s
5. 按执行规则生成 paper order
6. 用后续价格或结算结果计算 PnL
7. 分 wallet/topic/signal_type/latency/liquidity 分桶评估
```

### 7.3 回测评价指标

| 指标 | 含义 | 目标 |
|------|------|------|
| signal_count | 可交易信号数 | 太少无法部署，太多说明过滤不够 |
| fill_rate | 模拟可成交比例 | >70% |
| avg_slippage | 跟单价 - 信号价 | <2c |
| roi_to_resolution | 持有到结算 ROI | 正 |
| markout_1h / 6h / 24h | 买入后价格变化 | 判断短期 alpha |
| max_drawdown | paper 权益曲线最大回撤 | 控制仓位 |
| profit_factor | 总盈利 / 总亏损绝对值 | >1.5 才值得继续 |
| topic_hit_rate | 主题内胜率 | 用于验证 topic_fit |

### 7.4 关键对照组

没有对照组，跟单策略很容易把市场 beta 当 alpha。

| 对照组 | 目的 |
|--------|------|
| 随机同类别市场买入 | 检验鲸鱼是否优于类别基准 |
| 同市场延迟买入 | 检验信号衰减速度 |
| 跟所有 leaderboard 地址 | 检验筛选层是否有价值 |
| 只买价格上涨市场 | 排除动量本身的贡献 |
| 反向跟单 | 检验是否只是追高亏损 |

### 7.5 需要避免的回测偏差

| 偏差 | 说明 | 修正 |
|------|------|------|
| 未来函数 | 用已结算胜率筛出鲸鱼后回测同一时期 | 按时间滚动筛选，训练期和测试期分离 |
| 幸存者偏差 | 只看当前排行榜赢家 | 保存每次 leaderboard 快照 |
| 成交偏差 | 假设能以 cur_price 成交 | 用盘口深度模拟，设置滑点上限 |
| 延迟偏差 | 假设看到信号即刻成交 | 测 30s/60s/180s 延迟 |
| 主题泄漏 | 事后知道某市场属于盈利主题 | 分类规则必须在回测前固定 |
| 单事件污染 | 一个大事件贡献全部收益 | 按 condition/event 分组限权 |

---

## 八、执行与风控口径

### 8.1 仓位计算

不建议按鲸鱼 size 等比例复制，因为鲸鱼资金规模、风险承受和信息来源都不同。更稳妥的是“信号分数 × 固定风险预算”。

```text
base_notional = bankroll * risk_per_signal
copy_notional = base_notional
              * wallet_weight
              * topic_weight
              * signal_type_weight
              * liquidity_weight
              * price_quality_weight
```

建议初始参数：

| 参数 | 初始值 | 说明 |
|------|--------|------|
| bankroll | 手动配置 | paper 和 live 分开 |
| risk_per_signal | 0.25% - 1.0% | 单信号基础风险 |
| max_notional_per_order | $25 - $100 | 早期小额验证 |
| max_notional_per_market | 2% bankroll | 同一 condition 上限 |
| max_notional_per_wallet | 5% bankroll | 防止单鲸鱼失效 |
| max_notional_per_topic | 10% bankroll | 防止主题集中 |
| daily_loss_limit | 2% bankroll | 触发后停止当天新开仓 |

### 8.2 价格规则

| 场景 | 规则 |
|------|------|
| BUY YES/NO | 使用 best_ask + buffer 的限价单，不用无保护市价单 |
| SELL | 使用 best_bid - buffer 的限价单 |
| 价格 < 0.05 | 默认不跟，接近彩票且容易归零 |
| 价格 > 0.90 | 默认不追，收益空间不足 |
| 信号价未知 | 用当前 mid 作为近似，但降级为 B 级 |
| 当前价比鲸鱼 avg_price 高 > 0.03 | 不追，除非多鲸鱼共振 |

### 8.3 退出规则

跟单最大的风险是只跟买、不知道什么时候卖。初期建议把退出规则写死，便于回测。

| 退出类型 | 规则 |
|----------|------|
| 鲸鱼清仓 | 如果目标钱包 size 归零，跟随减仓 50%-100% |
| 鲸鱼明显减仓 | 如果 size 下降 >50%，跟随减仓 50% |
| 止损 | 从入场价下跌 35%-50% 或 thesis broken |
| 止盈 | 价格上涨 50%-100% 后卖一半，剩余跟随鲸鱼 |
| 到期 | 临近 resolution 且流动性恶化时主动降仓 |
| 时间止损 | OPEN 后 7 天无正 markout，且非长周期市场，退出 |

注意：止损不能太紧。很多预测市场会在信息释放前长时间横盘或浮亏，过紧止损会把鲸鱼 edge 变成跟单者亏损。

### 8.4 明确禁止下单的情况

| 禁止条件 | 原因 |
|----------|------|
| 体育单场 / spread / over-under | 信息和延迟优势不可复制 |
| 大选类一次性复刻市场 | 历史样本污染严重 |
| 流动性 < $1,000 或价差 > 5c | 跟单成本太高 |
| 同一事件已有重仓 | 防止相关性爆仓 |
| 鲸鱼当前持仓整体大幅浮亏且继续加仓 | 可能是 martingale |
| 信号来自当前不活跃钱包突然小单 | 可能是测试单或噪音 |
| 市场接近结算但规则不清 | resolution risk |
| 标题/规则无法机器解析 | 显式失败，不靠猜 |

---

## 九、数据模型建议

### 9.1 最小可用 SQLite 表

先用 SQLite 足够，等信号量变大再考虑 PostgreSQL。

```sql
CREATE TABLE whale_wallets (
  wallet TEXT PRIMARY KEY,
  label TEXT,
  status TEXT NOT NULL,              -- candidate / watch / paper / live / banned
  quality_score REAL NOT NULL,
  wallet_type TEXT,                  -- macro / geopolitics / culture / high_freq / etc.
  notes TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE whale_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  wallet TEXT NOT NULL,
  captured_at TEXT NOT NULL,
  token_id TEXT NOT NULL,
  condition_id TEXT,
  market_slug TEXT,
  title TEXT,
  outcome TEXT,
  size REAL,
  avg_price REAL,
  cur_price REAL,
  initial_value REAL,
  current_value REAL,
  raw_json TEXT NOT NULL
);

CREATE TABLE whale_signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  wallet TEXT NOT NULL,
  detected_at TEXT NOT NULL,
  signal_type TEXT NOT NULL,          -- OPEN_BUY / ADD_BUY / REDUCE_SELL / EXIT_SELL
  token_id TEXT NOT NULL,
  condition_id TEXT,
  market_slug TEXT,
  topic TEXT,
  outcome TEXT,
  inferred_delta_size REAL,
  inferred_delta_usd REAL,
  whale_avg_price REAL,
  observed_price REAL,
  signal_score REAL,
  decision TEXT NOT NULL,             -- ignore / observe / paper / live
  reason TEXT
);

CREATE TABLE copy_paper_orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  signal_id INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  side TEXT NOT NULL,
  token_id TEXT NOT NULL,
  notional_usd REAL NOT NULL,
  limit_price REAL NOT NULL,
  simulated_fill_price REAL,
  simulated_size REAL,
  status TEXT NOT NULL,
  exit_price REAL,
  exit_at TEXT,
  pnl_usd REAL,
  roi REAL
);
```

### 9.2 文件产物约定

建议先落在策略目录或 runtime 下，避免混入 weather 主线。

```text
runtime/copy_trade/
  whale.db
  snapshots/YYYY-MM-DD/*.jsonl
  reports/
    daily_signal_summary.json
    wallet_scoreboard.json
    paper_pnl_summary.json
```

### 9.3 每日研究报表

每天自动生成一份简短 JSON/Markdown：

| 模块 | 内容 |
|------|------|
| watchlist_status | 每个钱包是否活跃、持仓数、浮盈 |
| new_signals | 新建仓/加仓/减仓 |
| paper_orders | 今日模拟跟单成交与未成交 |
| pnl | 当日 mark-to-market 和已结算PnL |
| exceptions | API失败、字段异常、无法解析市场 |
| candidate_changes | 新增/移除候选钱包及原因 |

---

## 十、近期实验路线图

### 10.1 P0：把研究闭环跑通

1. 固化 watchlist：先只放 tdrhrhhd、Magamyman、0xafEe、Dropper、ScarletRot。
2. 每 60 秒采集一次 `/positions?user=`，写入 SQLite 和 JSONL。
3. 做 snapshot diff，生成 OPEN/ADD/REDUCE/EXIT 信号。
4. 所有信号先只 paper，不真实下单。
5. 每日输出 signal + paper PnL 报表。

验收标准：

| 项目 | 标准 |
|------|------|
| 采集稳定性 | 连续运行24h，无静默中断 |
| 信号去重 | 同一持仓变化不重复报警 |
| 可解释性 | 每个 ignore/paper 决策都有 reason |
| paper账本 | 能按 wallet/topic/signal_type 聚合 |

### 10.2 P1：验证 alpha

1. 跑满 2-4 周 paper。
2. 对每个信号计算 1h/6h/24h markout。
3. 比较 A/B/C 信号分层表现。
4. 比较 topic_fit=true vs false。
5. 比较 OPEN_BUY vs ADD_BUY。
6. 找出应该“跟买但不跟卖”还是“买卖都跟”。

继续推进到小额实盘的最低门槛：

| 条件 | 门槛 |
|------|------|
| A级信号数 | >= 30 |
| A级 profit_factor | > 1.5 |
| A级 avg 24h markout | > 0 |
| 最大回撤 | 可接受且来自可解释事件 |
| 成交滑点 | 平均 < 2c |
| 报警/账本 | 无明显漏记 |

### 10.3 P2：小额实盘

只在满足 P1 后开启，且初期保持硬边界。

| 风控 | 初始值 |
|------|--------|
| max_order_usd | $10 - $25 |
| max_daily_notional | $100 |
| max_open_positions | 10 |
| daily_loss_limit | $25 |
| kill_switch | 必须有 |
| live_wallets | 只允许 tdrhrhhd / Magamyman 中 paper 表现更好的 |

小额实盘目标不是赚钱，而是验证：

- CLOB 下单链路是否稳定
- 真实滑点和 paper 假设差多少
- 取消/部分成交/失败订单怎么记录
- 价格剧烈变化时风控是否真的阻断

### 10.4 P3：链上实时化

如果 Data API 轮询证明有 alpha，再考虑链上事件流。

优先级：

1. HyperSync 历史回填和近实时 OrderFilled。
2. Polygon JSON-RPC logs 订阅。
3. Alchemy pending tx / mempool 预确认。

不建议一开始做 mempool。当前目标是验证慢鲸鱼的可复制 edge，延迟 60 秒如果已经赚不到钱，亚秒级优化也可能只是把策略变成低流动性抢跑。

---

## 十一、当前结论更新

1. 当前最有研究价值的不是“排行榜复制”，而是“主题限定的慢鲸鱼信号”。
2. Data API 轮询足够作为第一阶段，因为推荐地址多数不是高频做市型。
3. 筛选层必须坚持全量 closed-positions 分页，否则胜率和利润因子会严重失真。
4. 大选/体育/单事件依赖是三类最大污染源，宁可漏掉也不要放进 live。
5. 真正要验证的是信号 markout 和可成交性，而不是鲸鱼历史净PnL。
6. 实盘前必须先有 paper 账本；没有跟单成交率、滑点、退出表现，就不能判断策略是否可复制。

---

## 十二、MVP 策略：Wallet-Centric Smart Money Research

### 12.1 核心定位

当前阶段不做重型 entity clustering，不试图复刻 Arkham/Nansen。

策略定位是：

> 找到一批中低频、有研究能力或信息优势的钱包；当它们新建仓或明显加仓时，把这个动作当作“研究触发器”，由 AI 快速研究市场规则、事实、价格和钱包历史主题能力，再决定是否 paper/live 跟单。

这不是高频套利策略，也不是无脑复制策略。它更像：

```text
聪明钱包发现机会 → 我们被提醒 → AI 二次研究 → 有条件跟单
```

### 12.2 必须保留的前提：Address != Entity

虽然 MVP 以 wallet 为基本对象，但任何判断都必须带着这个前提：

> 一个 Polymarket 地址不一定等于一个真实交易主体。

可能情况：

| 风险 | 说明 | 对策略的影响 |
|------|------|--------------|
| 多地址同人 | 同一个人可能有主号、小号、试单号、对冲号 | 单地址 conviction 可能被高估 |
| 对冲腿不可见 | 我们看到 A 地址买 YES，但同人 B 地址买 NO | 地址级 PnL/方向可能误导 |
| proxy wallet 分离 | Polymarket 用户常见 proxy wallet / funder 结构 | proxyWallet 不是完整资金身份 |
| 组合交易 | 一个地址只是组合的一部分 | 不能把单腿当成完整观点 |
| 外部标签缺失 | Arkham/Nansen 可能知道实体，但我们未必有 API/覆盖 | 默认不强行合并实体 |

因此策略里要有两条硬规则：

1. **不因为一个地址历史盈利就自动跟单。**
2. **一笔信号必须经过市场级 AI 研究和风险检查。**

当前阶段只做轻量相关地址记录：

```json
{
  "possible_related_wallets": [
    {
      "wallet": "0x...",
      "reason": "same market opposite leg / same funder / external manual label",
      "confidence": 0.35
    }
  ]
}
```

这类信息只用于降权、备注和人工复核，不作为自动合并依据。

### 12.3 MVP 数据对象

先保留 5 个核心对象，避免过度设计。

#### A. wallets：地址画像

```text
wallet_address
display_name
status              candidate / watch / paper / live / rejected
tags_json           主题、行为、质量标签
traits_json         结构化画像
first_seen_at
last_seen_at
notes
```

推荐 tags：

```text
主题标签:
  geopolitics / macro / crypto / tech / culture / politics / sports / weather

行为标签:
  low_freq / mid_freq / high_freq
  directional / hedged / market_maker_like
  concentrated / diversified
  early_positioner / late_chaser

质量标签:
  research_driven
  information_edge_possible
  active_recently
  inactive
  election_polluted
  single_event_dependent
  sports_noise
```

`traits_json` 示例：

```json
{
  "primary_topics": ["geopolitics", "macro"],
  "trade_frequency": "low",
  "profit_factor": 4.6,
  "non_election_pnl": 320000,
  "sports_ratio": 0.04,
  "single_event_dependency": 0.18,
  "buy_sell_ratio": 8.2,
  "avg_entry_price": 0.38,
  "current_open_positions": 9,
  "current_unrealized_roi": 0.18,
  "address_entity_warning": "single wallet only; related wallets not fully known",
  "style_summary": "低频地缘政治方向下注，偏早期建仓，较少做市迹象"
}
```

#### B. wallet_discoveries：发现证据

记录地址是怎么来的，不判断好坏。

```text
wallet_address
method              leaderboard / active_market_holder / active_market_trade / thematic_scan / seed_expand / manual
source_ref          leaderboard:POLITICS:ALL / market:<condition_id> / seed:<wallet>
evidence_json
discovered_at
strength
tags_json
```

#### C. wallet_reviews：地址深度评审

对候选地址做 full scan 后生成。

```text
wallet_address
reviewed_at
score
verdict             reject / watch / paper_candidate / live_candidate
metrics_json
ai_notes_json
summary
```

#### D. wallet_signals：钱包动作信号

从 positions 快照差分或链上事件生成。

```text
signal_id
wallet_address
detected_at
signal_type         OPEN_BUY / ADD_BUY / REDUCE_SELL / EXIT_SELL
condition_id
token_id
market_slug
title
outcome
delta_size
delta_notional
wallet_avg_price
observed_price
raw_json
```

#### E. wallet_signal_research_reports：AI 二次研究报告

这是 MVP 最关键的产物。

```text
signal_id
researched_at
market_summary
resolution_rules
key_facts_json
wallet_topic_fit
price_assessment
risks_json
related_wallet_warning
decision            follow / observe / reject
max_entry_price
suggested_notional
exit_plan
summary
```

### 12.4 搜索地址策略

发现层要宽，分析层要严。

#### 方法一：Leaderboard Scan

用途：找历史盈利账户。

```text
categories = OVERALL / POLITICS / CRYPTO / ECONOMICS / TECH / SPORTS
periods = ALL / MONTH / WEEK
offset = 0..950 step 50
```

处理方式：

- `SPORTS` 不直接丢弃，先标记 `sports_noise`。
- `ALL` 榜单高 PnL 地址必须检查大选污染。
- `MONTH/WEEK` 更适合发现近期活跃地址。
- 多 category 重复出现的地址优先级更高。

#### 方法二：Active Market Scan

用途：找当前正在下注的人。

流程：

```text
Gamma 拉 active + closed=false + volume24hr 排名前 N 市场
过滤 weather 主线市场
每个市场拉:
  /holders
  /trades
```

重点市场：

- geopolitics：Iran / Ukraine / Russia / China / Taiwan / Israel
- macro：Fed / CPI / inflation / tariff / recession
- crypto：BTC / ETH / SOL / stablecoin
- tech/culture：OpenAI / Nvidia / Tesla / Oscar / Grammy

#### 方法三：Thematic Scan

用途：围绕我们认为有 alpha 的主题深挖。

例子：

```text
Iran 系列市场 → holders/trades → 地址池
Fed/CPI 系列市场 → holders/trades → 地址池
BTC 价格区间市场 → holders/trades → 地址池
```

这个比全站扫更重要，因为我们不是找“所有会赚钱的人”，而是找“在某类主题上可能有信息优势的人”。

#### 方法四：Seed Expand

用途：从已知好地址扩展同圈层。

对每个 seed wallet：

```text
1. 拉当前 open positions
2. 拉历史盈利最高的 closed positions
3. 对这些 condition_id 反查 holders/trades
4. 找同市场出现的大户
5. 标记为 seed_neighbor
```

注意：seed expand 很容易扩出同事件污染地址，所以只进 candidate，不直接进 watch。

#### 方法五：Manual Intel

用途：人工从 Arkham/Nansen/Twitter/社区看到地址时录入。

外部平台只作为 evidence：

```text
method = manual_external_label
source_ref = arkham:<url> / nansen:<url>
tags = external_label, needs_verification
```

不因为外部标签直接 live。

### 12.5 地址评审规则

每个候选地址都要经过全量分页，而不是只看前 50 条。

最低指标：

| 指标 | 初始门槛 | 说明 |
|------|----------|------|
| closed_positions_count | >= 30 | 样本太小只观察 |
| non_election_pnl | > 0 | 大选外必须能赚钱 |
| profit_factor | > 1.5 | 低于此不值得跟 |
| sports_ratio | < 50% | 体育占比高默认噪音 |
| single_event_dependency | < 70% | 单事件贡献过高降权 |
| current_open_positions | > 0 | 完全不活跃降权 |
| trade_frequency | low/mid | 高频做市不适合我们 |

直接 reject：

```text
净PnL为负
非大选PnL为负
体育/盘口占比极高
利润来自单一事件
高频买卖比接近 1
当前持仓全归零且长期不活跃
```

进入 watch：

```text
非大选盈利
主题清晰
中低频
方向性强
近期有 open positions
没有明显做市/对冲污染
```

进入 paper_candidate：

```text
watch 地址近期信号通过 AI 市场研究
价格没有明显追高
市场规则清晰
流动性足够
该主题和钱包历史优势匹配
```

### 12.6 信号触发与 AI 研究

钱包动作只负责触发研究，不直接触发下单。

信号等级：

| 信号 | 价值 | 动作 |
|------|------|------|
| OPEN_BUY | 高 | 必须生成 AI research report |
| ADD_BUY | 中高 | 如果加仓幅度足够，生成报告 |
| REDUCE_SELL | 中 | 更新退出观察 |
| EXIT_SELL | 高 | 检查是否需要 paper/live 退出 |
| HOLD_PROFIT | 低 | 只记录 |
| HOLD_DRAWDOWN | 中 | 检查是否 thesis broken |

AI 研究必须回答：

```text
1. 这个市场具体如何结算？
2. 当前价格隐含概率是多少？
3. 公开事实支持哪一边？
4. 这个钱包历史上是否擅长该主题？
5. 这笔单是早期建仓还是追涨？
6. 是否有其他 watch 钱包同向？
7. 是否存在同一事件对冲/反向腿风险？
8. 流动性和价差是否允许跟？
9. 最多能接受什么入场价？
10. 退出条件是什么？
```

AI 决策输出：

```text
decision = follow / observe / reject
confidence = 0..1
max_entry_price
suggested_notional
exit_plan
main_risks
```

### 12.7 跟单规则

初期只 paper，不 live。

paper 入场：

```text
wallet.status in paper_candidate
signal_type in OPEN_BUY / ADD_BUY
AI decision = follow
current_price <= max_entry_price
spread <= 5c
market_liquidity_usd >= 1000
not sports_noise
not election_polluted
```

paper 仓位：

```text
base_notional = $25
高信心 + 主题匹配 + 多钱包共振: $50
低流动性 / 规则复杂 / 地址实体风险高: $10 or observe
```

退出：

```text
钱包清仓 → 减仓或退出
AI thesis broken → 退出
价格达到 2x → 卖一半
价格跌破入场价 40%-50% → 复核后止损
临近结算且规则不清 → 主动退出
```

### 12.8 与 Entity 问题的折中处理

当前不做完整 entity 系统，但每次研究报告要给一个 `address_entity_risk`：

| 风险等级 | 条件 | 处理 |
|----------|------|------|
| low | 地址行为独立、方向清晰、无明显对冲腿 | 正常评估 |
| medium | 同事件存在疑似相关地址/反向腿，但证据弱 | 降低仓位 |
| high | 明确同 funder/外部标签/强同步对冲 | 不跟或只观察 |
| unknown | 没查到任何相关信息 | 默认 medium-low，不加分 |

这个字段的目的不是解决 entity clustering，而是防止我们把单地址误当完整主体。

### 12.9 Agent 分工

#### Agent 1：Wallet Discovery

目标：不断找候选地址。

职责：

```text
1. 跑 leaderboard scan
2. 跑 active market scan
3. 跑 thematic scan
4. 跑 seed expand
5. 写 wallets + wallet_discoveries
6. 生成 candidate shortlist
```

不做：

```text
不判断最终能不能跟
不做 live 决策
不做复杂 entity 合并
```

#### Agent 2：Wallet Analyst

目标：判断地址是否值得监控，以及信号是否值得研究。

职责：

```text
1. 对 shortlist 做 closed-positions 全量分页
2. 计算 wallet metrics
3. 打 tags 和 traits
4. 输出 wallet_reviews
5. 监控 wallet_signals
6. 对新信号生成 AI research report
7. 给出 follow / observe / reject
```

Agent 2 要显式检查：

```text
address != entity 风险
单事件污染
大选污染
体育/做市污染
追高风险
规则结算风险
```

### 12.10 第一阶段落地目标

P0 不追求赚钱，追求闭环跑通。

```text
1. 建 wallets / wallet_discoveries / wallet_reviews / wallet_signals / wallet_signal_research_reports
2. 跑一轮 discovery，拿 500-2000 个候选地址
3. 深扫 top 100
4. 选出 10-30 个 watch 钱包
5. 每 60 秒监控 watch 钱包 open positions
6. 新信号触发 AI research report
7. 所有 follow 决策只进 paper
8. 每日汇总 paper PnL、markout、误判原因
```

P0 成功标准：

| 指标 | 标准 |
|------|------|
| watch 钱包数 | 10-30 |
| 每周有效信号 | >= 5 |
| AI 报告覆盖率 | 100% OPEN/ADD 信号 |
| paper 记录完整性 | 每笔有入场理由和退出计划 |
| reject 可解释性 | 每个拒绝都有明确原因 |
| address/entity 风险 | 每个 follow 信号都有风险等级 |

只有 P0 跑满至少 2-4 周，且 paper 有正向 markout，再考虑小额 live。
