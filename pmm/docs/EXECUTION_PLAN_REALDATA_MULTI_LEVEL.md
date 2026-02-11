# PMM 执行计划（真实数据优先 + 多档做市）

## 目标

按以下顺序推进，确保每一步都可验证、可回滚：

1. 先打通真实数据监听与标准化存储。
2. 再实现多档做市策略（`multi_level_v1`）。
3. 最后用真实回放数据对比 `single_level_v1` 和 `multi_level_v1`。

---

## 现状客观评估

当前项目已具备：

- PMM 主循环（含风控、库存倾斜、合并）。
- 生成式场景回测（scenario generator + replay runner）。
- 纸面撮合与指标输出。

当前仍缺：

- 真实 orderbook/order-flow 数据的稳定采集流水线。
- 多档挂单策略与多档 diff 执行。
- 更严格的真实回放评估闭环。

结论：可作为策略研发平台，尚未达到稳健实盘标准。

---

## 阶段拆解

## Phase C（先做）：真实数据监听与落盘

当前状态（2026-02-11）：

- 已完成：`record-live`、`convert-live`、scenario 校验器与 CLI（`validate` / `validate-dir`）。
- 待完成：采集端质量统计落地到独立报告文件、真实逐笔成交流接入（替代估算）。

### C1. 采集器

- 新增/重构实时采集器：
  - 订阅 WS（market channel）。
  - 按采样间隔输出快照。
  - 每个 tick 保存 `orderbooks`。
- 额外保存 `trade_flow`：
  - 若无逐笔成交事件，先用盘口深度变化估计（近似）。
  - 明确标注 `trade_flow_mode=estimated_from_book_delta`。

### C2. 存储格式

同时输出两种文件：

1. 原始时间序列（JSONL）：
- 每行一条 tick 快照，便于追溯和重建。

2. 回测场景（JSON）：
- 结构与 `scenario_generator` 输出对齐：
  - `scenario_id`
  - `token_ids`
  - `initial_state`
  - `ticks[]`（含 `orderbooks` + `trade_flow`）

### C3. CLI 能力

新增命令：

- `record-live`：直接录制并同时产出 JSONL + scenario JSON。
- `convert-live`：将已有 JSONL 转换为标准 scenario JSON。

### C4. 验收标准

- 指定 token 可录制 >= 10 分钟无崩溃。
- 生成的 scenario 可直接被 `run` / `run-all` 消费。
- 回放结果中 `trade_flow` 字段可见且非全零（在有波动时）。

---

## Phase B（第二步）：多档做市 `multi_level_v1`

当前状态（2026-02-11）：

- 已完成：`multi_level_v1` 策略实现、运行时注册、`OrderManager.diff_multi`、tick/replay 执行链路接入。
- 待完成：多档风控细化（层间 notional cap、改单速率上限）与真实数据回放对比基线。

### B1. 策略接口

- 保持 `strategy_key` 路由不变。
- 新增 `multi_level_v1` 策略实现文件（先支持 2-3 档）。

### B2. 多档参数

- `quote_levels`
- `level_spread_step`
- `level_size_decay`
- `per_side_notional_cap`

### B3. 执行层

- 新增 `OrderManager.diff_multi()`。
- 按 `(token_id, side, level)` 做目标订单匹配。
- 继续保留 `single_level_v1` 作为基准对照。

### B4. 验收标准

- 同场景可输出 single vs multi 两组结果。
- 多档策略在“高波动/薄深度”场景中出现更低吃穿风险。

---

## Phase E（第三步）：真实回放对比与上线门槛

### E1. 固定数据集

从真实录制中抽样三类数据集：

- 平稳
- 震荡
- 事件冲击

### E2. 统一评估表

固定指标：

- `PnL`
- `MDD`
- `Fill%`
- `orders/tick`
- `inventory drift`

### E3. 决策门槛（建议）

- `multi_level_v1` 的 PnL 中位数 > `single_level_v1`。
- MDD 不高于 baseline。
- 库存偏离不恶化。

---

## 风险与回滚

- Phase C 风险：WS 数据不连续或消息结构变化。
  - 处理：保留 REST fallback，所有异常写入 `meta.errors`。

- Phase B 风险：多档导致改单风暴。
  - 处理：对每档执行 deadband；每 tick 最大改单次数上限。

- 回滚策略：
  - `strategy_key=single_level_v1` 随时回退。
  - 采集与回测命令保持向后兼容。

---

## 里程碑输出

1. `record-live` + `convert-live` 可用。
2. `multi_level_v1` 可跑、可回测、可对比。
3. 一份真实回放对比报告（single vs multi）。
