# Busan sequential exact-NO baseline

## 结论

本轮修掉了 confirmation head 的 grain mismatch：不再用“首次cross事件”的总体胜率给后续连续checkpoint定价，而是在同粒度的15-minute pending state上训练。8/4是未参与训练/选型的真实transition forward；新head在transition score上优于旧base，并把11:45釜山本地的 `P(35 NO)` 从 `93.8%` 降到 `82.6%`。但仍高于市场 `64.0%`，conditional reheat证据也不足，所以仍不进入shadow。

## 固定分母

- anchored cross episodes：123 / 26 dates，2026-07-09..2026-08-03。
- next-routine labels：123/123；final labels：123/123。
- label lineage：next routine原始captured `110`、由PIT continuous补 `13`；final settlement原始captured `68`、由IEM hourly final补 `55`。两源重叠 `61` rows，label mismatch `0`。
- expanding OOF：99 rows / 21 dates。
- quotes：68 episodes；market不进模型，只作baseline/交易成本。
- 连续pending state：159 rows / 13 dates；development OOF `101` rows / `7` dates；8/4 frozen transition `10` rows。
- upstream prediction table SHA-256：`e5448077de2b50578e9fa18101f71faed44289b373126e0613651f7d491149c7`；canonical DB `/Volumes/jrs/pm_agents/runtime/weather.db`，device/inode `16777239/1636907`；upstream observed `2026-08-04T11:53:51.989500+00:00`。

## Signal funnel / evidence funnel

- signal：123 anchored episodes/26天 → 159 continuous pending states/13天 → 101 development OOF states/7天 → 4 positive-edge research expressions/3天。
- evidence：101 PIT weather+settlement states/7天 → 18 same-row PIT quote states/5天 → 4 executable research expressions/3天 → actual fills `0`。盘口缺失只记coverage gap，不是策略过滤。

## 概率质量

| model | rows | dates | logloss | Brier |
|---|---:|---:|---:|---:|
| physical development | 86 | 15 | 0.5228 | 0.1679 |
| sequential development | 86 | 15 | 0.1530 | 0.0384 |
| sequential market-aligned | 68 | 19 | 0.2054 | 0.0525 |
| market | 68 | 19 | 0.2216 | 0.0692 |

sequential−market target-date bootstrap：`{'delta': -0.01620668765555552, 'ci_low': -0.1346642425529704, 'ci_high': 0.09551185381839856, 'draws': 10000, 'target_dates': 19}`。点估为负表示sequential更好，但CI仍决定是否可声称战胜市场。

confirmation head中，expanding beta base logloss `0.3776`，basis logistic `0.3861`；选择 `base`。这说明当前source basis特征尚未提供稳定增量，不强行采用复杂head。

conditional reheat仅 `15` rows / `8` dates，继续使用五年long-history physical prior，不在17条上拟合天气交互。

## 连续state head结果

| target | model | rows | dates | logloss | Brier |
|---|---|---:|---:|---:|---:|
| next routine confirms | old date base | 101 | 7 | 0.4531 | 0.1392 |
| next routine confirms | geometry head | 101 | 7 | 0.3447 | 0.1015 |
| final exact NO | old sequential base | 101 | 7 | 0.3129 | 0.1044 |
| final exact NO | state geometry | 101 | 7 | 0.2349 | 0.0751 |
| final exact NO | market-aligned geometry | 18 | 5 | 0.5612 | 0.2033 |
| final exact NO | same-row market | 18 | 5 | 0.3665 | 0.1286 |

geometry state只有两个物理状态：`cross_retained`、`cross_not_retained`。边界是settlement lattice本身的 `+0.5°C`，不是针对8/4调出的阈值；二状态表达也保证 retained 对 confirmation 的方向单调。development geometry−base transition logloss CI：`{'delta': -0.10836978592466248, 'ci_low': -0.19547090497368041, 'ci_high': -0.02682148098285205, 'draws': 10000, 'target_dates': 7}`；同分母 geometry−market：`{'delta': 0.19470816937232885, 'ci_low': -0.15515716109409078, 'ci_high': 0.5445734998387485, 'draws': 10000, 'target_dates': 5}`。

## 8月4日 untouched transition forward

- 所有报告同时显示UTC/北京时间/釜山时间；`02:45 UTC = 10:45 北京 = 11:45 釜山`，不是凌晨样本。
- 11:45釜山 pending：AMOS peak giveback `1.2°C`，当前cross已不retained；`P(confirm)` 从旧base `83.8%` 降到 `54.9%`，组合 `P(35 NO)` 从 `93.8%` 降到 `82.6%`，market `64.0%`。
- 12:00釜山第一份routine未确认后切branch：`P(35 NO)=39.3%`。
- 12:15釜山：`39.3%`，仍高于market `21.0%`；这一段是reheat head的问题，不再误归因给confirmation。

8/4不进入训练或选型。next-routine transition label已经闭合；latest captured routine max为 `35`，但正式settlement尚未入库，因此exact-NO只列provisional诊断，不冒充settled score。

## 交易表达（research replay，不是fills）

- sequential正edge：20单/15天，PnL `$+4.8707`，ROI `6.08%`，date-bootstrap CI `[-0.1711262896638874, 0.2861868597675143]`。
- 同一OOF raw CrossNO-candidate全买：50单/19天，PnL `$+6.5139`，ROI `2.86%`，CI `[-0.04860296033783646, 0.11144753657023407]`。这是同分母研究基准，不是已部署CrossNO的31单真实/overlay口径。
- 连续state geometry正edge replay：4单/3天，PnL `$-2.4648`，ROI `-19.77%`，CI `[-1.0, 0.2318305001231831]`；仍是research replay，不是fills。

5-share taker、actual recorded ask/depth、官方Weather fee；未成交机会不能称actual fill。

## 为什么还不能shadow

旧head的训练/推理grain错误已修复，双时区展示也已修复；8/4验证新head能识别cross已回吐，但力度仍不足。更关键的conditional reheat只有17条/10天，不能诚实训练11维天气交互。market-aligned continuous states也只有18 rows/5天。

动作：稳定模型结构和本轮state head保留，概率adapter继续不部署；不改live。继续积累nonconfirmation与同刻盘口，达到预注册日期数后，在固定11维conditional ontology上训练path/atmosphere residual，并以新的未来日期做settlement forward。

验收：`significance=FAIL`，`baseline=FAIL`，`forward=NA_SETTLEMENT_PENDING`，`conclusion=inconclusive`。在2026-07-27..08-03的18个同盘口states/5个target dates上，state geometry相对market的logloss delta为 `+0.1947`（95% CI `[-0.1552, +0.5446]`）；没有可部署的market residual。
