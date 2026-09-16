# 市场样例卡｜Kraken 2026 年底前完成 IPO？

状态：`等待 GPT Pro Blind Research`
执行：`NO_ORDER`
行情时间：2026-08-28 09:55（北京时间）

## 一句话理解

这不是“Kraken 有没有提交上市申请”，而是 Kraken 的股票是否在 2026-12-31 11:59 PM ET 前真正完成首次公开发行，并在认可的证券交易所向公众出售/开放交易。只提交材料不够；若被一家已经上市的公司收购，规则直接判 No。

## 当前行情快照

- 展示概率：YES `11%` / NO `89%`
- YES best bid / ask：`9¢ / 13¢`
- YES spread：`4¢`，相对较宽
- last trade：`10¢`
- Gamma liquidity：约 `$2,361`
- 累计 volume：约 `$559,393`
- 市场链接：https://polymarket.com/event/kraken-ipo-in-2025/kraken-ipo-by-december-31-2026-513

## 盈利空间怎么理解

现在还不能宣称有 edge，因为尚未完成外部事实研究，也还没有 fresh paired YES/NO orderbook。

- 如果真实可成交 YES ask 仍是 `13¢`，只有当独立研究得到的 fair probability 明显高于 `13% + fee/slippage buffer`，YES 才可能有正期望。
- 以 `13¢` 买入 YES，若最终命中，每份 gross profit 是 `87¢`，gross return 约 `669%`；但这只是赔率，不代表胜率足够高。
- NO 侧暂不计算：Gamma 的 `89%` 是展示值，不能替代 NO token 的真实 ask/depth。
- 4¢ spread 对 11% 左右的市场很大，说明即使模型认为存在小幅偏差，也可能被交易成本吃掉。

## 程序目前怎么看

- new/changed recall：命中
- structural recall：未命中，产生 1 个 suppression
- controversy / wallet：本轮没有对应 source facts，typed skip
- book anomaly：按协议在 Blind research 前禁用
- Rule A：`PASS`
- GLM-4.7 语义初筛：`ADVANCE / ELIGIBLE / HIGH`
- Blind leakage scan：`PASS`

## 主要待查事实

1. Kraken 是否已经正式提交或更新 SEC 注册文件，以及当前处于什么阶段。
2. 公司、承销商或交易所是否给出明确的 2026 时间表。
3. 同类大型 crypto 公司从 filing 到挂牌通常需要多久，失败/延期的基准率是多少。
4. 监管、市场条件、公司并购或延迟因素是否足以推翻 2026 年底完成 IPO 的可能性。
5. child market 的 2026 规则与 event 层仍残留的 2025 描述存在 metadata 不一致，研究时应以本 child market 的 binding rule 为准，并单独记录该冲突。

## 下一步

复制 `MVP_GPT_PRO_PROMPT_CN.md` 的全部内容到一个全新的 GPT Pro 会话。把 GPT Pro 的完整回答原样交回 Codex；Codex 再做来源验收、fresh paired book、市场比较、Rule B 和 `NO_ORDER` ledger。
