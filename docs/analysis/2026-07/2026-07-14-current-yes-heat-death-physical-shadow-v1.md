# Current YES Heat-Death Physical Shadow v1

Status: snapshot
Date: 2026-07-14
Verdict: `inconclusive_forward_shadow_only`

Historical proxy performance is now quantified in
`2026-07-14-current-yes-heat-death-physical-backtest-v1.md`; its result
supersedes this report's initial `NA` gates while the complete new-feature
forward gate remains thin.

## 结论

`weather_state_v2` 新增的雨、云、风、观测时钟、solar geometry 和 forecast
weather 特征符合“剩余升温窗口是否已经死亡”的建模方向，可以开始实践；但它们目前是
机制特征，不是已经校准的胜率。策略先作为 zero-notional forward collector 运行，不改
任何 live policy，也不生成订单。

研究问题固定为：在城市本地 13:00-17:00，温度已从 running max 回落、forecast peak
已过、观测路径不再升温且高点已成熟时，额外的雨/云/湿度/海风/未来三小时预报/solar
证据能否识别市场仍高价出售的 current YES，或给出更便宜、更稳健的 d1 NO 表达。

## Feature 审计与根因修补

新增特征总体符合设想，尤其补上了原先 `fade-confirmed` 只有温度路径、缺少“不支持再
升温”的物理证据这一层。审计发现两个接线缺口并已修补：

1. 标准 `weather_data_feed_service/observations.py` 原先没有把 raw METAR、降水、云底、
   ceiling、风向和 1h 变化写入 observation cache；feature function 虽存在，生产输入拿
   不到。现已在共享 observation cache 层输出，策略不再私建 parser。
2. `forecast_window_features()` 在 forecast peak 已过后不再返回天气窗口。Busan 类样本
   恰恰需要判断接下来三小时是否仍有雨云压制，因此新增 PIT
   `forecast_*_remaining_3h_*` 字段；decision-to-peak 字段语义保持不变。

另将 `minutes_to_next_expected_obs` 改为有符号值：负数代表报文已 overdue，不再把
“已迟到”和“正好到点”都压成 0。

## Shadow 定义

入口：`scripts/ops/weather_current_yes_heat_death_shadow_v1.py`

- 分母：每份 paper snapshot 中所有 same-day city-date；缺观测和 PIT 不合法行显式审计。
- 研究窗口：城市本地 13:00-17:00；窗口外继续落分母，但不算策略候选。
- 基础确认：decline >= 0.5C、forecast peak 已过、path flat/cooling、running max 至少成熟
  60 分钟。
- 物理支持：只记录降水、云量、湿度、海风、remaining-3h forecast、solar falling 等连续
  证据；暂不把支持项个数解释为概率。
- 表达：同时记录 current-bracket BUY YES 与 d1 BUY NO 的直接 ask、depth 和 fee-adjusted
  cost；候选缺 book 时只读刷新对应两个 token。
- 安全：`zero_notional=true`、`no_order_placed=true`，代码没有 plan/order/executor 路径。

## 首次运行证据

首次 PIT 成功批次为 `snapshot_20260714_1947.json`：40 条 observation cache 行通过
PIT，产出 39 条 `weather_state_v2` 和 39 条策略分母，builder audit 为 39 `ok`、7
`missing_observation`，feature store frame id 为 `680f986c530f45130db7383d`。该批次发生
在多数亚洲城市研究窗口之后，因此旧调试版显示的 physical-confirmed 数量不能当作本
策略候选；13-17 窗口已在代码和测试中冻结。

Busan 当批机制字段能表达用户描述的形态：running 30C、current 26C、forecast peak 已过、
path flat、未来三小时降水概率和云量均为 100%，且 solar elevation falling。因决策时刻为
20:47 local，它只保留为窗口外机制样本，不进入午后候选。

## 三门结论

- 绝对收益显著性：`NA`，尚无 forward settlement 样本。
- 相对同价 baseline：`NA`，尚未形成可结算的候选 cohort。
- forward 稳定性：`FAIL_THIN`，collector 刚启动。

因此本轮动作是继续 zero-notional forward capture。至少积累跨日期、跨城市的完整
13-17 候选后，再拟合 `market probability + weather residual correction`，并同分母比较
raw market、current YES 与 d1 NO 的 proper score 和 fee-adjusted executable ROI。
