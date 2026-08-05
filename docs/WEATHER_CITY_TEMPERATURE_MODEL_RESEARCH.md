# 跨城市细粒度温度模型：统一研究与评测约定

Status: current-source  
Updated: 2026-07-30  
Scope: 城市级日内温度概率模型的方法、评测和知识沉淀；不规定统一算法或统一特征

## 1. 核心决定

本研究体系统一运行在 **Weather City Intraday Runtime（WCIR）**，框架标识
`weather_city_intraday_runtime_v1`，策略族 `weather.city_intraday_probability`。
Amsterdam、Busan、Helsinki、Seoul、Tokyo 和以后新增城市都必须通过 WCIR profile/adapter 接入。
模型尚未冻结时先接 `coverage-only` adapter 留完整分母与 blocker；这代表链路接入，不代表已有概率或 alpha。

不同城市的数据源、观测频率、可用特征、结算单位和盘口结构不同，**不强制共用同一个模型或训练模块**。

统一的只有三件事：

1. 研究问题必须写清目标、decision time、label 和 PIT 边界。
2. 模型最终导出同一种 prediction table。
3. 使用同一套概率指标；有 PIT 盘口时，再使用同一套 market baseline 和交易指标。

城市代码能自然复用就复用；不能复用时可以独立实现，只要最终导出统一结果。不要为了接口整齐扭曲城市自己的数据和物理机制。
目标模块边界、接口、迁移顺序和新城市接入工作单见
[WEATHER_CITY_INTRADAY_MODEL_RUNTIME_DESIGN.md](WEATHER_CITY_INTRADAY_MODEL_RUNTIME_DESIGN.md)。

### 1.1 当前五城知识账

城市报告不再按 `v7/v8/v9...` 文件名推断当前模型。下表只记录已吸收的耐久结论；
运行中的 adapter/process/订单仍从 production manifest 和 raw runtime 动态读取。

| 城市 | 稳定模型/接入身份 | 已吸收结论 | 当前研究动作 |
|---|---|---|---|
| Amsterdam | WCIR Amsterdam adapter；weather head 保留，交易头改为 market-prior + KNMI innovation | standalone weather probability 不能直接当 fair price；captured-book D1 上 market-prior 仅点估改善，CI 跨零且交易未胜纯 market direction | zero-notional；补真实 first-seen lineage 与 frozen forward · [paradigm](analysis/2026-08/2026-08-04-amsterdam-market-prior-event-innovation-paradigm-v1.md) · [executable null](analysis/2026-08/2026-08-04-amsterdam-executable-market-null-v2.md) |
| Busan | `busan_intraday_exact_no`；固定为 confirmation head + conditional reheat head | AMOS cross persistence 不能替代 source→routine/WU basis；v9/v10/v11 只是同一模型的历史 experiment。非线性 tournament 和连续 residual 都受独立日期、market-aligned rows 与 conditional-reheat 样本限制 | coverage-only；不继续造版本号，按固定 ontology append 新日期 · [stable architecture](analysis/2026-08/2026-08-04-busan-stable-model-architecture-v1.md) · [family benchmark](analysis/2026-08/2026-08-04-busan-model-family-benchmark.md) |
| Helsinki | frozen remaining-heat/weather artifact + market-expression research head | weather head 有历史 OOF 增量，但当前 expression reliability 未通过；少量正 ROI 不能覆盖 market baseline、active grain 和独立日期不足 | 保持 zero-notional，不改 artifact；补 PIT book/forward · [v7 lineage](analysis/2026-07/2026-07-31-helsinki-v7-forecast-lineage-missing-expert.md) · [reliability](analysis/2026-07/2026-07-31-helsinki-model-reliability-audit-v1.md) |
| Tokyo | frozen weather path head；market-offset 仍是 development challenger | 历史 collector-exact 覆盖不足以重训后恢复旧 holdout；state-entry 仍未稳定胜 market | zero-notional collector，达到预注册 exact train/holdout 日期后再评审 · [coverage](analysis/2026-07/2026-07-31-tokyo-market-anchor-training-coverage-v7.md) |
| Seoul | Korea source-event adapter，尚无独立 frozen probability artifact | 与 Busan 共用的 CrossNO/dual-head 不能绕过城市 source→settlement basis；外部负面对照使当前表达不能晋级 | coverage-only；先建 Seoul 自己的 PIT probability/basis，再谈 expression · [Korea dual head](analysis/2026-08/2026-08-03-korea-cross-event-dual-head-v5.md) |

跨城共同结论：模型是否“预测天气不错”与是否“打败同刻 market”必须分开。
没有冻结 artifact 的城市输出 structured blocker；有 artifact 的城市也只有在同 checkpoint
proper score、market baseline、frozen forward 和 executable expression 四层闭合后才能改变交易状态。

## 2. 城市内部可以不同

以下内容允许每个城市独立：

- 历史与实时数据 adapter；
- 特征集合和缺失值处理；
- logistic、HGB、survival、hazard 或其他算法；
- 1h、2h、EOD、remaining-heat、exact-bracket 等模型 head；
- source-to-settlement basis 和 native-unit lattice；
- Polymarket condition / bracket 映射。

天气模型与市场表达应分层：

```text
city weather/source state
  -> city-specific probability model
  -> standardized prediction rows
  -> city-specific market mapping
  -> executable residual / signal / PnL evaluation
```

## 3. 最薄的统一接口：prediction table

每个模型至少导出以下字段；CSV、Parquet 或 DataFrame 均可：

| 字段 | 含义 |
|---|---|
| `city` | 城市规范名 |
| `target_date` | 结算城市本地日期 |
| `decision_ts_utc` | 概率真正可计算的时点 |
| `target_id` | 明确的预测目标，如 `eod_cross_d1` |
| `p_model` | 对该目标的预测概率 |
| `label` | 最终 0/1 标签；未结算时为空 |
| `split` | `train` / `validation` / `oof` / `frozen_forward` |
| `model_id` | 城市内可复现的模型版本 |
| `feature_set_id` | 特征版本或稳定 hash |
| `pit_provenance` | `live_capture` / `archive_reconstruction` / `historical_non_pit` |

有盘口时可附：

| 字段 | 含义 |
|---|---|
| `market_p` | 同一 row、同一时点、同一 outcome 的市场概率 |
| `expression_side` | 实际映射的 YES/NO |
| `executable_cost` | 真实 side ask/VWAP 加官方 fee 后成本 |
| `market_snapshot_ts_utc` | 行情证据时间 |

`target_id` 不得混淆 touch、break、stop-exact 和 final-exact。不同 horizon 或不同目标必须使用不同 `target_id`。

## 4. 统一切分和 PIT 规则

- 按 `target_date` 做时间切分，不随机拆同一天的 observation rows。
- 参数和特征选择只能发生在 train/validation 或 expanding OOF 内。
- frozen holdout/forward 只复核，不继续调参。
- 每个特征必须在 `decision_ts_utc` 已真实可得。
- forecast 必须保存 issue/run/first-seen；不能用后发 run 回填。
- METAR/WU/settlement 后到值只能作 label，不能作事前特征。
- 历史 EDR、archive reconstruction 等非 PIT 数据必须显式标记，不能冒充实时领先性证据。

## 5. 统一评测体系

### 5.1 数据覆盖

每份结果先报告：

- 独立 `target_date` 数；
- prediction rows 数；
- 正例率；
- 各 split 日期范围；
- 关键特征覆盖率和缺测日期；
- PIT / non-PIT rows 数。

### 5.2 天气概率模型

主指标：

- Log loss；
- Brier score；
- calibration table / reliability curve；
- 相对同 rows 简单 baseline 的 delta。

辅助指标：

- AUC 或 rank 指标；
- 固定阈值 accuracy、precision、recall；
- mean predicted probability 与真实 base rate。

Accuracy 不能代替概率指标。阈值必须在验证集冻结，不能在 holdout 上寻找最好正确率。

至少保留一个简单 baseline，例如 train base rate、clock climatology 或城市当前最简单模型。比较算法或特征时固定 rows、label 和 split。

### 5.3 有 PIT 盘口时

在 prediction rows 与盘口完全对齐后，增加：

- model 与 raw/calibrated market 的同 rows Log loss、Brier 和 calibration；
- `p_model - executable_cost`；
- market coverage gap，不能把缺盘口当成策略过滤。

天气模型能预测天气，不自动等于打败市场。

### 5.4 策略执行映射

信号 policy 必须事前固定，然后报告：

- 信号数和独立 `target_date` 数；
- BUY 升温 / BUY 不升温或具体 YES/NO expression 数；
- 平均和分位 executable cost；
- 胜率、正确/错误清单；
- 官方 fee 后 PnL 与 ROI；
- 最大单笔损失、日期集中度；
- 缺盘口、不可执行和未结算数量。

没有历史盘口时，这一层标 `not_available`，不能填 0，也不能用天气 accuracy 代替 ROI。

## 6. 知识库怎么维护

不要为每个城市再建一套重型架构。每个 durable 城市研究在报告中固定保留五段：

1. 数据源、结算源、单位和 PIT 边界；
2. 预测目标、特征和模型；
3. 数据覆盖与切分；
4. 概率结果，以及有盘口时的信号/胜率/ROI；
5. 遇到的坑、反例和可复用启示。

单城发现标 `single_city_evidence`。只有在其他城市复现，或机制适用边界已有明确证据时，才写进本文件作为跨城默认经验。

实验数字留在 `docs/analysis/YYYY-MM/` 和可重复生成产物中；本文件只保存稳定方法，不保存不断变化的排行榜。

## 7. 给其他 Codex 对话的短指令

可以直接说：

> 这项城市温度模型研究请遵循
> `docs/WEATHER_CITY_TEMPERATURE_MODEL_RESEARCH.md`：模型、特征和数据 adapter 可以按城市独立，
> 不强行复用；但必须导出统一 prediction table，按 target_date 做 PIT/OOF/frozen-forward 切分，
> 统一报告 logloss、Brier、calibration 和 baseline。有同一时点盘口时，再报告同分母 market
> baseline、信号数、胜率、官方 fee 后 PnL/ROI；没有盘口就明确标 not_available。

