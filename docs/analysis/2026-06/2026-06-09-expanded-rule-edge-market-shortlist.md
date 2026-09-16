# Rule-edge market shortlist - 2026-06-09

## 结论先行

按 OpenAI 硬件那套思路继续扫了一批市场后，当前最适合继续深挖的不是纯方向预测，而是这几类：

1. **AI release / leaderboard / benchmark**：规则和外部数据源明确，散户容易把“传言”“模型名”“release”“Arena 口径”混在一起。
2. **IPO / listing / lead bank**：不是股价预测，核心是法律/流程/公告定义和时间窗口。
3. **officially announced / bankruptcy announced / regulatory order**：结算常卡在“官方宣布”“法院/政府文件”“是否真的 order”。

这轮我至少找到 **15 个可研究 market**。其中优先级最高的是：

| Rank | Market | 当前 YES | 初始倾向 | 为什么有 edge |
|---:|---|---:|---|---|
| 1 | next OpenAI model Arena debut >=1470 | 32.4c | 深挖 YES/NO 都可 | release 后首次 Arena 分数口径容易被误读 |
| 2 | GPT-5.6 release week buckets | 18.5c / 53c / 17.4c | 做时间梯子 | release 定义 + 官方命名 + 多 bucket 互斥关系 |
| 3 | Claude Mythos release date ladder | 63c-91.5c | 不追高，找错位 | June 12/15/30/July 多期限价格存在可比错位 |
| 4 | Anthropic IPO by Sep 30 | 42.5c | 深挖 | IPO/listing 定义、S-1、direct listing/SPAC、新闻误读 |
| 5 | OpenAI IPO by Sep 30 / Dec 31 | 37.5c / 75.5c | 偏谨慎，重点看 NO signal | `cqk`、`ImJustKen` 等 top holder 在 OpenAI IPO 上偏 NO |
| 6 | Anthropic vs OpenAI IPO first | 71c Anthropic | 只做规则审计后下 | “IPO first” 的触发条件比普通 IPO by 更容易出争议 |
| 7 | Google second-best AI model end of June | 22.5c | 可继续 | LMArena 固定时点排名，比 best market 更有价格空间 |
| 8 | Best Math AI model end of June | Anthropic 22c / OpenAI 13.5c / Google 65c | 可继续 | 专项榜单和 Overall 榜单容易被散户混用 |
| 9 | Claude Humanity's Last Exam >=45% / >=50% | 53.5c / 18.5c | 可继续 | benchmark 版本、公布主体、model eligibility 是规则点 |
| 10 | FrontierMath >=90% before 2027 | 24c | 可继续 | benchmark milestone，适合跟踪官方/论文/leaderboard |
| 11 | Gemini Pro next release date / no release by Jun 30 | 8c-20.5c | 小仓研究 | 多日期 bucket + “next Pro model” 口径 |
| 12 | Lead bank in Anthropic IPO | MS 37.5c / GS 37c | 观察 | 条件市场，若 IPO 时间线推进会有信息差 |
| 13 | Tesla and SpaceX merger officially announced | 41c | 低优先 | official announcement 定义清楚，但真实概率和叙事噪音大 |
| 14 | Trump federal review of AI model releases | 31.5c | 低优先 | legal/regulatory wording 可研究，但政治噪音高 |
| 15 | Frontier Airlines announce bankruptcy | 23.5c | 低优先 | “announce bankruptcy” 是法律/公告事件，不是股价本身 |

不建议纳入主策略：Google stock close above、bank earnings threshold、MicroStrategy bankruptcy、crypto/commodity、sports、weather、Fed、普通总统/选举市场。

## 本轮数据来源

Discovery run:

```text
docs/analysis/2026-06/2026-06-09-rule-edge-market-discovery.json
generated_at_utc=2026-06-09T01:36:10Z
events_scanned=1500
markets_found=424
```

Expanded holder run:

```text
docs/analysis/2026-06/2026-06-09-expanded-rule-edge-holders.json
generated_at_utc=2026-06-09T01:51:33Z
markets_count=69
wallets_found=1146
reviewed=0
```

复现脚本：

```bash
python3 scripts/copy_trade/discover_rule_edge_markets.py \
  --out docs/analysis/2026-06/2026-06-09-rule-edge-market-discovery.json \
  --pages 15 \
  --limit 100 \
  --min-liquidity 300

python3 scripts/copy_trade/copy_trade_rule_edge_wallet_research.py \
  --out docs/analysis/2026-06/2026-06-09-expanded-rule-edge-holders.json \
  --holders-per-market 15 \
  --max-wallets 0 \
  --max-closed-rows 0 \
  --min-market-liquidity 300 \
  --event-slug <selected-event-slug>
```

注意：完整 wallet closed-position review 对 20-60 个钱包会超时；这轮只做 holder-only 初筛。下单前要对目标 market 的关键 holder 做单钱包 full review。

## Top-holder 观察

这轮 holder-only 里出现的重点钱包：

| Wallet | 这轮出现 | 解读 |
|---|---:|---|
| `cqk` / `0x7365...64da` | 17 markets | 上轮 Tier A。当前偏 YES Claude Mythos / GPT-5.6，偏 NO OpenAI IPO before 2027。值得继续跟踪。 |
| `AJSV` / `0xad53...ef24` | 25 markets | 上轮 Tier B。覆盖很广，像 scanner/basket，适合作 confirmation，不适合作主导。 |
| `5atka` / `0x1c26...6e31` | 13 markets | 上轮 Tier A/规则市场可观察。当前偏 IPO basket YES。 |
| `Lavincey` / `0x1cdd...595` | 19 markets | 多个 IPO YES + Trump AI review YES；更像 broad thematic。 |
| `ultralisk` / `0x7495...67fcf` | 18 markets | 上轮已归为混合/彩票风格；不建议主跟。 |
| `balthazar` / `0x5a21...318` | 13 markets | GPT-5.6 时间梯子仓位很集中，适合专项 review。 |
| `ArmageddonRewardsBilly` / `0xc8ab...418` | 14 markets | Claude Mythos / Anthropic IPO 有明显 ladder 仓位，需查历史。 |
| `cobaltharborfmz` / `0x91d7...dfc` | 10 markets | OpenAI IPO YES 大仓，与 `cqk`/`ImJustKen` 相反，可作为对立信号。 |
| `ImJustKen` / `0x9d84...344` | 8 markets | OpenAI IPO / Anthropic IPO / Stripe IPO 多个 NO 大仓，适合 IPO NO basket 审核。 |
| `cowcat` / `0x38e5...5e7` | 12 markets | IPO NO basket，可能是流程/窗口判断型钱包。 |

## Market 细分

### 1. Next OpenAI model Arena debut >=1470

- Link: [Polymarket](https://polymarket.com/event/next-openai-model-arena-debut-685)
- 当前 YES：32.4c。
- Edge 类型：release 后 Arena debut score 定义。
- 钱包信号：`ultralisk` YES 1470；`AJSV` NO 1450；`molodoyy` YES 1450。
- 研究重点：
  - “next model released by OpenAI” 是否排除 minor/checkpoint/preview；
  - LMArena 首次上榜分数如何取；
  - 如果模型先 stealth 上榜或改名，市场如何处理。
- 初始动作：优先深挖，不直接下。这个市场的规则 edge 比普通“GPT release by date”更干净。

### 2. GPT-5.6 release week buckets

- Link: [Polymarket](https://polymarket.com/event/when-will-gpt-5pt6-be-released)
- 当前 YES：June 8-14 18.5c；June 15-21 53c；June 22-28 17.4c；not by June 28 10.5c。
- Edge 类型：多 bucket 时间梯子 + release 命名定义。
- 钱包信号：`cqk` YES GPT-5.6 June 30 / YES June 8-14；`balthazar` YES not by June 28、NO GPT-5.6 by June 30；`libratus1` YES June 8-14。
- 研究重点：
  - official release 是否必须明确叫 GPT-5.6；
  - API-only / limited preview / ChatGPT rollout 是否算 release；
  - 多 bucket 概率是否合计异常。
- 初始动作：适合做 ladder，不适合只买单腿。

### 3. Claude Mythos release date ladder

- Link: [Polymarket](https://polymarket.com/event/claude-mythos-released-by)
- 当前 YES：June 12 63c；June 15 72.5c；June 30 88c；July 31 91.5c。
- Edge 类型：model-name release definition + date ladder。
- 钱包信号：`cqk` YES June 12；`Bikesarethebest` NO June 15；`ArmageddonRewardsBilly` YES July 31, NO June 30。
- 研究重点：
  - Mythos 是否是正式 model 名、codename 还是第三方命名；
  - Anthropic 官方 release note / API model card 触发条件；
  - June 30 和 July 31 之间价差是否过窄。
- 初始动作：不追 June 30/July 31 高价 YES；更适合找日期错位或 NO 短腿。

### 4. Anthropic IPO by September 30, 2026

- Link: [Polymarket](https://polymarket.com/event/anthropic-ipo-by)
- 当前 YES：Sep 15 14c；Sep 30 42.5c；Oct 31 81c；Dec 31 89.5c。
- Edge 类型：IPO/listing 定义 + 多期限梯子。
- 钱包信号：`ArmageddonRewardsBilly` YES Sep 30；`Dr.PNL` YES Sep 30, NO Oct 31；`HerrieDavis` NO before 2027。
- 研究重点：
  - S-1 confidential filing 不等于 IPO；
  - direct listing/SPAC 是否算；
  - “by date” 是 pricing、trading first day 还是 official listing。
- 初始动作：Sep 30 是最值得研究的中间价位，Dec 31 接近 90c 不适合主交易。

### 5. OpenAI IPO by September 30 / December 31, 2026

- Links: [OpenAI IPO by](https://polymarket.com/event/openai-ipo-by), [IPOs before 2027](https://polymarket.com/event/ipos-before-2027)
- 当前 YES：Sep 30 37.5c；Dec 31 75.5c；before 2027 76c。
- Edge 类型：corporate/legal event definition。
- 钱包信号：`cqk` NO OpenAI IPO before 2027；`ImJustKen` NO OpenAI IPO by Dec 31 / before 2027；`cobaltharborfmz` YES OpenAI IPO before 2027 / Dec 31。
- 研究重点：
  - OpenAI corporate restructuring 和 IPO 是否被市场混同；
  - tender/secondary/private share sale 不等于 IPO；
  - nonprofit/control structure 是否拖慢 IPO。
- 初始动作：这是强分歧市场，适合深挖，不适合直接复制任何一边。

### 6. Anthropic or OpenAI IPO first

- Link: [Polymarket](https://polymarket.com/event/will-anthropic-or-openai-ipo-first)
- 当前 Anthropic：71c。
- Edge 类型：relative event + first trigger definition。
- 钱包信号：`ArmageddonRewardsBilly` 持 Anthropic；OpenAI IPO 单市场里 `cqk` 偏 NO OpenAI。
- 研究重点：
  - 如果两者都不 IPO，市场如何结算；
  - direct listing/SPAC 是否算 IPO；
  - first trading date vs official announcement。
- 初始动作：先读完整规则再判断；不要只用 Anthropic/ OpenAI 单腿价格推导。

### 7. Google second-best AI model end of June

- Link: [Polymarket](https://polymarket.com/event/which-company-has-second-best-ai-model-end-of-june)
- 当前 YES：Google 22.5c；Anthropic 70.5c。
- Edge 类型：LMArena fixed-time rank。
- 钱包信号：`cqk` 上轮已有 YES Google second-best；本轮 `Haradwaith` NO Google second-best；`dumbfuqLIQUIDITYprovider` YES Google second-best。
- 研究重点：
  - second-best 比 best 更容易被大众忽视；
  - fixed cutoff time 和 Style Control / Overall 榜单口径；
  - 新模型 release schedule 对排名挤压。
- 初始动作：保留在主跟踪队列。相比 Google best 8.5c，这个价格更符合“不做 90%+”。

### 8. Best Math AI model end of June

- Link: [Polymarket](https://polymarket.com/event/which-company-has-the-best-math-ai-model-end-of-june)
- 当前 YES：Anthropic 22c；OpenAI 13.5c；Google 65c。
- Edge 类型：专项榜单而非 Overall。
- 钱包信号：`molodoyy` YES Anthropic/OpenAI/Google 多腿；`dumbfuqLIQUIDITYprovider` YES 三方；`Haradwaith` NO OpenAI。
- 研究重点：
  - Math ranking 数据源是否稳定；
  - same family / model alias 去重；
  - 是否有新推理模型在月底前影响专项榜。
- 初始动作：比 Overall best 更值得研究，因为散户容易把 Overall 结论套到 Math。

### 9. Claude on Humanity's Last Exam >=45% / >=50%

- Link: [Polymarket](https://polymarket.com/event/anthropic-claude-score-on-humanitys-last-exam-by-june-30)
- 当前 YES：>=45% 53.5c；>=50% 18.5c。
- Edge 类型：benchmark threshold。
- 钱包信号：`iusedtowritepoetryforaliving` NO >=45%；其他 AI wallets 分散。
- 研究重点：
  - HLE 版本、scoring method、是否允许 tools / thinking；
  - “Anthropic Claude model” 是否包括 unreleased/internal；
  - 官方/第三方 benchmark source 触发条件。
- 初始动作：适合深挖 benchmark 规则，不适合凭模型传言下。

### 10. AI model scores >=90% on FrontierMath before 2027

- Link: [Polymarket](https://polymarket.com/event/ai-model-scores-90-on-frontiermath-benchmark-before-2027)
- 当前 YES：24c。
- Edge 类型：longer-dated benchmark milestone。
- 钱包信号：`TraderProMax` YES 71.95；低样本，需单独 review。
- 研究重点：
  - FrontierMath public/private split；
  - “AI model scores” 的公布主体和可验证性；
  - tool-use / ensemble 是否算。
- 初始动作：好候选。价格有空间，但需要规则原文和 benchmark pipeline。

### 11. Next Google Gemini Pro model release by date / no release by June 30

- Link: [Polymarket](https://polymarket.com/event/next-google-gemini-pro-model-released-onptptpt)
- 当前 YES：June 16 8.5c；June 17 8c；June 23 19c；June 30 13c；no next release by Jun 30 20.5c。
- Edge 类型：multi-date release bucket。
- 钱包信号：`AJSV` YES no next release by June 30；`Haradwaith` NO June 30；`balthazar` YES June 16。
- 研究重点：
  - “next Gemini Pro model” 是否包含 experimental / preview；
  - Google release cadence 和 event calendar；
  - 单日 bucket 是否被过度交易。
- 初始动作：只做小仓研究，避免过度押具体日期。

### 12. Lead bank in Anthropic IPO

- Link: [Polymarket](https://polymarket.com/event/lead-bank-in-anthropics-ipo)
- 当前 YES：Morgan Stanley 37.5c；Goldman Sachs 37c。
- Edge 类型：conditional IPO process / underwriting role。
- 钱包信号：holder-only 显示有分散仓位，尚未发现上轮 Tier A 大仓。
- 研究重点：
  - 如果没有 IPO 如何结算；
  - lead underwriter vs co-manager；
  - underwriting affiliate 是否扩大范围。
- 初始动作：观察。若 Anthropic IPO 时间线推进，这类市场可能比 IPO-by 主市场更有信息差。

### 13. Tesla and SpaceX merger officially announced

- Link: [Polymarket](https://polymarket.com/event/tesla-and-spacex-merger-officially-announced-by-june-30)
- 当前 YES：41c。
- Edge 类型：official announcement wording。
- 钱包信号：holder-only 有仓位，但未见已验证 Tier A 明确主仓。
- 研究重点：
  - “officially announced” 是否需要双方公司/董事会/SEC 文件；
  - partnership、equity investment、Musk tweet 是否不够；
  - slug 与 question 日期存在不一致迹象，要读完整规则。
- 初始动作：低优先，先做规则审计。这个盘容易被 Musk 叙事带偏。

### 14. Trump orders federal review of AI model releases by June 30

- Link: [Polymarket](https://polymarket.com/event/trump-orders-federal-review-for-ai-model-releases-by-may-31)
- 当前 YES：31.5c。
- Edge 类型：government order / regulatory wording。
- 钱包信号：`Lavincey` YES 312.69。
- 研究重点：
  - executive order、agency memo、public statement 哪些算 order；
  - “AI model releases” 是否要求明确覆盖 release review；
  - market slug 和 question 日期也有不一致迹象，要读规则。
- 初始动作：低优先但可观察。政治新闻噪音高，只有规则明确时才适合。

### 15. Frontier Airlines announce bankruptcy by December 31

- Link: [Polymarket](https://polymarket.com/event/which-airlines-will-announce-bankruptcy-by-december-31)
- 当前 YES：23.5c。
- Edge 类型：bankruptcy announcement / legal filing。
- 钱包信号：holder-only 未出现已验证 Tier A 大仓。
- 研究重点：
  - Chapter 11 filing、company press release、creditor-led restructuring 是否算；
  - parent/subsidiary filed 是否触发；
  - airline industry distress 数据。
- 初始动作：低优先。不是纯股价，但需要财务/法律数据，不如 AI release/benchmark 轻。

## 下一步建议

第一批深挖顺序：

1. `next-openai-model-arena-debut-685`
2. `when-will-gpt-5pt6-be-released`
3. `anthropic-ipo-by`
4. `openai-ipo-by` / `ipos-before-2027`
5. `which-company-has-second-best-ai-model-end-of-june`
6. `which-company-has-the-best-math-ai-model-end-of-june`

每个 market 的标准流程：

```text
读完整规则 -> 拉 orderbook/top holders -> 对关键 holder 做 closed-position full review -> 查官方/权威外部源 -> 写入 entry price / skip 条件
```

当前阶段不建议自动下单，也不建议直接按 top holder 复制。top holder 只用来决定“哪个 market 值得花时间研究”。
