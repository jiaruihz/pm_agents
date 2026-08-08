# Weather CLOB Orderbook Capture（历史 tombstone）

Status: `superseded / implemented-in-current-market-books`
Source of truth: no
Current authority: `WEATHER_DATA_PIPELINE.md`; `WEATHER_SYSTEM_CONTRACT.md`; production manifest

本文原为 2026-05 的 N100/`weather-predict` orderbook sidecar 设计。其耐久结论已经进入当前数据合同：

- executable research 使用真实 bid/ask/depth，不用 UI price 或 midpoint 冒充成交价；
- raw orderbook 与策略 join 分层保存，并携带 request/response/parsed/available 时钟；
- 缺失的历史盘口是 coverage gap，不能事后伪造或计作策略过滤。

当前唯一 raw owner 是 controller 管理的 `weather_market_books`，写入
`market_books/latest.json` 与 `market_books/batches/`；完整分布物化到 `market_ladder_snapshots/`。
旧 `weather-predict/output/orderbook_snapshots`、N100 timer 和本地 mirror 路径只解释历史证据，不是当前入口。

完整 2026-05 设计和当时的 production checks 可从本文件的 git history 恢复。
