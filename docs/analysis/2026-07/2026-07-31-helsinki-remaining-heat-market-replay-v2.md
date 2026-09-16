# Helsinki remaining-heat v7 × Polymarket replay v2

Status: `research counterfactual / displayed-book taker replay / no live change`

## 结论

- 固定盘口窗口 2026-07-15..29；signal=2115 checkpoints，same-row score=534 rows/14 dates。
- v2：37 signals，5-share cost=$119.90，PnL=$15.10，ROI=12.59%。
- v7：44 signals，5-share cost=$156.33，PnL=$13.67，ROI=8.74%。
- 同分母 Brier：v2=0.09137，v7=0.08708，market=0.06252。
- 同分母 logloss：v2=0.27542，v7=0.26298，market=0.21789。
- v7 相对 v2 的 Brier delta=-0.00429，95% CI [-0.01624, +0.00676]；点估更好但尚不显著。
- v7 的 5-share ROI date-block 95% CI=[-3.70%, 18.51%]，仍跨 0。
- v7 新增 8 个 date-X，8/8 赢，但多数买在 0.99 左右：投入 $39.89 只赚 $0.11；同时少了 v2 的 1 个 date-X（该笔赚 $1.89）。
- 没有增加阈值；仍是每个 date-X 第一次 fee-adjusted edge>0，direct X NO 与完整 upper-YES strip 二选一。
- 这是 displayed-ask counterfactual，不是历史 fill；actual fills=0。

## 漏斗与分情况

- signal funnel：2115 个 10 分钟 checkpoint / 15 dates。
- evidence funnel：first-seen=1268 → PIT book=534 → settled same-row=534 → 5-share executable=560。
- 本盘口窗口所有可评分行 forecast_available=1，因此这次检验的是 v7 的 v5 base 路由；missing-forecast METAR expert 没有被该窗口触发。
- v7 分情况：13–15点 ROI 为正且贡献主要利润；pre-peak 0–60m 仍为明显坏段。这里只作诊断，没有变成事后 gate。
- 完整概率校准、逐笔交易和 hour/peak/path/forecast-availability 切片见同目录 CSV。

## 资格

- weather v7 可继续作为 Helsinki challenger；market alpha 仍未确认，不具备改 live / 真实下单资格。
- 2026-07-31+ frozen forward 未读取、未调参。
