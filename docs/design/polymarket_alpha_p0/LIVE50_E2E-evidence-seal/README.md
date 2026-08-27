# Polymarket Alpha 50-market controlled E2E evidence seal

## 结论

`CONTROLLED_READ_ONLY_E2E_COMPLETE_WITH_LIMITATIONS`

2026-08-27 在用户明确授权下，通过已封装的只读 Gamma transport 和现有
`weather_market_books` owner 实现做了一次 50-market 受控实测。结果证明：

- 50 个真实 active/open market 可进入 Alpha catalog；
- 五个 recall provider 均被调用并产生结构化 receipt；
- 52 个 RecallHit 聚合成 50 个 Candidate，0 rejection；
- 目标 market `3867798` 从 Gamma ingest 一直走到 Rule B、Decision 和
  Prediction，最终 state 为 `FINALIZED`；
- 最终 decision 为 `SIMULATE / NO / NO_ORDER`，没有 order、signing、private
  key 或生产写入能力。

这不等于 `READ_ONLY_DAILY_OPERATION_READY`。本次 research result 是 deterministic
engineering fixture，只用于验证 orchestration、隔离、hash binding 和状态机，不是
事实研究，也不能据此交易。

## 50-market scan

Gamma 通过显式 sealed proxy profile `mac_local_market_proxy_v1` 请求两页
`GET /events`：

| offset | events | nested distinct markets | raw bytes | redirect/auth |
|---:|---:|---:|---:|---|
| 0 | 20 | 84 | 379,568 | 0 / false |
| 20 | 20 | 466 | 1,742,134 | 0 / false |

从返回值中按 active/open、唯一 market id 选出 50 个 market。catalog 写入结果：

- pages: 1（冻结后的 50-market selection artifact）；
- raw market artifacts: 50；
- snapshots: 50；
- aliases: 50；
- normalization errors: 0；
- schema drift receipts: 50（未知字段被保留和 receipted，没有静默丢弃）。

第一次抓取 offset 20 时 1 MiB response cap 正确 fail closed；随后在同一授权范围内
把 sealed cap 提高到 5 MiB，成功抓取 1,742,134 bytes。失败 receipt 保留在 artifact
root。

## Recall routes

| Provider | Status | Hits | Suppressions | Skips | 说明 |
|---|---|---:|---:|---:|---|
| new_changed | SUCCESS | 50 | 0 | 0 | 初次 ingest 全部为 NEW |
| structural_metadata | SUCCESS | 0 | 50 | 0 | NEW snapshot 按合同不重复报 structural anomaly |
| controversy | SUCCESS | 0 | 0 | 1 | 绑定实际 dispute source，但本次没有 supplied current mapped case |
| specialist_wallet | SUCCESS | 0 | 0 | 0 | 绑定实际历史 wallet store；没有伪造当前事实 |
| book_anomaly | SUCCESS | 2 | 0 | 6 | 对 11 个 paired weather markets 的 fresh books 运行 |

book owner one-shot batch 请求 22 个 token，22/22 status `ok`，11/11 paired book
被 Alpha bridge 接受。book anomaly 命中：

- market `3867798`: YES/NO spread 均为 `0.17`；
- market `3867799`: YES/NO spread 均为 `0.15`。

最终 accepted RecallHit 52、Candidate 50、aggregation rejection 0。

## Full chain

目标：market `3867798`，问题为 “Will the highest temperature in Amsterdam be
27°C on August 27?”。

实际状态链：

```text
GAMMA_INGESTED
  -> CANDIDATE_SCANNED
  -> Rule A PASS
  -> BLIND_PACKET_FROZEN
  -> BLIND_RESULT_ACCEPTED
  -> FORMAL_REVIEW fresh paired book demand/receipt
  -> MARKET_PACKET_FROZEN
  -> MARKET_RESULT_ACCEPTED
  -> Rule B PASS
  -> Decision + Prediction
  -> FINALIZED (execution=NO_ORDER)
```

Blind 和 Market 两阶段都使用 deterministic fixture provider。Blind result 被
allowlist-only packet 约束；fresh formal book 只在 Blind result accepted 后请求；
Market brief/job 绑定 exact accepted Blind result payload/hash。最终 action 仅是
`SIMULATE`，不是下单建议。

## 实测发现并修复的根因

1. 当前 Gamma active payload 使用 `description`、`endDateIso`，且可能省略
   `resolved`。normalizer 现在只在 `active=true && closed=false` 时推导 ACTIVE；closed
   或矛盾生命周期仍 fail closed。
2. existing owner batch 对完整 CLOB response 计算 hash，却只归档 top-20 summary，导致
   bridge 无法重算 raw hash。owner artifact 现在保存 exact full raw response，summary
   仍保持 top-20。
3. Market research orchestration 没把 accepted Blind baseline 传给 brief builder，且
   work-order validator 对 packet JSON mode/hash 的比较口径错误。现在 exact Blind
   payload、packet id/hash、evidence、clock 和 provenance 都被验证。
4. 独立 review 发现 expanded Gamma executor 接受 policy/budget 参数，却没有把它们
   hash-bind 到 authorization，CLI 也不能复现 offset page。现已给 authorization 增加
   exact policy/budget hash，增加跨进程 append-only request/artifact budget ledger，并
   让 CLI 强制加载 sealed policy、budget、offset；缺 event id 的 Gamma row 也会 fail
   closed。

## Readiness boundary

- `CONTROLLED_READ_ONLY_E2E_PILOT`: PASS
- `OFFLINE_REGRESSION`: PASS
- `READ_ONLY_DAILY_OPERATION`: NOT APPROVED
- `PRODUCTION_CAPTURE_EXPANSION`: NOT AUTHORIZED
- `LIVE_ORDER_OR_SIGNING`: STRICTLY OUT OF SCOPE

本轮没有 restart/deploy 生产 owner，没有写 current runtime DB。实测期间发现生产
weather WS owner 的 JRS 写权限故障导致其 fresh coverage 不足，所以 fresh sensing/formal
books 使用同一个现有 owner batch implementation 做 bounded local one-shot；这验证 owner
代码与 Alpha bridge，但不证明常驻生产 capture 已健康。

原始 live50 请求发生在 review 修复之前：它有用户的显式 scope authorization、exact
transport policy receipt 和预算内的真实请求，但旧 authorization contract 尚未直接
携带 policy/budget hash。这个 gap 已在代码和 3 个 adversarial regression 中关闭；因
原 authorization 已到期，没有伪造 post-fix live receipt。

## Evidence pointers

机读摘要见 `manifest.json`。原始网络与 runtime artifacts 保留在：

`/private/tmp/polymarket-alpha-pilot/live50-iNMR73`

关键 hash 见 `hashes.sha256`；命令与测试见 `commands.log`、`test-results.txt`；独立
review 见 `independent-review.md`。
