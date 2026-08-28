# 可直接复制给 GPT Pro

你是一名独立的事件概率研究员。请对下面命题做一次 point-in-time、price-blind 的深入研究。

## 强制隔离

- 不得访问或引用 Polymarket、其他预测市场、赔率页面或其镜像。
- 不得搜索、猜测或讨论市场价格、盘口、交易方向或其他模型的结论。
- 只使用在 `2026-08-28T02:03:23Z` 之前已经公开可得的资料。
- 优先使用 SEC 文件、Kraken 官方公告、相关交易所公告等 primary sources；primary source 不足时才使用 Reuters、Bloomberg、FT 等可信独立报道。
- 不要把“提交 IPO 文件”“秘密递交”“计划上市”当成“完成 IPO”。

## 待研究命题

Kraken 是否会在 2026 年 12 月 31 日 11:59 PM ET 前完成首次公开发行？

这里的“完成 IPO”指 Kraken 的股票首次在认可的证券交易所向公众出售并开放交易。仅提交注册文件、宣布计划或选择承销商不算完成。如果 Kraken 被一家已经上市的公司收购，则命题为否。

## 请回答

1. 截止研究时点，Kraken IPO 已完成到哪一个可验证阶段？
2. 哪些 primary evidence 直接支持或反对 2026 年底前完成？
3. 关键剩余步骤、通常耗时和可能导致延期/失败的条件是什么？
4. 给出最相关的 base rate；说明样本选择和局限，不要机械套用。
5. 分别写出最强 Yes case、最强 No case，以及未来哪些新事实会显著改变判断。
6. 给出你独立估计的：
   - `probability_yes`（0 到 1）
   - 合理区间
   - confidence（LOW / MEDIUM / HIGH）
   - 主要不确定性

## 来源要求

每项关键事实都附直接链接，并记录：publisher、标题、published/updated 时间、accessed 时间、primary/secondary、支持 Yes 或 No、对应原文位置或简短摘录。若只能找到引用而无法保存或核验正文，请明确标为 `REFERENCE_ONLY`，不要用它单独支撑关键结论。

## 输出格式

先写一份简明中文研究报告，然后在结尾给出一个 JSON 代码块：

```json
{
  "as_of_utc": "2026-08-28T02:03:23Z",
  "probability_yes": 0.0,
  "probability_interval": [0.0, 0.0],
  "confidence": "LOW|MEDIUM|HIGH",
  "yes_case": [],
  "no_case": [],
  "key_unknowns": [],
  "claims": [
    {
      "claim": "",
      "supports": "YES|NO|NEUTRAL",
      "source_url": "",
      "source_tier": "PRIMARY|SECONDARY",
      "published_at": null,
      "accessed_at": "",
      "effective_as_of": "",
      "location_or_excerpt": "",
      "confidence": "LOW|MEDIUM|HIGH"
    }
  ]
}
```

如果证据不足，请照实给出 `INSUFFICIENT_EVIDENCE`，不要为了形成结论而补造事实。
