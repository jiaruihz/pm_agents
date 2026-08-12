# Forecast Repricing：quote-level EV 重训

status=`rejected_for_expression / runnable zero-notional abstain`

production: `live_action=none` · `orders_changed=0`

## 结论

后续 raw-path 体检确认，旧模型的根因比“低价 tick 偏置”更深：训练输入是 legacy strategy-snapshot
innovation，并不是真实 provider-run first-seen；`min ask` 又没有 first-touch 时钟，导致 maker 条件收益把
未成交好单与被动成交坏单混在一起。现已完成两项修复：

- 从独立 append-only `market_books/batches` 重建每个 quote 的 first observed ask touch，并从 touch clock
  起算 5/15/30/60/120m executable bid；quote `<1¢` 全部排除，touch 明确只是 proxy，不冒充 fill。
- D-1 runner 现在保存 revision 前/后的 multi-model mean/median/IQR，并把每个完整 native ladder 的真实
  bid/ask/depth写入 checkpoint；同一对 book snapshots 之间的多个 provider updates 只归因一次。

修复后，所有 observed-touch 样本在 5–120m 均为负：全路径30m ROI `-17.20%`，collector-exact
30m `-14.50%`；简单延长持仓不能消除 adverse selection。旧 quote-EV 在 collector-exact holdout
3,406 actions / 4 dates 上选择0笔，状态保持 `no_admitted_quote_rejected_for_expression`。

真实 run-aware D-1 `18–24h` consensus revision 对30m盘口方向有弱信号，但 fixed 2°F distribution 的
direct ask→future bid 在官方双边fee与每边1 tick slippage后，30/60/90m全分母ROI分别为
`-12.68%/-11.98%/-13.47%`；latest-two-date holdout为`-9.21%/-8.79%/-8.40%`。当前只够支持扩
event-driven WS 采集，不足以生成交易策略。

旧单腿 maker selector 有结构性错误，不是简单排除 `0.1¢ tick` 即可修复：它预测
`h60_relative_bid_move`，却把相对整条 ladder 的涨幅直接当绝对现金 markout；阈值又按假设
best-bid 全成交后的 selected ROI 选择，未联合建模 quote price、fill、queue 与逆向选择。修复后在
`forecast event × rung × legal post-only quote action` 上训练 dollar EV，development expanding OOF
与最后15日 secondary reconstructed holdout 都选择0笔。正确动作是 `NO_QUOTE`，不是制造一个正ROI切片。

## 固定数据与动作

- input：`full_ladder_base_20260811_raw_pit_v2/forecast_event_rungs.csv`
- SHA-256：`738c954652d3a98ec731598f5670946d2d75e108318e0c404d7ffef1e8ebab1c`
- quote actions：best bid、bid+1/3 native ticks、bid+0.5¢、1/4 spread、mid、ask-1 tick；严格 post-only、同价去重。
- label：30分钟 `min ask <= quote` 只作 touch proxy；event+60分钟 executable bid 扣 Weather exit fee，再减真实 quote price。未touch记0；缺时钟不评分。
- 主目标：每股 proxy expected dollar PnL；date→event→rung→quote action 等权。禁止用ROI选阈值。
- 训练：57 development dates；其中42日 expanding OOF。secondary holdout为2026-07-27..08-10共15日，未作为 formal fresh forward。

## 结果

| slice | quote rows | events | touch proxy | touch AUC / Brier | admitted quotes |
|---|---:|---:|---:|---:|---:|
| development OOF | 173,279 | 5,117 | 17,550 | 0.7973 / 0.07972 | **0** |
| secondary holdout | 46,375 | 1,228 | 3,761 | 0.8123 / 0.06601 | **0** |

holdout 的 conditional value head 对所有 quote 均预测为负，因此不是阈值过严。宽分母3,761个
ask-touch quote-action 的描述性 PnL 为 `-$79.005 / $484.355 = -16.31%`；它包含同一 rung 的多个
报价动作，只用于反证 execution expression，不能称为策略ROI。按 tick 看，`0.001` 为 `-61.14%`，
`0.01` 为 `-13.22%`。历史 tape 的 queue-conservative fills 同方向为负。

weather+market quote-EV head 相对 market-only：development OOF MSE delta
`-1.611e-6`，95% CI `[-2.377e-6,-0.918e-6]`；holdout `-0.824e-6`，CI
`[-1.990e-6,+0.318e-6]`，不显著。相对 market+static-microstructure 的 holdout delta为
`+0.386e-6`，CI `[-0.456e-6,+1.177e-6]`。因此天气信息在开发期有微弱增量，但不足以形成正的可执行报价。

## 可运行闭环

新 stable identity 为 `forecast_repricing_quote_ev_v1`，artifact：

`forecast_repricing/quote_ev_retrain_20260812_oof_v3/quote_ev_policy.joblib`

SHA-256：`f0ee8e74e456e40cb21f49948798461190dc5ba0df0f69961f5c4705514a2d10`。

现有 zero-notional runner 已可加载该schema：entry联合选择rung与quote，`NO_QUOTE`为正式结果；pending
每个完整 ladder 用当前mid分布重估并KEEP/CANCEL，trade-through不冒充fill；signal TTL为30分钟。
只有own fill evidence才能开启position clock；旧dynamic exit未通过，所以当前诚实fallback是实际fill后固定
60分钟hard exit，不宣称模型重估退出有效。

当前生产snapshot one-shot smoke成功：984个collector-exact ladders、44 streams、left-censored baseline，
0 candidate、0 plan、0 order。未部署/重启任何生产实例。

## 判定

该次重训完成了可复跑、可加载、会安全 abstain 的策略位置，但没有找到盈利策略。被拒绝的是
`30m ask-touch proxy + event+60 liquidation` 的单腿被动买入表达，不是否定 forecast revision 或
full-ladder 特征本身。下一步若继续该family，只能用同分母 own-order lifecycle / active SELL tape 建立
fill-time-relative label，或改变交易表达；不能再回到假设best-bid全成交。
