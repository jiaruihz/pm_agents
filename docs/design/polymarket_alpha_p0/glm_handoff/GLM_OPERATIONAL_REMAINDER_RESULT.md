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
| GLM-OP-02 Alpha demand outbox writer | COMPLETE | `src/polymarket_alpha/operational/demand_outbox.py`;15 个测试全过 |
| GLM-OP-03 existing owner book artifact bridge | COMPLETE | `src/polymarket_alpha/operational/book_bridge.py`;7 个测试全过 |
| GLM-OP-04 resumable operational coordinator | COMPLETE | `src/polymarket_alpha/operational/coordinator.py` + `scripts/ops/polymarket_alpha_operational_coordinator.py`;16 个测试全过(含 CLI e2e) |

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
# 首次全量(2026-08-27 ~15:3x CST):527 passed(当时无并行改动)
# 中途快照(15:5x CST):527 passed, 2 failed —— 并行 workstream(P1 learning)在 15:51
#   修改 contracts/storage(git diff +803 行)导致 2 个 P0-01 golden 失败;非本任务改动。
# 最终全量(自审修复后,2026-08-27 ~17:0x CST):587 passed in 8.00s,0 failed
#   (并行 workstream 已将其 contracts/golden 收敛,包括其新增 learning 测试)。

.venv/bin/python -m pytest -q tests/polymarket_alpha/test_operational_gamma_ingest.py \
  tests/polymarket_alpha/test_operational_demand_outbox.py \
  tests/polymarket_alpha/test_operational_book_bridge.py \
  tests/polymarket_alpha/test_operational_coordinator.py
# 50 passed(12 + 15 + 7 + 16)

# 交接文档指定的 tests/test_market_data_capture_demand.py / test_market_data_capture_contract.py
# 在仓库中不存在;`rg --files tests | rg 'capture_(demand|contract)'` 亦无匹配。
# 真实等价文件为 tests/test_platform_market_data_contracts.py:
.venv/bin/python -m pytest -q tests/test_platform_market_data_contracts.py
# 4 passed in 0.06s

# 交接文档指定的 scripts/audit_no_live_paths.py 不存在;真实等价入口为
# src.polymarket_alpha.security.audit_source_tree(P0-11 Wave-0):
.venv/bin/python -c "from src.polymarket_alpha.security import audit_source_tree; \
  print(sum(len(audit_source_tree(t).violations) for t in \
  ('src/polymarket_alpha/operational','scripts/ops/polymarket_alpha_operational_coordinator.py')))"
# 0
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

## 5.5 第二轮自审(主 agent 亲自重读全部交付代码)

逐文件重读 5 个源文件 + CLI + 4 个测试后确认的发现与处置(全部已修复并复测):

1. `seal_state` 在同 locator 不同 bytes 时抛未包装的 `HandoffConflictError` → 已包为 `CoordinatorBlocked`(CLI 现以 exit 3 + typed message 退出)。
2. blind/market resume 与 book export 只捕获 `ReviewPipelineBlocked`;真实操作员错误(替换已 seal 的 result 文件后重放)会以未类型化 conflict 逃逸 → 三个阶段均已加 `HandoffConflictError` 映射,并新增专项测试。
3. 状态重建 helper 对手工篡改 manifest 的 `KeyError` 未设防 → `_load_accepted_stage`/`_manifest_from_payload` 均转 `CoordinatorBlocked`。
4. 删除 coordinator 未使用的 `FrozenOwnerBookArtifact` 导入;CLI ingest 输出补 `catalog_error_receipts`/`catalog_drift_receipts` 计数。
5. 覆盖缺口:coordinator 单市场拒绝(`MARKET_COUNT_OVER_CALLER_LIMIT` 路径)与"替换 result 文件后重放"此前无测试 → 新增 2 个测试。
6. 文档校正:§5 NIT-8 原写"仅文档化",实际提交代码已含 future-clock 拒绝与保守窗口(见上)。

处置过程说明:修复期间并行 workstream 已在我两个提交之上落了多个提交;一次误操作 `--amend` 曾把三个修复文件折进并行提交 `10ebb309`,已立即经 reflog 软恢复,并行提交逐字节还原,修复改以独立 scoped commit(fix(alpha): harden coordinator typed-failure boundaries after self-review)提交。因此本交付共 3 个 local commit(超出交接"最多两个"的原因与恢复证据如上;amend 更早的历史提交需要改写他人已基于其工作的共享历史,风险更大,故未采用)。

复测:scoped 50/50 通过;全量 `tests/polymarket_alpha` 587 passed / 0 failed;audit 0 violations。

## 6. 自审:no-network / no-production / no-order

- 全部新代码只使用 fixture、caller-supplied bytes、tmp 目录;无 socket/requests/urllib/subprocess import(`audit_source_tree` 0 violations)。
- 未访问 `/Volumes/jrs`、`runtime/weather.db`、`research.db`、任何 current runtime 或生产 artifact root;CLI 主动拒绝这些路径。
- 未创建任何 order/signing/key 能力;coordinator 显式断言并拒绝非 `NO_ORDER` 的 decision。
- 未修改 shared contracts、migration、`src/polymarket_alpha/security/`、Gamma live executor、weather production controller/config;他人 dirty changes 全部保留。

## 7. 已知限制

- OP-04 的 coordinator 每个状态机只驱动一个 market(多 market 响应在 ingest 阶段显式拒绝);scan 阶段只用 pre-book 双 provider(controversy/wallet/book 显式 SKIPPED),与 P0 设计一致。
- Rule B 的 BLOCK 分支无法通过诚实输入触发(Market packet 内嵌同一 RuleContract),测试用 seam monkeypatch 覆盖;诚实路径上的"mismatch"由 importer quarantine 阻断,同样有测试。
- outbox 每分钟预算原信任 bundle 自身时钟;自审轮确认已收紧为:future-dated `requested_at_utc` 直接拒绝,且 rate window 对晚于 caller 时钟的既有行保守计数,并有专项测试(`test_future_request_clock_is_rejected_and_cannot_evade_rate_budget`)。
- 本机(sandboxed darwin)并发 `openat(O_CREAT)` 新文件存在偶发 ENOENT(实测约 20-40%),非代码缺陷;append 在持锁路径上对该 errno 做了有界重试并保持其余 errno fail-closed。
- `pipeline.review.build_market_review()` 在交接文档中的名称与仓库不符,实际为 `accept_formal_book()`;未改任何既有合同(见 DECISION_REQUESTS)。

## 8. DECISION_REQUESTS(未擅自修改 P0-01 合同)

1. **Gamma request/response receipt 无共享合同**:OP-01 需要绑定 captured response 的 sealed receipt(hash/长度/status/endpoint/时钟),仓库中无既有共享 contract;本任务在 owned scope 内定义了局部 `GammaResponseReceipt`。若后续多个 seam 复用,建议入 P0-01。
2. **`build_market_review` 命名漂移**:交接文档引用的 `pipeline.review.build_market_review()` 不存在;现签名为 `accept_formal_book(repository, stage, yes_artifact, no_artifact, received_at, artifact_root, packet_locator, manifest_locator, packet_created_at, handoff_created_at)`。建议更新交接文档或别名。
3. **验证入口命名漂移**:`scripts/audit_no_live_paths.py` 与 `tests/test_market_data_capture_{demand,contract}.py` 不存在;真实入口分别为 `audit_source_tree` 与 `tests/test_platform_market_data_contracts.py`(已在 §4 记录真实命令)。
4. **operational 包复用 P0-08 私有文件系统原语**:`operational/` 复用了 `research/handoff.py` 的 `_write_immutable`/`_read_allowed`/`_artifact_root`/`_open_parent`/`_relative_locator`(同包内私有导入,避免复制实现)。若 P0-08 希望收口,建议将其提为公开 API(需要 P0-08 owner 决定)。

## 9. Local commits

```text
commit 1(实现+测试)= b7a811d0 feat(alpha): add GLM operational remainder (gamma ingest, demand outbox, book bridge, coordinator)
commit 2(结果文档)  = 401a636c docs(alpha): seal GLM operational remainder result document
commit 3(自审修复+文档更新)= fix(alpha): harden coordinator typed-failure boundaries after self-review(本文件随该提交入库,SHA 以 git log 为准)
```

注:commit 3 超出交接文档"最多两个 scoped local commits"的字面上限,原因是自审修复发生时并行 workstream 已在 commit 2 之上落了多个提交,继续 amend 需要改写共享历史(见 §5.5 处置说明);三个提交均只包含本任务 owned scope 的文件。

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
