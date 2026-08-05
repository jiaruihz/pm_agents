# Helsinki market expression v2：新版训练、同分母比较与交易 case 审阅

## 数据快照

- 研究层：`research replay`，不是 shadow/live fill；`actual_fills=0`。
- production manifest：2026-07-31 本轮复核为 `status=warning`、`db_route=healthy`，canonical DB 与仓库兼容入口指向同一物理库；本研究不从 DB 自算交易 PnL。
- 天气与完整 ladder 主表：`generated/helsinki_remaining_heat_market_replay_v2/checkpoint_market_states_v2_v7.csv.gz`，文件快照 `2026-07-31 16:57:57 +08:00`。
- source 后 active book：JRS runtime 的 `helsinki_pre_cross_active_ladder_shadow/active_bracket_books/*.jsonl`；训练输入 artifact 快照 `2026-07-31 17:13:45 +08:00`。
- 覆盖：2,115 个天气 checkpoint / 14 dates；合并后 598 个唯一 PIT market rows / 14 dates，其中 full-ladder 534 rows、post-source active 231 rows / 4 dates。settlement 完整，未结算与缺 winner-bracket 均为 0。
- expanding OOF 固定为 2026-07-20..29：362 checkpoints / 9 target dates、142 state entries、31 date-X entries。2026-07-31+ clean forward 标签未读取，未参与训练或选择。
- incumbent artifact SHA：`398b92b295f43e04c7b4deab26ccdd2b1f40354474e2d187f453092dfa3a3ed9`；其 OOF 复现最大误差 `1.11e-16`。

## 结论

新版完成训练，但**不替换 incumbent、不冻结为正式策略**。七个候选中，较强正则的 compact logistic `p_balanced_compact_r16` 最适合作为并行 research challenger：它统一修复了旧模型修正幅度过大、训练 grain 偏向高频 checkpoint 的结构问题，也改善了 date-X 点估和交易资金效率；但 active date-X 概率仍差于 market，三个亏损 case 一个也没有消失。

因此当前动作是：保存 challenger artifact，后续只做 zero-notional 双模型 forward 评分；incumbent forward 保持 untouched。不能根据本次开发窗 ROI 升 live。

## 训练结构与候选

新版目标函数把 checkpoint、state-entry、date-X-entry 三种 grain 各占 `1/3`，每种 grain 内再按 target_date 等权；概率模型选择不使用 ROI。所有连续特征在每个 expanding fold 内以训练集 median 补缺并标准化，避免量纲支配；market logit 保持固定 offset。

固定比较七个候选：compact logistic ridge 4/16/64、full-feature ridge 4、active-clock interaction ridge 16、四分类 `Δmax={0,1,2,3+}` joint offset、shallow HGB。这是 K=7 的开发搜索，没有 multiple-testing correction，故不能把最优点估当独立确认。

| 模型 | checkpoint Brier / logloss | date-X Brier / logloss | active date-X Brier / logloss | 判断 |
|---|---:|---:|---:|---|
| market | 0.07641 / 0.24875 | 0.08180 / 0.26162 | **0.09167 / 0.29274** | 同 rows baseline |
| frozen incumbent | 0.06125 / 0.19870 | 0.07185 / 0.25795 | 0.13196 / 0.47753 | active 过度修正 |
| compact r4 | **0.05431 / 0.18737** | **0.06554 / 0.22227** | 0.12139 / 0.39431 | 总体最好，但 active 仍弱且修正较大 |
| compact r16 | 0.05625 / 0.20072 | 0.06669 / **0.21921** | 0.10888 / 0.33639 | research challenger |
| compact r64 | 0.06295 / 0.22011 | 0.06942 / 0.23124 | 0.09447 / 0.30264 | active 最接近 market，但总体退化 |
| joint `Δmax` | 0.06638 / 0.22509 | 0.08315 / 0.26519 | 0.13340 / 0.40460 | primary binary 退化 |
| shallow HGB | 0.10558 / 0.30841 | 0.11471 / 0.35708 | 0.13046 / 0.44681 | 明显过拟合 |

`r16` 相对 market 在重复 checkpoint 上 Brier delta `-0.02016`、95% date-block CI `[-0.04118,-0.00092]`，logloss delta `-0.04803`、CI `[-0.09879,-0.00292]`；但更接近交易决策的 date-X CI 均跨 0。active date-X 相对 market 反而为 Brier `+0.01721`、logloss `+0.04365`，CI 也跨 0。它相对 incumbent 的 date-X 点估更好，但 bootstrap 同样未确认。

四分类 joint 模型在 checkpoint 的 exact accuracy 从 64.1% 提到 68.8%，但主要 date-X 的 multiclass logloss/Brier/RPS 从 `0.6366/0.3698/0.0614` 退化到 `0.6817/0.3906/0.0704`。它只是把第一名猜得更频繁正确，整条概率分布反而更差，不能入选。

## 结构性问题修复了多少

- 旧模型 31 个 date-X 中有 10 个 market-logit correction 绝对值大于 2，最大 4.73；r16 为 `0/31`，最大 1.58。过度自信问题已统一压住，不是靠某个 case 加阈值。
- 训练分母已从“checkpoint 多的日子权重大”改为三 grain、日期等权；所有 normalization 只在 train fold 内拟合。
- 但 active 只有 4 个独立日期，source 后时钟与天气状态的 interaction 没有足够证据；显式 clock 模型反而退化。
- 三个错误仍都是 `active + fresh_runway + 当日最后一档`。这说明剩余问题不是一个简单 fade 标签，也不能用事后 `path_state` hard gate 包装。

## 交易回放比较

保持原 first-positive/date-X router，不追加 price、edge、path 或时间阈值；成交价使用同一 PIT book 和官方 Weather taker fee。

| 5-share 回放 | signals / dates | 胜负 | 成本 | 净 PnL | ROI（date bootstrap 95% CI） |
|---|---:|---:|---:|---:|---:|
| incumbent 全部 | 28 / 8 | 25 / 3 | $109.33 | +$15.67 | 14.33% [4.87%, 21.82%] |
| r16 全部 | 19 / 8 | 16 / 3 | $63.06 | +$16.94 | 26.86% [7.47%, 52.47%] |
| incumbent active | 11 / 4 | 8 / 3 | $35.66 | +$4.34 | 12.16% [-12.76%, 35.10%] |
| r16 active | 11 / 4 | 8 / 3 | $33.82 | +$6.18 | 18.28% [-12.76%, 59.82%] |

r16 相对 incumbent 的全分母 paired ROI delta 为 `+12.53pp`、CI `[+1.67,+34.17]`；active paired delta `+6.12pp`、CI `[0,+27.13]`。10-share 结果方向一致：incumbent 28 笔 ROI 14.22%，r16 因深度只剩 18 笔，ROI 28.86%。

这不是“新版更会判断输赢”：active 仍是相同 11 笔、相同 8 胜 3 负。改善来自少花钱和更晚/更便宜入场。新版删除的 9 笔全部是旧模型赢家，但合计只贡献 `+$0.57`，多为接近 0.99 的统计尘埃；它没有通过额外阈值删除，而是正则化后概率不再虚高到穿过原始成本。

## 具体交易 case

时间同时给 UTC 和 Helsinki 当地 EEST（UTC+3）。

| 日期 / 档位 | 旧版 → r16 | 结果与直觉审阅 |
|---|---|---|
| 7/25，18 NO | 09:34 EEST、p=0.981、cost=0.943 → **13:23 EEST、p=0.679、cost=0.612** | 赢；同一档晚 3h49m、便宜 0.331，5-share PnL 从 +$0.29 到 +$1.94。新版不再为早盘高价“几乎确定”买单，符合直觉。 |
| 7/23，19 NO | 13:02 → 13:13 EEST；p 0.789 → 0.666，cost 都为 0.612 | 赢，PnL 均 +$1.94；只是把不必要的高置信度收回，没有制造新交易。 |
| 7/26，19 NO | 14:04 EEST；p **0.970 → 0.780**，cost=0.482 | 输，最终 Tmax 正好 19。新版大幅修复过度自信，但仍给出约 0.30 的净 edge，错误信号仍触发；这是最重要的未修 case。 |
| 7/25，20 NO | 16:33 EEST；p 0.213 → **0.238**，cost=0.136 | 输，最终 Tmax 正好 20；当时 forecast margin -1.4°C、剩余 reheat 仅 0.1°C。新版反而略升概率，说明 terminal heat / 最后一档表达仍没学好。 |
| 7/29，24 NO | 15:32 EEST；p 0.363 → 0.345，cost=0.198 | 输，最终 Tmax 正好 24；只做了很小收缩，仍未理解市场相对天气模型的反向信息。 |
| 7/22，18 NO | p 0.321 → 0.495，market=0.265、cost=0.300 | 赢；新版把 forecast remaining-heat 的有效信息保留并增强，属于合理赢家。 |
| 7/24，20 NO | p 0.783 → 0.657，market=0.600、cost=0.622 | 赢；新版仍有小正 edge，但不再远离 market，符合“市场已吸收大部分天气信息”的直觉。 |

完整逐 case 数据见 `generated/helsinki_market_expression_v2/selected_trade_case_atlas.csv` 和 `trade_case_comparison.csv`。

## 双漏斗与覆盖缺口

Signal funnel（单位明确分开）：2,115 weather checkpoints / 14 dates → 598 PIT market rows / 14 dates → 362 OOF checkpoints / 9 dates → 142 state entries → 31 date-X entries → r16 19 个 5-share intents / 8 dates。没有用“晚上不升温”等事后切片缩分母，每 10 分钟可用 checkpoint 都在原始分母里。

Evidence funnel：534 full-ladder rows + 231 active matches → 598 去重 PIT+settled rows → active date-X 12 rows / 4 dates → active 可执行 intents 11 / 4 dates；10-share 因盘口深度只有 18 个全分母 intents。actual fill=0，upper-strip 同刻证据仍为 0，因此当前只能评价 displayed-book replay，不能评价排队、真实成交或组合占用。

## 每日触发、盈利稳定性与入场分布

这里保留全部尘埃单，不用价格过滤缩分母。固定9个OOF评分日（7/28是PIT coverage gap，不属于评分日）：incumbent在8/9日触发，共28笔，平均每个评分日3.11笔、每个触发日3.50笔；r16同样在8/9日触发，共19笔，平均2.11/2.38笔。两版都是6个盈利日、2个亏损日、1个无信号日，故目前只能说“多数交易日盈利”，不能说逐日稳定。

| target date | incumbent 笔数 / 胜率 / PnL | r16 笔数 / 胜率 / PnL |
|---|---:|---:|
| 7/20 | 4 / 100% / +$3.04 | 1 / 100% / +$2.79 |
| 7/21 | 0 / NA / $0 | 0 / NA / $0 |
| 7/22 | 6 / 100% / +$3.95 | 3 / 100% / +$3.88 |
| 7/23 | 3 / 100% / +$2.19 | 1 / 100% / +$1.94 |
| 7/24 | 3 / 100% / +$2.15 | 3 / 100% / +$2.15 |
| 7/25 | 4 / 75% / +$2.60 | 3 / 66.7% / +$4.25 |
| 7/26 | 3 / 66.7% / **-$1.50** | 3 / 66.7% / **-$1.50** |
| 7/27 | 3 / 100% / +$3.90 | 3 / 100% / +$4.09 |
| 7/29 | 2 / 50% / **-$0.65** | 2 / 50% / **-$0.65** |

incumbent总胜率为25/28=`89.3%`，r16为16/19=`84.2%`。两版利润最高的两个日期都贡献约总PnL的50%，不算单日独占，但样本仍只有9个独立日期。

真正决定收益的不是高胜率尘埃单，而是中等赔率单：incumbent的fee-adjusted entry cost中位数为0.948，19/28笔在0.80以上且19笔全胜，但只赚`+$3.49`；其中cost≥0.98的9笔全胜，仅赚`+$0.16`。相反，cost 0.20–0.80只有7笔、6胜，却赚`+$13.84`，占总PnL的88.3%。cost<0.20的2笔全部亏损、合计`-$1.67`；这只是分布诊断，不升级成价格gate。

入场时间按Helsinki当地时间：incumbent范围06:00–16:33，中位12:32；13/28笔在12–15点，贡献`+$12.88`，占总PnL的82.3%。06–12点共11笔、全部获胜，却只赚`+$0.97`，主要是高价尘埃。r16把中位入场推迟到13:31，10/19笔集中在12–15点；它的资金效率提升主要来自不再过早买高价确定性，而不是发现更多日期。

Evidence需要单列：17笔source前full-ladder旧盘口全部获胜、贡献`+$11.33`，占incumbent总PnL的72.3%，但不能证明source到达后仍可按该价格成交。真正post-source active只有4日、11笔、8胜3负，ROI 12.16%但target-date CI `[-12.76%,35.10%]`；逐日为7/25 `+$2.60`、7/26 `-$1.50`、7/27 `+$3.90`、7/29 `-$0.65`，即2日赚、2日亏。active入场cost中位0.856、当地时间中位14:04。**所以当前“扩分母后历史总收益为正”成立，但“可执行条件下稳定盈利”仍未成立。**

## Gate 与下一步资格

- significance：`FAIL`。primary date-X 相对 market / incumbent 的 date-block CI 跨 0。
- baseline：`FAIL`。active date-X Brier/logloss 仍比 market 差。
- forward：`NA`。2026-07-31+ label 未读，尚无 artifact-freeze 后 settled forward。
- 结论：`inconclusive / research challenger only / no-live-change`。

下一阶段具备继续收集 zero-notional forward 的资格，但不具备替换模型或 live 的资格。继续时固定 r16 artifact SHA `e5290f36ad033d1a526162c124a54106ecfb066e0b1538a3864ed0db1c841288`。后续只并行评分 incumbent、r16 与 market，不再用本窗口挑 ridge、特征或阈值；补足 active dates、upper-strip 同刻 book 和真实 fill/queue 证据后再做 frozen audit。
