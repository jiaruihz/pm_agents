# Fast-source BUY_NO share-cap incident v1

结论：旧执行链把 FOK/FAK BUY 当成 share-sized order，但 CLOB 实际按 USDC maker amount 全额成交；价格改善会增加 shares。逐笔 canonical 复核发现 14 笔 fill 中 12 笔超过 planned/max share cap。

## 影响半径

- 窗口：`2026-07-09` 至 `2026-07-14`。
- affected：12 orders；excess 16.562142 shares，额外 cost $8.646827，额外 fee $0.168625。
- 已结算 affected 实际 PnL $2.243526；按同 VWAP/同每股 fee 缩回 cap 的 size-only counterfactual 为 $3.813940，bug 净放大 PnL $-1.570414。负数表示 bug 让 PnL 更差。
- 全策略 11 笔已结算：实际 $2.625126 / ROI 3.789%；share-capped size-only counterfactual $4.195540 / ROI 6.900%。
- 2 笔未结算 affected 额外占用 cash+fee $0.337209；不把 open cost 写成亏损。
- 完整 order_id 与原始数值：`docs/analysis/2026-07/generated/fast_source_share_cap_incident_v1/affected_orders.csv`。

这个 counterfactual 只回答 size 放大造成多少，不声称 maker GTD 会得到相同 fill/VWAP。

## 逐单清单

| date | city | bracket | cap | actual | excess | px | actual pnl | capped pnl | amplification | order |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 2026-07-09 | Helsinki | 17 | 5.000000 | 5.609755 | 0.609755 | 0.820000 | 0.968366 | 0.863109 | 0.105257 | `0x847bf2533bd18fdf08a2ff7771be59068b7d5eb6a33d9ffe21d52e374ba7fe17` |
| 2026-07-09 | Helsinki | 18 | 10.000000 | 11.084335 | 1.084335 | 0.830000 | 1.806147 | 1.629459 | 0.176688 | `0x009f5e169771d40011acceacd4ba208e0707f93836b1b8dd44bf522399bd1616` |
| 2026-07-10 | Singapore | 31 | 5.000000 | 5.111110 | 0.111110 | 0.900000 | 0.488121 | 0.477510 | 0.010611 | `0xb387da3d51ac6e25bc7725d41a80239cd32b4c4166a1e539ab30caddc721e83c` |
| 2026-07-10 | Helsinki | 21 | 10.000000 | 12.562734 | 2.562734 | 0.732324 | 3.239607 | 2.578744 | 0.660863 | `0x8281c2d12f7c10318cc5b4b3a651fcecf76279459dd8a09b00a1b5a37042d9ba` |
| 2026-07-11 | Busan | 33 | 5.000000 | 6.969694 | 1.969694 | 0.660000 | 2.291506 | 1.643907 | 0.647599 | `0xf9589ffad5a79b9bb5e5ad79445436d6614384a75db41e383390d5c0ba99410c` |
| 2026-07-11 | Busan | 34 | 5.000000 | 13.823528 | 8.823528 | 0.332766 | -4.753459 | -1.719336 | -3.034123 | `0x3dd3d9f4be2233df290140e6e7c6baacfcdd9abc5af0d47288cef93aea8967ad` |
| 2026-07-11 | Helsinki | 27 | 10.000000 | 10.238093 | 0.238093 | 0.840000 | 1.569305 | 1.532810 | 0.036495 | `0x2bd2d74fa0e3d4e56397ed02281798bbf20d08baf71ff6900a888cbc071e37e5` |
| 2026-07-12 | Busan | 32 | 5.000000 | 5.109888 | 0.109888 | 0.900215 | 0.486940 | 0.476468 | 0.010472 | `0xca80c368f3fba046d3249ce6abc09d216c432458d1e9ad5c17e64fd43429b601` |
| 2026-07-12 | Helsinki | 26 | 10.000000 | 10.222220 | 0.222220 | 0.900000 | 0.976232 | 0.955010 | 0.021222 | `0x1a8778b6798b05c251d11abe51fc2d4d52b319ea838fbd5ee0cb3dfa2b3f345a` |
| 2026-07-13 | Busan | 31 | 10.000000 | 10.444443 | 0.444443 | 0.450000 | -4.829239 | -4.623740 | -0.205499 | `0xbe49fe6c176649076dad94293b530788039bf5804f00199ce0606bba83b68c10` |
| 2026-07-14 | Busan | 30 | 10.000000 | 10.348835 | 0.348835 | 0.860000 |  |  |  | `0x65ef26d51e094452cd75a9b4feb3f2c1a8d1756b46def53f8e1aa9fad20f444d` |
| 2026-07-14 | Tokyo | 32 | 5.000000 | 5.037507 | 0.037507 | 0.933000 |  |  |  | `0xecf7751087080ccf72941eebfab92167b0b37565357a607fa83cb5ff67219049` |

## 根因与修复

- [官方 CLOB order contract](https://docs.polymarket.com/trading/orders/create)：FOK/FAK 是 market order type；BUY 指定的是 USDC spend，price 只是 worst-price protection。旧代码即使通过 `OrderArgs(size=shares)` 签名，只要以 FOK post，撮合仍会花完 makerAmount。
- 7/11 的 `max_no_ask → best_ask+cushion` 只缩小偏差；7/13 仍 10 → 10.444443，7/14 仍 10 → 10.348835。
- 修复后的 runner 使用 one-tick-below-ask 的 `GTD + post_only` share order。它若会立即 crossing 就被拒；一旦 resting，full/partial fill 都受 signed size 限制。代价是即时 fill rate 会下降，这是硬 share cap 与 taker BUY 语义之间的真实取舍。
- 同一 FOK helper 的 HKO hard-share caller 也已切到相同 post-only GTD 路径；当前没有 HKO live process 或历史 order，因此该 caller 的已发生影响为 0。
- runner 的 `share_cap_health` 继续报告历史异常并默认持久化 pause；旧 incident 只能通过显式 `--acknowledge-historical-share-cap-incidents` 解锁。任何 post-fix actual fill 超 cap 同样会写 `share_cap_violation` 并阻止后续 intent，且不能用该 historical acknowledgement 清除。

## 数据治理

以上 12 笔保留真实 cash/PnL，不删除；凡研究 per-order sizing、cap compliance 或策略 ROI 时标记 `share_cap_affected` 并同时报告真实与 size-only counterfactual。

本次只改本地代码与研究产物；未重启、未部署、未下真实订单。
