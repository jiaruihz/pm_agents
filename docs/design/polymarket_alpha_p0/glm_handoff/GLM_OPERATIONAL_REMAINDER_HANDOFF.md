# GLM handoff — Polymarket Alpha operational remainder

```text
HANDOFF_STATUS=READY_TO_EXECUTE
READINESS_SCOPE=IMPLEMENTATION_AND_OFFLINE_FIXTURES_ONLY
NETWORK=FORBIDDEN
PRODUCTION_DEPLOY=FORBIDDEN
CURRENT_RUNTIME_DB_WRITE=FORBIDDEN
ORDER_SIGNING_PRIVATE_KEY=STRICTLY_OUT_OF_SCOPE
RESULT_DOCUMENT=docs/design/polymarket_alpha_p0/glm_handoff/GLM_OPERATIONAL_REMAINDER_RESULT.md
```

## 1. 任务目标

完成 Alpha 从已经捕获的 Gamma 原始响应到现有 offline review pipeline 的剩余工程接缝。不要新建第二套 Gamma collector、book collector、scheduler、Rule Lawyer 或数据库 migration owner。

本任务不解决代理或真实联网。Codex 单独拥有 explicit proxy/security transport。GLM 必须只使用 fixture、caller-supplied bytes 和临时目录。

## 2. 开工前只读入口

按以下顺序读取，不要全仓扫描：

1. `AGENTS.md`
2. `docs/design/polymarket_alpha_p0/05_CONTRACTS.md`
3. `docs/design/polymarket_alpha_p0/DEPENDENCY_GRAPH.md`
4. `src/polymarket_alpha/adapters/gamma_pages.py`
5. `src/polymarket_alpha/census/catalog.py`
6. `src/polymarket_alpha/books/adapter.py`
7. `src/polymarket_alpha/pipeline/recall.py`
8. `src/polymarket_alpha/pipeline/review.py`
9. `src/polymarket_alpha/research/handoff.py`
10. `src/platform/market_data/capture_demand.py`

已有合同和 normalizer 必须复用；不得复制实现。

## 3. Work packages

### GLM-OP-01 — captured Gamma response ingest

Sole ownership：

- 新建 `src/polymarket_alpha/operational/gamma_ingest.py`
- 新建或更新 `src/polymarket_alpha/operational/__init__.py`
- 新建 `tests/polymarket_alpha/test_operational_gamma_ingest.py`

实现一个纯离线 adapter，输入为：

- caller-supplied raw response bytes；
- 已封存的 Gamma request/response receipt；
- `AlphaRepository`；
- run/observed/ingested clocks；
- finite page budget。

它必须：

1. 验证 raw bytes hash、长度、HTTP status、Gamma endpoint identity 与 receipt 一致；
2. 解析 `/events` 的 event payload，将 nested `markets` 展平成既有 `CapturedPage`；
3. 把 event ids 保留在既有 Gamma payload/extension 路径；
4. 调用既有 `GammaCatalogIngestor`，不得另写 identity/YES-NO/rule normalizer；
5. 对 malformed JSON、非 list、nested market 非 mapping、receipt/hash mismatch、数量超预算给出 typed fail-closed receipt；
6. 重放相同输入时保持 idempotent；任何失败都不得无收据地部分成功。

验收至少覆盖：正常 nested events、多个 event、无 markets、schema drift、hash mismatch、oversize、重复重放、condition/token identity 不交换。

### GLM-OP-02 — Alpha demand outbox writer

Sole ownership：

- 新建 `src/polymarket_alpha/operational/demand_outbox.py`
- 新建 `tests/polymarket_alpha/test_operational_demand_outbox.py`

输入只能是 `build_owner_capture_demands()` 产生的 paired YES/NO `CaptureDemand`。固定输出相对路径：

```text
<artifact_root>/polymarket_alpha/capture_demands.jsonl
```

实现要求：

1. 一次提交必须同时包含 YES/NO 两腿；任何一腿不合法则零写入；
2. 验证共同 `condition_id`、不同 token、共同 trigger/expiry、`consumer_id=polymarket_alpha`、`strategy_key=polymarket_alpha.p0_offline`；
3. `artifact_root` 与所有父目录必须拒绝 symlink；使用 dirfd、`O_NOFOLLOW`（平台支持时）、`O_APPEND`、文件锁、单次 append syscall、`fsync`；
4. canonical JSONL；同一 bundle 重试不得重复；同 id 不同 bytes 必须 typed conflict；
5. 实施 fixed budgets：每次最多一对、每分钟最多 5 个 bundle、未过期需求最多 5 个；禁止 caller 自定义路径；
6. 返回 append receipt，包含 file identity、pre/post size、line hashes、bundle hash、fsync/lock observation；
7. 不读取网络、不启动 owner、不修改 weather config。

验收至少覆盖并发 append、crash/partial-write 模拟、symlink、重复重试、conflict、单腿、过期、预算超限、路径逃逸。

### GLM-OP-03 — existing owner book artifact bridge

Sole ownership：

- 新建 `src/polymarket_alpha/operational/book_bridge.py`
- 新建 `tests/polymarket_alpha/test_operational_book_bridge.py`

实现 caller-supplied manifest/bytes 到既有 `FrozenOwnerBookArtifact` 的纯离线 bridge。它不得扫描 production 路径、不得打开 socket、不得运行 owner。

要求：

1. 输入必须显式给出两腿 artifact locator、raw bytes、capture metadata、owner receipt 与 demand bundle；
2. owner identity 必须是既有 `weather_market_books`，且 receipt 必须绑定 Alpha demand id、condition、token、request batch 与 capture id；
3. 重算 raw payload hash；验证 exact response clock、PIT scorable、YES/NO paired coverage、freshness 和 demand validity；
4. 调用既有 `normalize_paired_owner_books()`，不得另写 book parser/depth calculation；
5. 缺腿、过期、owner mismatch、receipt mismatch、hash mismatch、stale/unscorable 都返回 typed failure；
6. 输出可直接交给 `pipeline.review.build_market_review()`（以现有函数签名为准）。

验收使用 fixture artifact；不得访问 `/Volumes/jrs`、`runtime/weather.db` 或任何 current runtime。

### GLM-OP-04 — resumable operational coordinator

Sole ownership：

- 新建 `src/polymarket_alpha/operational/coordinator.py`
- 新建 `scripts/ops/polymarket_alpha_operational_coordinator.py`
- 新建 `tests/polymarket_alpha/test_operational_coordinator.py`

组装现有服务，不复制业务逻辑：

```text
captured Gamma bytes
  -> GLM-OP-01 catalog ingest
  -> existing change/recall/candidate pipeline
  -> Rule A + Blind packet filesystem handoff
  -> caller supplies Blind result
  -> FORMAL_REVIEW paired demand
  -> GLM-OP-02 outbox append
  -> caller supplies existing-owner paired book artifacts
  -> GLM-OP-03 book bridge
  -> Market packet filesystem handoff
  -> caller supplies Market result
  -> Rule B + NO_POSITION/SIMULATED ledger
```

Coordinator 必须是显式 stage machine，可在每个 filesystem handoff 后停止并从 immutable manifest/hash 恢复。不得调用 GPT/模型，不得轮询网络，不得常驻调度，不得自动等待生产 artifact，不得产生 order/signing capability。

CLI 只接受显式 stage、临时 artifact root、临时 Alpha DB、caller-supplied manifest/result 路径；必须拒绝 current DB、production artifact root、proxy/env credentials 和任意 command hook。

验收至少覆盖：完整 fixture 路径；每个 stage 的 stop/resume；重复重放；hash tamper；Blind 未 accepted 时不得写 FORMAL_REVIEW demand；book 未配对/过期时不得生成 Market packet；Rule B mismatch 阻断；最终 ledger `execution=NO_ORDER`。

## 4. 统一限制

- 不修改 shared contracts、migration、`src/polymarket_alpha/security/`、Gamma live executor、weather production controller/config。
- 不访问网络，不运行真实 Gamma/CLOB，不写 current `research.db`/`weather.db`，不部署、不重启进程。
- 不删除或覆盖现有文件；仓库有其他人的 dirty changes，必须避开并保留。
- 可新增 `operational/` glue，但 core calculation 必须调用已有 owner。
- failure receipts、state transitions、repository writes 均保持 append-only/idempotent。
- 如发现 shared contract 缺口，写入结果文档的 `DECISION_REQUESTS`，不要擅自改 P0-01 合同。

## 5. 验证与交付

至少执行：

```bash
.venv/bin/python -m pytest -q tests/polymarket_alpha
.venv/bin/python -m pytest -q tests/test_market_data_capture_demand.py tests/test_market_data_capture_contract.py
.venv/bin/python scripts/audit_no_live_paths.py
```

如果实际测试文件名不同，用 `rg --files tests | rg 'capture_(demand|contract)'` 找到准确文件并在报告记录真实命令。测试失败不得写成通过。

最多提交两个 scoped local commits；不要包含其他 dirty files。最后必须生成：

`docs/design/polymarket_alpha_p0/glm_handoff/GLM_OPERATIONAL_REMAINDER_RESULT.md`

结果文档必须包含：

- 每个 GLM-OP task 的 COMPLETE / BLOCKED；
- changed files；
- 关键合同与未决决定；
- 原样测试命令和 pass/fail 数字；
- no-network/no-production/no-order 自审；
- 已知限制；
- local commit SHA；
- 模型、effort、input/output/cached token telemetry；拿不到则明确 `TELEMETRY_UNAVAILABLE`。

不要自称 operational pilot 已通过。最终 disposition 只能是：

```text
COMPLETE_FOR_CODEX_REVIEW
COMPLETE_WITH_LIMITATIONS
BLOCKED_WITH_DECISION_REQUEST
```
