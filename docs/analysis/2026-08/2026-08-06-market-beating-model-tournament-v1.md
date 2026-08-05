# Weather Market-Beating Model Tournament v1

> status: complete
> scope: research / zero-notional only
> live_action: none
> orders_changed: 0

## 目标与通过标准

本轮最多比较 10 个固定版本。`beat_market` 只允许表示：

1. 同一 target、同一 checkpoint、同一 rows/labels/market distribution；
2. 所有 feature 在 decision clock 前 available；
3. 模型与正则只在 inner train 选择，城市选择不得看 outer outcome；
4. outer 按 `target_date` 整块切分，primary grain 不用 repeated checkpoints 冒充独立样本；
5. logloss、Brier/RPS、calibration 同时报告，target-date block bootstrap 的 primary delta CI 不跨 0；
6. market residual 的零参数必须严格退化成 market；
7. executable EV 只能在 probability gate 通过后报告，并使用 ask、official fee、slippage/depth；
8. historical backfill、estimated forecast run、midpoint proxy 和缺失 native ladder 必须单列 evidence tier，不能冒充 clean forward。

## 固定版本预算

| ID | 方向 | 预注册角色 | 主要失败风险 |
|---|---|---|---|
| V01 | D-1 market residual | strong regularization / global | reconstructed lineage |
| V02 | D-1 market residual | compact weather residual | reconstructed lineage |
| V03 | D-1 market residual | revision/spread residual | run lineage coverage |
| V04 | D-1 market residual | partial city/source residual | city overfit |
| V05 | city candidate | best preregistered city target | target-date support |
| V06 | city candidate | second city/group target | target-date support |
| V07 | city candidate | pooled city-group target | inner city selection |
| V08 | intraday Tmax | legacy four-bucket fusion audit | historical forecast backfill; not native ladder |
| V09 | intraday Tmax | coherent quote calibrator audit | research window already observed |
| V10 | Tokyo current-break | fixed-logit market offset audit | state-entry grain / clean forward failure |

不再增加 V11。若 10 个版本均未通过，交付 best zero-notional candidate 和明确 blocker，不把点估、selected ROI 或事后城市切片写成 `beat_market`。

## 已确认的历史审计边界

- 旧 intraday atlas/P3 是 relative `current/d1/d2/tail`，不是完整 native ladder；historical forecast 字段含 backfill，不能作为 clean PIT winner。
- absolute-ladder 历史审计只有 47 个可评分 state / 15 个日期，只足够 plumbing smoke test。
- V09 在已经参与诊断的 16 日窗口相对旧模型改善，但 `forward=FAIL_research_window_already_observed`。
- V10 的开发 OOF checkpoint 点估优于 market，但 state-entry grain 输；首个 clean forward 日也输给 market。

## Tournament Results

| ID | primary result vs market | statistical / evidence verdict |
|---|---|---|
| V01 | Δlogloss `+0.000000` | 17/17 outer folds 选择 market null；无 weather residual |
| V02 | Δlogloss `+0.000124` | 变差；reject |
| V03 | Δlogloss `+0.000000` | 17/17 outer folds 选择 market null；无 weather residual |
| V04 | Δlogloss `-0.000077` | 95% CI `[-0.000232, 0]`，K=4 Bonferroni `[-0.000310, 0]`；16/17 folds 为 null，改善只来自 2026-07-23；reject as alpha |
| V05 | secondary Δlogloss `+0.003035` | all-city calibrated market 变差；reject |
| V06 | secondary Δlogloss `-0.011047` | CI `[-0.030438,+0.013381]`，Brier 变差且 inner validation 失败；reject |
| V07 | inner Δlogloss `-0.025391`，secondary `+0.023650` | 明确反号过拟合；reject |
| V08 | legacy four-bucket Δlogloss about `-0.0658` | historical forecast backfill、relative bucket、非完整 native ladder；不满足本轮 evidence contract |
| V09 | relative to prior full-feature model Δlogloss `-0.0020` | 没有同分母 market primary，且 research window 已观察；reject as market-beating claim |
| V10 | Tokyo dev checkpoint Δlogloss `-0.01637`，首个 clean forward 日输 market | state-entry grain 输、clean forward failure；reject |

V01–V04 的共同 outer 分母为 168 states / 17 target dates / 31 cities；M0 market date-equal logloss `1.560207`。V05–V07 使用 279 primary states / 27 dates / 34 cities 的同一 exact-native-ladder parent grain，再拆预注册 phases/cohorts。两个实验都使用 normalized contemporaneous market mid，不是 executable ask/depth。

V01–V07 的 forecast 是 conservative single-run reconstruction，不是真实 provider-run/first-seen W1；因此即使点估显著，也仍只能进入 clean forward shadow，不能直接升 live。本轮实际没有候选走到这一步。

## Final Verdict

```text
weather-only:
significance=FAIL_TO_BEAT_MARKET
calibration=legacy/reconstructed only
pooled_baseline=robust-tail W0 retained as physical reference
forward=clean W1 not yet scoreable

market residual:
baseline=M0 same-row normalized market
forward=FAIL; selected_version=null
execution=not_run_by_probability_gate

production:
live_action=none
orders_changed=0
```

10 个版本中没有一个满足 `beat_market`。因此不冻结“盈利参数”，也不输出可误用的真实下单配置。

保留的方向是 V04 的结构而不是它的系数：

```text
log posterior(rung)
= log market(rung)
+ strongly-shrunk residual(
    real provider run revision,
    model spread,
    assigned-minus-consensus,
    ordinal location/scale
  )
- log Z
```

理由：V01/V03 被完整收缩为 market，证明直接 weather likelihood 没有增量；V04 是唯一方向一致但证据不足的版本，而且它用的 revision 仍是 reconstruction proxy。正确下一步是让同一结构吃 clean W1，而不是继续在已看过的 27 日调参。

已完成可运行的 zero-notional harness：

- frozen artifact SHA 校验；
- `delta=0` 严格回 market；
- checkpoint-global + per-rung feature；
- feature/execution book 独立时钟；
- native ladder、fee、depth fail-closed；
- 全 universe scored/blocked；
- candidate/intent 默认关闭，显式开启也只能输出 `requested_size=0`；
- settlement date-equal logloss/Brier/RPS 和 target-date bootstrap；
- 不含 exchange/order/network dependency。

当前 V04 artifact `baseline_gate_pass=false`、`shadow_eligible=false`，且 clean collector 尚未产生 settlement-complete + market-complete W1 rows，所以 runtime 只能做接口/demo 与未来 clean checkpoint scoring，不能被称为已验证可交易策略。
