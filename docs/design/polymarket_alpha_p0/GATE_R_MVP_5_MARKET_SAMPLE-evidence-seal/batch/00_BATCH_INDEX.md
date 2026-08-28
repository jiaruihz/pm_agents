# Polymarket Alpha｜5 市场 MVP 批次

统一状态：`WAITING_FOR_MANUAL_GPT_PRO_BLIND_RESULT`
统一执行：`NO_ORDER`
行情快照：2026-08-28 09:55（北京时间）

| # | 市场 | YES ask | spread | 命中时 gross return | 卡片 | 提示词 |
|---:|---|---:|---:|---:|---|---|
| 1 | [Kraken IPO by December 31, 2026?](https://polymarket.com/event/kraken-ipo-in-2025/kraken-ipo-by-december-31-2026-513) | 13.0¢ | 4.0¢ | 669% | [CARD_01_691547.md](CARD_01_691547.md) | [PROMPT_01_691547.md](PROMPT_01_691547.md) |
| 2 | [Macron out by December 31, 2026?](https://polymarket.com/event/macron-out-in-2025/macron-out-by-december-31-2026-20260728190614227) | 6.8¢ | 2.4¢ | 1371% | [CARD_02_3206940.md](CARD_02_3206940.md) | [PROMPT_02_3206940.md](PROMPT_02_3206940.md) |
| 3 | [Will the next UK election be called by December 31, 2026?](https://polymarket.com/event/uk-election-called-by/will-the-next-uk-election-be-called-by-december-31-2026) | 8.0¢ | 1.0¢ | 1150% | [CARD_03_2354064.md](CARD_03_2354064.md) | [PROMPT_03_2354064.md](PROMPT_03_2354064.md) |
| 4 | [China x India military clash by December 31, 2026?](https://polymarket.com/event/china-x-india-military-clash-by-december-31/china-x-india-military-clash-by-december-31-2026) | 8.0¢ | 1.0¢ | 1150% | [CARD_04_677404.md](CARD_04_677404.md) | [PROMPT_04_677404.md](PROMPT_04_677404.md) |
| 5 | [NATO/EU troops fighting in Ukraine by December 31, 2026?](https://polymarket.com/event/natoeu-troops-fighting-in-ukraine-in-2025/natoeu-troops-fighting-in-ukraine-by-december-31-2026) | 6.7¢ | 2.3¢ | 1393% | [CARD_05_2749382.md](CARD_05_2749382.md) | [PROMPT_05_2749382.md](PROMPT_05_2749382.md) |

## 建议的 MVP 阅读顺序

1. UK election：规则最直接、官方源明确、spread 最窄。
2. Macron：官方身份事实明确，适合测试低概率政治风险。
3. Kraken IPO：公司/SEC 资料丰富，但存在 event/child metadata 冲突。
4. China–India：适合测试复杂事件定义与报道共识。
5. NATO/EU troops：适合测试“事实发生”与“官方承认后才算”的区别。

请分别使用五个全新的 GPT Pro 会话，避免市场之间相互污染。


## Blind prompt verification

五份人工提示词已通过 recursive semantic leakage scan；`blind_leak_reasons = []`。卡片包含行情和链接，但卡片不得复制到 Blind 会话。
