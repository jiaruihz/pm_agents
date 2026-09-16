# Helsinki fade morphology freeze v1

## 结果

- 选择窗：2025 expanding quarterly OOF，fade 16,585 checkpoints/365 dates；历史训练共40,550 fade rows。
- 按预注册规则选择 `shallow_hgb`：date-equal integrated Brier/logloss=0.02771/0.09673。
- 相对forecast logistic，integrated Brier delta=-0.00164 CI[-0.00274,-0.00056]；logloss delta=-0.00773 CI[-0.01412,-0.00208]。
- artifact SHA256 `4a34089bab953c4e1e7ae95560222b812f553138409b57e301b779721942f7e2`；train_end=2025-12-31；clean forward start=2026-07-31。
- OOF forecast coverage=94.43%；missing保留并由HGB原生处理，不作为eligibility filter。
- production manifest为warning但DB route healthy/same inode；本研究读取冻结历史artifact，未使用canonical trade facts。

## 漏斗与边界

- signal funnel：historical labeled states → 40,550 deterministic fade rows → 16,585 2025 OOF rows → 4 coherent probability heads。
- evidence funnel：historical observation-clock FMI/METAR + fixed-lag forecast；PIT market/executable/fill在本层NA，不能据此升live。
- artifact只提供连续fade-reheat概率特征；不增加price/path/time gate，不读取2026标签，不修改live。

## 8环

- 已覆盖：信号判别、概率分布、统计推断、简单prior反事实。
- 未覆盖：同rows market baseline、执行微结构、容量、真实fill、组合相关性。
- significance=PASS（仅weather-head vs forecast logistic）；market baseline=NA；forward=NA；结论feature-only frozen challenger。
