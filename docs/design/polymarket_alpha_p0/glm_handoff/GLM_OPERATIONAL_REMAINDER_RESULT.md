# GLM operational remainder — 结果文档

```text
HANDOFF=docs/design/polymarket_alpha_p0/glm_handoff/GLM_OPERATIONAL_REMAINDER_HANDOFF.md
DISPOSITION=COMPLETE_FOR_CODEX_REVIEW
GLM-OP-01=COMPLETE
GLM-OP-02=COMPLETE
GLM-OP-03=COMPLETE
GLM-OP-04=COMPLETE
NETWORK_USE=NONE
PRODUCTION_ACCESS=NONE
ORDER_SIGNING_CAPABILITY=NONE
```

## 1. 每个 GLM-OP task 的状态

| Task | 状态 | 说明 |
|---|---|---|
| GLM-OP-01 captured Gamma response ingest | COMPLETE | `src/polymarket_alpha/operational/gamma_ingest.py`;12 个测试全过 |
| GLM-OP-02 Alpha demand outbox writer | COMPLETE | `src/polymarket_alpha/operational/demand_outbox.py`;14 个测试全过 |
| GLM-OP-03 existing owner book artifact bridge | COMPLETE | `src/polymarket_alpha/operational/book_bridge.py`;7 个测试全过 |
| GLM-OP-04 resumable operational coordinator | COMPLETE | `src/polymarket_alpha/operational/coordinator.py` + `scripts/ops/polymarket_alpha_operational_coordinator.py`;12 个测试全过(含 CLI e2e) |

## 2. Changed files(全部为新增文件;未修改任何既有文件)

```text
src/polymarket_alpha/operational/__init__.py
src/polymarket_alpha/operational/gamma_ingest.py
src/polymarket_alpha/operational/demand_outbox.py
src/polymarket_alpha/operational/book_bridge.py
src/polymarket_alpha/operational/coordinator.py
scripts/ops/polymarket_alpha_operational_coordinator.py
tests/polymarket_alpha/test_operational_gamma_ingest.py
tests/polymarket_alpha/test_operational_demand_outbox.py
tests/polymarket_alpha/test_operational_book_bridge.py
tests/polymarket_alpha/test_operational_coordinator.py
docs/design/polymarket_alpha_p0/glm_handoff/GLM_OPERATIONAL_REMAINDER_RESULT.md
```

## 3. 关键合同与实现要点

- **OP-01**:输入为 caller-supplied raw bytes + sealed `GammaResponseReceipt`(方法/host/path/status/长度/hash/响应时钟)。校验顺序:status==200 → endpoint==`GET gamma-api.polymarket.com /events` → bytes hash/长度 → 时钟序(observed ≥ receipt ≥ …,ingested ≥ observed)→ JSON list → event/market 形状 → 展平数量 ≤ page budget;任何一步失败都发生在首个 repository 写之前(typed `GammaIngestFailure`,零写入)。展平把 parent event(去掉 `markets` 键)合并进 market payload 既有 `events` 路径(去重 by id),复用 `extract_event_ids`/`normalize_json_list`;之后调用既有 `GammaCatalogIngestor`,不重写 identity/YES-NO/rule normalizer。raw bytes 只由 receipt hash 绑定;catalog artifact 按其发布合同保存 canonicalized merged payload(非逐字 HTTP bytes)。
- **OP-02**:输入只接受 `build_owner_capture_demands()` 的 bundle;固定 locator `polymarket_alpha/capture_demands.jsonl`,签名无路径参数。配对校验(共同 condition、token 互异且映射 Alpha identity、共同 trigger/expiry、consumer/strategy key)失败零写入。文件系统:dirfd + `O_NOFOLLOW` 逐级解析、`O_APPEND`、根级 lock file + 目标文件 `flock` 双重串行、单次 `os.write`、`fsync`(文件+父目录)。同 bundle 重放幂等(replay receipt);同 id 不同 bytes → typed conflict;预算:每次恰好一对、每分钟 ≤5 bundle(按 bundle 自身 `requested_at_utc` 窗口)、未过期 bundle ≤5。torn line / 坏 JSON → typed corrupt 拒绝后续 append。
- **OP-03**:输入为显式两腿(locator、raw bytes、capture metadata、artifact id)+ owner receipt + demand bundle。校验 owner 必须是 `weather_market_books` 且 receipt 绑定 Alpha demand id/condition/两 token/request batch/两 capture id;重算 `canonical_json_hash(raw_book)`;预检 exact response clock 与 PIT scorable;再构造既有 `FrozenOwnerBookArtifact`(复用其全部校验)并调用既有 `normalize_paired_owner_books`(包 ValueError → typed failure)。只有 ACCEPTED(非 stale)配对输出;可直接交给 `pipeline.review.accept_formal_book()`(交接文档写的 `build_market_review()` 在现仓库中的既有签名为 `accept_formal_book`,已按"以现有函数签名为准"处理)。
- **OP-04**:显式六状态机(GAMMA_INGESTED → CANDIDATE_SCANNED → BLIND_PACKET_FROZEN → BLIND_RESULT_ACCEPTED → MARKET_PACKET_FROZEN → FINALIZED),每个 handoff 后停止;状态以 canonical JSON seal 在 `state/<stage>.json`(经 P0-08 `_write_immutable`,同 bytes 幂等、异 bytes 冲突),恢复时逐 record_id 从 repo 重建并校验 canonical hash。组装的是既有 owner:OP-01 ingest、`detect_market_change` + `MultiRecallScanner`(真实 pre-book provider)、`persist_scan_candidates`、`start_blind_review`/`resume_blind_result`(Blind 未 accepted 不产生 demand 也不写 outbox)、`build_owner_capture_demands` → OP-02、OP-03 → `accept_formal_book`、`resume_market_result`(强制 NO_ORDER)。book 阶段额外要求 outbox 中存在已宣告的 bundle 行,否则 fail closed。CLI 只接受显式 stage、`/tmp/polymarket-alpha-pilot/` 下的 artifact root 与 Alpha DB、caller manifest/result 路径;拒绝 proxy 环境变量、`/Volumes/jrs`、`runtime/weather.db`、`research.db` 与任何 manifest 引用的生产路径;无任何 command hook。

## 4. 测试命令与真实结果

```bash
.venv/bin/python -m pytest -q tests/polymarket_alpha
# 首次全量(2026-08-27 ~15:3x CST,当时仓库无并行改动):527 passed in 6.56s
# 最终全量(2026-08-27 15:5x CST):527 passed, 2 failed
#   失败项 test_contracts_p0_01.py::test_contract_golden_fingerprint_and_compatibility_matrix
#        与 test_contracts_p0_01r2.py::test_r2_schema_bundle_and_golden_release_are_stable
#   原因:并行 workstream(P1 learning)在 15:51 修改了 src/polymarket_alpha/contracts/*
#   与 src/polymarket_alpha/storage/*(git diff +803 行,mtime 15:51:10),改变了
#   contract_schema_fingerprint;这些文件不属于本任务且本任务从未写过它们。
#   佐证:同批出现的还有他人未跟踪的 tests/polymarket_alpha/test_learning_*.py(部分自身失败)。

.venv/bin/python -m pytest -q tests/polymarket_alpha/test_operational_gamma_ingest.py \
  tests/polymarket_alpha/test_operational_demand_outbox.py \
  tests/polymarket_alpha/test_operational_book_bridge.py \
  tests/polymarket_alpha/test_operational_coordinator.py
# 45 passed in 1.10s(12 + 14 + 7 + 12)

# 交接文档指定的 tests/test_market_data_capture_demand.py / test_market_data_capture_contract.py
# 在仓库中不存在;`rg --files tests | rg 'capture_(demand|contract)'` 亦无匹配。
# 真实等价文件为 tests/test_platform_market_data_contracts.py:
.venv/bin/python -m pytest -q tests/test_platform_market_data_contracts.py
# 4 passed in 0.06s

# 交接文档指定的 scripts/audit_no_live_paths.py 不存在;真实等价入口为
# src.polymarket_alpha.security.audit_source_tree(P0-11 Wave-0):
.venv/bin/python -c "from src.polymarket_alpha.security import audit_source_tree; \
  print([audit_source_tree(t).violations for t in \
  ('src/polymarket_alpha/operational','scripts/ops/polymarket_alpha_operational_coordinator.py')])"
# [[], []]  → 0 violations
```

## 5. 只读 review(单轮,按 AGENTS.md §7)

Reviewer(独立 subagent,只读,未改代码;自跑 527 passed)结论:无 BLOCKER;1 MAJOR + 4 MINOR + 5 NIT。全部已处置:

1. MAJOR:book bridge 对 `normalize_paired_owner_books` 未包 ValueError → 已包为 typed `NORMALIZATION_FAILED`。
2. MINOR:SHORT_PAGE 以 market 数对比 events limit → 已改为按 event 数判定。
3. MINOR:CLI manifest 引用的读路径绕过生产路径拒绝 → 已加 `_require_readable_input_path`(response/raw_book/source/manifest 全部过检)。
4. MINOR:outbox 既有行时间戳解析异常未类型化 → 已包为 `DemandOutboxCorruptError`。
5. MINOR(覆盖):gate-B BLOCK 分支与 CLI book/market-resume 未测 → 已补 seam 测试(monkeypatch evaluate_gate_b)与 CLI 全链路 e2e。
6-10. NIT:state manifest KeyError → CoordinatorBlocked;book 阶段交叉校验 outbox 行;per-minute 窗口可被未来时钟绕过(已在 docstring 记录,受未过期预算约束);父目录 fsync(已加,receipt 含 `parent_fsynced`);展平后 artifact 为 canonicalized merged payload 而非逐字 bytes(已在 docstring 记录)。

修复后复跑:OP 测试 45/45 通过;全量 suite 除上述并行改动导致的 2 个 golden 失败外全部通过。

## 6. 自审:no-network / no-production / no-order

- 全部新代码只使用 fixture、caller-supplied bytes、tmp 目录;无 socket/requests/urllib/subprocess import(`audit_source_tree` 0 violations)。
- 未访问 `/Volumes/jrs`、`runtime/weather.db`、`research.db`、任何 current runtime 或生产 artifact root;CLI 主动拒绝这些路径。
- 未创建任何 order/signing/key 能力;coordinator 显式断言并拒绝非 `NO_ORDER` 的 decision。
- 未修改 shared contracts、migration、`src/polymarket_alpha/security/`、Gamma live executor、weather production controller/config;他人 dirty changes 全部保留。

## 7. 已知限制

- OP-04 的 coordinator 每个状态机只驱动一个 market(多 market 响应在 ingest 阶段显式拒绝);scan 阶段只用 pre-book 双 provider(controversy/wallet/book 显式 SKIPPED),与 P0 设计一致。
- Rule B 的 BLOCK 分支无法通过诚实输入触发(Market packet 内嵌同一 RuleContract),测试用 seam monkeypatch 覆盖;诚实路径上的"mismatch"由 importer quarantine 阻断,同样有测试。
- outbox 每分钟预算信任 bundle 自身 `requested_at_utc`;未来时钟可绕过该窗口但受未过期预算约束(已文档化)。
- 本机(sandboxed darwin)并发 `openat(O_CREAT)` 新文件存在偶发 ENOENT(实测约 20-40%),非代码缺陷;append 在持锁路径上对该 errno 做了有界重试并保持其余 errno fail-closed。
- `pipeline.review.build_market_review()` 在交接文档中的名称与仓库不符,实际为 `accept_formal_book()`;未改任何既有合同(见 DECISION_REQUESTS)。

## 8. DECISION_REQUESTS(未擅自修改 P0-01 合同)

1. **Gamma request/response receipt 无共享合同**:OP-01 需要绑定 captured response 的 sealed receipt(hash/长度/status/endpoint/时钟),仓库中无既有共享 contract;本任务在 owned scope 内定义了局部 `GammaResponseReceipt`。若后续多个 seam 复用,建议入 P0-01。
2. **`build_market_review` 命名漂移**:交接文档引用的 `pipeline.review.build_market_review()` 不存在;现签名为 `accept_formal_book(repository, stage, yes_artifact, no_artifact, received_at, artifact_root, packet_locator, manifest_locator, packet_created_at, handoff_created_at)`。建议更新交接文档或别名。
3. **验证入口命名漂移**:`scripts/audit_no_live_paths.py` 与 `tests/test_market_data_capture_{demand,contract}.py` 不存在;真实入口分别为 `audit_source_tree` 与 `tests/test_platform_market_data_contracts.py`(已在 §4 记录真实命令)。
4. **operational 包复用 P0-08 私有文件系统原语**:`operational/` 复用了 `research/handoff.py` 的 `_write_immutable`/`_read_allowed`/`_artifact_root`/`_open_parent`/`_relative_locator`(同包内私有导入,避免复制实现)。若 P0-08 希望收口,建议将其提为公开 API(需要 P0-08 owner 决定)。

## 9. Local commits

```text
commit 1 (实现+测试) = b7a811d0 feat(alpha): add GLM-OP operational remainder (gamma ingest, demand outbox, book bridge, coordinator)
commit 2 (本结果文档) = 本文件所在提交(提交后即 HEAD)
```

## 10. Telemetry

```text
MODEL=builtin:bigmodel-coding-plan/GLM-5.3
EFFORT=handoff-driven autonomous execution,单 coordinator(主 agent)+ 1 个只读 review subagent
INPUT/OUTPUT/CACHED_TOKENS=TELEMETRY_UNAVAILABLE
  (主会话无逐 token 计量接口;review subagent usage: subagent_tokens=719732,
   tool_uses=24, duration_ms=341422)
```

## 11. Disposition

```text
COMPLETE_FOR_CODEX_REVIEW
```

不自称 operational pilot 已通过;本交付仅覆盖离线实现与 fixture 验收,任何 read-only operational pilot 仍需按 `READ_ONLY_OPERATIONAL_PILOT_GATE.md` 单独授权。
