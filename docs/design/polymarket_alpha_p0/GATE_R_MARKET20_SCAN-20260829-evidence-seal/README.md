# Gate R｜2026-08-29 当前市场 20-Candidate 扫描

## 结论

本轮完成了一次当前 Gamma 只读抓取和 Candidate-stage 试跑：从 50 个 events
展开 608 个 nested markets，经确定性可交易性、期限、规则文本、流动性、成交量、
spread 和类别分散筛选，得到 192 个 eligible markets，并冻结其中 20 个为
`CANDIDATE_MERGED`。

这 20 个市场是“可以进入下一轮规则与研究”的 Candidate，不是已确认错价、
盈利机会或交易建议。

```text
RUN_ID=market20-20260829-candidate-scan
DISPOSITION=CANDIDATE_STAGE_COMPLETE_WITH_LIMITATIONS
EXECUTION=NO_ORDER
GAMMA_HTTP=200
EVENTS=50
NESTED_MARKETS=608
NORMALIZED_SNAPSHOTS=229
DETERMINISTIC_ELIGIBLE=192
RECALL_HITS=20
CANDIDATES=20
RULE_A=NOT_RUN
GLM=FAILED_NO_IMPORT
FORMAL_PAIRED_BOOK=NOT_REQUESTED
GPT_PRO=NOT_RUN
RULE_B_AND_LEDGER=NOT_REACHED
```

## 可读结果

- 20 个市场、抓取时 top quote、liquidity、volume：[`USABLE_MARKETS_20.md`](USABLE_MARKETS_20.md)
- 机读运行结果：[`usable-markets-20.json`](usable-markets-20.json)
- 确定性预筛统计：[`preselection-summary.json`](preselection-summary.json)

Gamma top quote 只用于 pre-book eligibility，不是 Blind 研究输入、fresh paired book
或独立 fair probability。

## 实际链路

```text
current Gamma /events (read-only)
  -> raw artifact + transport/security receipts
  -> catalog normalization
  -> deterministic pre-book eligibility
  -> diversity freeze (20)
  -> new_changed RecallHit
  -> Candidate aggregation / persistence
  -> stop
```

Recall receipt：

- `new_changed`: `SUCCESS`, 20 hits；
- `structural_metadata`: `SUCCESS`, 20 次因同一市场已有 `NEW` hit 而抑制重复；
- `controversy` / `specialist_wallet`: 本轮未提供相应 facts，显式 `SKIPPED`；
- `book_anomaly`: book route 未启用，显式 `SKIPPED`。

## 幂等与数据库审计

使用固定 observed/ingested clock 对同一输入连续重放两次，最终临时数据库计数不变：

```text
alpha_recall_hit=20
alpha_candidate=20
alpha_book_capture_demand_v2=0
alpha_research_result=0
alpha_review_decision=0
alpha_prediction_record=0
```

详见 [`db-audit.txt`](db-audit.txt)。调试期间生成的旧 scratch `alpha.db` 因代码修正前
使用变化的 ingest clock 出现重复 RecallHit，已明确排除，不作为证据，也未删除。

## GLM 状态

现有受控 sidecar 只允许 `haiku` alias；本机配置将它解析为 `glm-4.7`，provider 返回
`unrecognized_model`。本轮没有导入任何 GLM 结果，也没有绕过 allowlist 改用其他 alias。
详见 [`glm-failure.txt`](glm-failure.txt)。

## Transport 与安全边界

- 使用显式本地 market proxy；
- `GET https://gamma-api.polymarket.com/events?closed=false&limit=50&offset=0`；
- HTTP 200，redirect 0，无 Authorization；
- raw bytes 2,370,159；未压缩 raw SHA256：
  `433b2f5c1afa334d099c88b834803d68eb0bd338b836985c6c15c552fc069ba6`；
- 没有 signing、private key、order client 或生产配置变更。

凭证在 [`transport/`](transport/)，压缩 raw artifact 在
[`artifacts/gamma-events-50.raw.json.gz`](artifacts/gamma-events-50.raw.json.gz)。

## 验证

相关测试：`71 passed in 1.41s`。

覆盖 Gamma executor、operational ingest、multi-recall pipeline 和 GLM semantic triage。
命令见 [`commands.log`](commands.log)，seal 文件指纹见
[`hashes.sha256`](hashes.sha256)。

## 下一状态

这些 Candidate 若继续推进，仍需依次经过 Rule A、price-blind research packet、真实
GPT Pro return/import、accepted 后 fresh paired YES/NO book、Rule B 和 `NO_ORDER`
Prediction Ledger。本 seal 不批准每日扫描、生产 capture 扩容或真实交易。
