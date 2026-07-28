# D1 `europe_cloud_break` 五城模式审计 v3

## 结论

**不是纯随机凑出的五城，但现有证据更像“欧洲市场结构 + 模型选边”的候选模式，不能归因成已经证实的 cloud-break 天气 alpha。**

- 五城 139 baskets / 19 target dates，fee-adjusted ROI +8.32%，target-date bootstrap CI [+4.48%, +12.19%]。
- 但欧洲其余五城也有 +4.64% ROI；五城相对它们仅 +3.68%，CI [-2.95%, +11.54%]，跨 0。
- 五城在全部 43 城任取 5 城中位于 99.55% 分位，raw randomization p=0.0045；但只在 10 个欧洲城市内比较仅位于 91.27% 分位，p=0.091。
- 保持现有 5 个 family 的规模、随机打乱城市并每次挑 ROI 最高 family，max-selection p=0.0054。所以它不像全宇宙随机噪声，但很大一部分可由“欧洲本身较强”解释。

动作：保留为 `frozen shadow hypothesis`，下一批新 target dates 同时跑 `Europe-all` 与这五城；不要把五城直接变成 live allowlist，也不要再按样本删成 Amsterdam+Munich。

## 它是不是事前定义

- taxonomy 首次进入 git：`2026-06-24T20:24:38+08:00`。它来自 `current-bracket NO / no-reheat` 的描述性 climate taxonomy，不是为本次 D1 distance-2 NO 策略创建。
- 本研究 target_date 为 2026-06-17..2026-07-07；所以对 6/17..6/24 不能声称严格事前，对 6/25 起可视为 taxonomy 定义后的时间切片。
- 严格 post-taxonomy 切片仍有 74 baskets / 11 dates，ROI +8.19%，CI [+3.80%, +12.84%]。
- 但 `europe_cloud_break` 是在看完本轮 5 个 family 结果后被挑为赢家；上面的 max-of-five randomization 已校正横截面择优，尚未替代真正未来期。

## 更像什么模式

### 1. 广义欧洲 / 摄氏市场结构效应

- Europe family ROI +8.32%，EU non-family +4.64%，C-unit non-family +1.46%。
- family 平均 NO ask 0.879，EU non-family 0.909；较便宜的 carry 是一部分来源。
- family 相对 mechanical-half 仍多 +5.40%，CI [+1.12%, +10.22%]，说明不只是两边都买的基础 carry。

### 2. 模型在真正需要选边时，family 内表现不同

- 只有一个 distance-2 NO 会赢的 discriminating baskets：family 16 个，模型选对 10/16=62.5%，market 选对 8/16=50.0%。
- EU non-family 仅 7 个，模型 0.0%、market 85.7%。样本很小，说明 family 与其他欧洲城市的收益生成方式并不相同，但不足以确认气象机制。

### 3. 不能叫 cloud-break alpha 的原因

- 五城训练期平均多模型 MAE 2.37F，EU non-family 2.29F：不是因为这五城forecast 普遍更准。
- family Brier delta vs market -0.00476，CI [-0.01617, +0.00571]；logloss delta -0.00601，CI [-0.04219, +0.02596]。proper-score 均未确认打赢 market。
- 当前 D1 signal 没有使用 cloud cover、cloud-break timing 或 remaining heating；family 名称只是旧 taxonomy 标签。因此收益不能反推为“云层破口”机制成立。

## 研究口径

- 固定分母：沿用 v2 的 1,190 paired baskets、每 basket 两个 distance-2 executable NO；本报告没有按结果删机会。
- baseline：同 basket 的 market-only selection 与 mechanical-half distance2；fee 已计入。
- 不确定性：按 target_date block bootstrap；组合枚举仅用于城市选择偶然性诊断，不能代替时间序列 forward。
- evidence funnel：这是 immutable snapshot research replay，非 fill；未检验 queue、capacity、真实成交与滑点。

## 冻结验证

- 冻结两个互斥 hypothesis：`Europe-all` 与固定五城 `Amsterdam/Helsinki/Madrid/Munich/Warsaw`。
- 继续固定 all-5 consensus、distance=2、前一天 snapshot 和同分母 market/mechanical baselines；不得追加城市或阈值。
- 至少积累 15 个新 target dates，再看：fee-adjusted ROI、相对 market/mechanical delta、Brier/logloss delta，以及 discriminating baskets 选边准确率。proper score 不过，仍不升 live。
