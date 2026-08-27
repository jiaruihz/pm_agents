# WCIR Next Official Print — Stage 0–3 工作总结与后续方向征求

## 本次请你做什么

请基于下面的工作总结：

1. 审阅 Stage 0–3 的阶段结论是否合理；
2. 判断当前应该继续采集、重做 oracle，还是可以有条件进入模型训练；
3. 给出后续数据、模型和验收门槛的明确建议。

这是一份 decision brief，不含原始数据、逐行 evidence、模型 artifact 或历史 zip。若某项结论需要独立复算，可以在 disposition 中指出需要补交的具体证据。

Authoritative plan 的核心顺序是：

```text
可信边界 → causal event contract → deterministic book truth
→ perfect-print executable oracle → next-print model
→ market reaction model → strategy/runtime
```

当前项目保持 zero-notional。本次不请求 live、production、collector、selector、threshold、position policy 或 order path 变更。

---

## 一、我们想验证的事情

城市范围：Amsterdam、Helsinki、Busan、Seoul、Tokyo。

统一假设：更快或更细粒度的天气观测，能否在下一次 settlement-relevant official print 之前预测该 print，并在市场完成定价前产生扣费后可执行的 repricing edge。

研究被拆成三层：

```text
A. NextPrintModel：预测下一次 official print
B. MarketReactionModel：预测市场会如何对该 print 重新定价
C. SettlementUpdateModel：更新最终结算分布
```

正确顺序必须是：先证明 lead window 和 perfect-print oracle 的经济上限，再训练 A；A 通过后才能研究 B。不能用“天气预测很准”替代 executable market alpha。

---

## 二、Stage 0 — 仓库、数据与运行边界

### 做了什么

- 冻结 repository、runtime、config、data cutoff 和 artifact identity。
- 复现旧 WCIR scorecard，并建立 immutable offline input。
- 查清真实 position-scope/bracket-lock：当前 production key 包含 `model_id`；没有把 future global invariant 伪装成历史事实。
- 对 duplicate candidate serialization 做 payload immutability audit。
- 证明 WCIR orders/fills/notional=`0/0/0`。
- 完全离线复现 336/336 exact market identities，0 Gamma errors。

### 结论

Stage 0 的 repository/data/policy boundary 已经闭合。旧 101-row headline 保持 immutable。

当时 production controller health 有 critical 记录。它不影响 offline evidence 本身，但任何未来 production deployment 都必须重新做当前健康验收。

---

## 三、Stage 1 — 五城 causal event/clock contract

### 做了什么

- 建立统一实体：source observation、official print、next-print link、exact market identity、experiment epoch、legacy registry。
- 明确区分 observed、issued、first-seen、decision、feature-book、execution-book、markout clocks。
- 为五城分别冻结 observation semantics：
  - Amsterdam：`ta` point、`tx` interval max、revision 分离；
  - Helsinki：provider observation time 不能被 poll time 替代；
  - Tokyo：native 10-minute checkpoint；
  - Busan/Seoul：多 runway min/mean/max/consensus、revision，Seoul 另保留 preferred runway。
- station/source mismatch、clock ambiguity、payload conflict 全部 fail closed。
- 18,726 legacy serializations 原样保留；旧输入缺 native outcome/book/markout clocks 的 rows 不被伪造成新 causal performance denominator。

### 结论

Stage 1 建立的是可信研究合同，不是 alpha 证据。独立 reviewer 的三项 finding 已修复，58 tests passed。

---

## 四、Stage 2 — deterministic book truth

### 做了什么

- 实现 snapshot + ordered delta 的确定性重建。
- 显式处理 duplicate、乱序、gap、reconnect、baseline reset 和 parity failure。
- 输出稳定 book identity、raw lineage、`book_valid`、`gap_reason`。
- 实现 fee-aware 1/5/10-share 双边 sweep。
- 明确 `queue_truth=false`；quote/book reconstruction 不冒充 maker fill 或 queue truth。
- 实现 pre-source、source t0、pre-official、official 后固定 horizons 的 as-of alignment。

### 真实样本结果

- 一完整 transport 日；608 epochs、4 reconnect chains、12,429 relevant frames。
- 重建 6,250 book states；正反输入顺序得到完全相同 run ID。
- 399 个 blocker intervals 全部后续恢复，open gap=0。
- 4,032 REST/WS comparisons 中，3,644 因 exchange clock skew >2s 不可比。
- 72 个事件中，source t0 只有 2 个 valid books。

### 结论

book reconstruction 合同和实现是可复现的，但真实 event-aligned coverage 很差。需要审阅者判断：一日样本是否足以接受“算法合同”，以及是否必须扩大到完整 archive 才能接受 Stage 2。

---

## 五、Stage 3 — next-report table 与 executable oracle

### 固定数据范围

841 个 distinct fast observation → next official print events，覆盖 2026-08-09 至 2026-08-26。

| City | events | target dates | source exact next-print | within 1 tick |
|---|---:|---:|---:|---:|
| Amsterdam | 87 | 16 | 79.3% | 97.7% |
| Tokyo | 42 | 14 | 66.7% | 100% |
| Helsinki | 59 | 15 | 76.3% | 100% |
| Seoul | 268 | 14 | 84.3% | 100% |
| Busan | 385 | 16 | 79.0% | 99.7% |

Median nominal lead window 约 11.5–15.8 分钟。

### 关键问题

source prediction 看起来有信息，但 executable evidence funnel 几乎塌空：

- source-t0 valid book：2/72 sample-day events；
- full two-sided information oracle：无法计算；
- same-row market-only baseline：无法计算；
- forecast-only baseline：缺 frozen PIT forecast identity；
- 5-share / official+30s executable oracle：只有 Busan 1 row / 1 date；
- 该 row 扣费后 markout：`-$0.3233`；
- 日期不足，无法给 target-date block CI；
- 无法可靠判断市场主要在 source 前、source 后还是 official 后反应。

### 当前正式结论

五城全部：

```text
CONTINUE_COLLECTION_ONLY
```

没有城市满足预注册 modeling gate：至少20个 target dates、100个 eligible events、正的5-share feasible-latency oracle、one-sided 90% date-block CI lower >0、收益不过度集中、市场没有主要在 source 前反应。

因此当前没有自动授权 Stage 4。

---

## 六、我们建议的下一步

### 第一优先级：补完整 executable evidence

1. 先重放现有完整 WS archive，量化可恢复 coverage；不要立即改 collector。
2. 对每个 fast event 冻结：pre-source、t0、pre-official、official +1/+3/+5/+15/+30/+60/+120/+300s。
3. 同时保留 YES/NO complementary tokens 和邻近 brackets，避免只有 prior-bracket NO 的单边 oracle。
4. 每个 checkpoint 保存 book identity、gap/reconnect、age、1/5/10-share sweeps 和官方 fee。
5. 补 frozen PIT forecast identity 和 same-row market-only baseline。
6. 按 target_date 和 official-print dependence group 计权，不按 raw message 数量计权。

### 第二优先级：重新跑 Stage 3 oracle

必须同时得到：

- information oracle；
- feasible-latency oracle；
- full two-sided expression；
- market-only matched baseline；
- fixed horizons；
- target-date block CI；
- concentration 和 pre-source reaction audit。

### 第三优先级：满足 gate 后再训练 Stage 4

建议从简单模型开始：

1. ordinal/multinomial logistic；
2. monotonic GAM/spline；
3. strongly regularized shallow GBDT。

Primary target：

```text
next_official_delta_native_tick
```

Derived targets：up/down cross、unchanged、new running max、revision、进入各 settlement-relevant brackets 的概率。

训练合同：target-date blocked expanding OOF、independent outer dates、cross-fit calibration、最终 clean-forward；用 proper score 与同 rows persistence/recent-slope/forecast-only/market-only 比较，禁止用 ROI 选模型。

建议先做 city-specific simple models，只有五城 label/feature semantics 和有效样本充分对齐后，再评 pooled/shared model + city calibration。

---

## 七、请 GPT Pro 重点裁决

1. Stage 0、1 的 offline evidence 是否可以接受，同时把 production health 作为独立 deployment hard gate？
2. Stage 2 的一日真实 transport replay 是否足以接受 reconstruction contract？若不足，需要补到什么范围？
3. Stage 3 应选择：

```text
CONTINUE_COLLECTION_WITHOUT_MODELING
REWORK_ORACLE_OR_MARKOUT_DEFINITION
REDIRECT_AWAY_FROM_PRE_REPORT_ALPHA
```

4. full two-sided oracle、market-only baseline、forecast-only baseline 是否都是 Stage 3 acceptance 前的 blocker？
5. 是否允许任何城市现在做 provisional measurement-only fit？如果允许，请明确：城市、目的、禁止用途、数据门槛和退出条件。
6. 每城最低需要多少 target dates、events 和 source-t0 book-valid coverage？
7. 应先重放现有 archive，还是优先 clean-forward collection？
8. 模型应先 city-specific，还是从 pooled hierarchical 开始？
9. market state 在 Stage 4 应作为强 prior/input，还是仅作独立 baseline，以避免把市场能力误归因于天气？
10. Stage 4 的最小晋级门是否应高于当前计划的20 untouched target dates？

---

## 八、请按这个格式返回

```text
1. EXECUTIVE VERDICT

2. STAGE DISPOSITIONS
   Stage 0 rev2: ...
   Stage 1: ...
   Stage 2: ...
   Stage 3: ...

3. BLOCKING FIXES
   每条包含 severity / reason / exact fix / acceptance test

4. DATA COLLECTION PRESCRIPTION
   city / minimum dates / minimum events / book coverage / identities / stop condition

5. STAGE 4 AUTHORIZATION
   NOT AUTHORIZED / PROVISIONAL-MEASUREMENT-ONLY / AUTHORIZED
   cities / allowed work / prohibited uses / entry gate / exit gate

6. MODELING RECOMMENDATION
   target / grain / features / baseline / split / first model / calibration / metrics

7. CODEX NEXT-ACTION CHECKLIST
   按执行顺序，不超过10项

8. CONFIDENCE
   0.00–1.00
```

如果现有证据不够，请明确要求补什么，不要因为 source accuracy 高就跳过 executable oracle、market baseline 或 clean-forward gate。
