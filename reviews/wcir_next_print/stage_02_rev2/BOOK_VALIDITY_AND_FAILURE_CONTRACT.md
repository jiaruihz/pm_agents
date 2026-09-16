# WCIR Stage 2 rev2 — Book validity 与 failure contract

## Boundary

本合同只解释 frozen WS archive 在 event checkpoint 上能证明什么。REST 只用于 market/token identity 与独立 parity，绝不填补 WS gap。collector、selector、staleness policy、production config 与 order path 均不修改。

## 独立层次

`book_valid` 只表示：在 checkpoint 前存在同一 token/condition 的 verified WS baseline 与可验证 delta state，receive clock 不倒退、没有 open blocker，且最后状态 age 不超过 frozen 120 秒。

执行能力另行报告：

- `side_available.buy/sell`
- `sweep_1_feasible.buy/sell`
- `sweep_5_feasible.buy/sell`
- `sweep_10_feasible.buy/sell`
- `exit_feasible`（5-share bid sweep）

有效但单边或深度不足的 row 保留在 denominator。它们是 economic infeasibility，不是 reconstruction invalid；两类字段必须互斥。

## 唯一 primary status

每个 event × token × checkpoint 有且仅有一个：

- `ARCHIVE_MISSING`：token 从未出现在 frozen subscription epochs；
- `IDENTITY_MISMATCH`：epoch token metadata 与 frozen condition 不一致；
- `CLOCK_UNCERTAINTY`：receive clock 缺失、倒退或 checkpoint 先于状态；
- `NO_BASELINE`：已订阅但 checkpoint 前无 verified full-book baseline；
- `OPEN_GAP`：parity、sequence 或 exchange-clock blocker 尚未恢复；
- `STALE_BOOK`：verified state age > 120 秒；
- `VALID_BUT_ONE_SIDED`：可重建但至少一侧无 quote；
- `VALID_BUT_INSUFFICIENT_DEPTH`：两侧存在，但至少一侧 5-share sweep 不完整；
- `VALID_TWO_SIDED_DEPTH`：可重建且两侧 5-share sweep 完整。

`UNKNOWN` 不允许出现。任何新增状态必须先改合同、测试和 reviewer packet。

## Archive replay

覆盖 2026-08-08 至 2026-08-26 全部可见 WS `.jsonl` 与同边界 epoch manifests。每个 raw 文件冻结 path、byte size、row count、SHA-256、first/last receive clock 与 within-file clock regression。重放顺序由 `(received_at_ns, canonical_ws_frame_id)` 决定；forward、reverse-file-list 与 chunked-file-list 必须得到相同 raw-frame ordered identity 和 coverage identity。

## Stage 2 gate

Stage 2 data capability 只有同时满足以下条件才关闭：至少 10 transport days、至少 3 reconnect/gap days、至少 3 normal days、所有 event 原因唯一且 `UNKNOWN=0`、source-t0 `book_valid >= 90%`。未满足时只能返回 Stage 2 collector/clock diagnosis；本轮无权修改 collector。
