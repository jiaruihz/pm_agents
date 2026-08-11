# Forecast Repricing

## Tape / queue-conservative execution update（2026-08-11）

`0/48` 只表示旧 48 个 holdout quotes 没有 observed ask 跌到原 best bid，不能排除主动 SELL 打 bid；现已用
现有 WS `last_trade_price` 做独立 execution transport 复核。固定 60s post、真实 SELL volume 吃完 visible
queue+5 股才记保守成交：4,017 posts、44 possible fills、23 个 exit-scoreable fills，fixed60 ROI `-6.57%`
（1/23 为正）。tight spread/light queue/supportive tape 仍为负；首次可保本动态退出为 `-6.93%`。
盘口恶化前撤单只剩2 fills/1 date、ROI `+6.72%`，不足以冻结且不与 D-1 candidate 同分母。

inside-spread v2 已区分固定 +1c 与 Polymarket native +1 tick（0.01/极端价0.001）。native +1 tick 把保守
fill rate 从 `1.10%` 提到 `3.59%`（`3.28×`），但69个exit-scoreable fills的fixed60/dynamic ROI仍为
`-3.69%/-3.62%`；adverse-cancel+tight/light也只有 `-1.22%/-1.25%`。因此 +1 tick 是更合理的
signal-conditioned 报价 challenger，不是 generic maker alpha；要证明它能用于本 family，仍需 D-1 forecast
candidate 同分母 WS + own order lifecycle，不能拿本次 hot-strip 1日样本替代。

generic 60s 结果不得用于扣减旧 D-1 selected-position `+36.39%`：旧策略实际是30m continuation
checkpoint / 60m hard exit。对旧49笔改成native bid+1 tick，3笔因达到ask不再post-only；余下46笔在冻结
原dynamic exit path后，条件ROI仍为`+25.20%`（原同子集`+36.60%`），剔除最大赢家仍为`+13.69%`。
所以1个native tick不会消灭条件edge；当前唯一关键未知是actual fills是否逆向集中于31笔亏单。generic tape
只能证明无signal普挂逆选，不能回答D-1 selected fill distribution。

历史 D-1 已更新至 `2026-08-10`：5,973 events/62,173 rungs/66 dates。entry challenger 相对 M0 的 holdout
MSE delta `-0.000000487`，CI `[-0.000001851,+0.000000939]`，仍跨0；新 holdout anti-toxic selector 选择0笔，
full-ladder completion 仍未通过。generic single-leg passive expression 判 `rejected_for_expression`，completion runner
继续 zero-notional abstain。完整证据见 [tape execution v1](2026-08/2026-08-11-forecast-repricing-tape-execution-v1.md)。

## Anti-toxic maker / full-ladder completion handoff（2026-08-10）

旧 position policy 的 maker-fill-conditional `+36.39%` 已完成 fillability 体检：secondary holdout 48 个旧
quotes 中 `0/48` 出现 best-bid trade-through；全体 D-1 的 trade-through rows 98% 以上 60m markout 为负。
这说明旧收益来自未成交报价，不是可复制 maker edge。linear/HGB 能识别 toxicity（holdout ROC-AUC
`0.84156`），但找不到成交后为正的单腿 slice。

当前可运行 expression 改为 `full_ladder_completion_v1` zero-notional probe：一档 best-bid maker fill 后只在
其余所有 YES asks + official fee + 1 tick/leg 仍把完整 set 成本压到 `$0.99` 以下时立即补齐；挂单期间每个
完整 ladder checkpoint 重算并动态撤单。历史 development 33 quotes 仅1个 trade-through，触价时 completion
ROI `-11.45%`；holdout 2 quotes/0 touch，所以 research gate 为 FAIL，不能 live。current raw 106 个真实 revision
pairs/1,155 rungs 中最大 margin `-4.72c`，runner 正确输出0个 POST_MAKER。完整证据、artifact 和命令见
[anti-toxic maker v1](2026-08/2026-08-10-forecast-repricing-antitoxic-maker-v1.md)。

Runner 默认输入已从历史 `full_ladder_output` 修正为 current canonical `strategy_snapshots`。当前 WS 只覆盖
intraday hot strip，D-1 candidate tape/queue/own-fill 仍缺；扩 subscription 前需显式部署确认。

## Full-ladder position handoff（2026-08-10）

本 family 已有可加载的 `forecast_repricing_full_ladder_position_v1`：D-1 revision 后按
`weather shock × signed mode distance × neighbor propagation` 给所有 rungs 评分，maker-fill-gated entry，
30m 用完整 ladder continuation head 做 HOLD/EXIT，60m hard exit；不使用 max/min selector。2026-08-10 已修复
历史重建适配器漏接独立 orderbook/full-ladder 数据层的问题，重建范围由 `2026-07-07` 扩至默认 T-1
`2026-08-09`。Secondary reconstructed holdout 的 maker-fill-conditional dynamic ROI 为 `+36.39%`
CI `[+17.20%,+52.44%]`（49 positions/11 active dates），但同 rows dynamic 相对 fixed60 为 `-1.07pp`
CI `[-7.46pp,+5.01pp]`，taker 反事实 `-48.25%`，entry relative-markout 相对 M0 的 OOF/holdout CI 均跨0、
actual fills=0、formal forward=NA。因此状态仍是 `runnable zero-notional / inconclusive`，不是 live alpha。
旧 39 positions/6 dates/`+13.13%` 结果已 superseded-for-decision-use。实现、命令和证据见
[full-ladder position v1](2026-08/2026-08-10-forecast-repricing-full-ladder-position-v1.md)。

`Forecast Repricing` 是独立于 Core Carry 的短周期 market-response family。它预测 D-2/D-1 forecast revision 被本系统 first-seen 后，完整 temperature ladder 在未来 5/15/30/60 分钟的可执行价格变化；它不预测最终 Tmax winner，也不继承 Core Carry 的持仓、阈值或 live 授权。

## 当前判定

状态：`inconclusive / no tradable edge / collector-exact event denominator accumulating / event-book forward not deployed`。

- 固定 `city × target_date × forecast-event` 分母的修复后历史重建覆盖 6,003 个 events、62,444 个 ladder rungs；D-1 position universe 为 5,902 events/61,392 rungs/65 dates，目标日期覆盖 `2026-05-21..2026-08-09`。`2026-07-08..2026-07-15` 仍因全城 full-ladder collector 尚未开始而是真实 evidence gap。
- 5m 无覆盖；15m 只有 4 dates；只有 30m/60m 达到历史建模下限。development expanding OOF 选中 60m `weather + market level` Ridge，threshold 固定为 predicted taker net markout > 0。
- 修复后 secondary chronological holdout `2026-07-29..2026-08-09` 中，entry challenger 相对 market-level M0 的 full-ladder MSE delta 为 `-0.00000058`，target-date bootstrap 95% CI `[-0.00000195,+0.00000073]`，跨 0；development OOF 也跨 0。因此尚未证明独立 forecast alpha。
- 早期 fixed-horizon taker diagnostic 为 28 个 signals/4 dates、ROI `-31.96%`；修复后同一 49 个 selected position 的 taker 反事实为 `-48.25%`。两种口径方向一致：拒绝 taker。
- 修复后 maker-fill-conditional dynamic point estimate 为 `+36.39%`，CI `[+17.20%,+52.44%]`，但同 rows 不胜 fixed60，且同一 selected rows 的 taker 反事实为 `-48.25%`。actual maker fills=0；queue、partial fill、expire 与 adverse selection 均未观测，所以它不是可实现 ROI。
- terminal settlement head 单独失败：legacy model Brier `0.08423` vs market `0.06716`，delta `+0.01707` CI `[+0.01532,+0.01877]`；该负结论不能拿来替代短周期 repricing 检验。

## 跨城市 first-seen 关系

本 family 只池化 `forecast_revision`，不把 FMI/JMA/KNMI/AMOS 等 intraday observation first-seen
混进同一模型。两者共用 event-ladder panel、market-prior correction、四组 baseline 和执行评测，
但 observation family 的主 horizon 是 `30/120/300s/next_official`，source-basis/settlement adapter 也不同。
跨城完整 review 见 [城市模型 living doc](../WEATHER_CITY_TEMPERATURE_MODEL_RESEARCH.md#12-first-seen--repricing-跨城-review2026-08-09)。

2026-08-09 current raw 已有 52 城 collector-exact forecast content first-seen：D-1 `5,339` 个 unique events /
`13` 个 first-seen dates（material `2,342`），D-2 `555/12`（material `177`）。这说明事件分母已开始积累，
但 source response 不暴露 provider run/issue time，且没有把每个 revision 绑定到 event-driven
pre/t0/5/15/30/60m full-ladder burst；周期 market-books 不能事后拼成 formal forward。

## 冻结研究合同

- primary horizon：60m；5/15/30m 只保留 coverage/secondary diagnostics，不能在 forward 中重选。
- primary model：`weather_plus_market_level` fixed Ridge；threshold=0；每个 forecast event 最多一档、每个 city-target_date 只取 first signal。
- required comparisons：market level only、weather innovation only、weather+market、weather+market+static microstructure；另保留 market+static microstructure control，用于隔离 weather 的真实增量。
- 权重：target_date 等权，再 event 等权，再 rung 等权；bootstrap block=`target_date`。
- clocks：forecast issue time、provider availability/first-seen、collector first-seen、book snapshot/available time、virtual execution time 分列。缺 provider clock 时保持 missing，不得借 collector clock替代。
- taker：entry ask、真实 top depth、future bid、双边 Weather fee；maker：posted bid、queue ahead、partial fill、expire、adverse selection；touch-only 仅作诊断。
- formal forward：只能用冻结后 `collector_exact` forecast events 与 policy-valid D-2/D-1 ladder capture；archive secondary holdout 不叫 forward。

## 下一步唯一动作

共享 all-event `first_seen_event_ladder_panel_v1` materializer 已实现并离线 contract-test；Amsterdam golden
2,756 events 的 slot shape coverage 为96.95%–99.06%，但旧 dedicated post captures 没有严格 request/response/parse clocks；
Helsinki 255 events 的 t0仅10且完整链为0，证明当前周期 snapshot不能替代 event burst。下一步经显式部署确认后补
D-2/D-1 与日内 observation event burst；不部署真实订单、不改现有 live 策略。固定设计见
[2026-08-09 full-ladder/readiness snapshot](2026-08/2026-08-09-forecast-repricing-full-ladder-readiness-v1.md)。

可复跑 artifact：

- base：`/Volumes/jrs-archive/pm_agents/research/artifact_store/active/forecast_repricing/full_ladder_base_20260810_tminus1`
- position：`/Volumes/jrs-archive/pm_agents/research/artifact_store/active/forecast_repricing/full_ladder_position_20260810_tminus1`
- input SHA-256：`4f7b0f002b19dafd414aaac69a437d03c79f5f6146e2c2de6b5a58ca1ff449a3`
- position model SHA-256：`812adb1e8f548babedc486a2f35f7b5b967c6d3b0c7bc84f3b155eecf5833c16`；zero-notional artifact，不是 live 授权。
