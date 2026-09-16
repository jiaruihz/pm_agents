# Helsinki pre-freeze optimization final v1

## 结论

- 旧state → fade-v1确实改善：checkpoint accuracy `90.88%→91.99%`，Brier
  `0.06773→0.06125`，logloss `0.21575→0.19870`；两项proper-loss delta的
  target-date CI均全负。
- 继续加入更丰富动态特征并没有继续改善market residual。最终选择简单的
  `offset_fade`，不选dynamic fade、fresh-runway specialist或full dynamic path。
- 最终research artifact已冻结；这是zero-notional forward模型，不是live授权。

## 优化消融

历史天气层（2025 expanding OOF）：

| Specialist | Base Brier/logloss | Dynamic Brier/logloss | 结果 |
|---|---:|---:|---|
| fade | `0.02771/0.09673` | `0.02563/0.08901` | dynamic显著更好 |
| fresh runway | `0.13706/0.41834` | `0.09932/0.31553` | dynamic显著更好 |

同一7/15–29 market开发窗（362 checkpoints/9 OOF dates）：

| Market-offset feature family | Accuracy | Brier | Logloss | 相对offset_fade |
|---|---:|---:|---:|---|
| `offset_fade` | `91.99%` | `0.06125` | `0.19870` | selected |
| dynamic fade replacement | `91.44%` | `0.06795` | `0.21884` | 两项显著退化 |
| fade-v1 + fresh specialist | `92.27%` | `0.06120` | `0.19907` | 两项CI跨0，零增量 |
| full dynamic path | `91.16%` | `0.06816` | `0.21965` | 两项显著退化 |
| market | `91.99%` | `0.07641` | `0.24874` | offset_fade点估更好，CI仍跨0 |

天气层更准但market residual变差，说明这些absolute weather probabilities与market
logit已有信息重复；在只有9个market OOF dates时加入更多specialist维度会增加估计噪声。
本轮有3个预注册/单因素challenger，未做额外多重检验校正；最终采用“必须同时改善
Brier与logloss”的保守规则。

## ROI与错误

- `offset_fade`：28个date-X，25胜3负，投入`$109.33`、净`+$15.67`、ROI
  `14.33%`，target-date CI `[4.87%,21.82%]`。
- dynamic/fresh/path候选均未改变25胜3负；最高ROI仅`14.38%`，来自一笔更低
  `$0.05`的入场成本，不是错误减少或新alpha。
- 三个错误仍全是fresh runway；当前数据不支持再追加clock/path gate。该问题留给
  clean forward全量概率记录，而不是继续调这9个日期。

## 最终freeze

- model：market-logit fixed offset + v7 weather probability + frozen fade-v1 four-horizon features。
- training grain：每个date-X/path-state首次evidence-backed state entry。
- final train：249 rows/14 target dates，截止`2026-07-29`。
- clean forward：`2026-07-31+`，只评分，不调参。
- entry：每个10分钟checkpoint重估；未持仓date-X首次`p_model > executable_cost`
  才产生intent；position state负责去重/HOLD。
- price threshold：无。`cost<0.98`仅研究摘要。
- artifact SHA256：
  `398b92b295f43e04c7b4deab26ccdd2b1f40354474e2d187f453092dfa3a3ed9`。

## 漏斗、资格与动作

- signal funnel：2,115 weather checkpoints → 598 settled market rows/14 dates →
  249 state-entry train rows → frozen continuous residual → first date-X intent。
- evidence funnel：534 full-ladder rows + 231 active matches → 598 unique rows；
  actual fill=`0`，执行/容量仍NA。
- significance：PASS vs old state；baseline：FAIL_CI vs market；forward：NA。
- conclusion：`inconclusive / final research freeze / zero-notional`。
- action：开始clean forward collector；不改live。dynamic path artifact保留为
  `superseded-for-now`，不删除。

## 后续逐笔执行审计补充

这里的“final research freeze”只冻结 probability artifact，不代表 expression /
execution policy 已具备冻结资格。逐笔审计确认17/28入场仍使用source前full-ladder，
post-source active只有11笔/4日；upper-strip同刻可执行覆盖为0/28。active 10-share
时钟修正后，全回放25胜3负、ROI14.22%，但active-only ROI 11.94%且日期CI跨0。
因此最终状态按[逐笔case review](2026-07-31-helsinki-pre-freeze-trade-case-review-v1.md)
解释为`probability artifact frozen / expression execution pending`。
