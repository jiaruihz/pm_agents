# GLM Round-2 Review Report — P0-03 Gamma Catalog (post-rework, post-integration)

Review date: 2026-08-27 (UTC+8)
Reviewer round: 2 (per `AGENTS.md`/`CLAUDE.md` §7 pre-delivery subagent review
convention; independent read-only general-purpose subagent, main-agent
verification of every repo-state claim)
Reviewed iteration: GLM rework v2 bytes, cross-checked against the
concurrently landed integration

## 1. Executive disposition

```text
ROUND2_VERDICT=INTEGRATION_RECONCILIATION_REQUIRED
ACCEPT_GATE_ADAPTER_SIDE=PASS_WITH_OPEN_FINDINGS
BLOCKING=S-07, S-01, S-02, S-03
NON_BLOCKING=S-04(partial), S-05, S-06(fixed by integration)
```

核心事实：round-2 审阅运行期间，一个并发会话（owner/coordinator）落地了
两个新 commit —— `fe5598ca feat: integrate polymarket alpha gamma catalog` 与
`f6832e2e docs: seal polymarket alpha gamma integration` —— 以 repository-native
变体取代了 GLM rework v2 的 hook-based 实现并入库。被审阅的 v2 字节已不在
工作树。以下 findings 均已由主 agent 在**当前落地代码**上重新验证适用性。

## 2. Current verified repo state (main-agent evidence)

| 检查 | 结果 |
|---|---|
| git HEAD | `f6832e2e`（父链含 `fe5598ca` 集成 commit） |
| `GammaCatalogIngestor.__init__` | `(self, repository, *, source_version)` — 无 hook，condition 查重走 `repository.market_id_for_condition()`（`census/catalog.py:448`） |
| P0-02 delta 落地情况 | D-1..D-5 全部原生实现：`market_id_for_condition`（repository.py:41）、`save_market_alias`（:58）、`link_run_artifact`（:120）、`_save_raw_artifact_projection`（:223/:349/:352）+ 新 migration |
| `tests/polymarket_alpha/` 全套 | 166 passed（集成新增 storage/rule 测试） |
| legacy gamma regression（3 文件） | 8 passed |
| P0-03 模块测试数 | 37 个测试函数（v2 的 50 个 adversarial 用例被集成为 37 个变体） |
| seal generator | `generate_seal_artifacts.py:101` 仍传 `market_identity_reader=`，当前构造器拒绝该参数 |
| seal manifest | 仍为 `seal_revision: rework_v2`，描述的是已被取代的 hook-based 迭代 |

## 3. Findings（适用性按当前落地代码验证）

```text
Finding ID: S-07
Severity: BLOCKING（repo state）
Evidence: git log f6832e2e/fe5598ca；generate_seal_artifacts.py:101 vs catalog.py:220-225
Problem: 验收门"evidence seal verifies from its declared integration identity"不成立——
  seal 描述/生成的对象是 v2 hook-based 迭代，当前树是 native 集成；generator 在当前
  树上运行即 TypeError 崩溃；v2 与 native 两个迭代不得混用。
Recommended owner: coordinator
Required decision: 固定唯一迭代（native 变体），对照该迭代重写 seal generator 与
  manifest（bf_status、测试数 166/37、integration commit）并重算 hashes。
Acceptance evidence: 在 f6832e2e 检出上 generator 可运行、shasum -c 全 OK、
  manifest 描述与树一致。
```

```text
Finding ID: S-01
Severity: BLOCKING（BF-P003-02 验收项残留空洞）
Evidence: src/polymarket_alpha/adapters/gamma_raw.py:61-78（落地版保留同一实现）
Problem: events 数组序规范化仅在"全部元素为 Mapping"时生效，且 sort key 只取 id 文本；
  string-encoded events（本 adapter 其他路径显式支持的形状）、混合数组、重复 id、
  无 id 事件的"重排"仍会改变 payload_sha256/page_sha256，而 canonical identity 不变
  —— 同一逻辑捕获产生第二个 snapshot revision，违反"事件重排不改变任何 hash"。
Recommended owner: P0-03 adapter owner（下轮 bounded fix）
Required fix: 解析 string-encoded events（json.loads→排序→回写）；sort key 改为全序
  （Mapping: (0, id_text, canonical_json(e))，非 Mapping: (1, canonical_json(e))）；
  补 string-events 与混合数组重排测试。
Acceptance evidence: 上述五种形状重排后 hash 不变且 identity 不变的测试。
```

```text
Finding ID: S-02
Severity: BLOCKING（合法幂等重试产生伪冲突 receipt）
Evidence: gamma_raw.py GammaPageArtifact.record_identity（缺 page_index/source_version）
  vs canonical bytes（含两者）
Problem: 同一 run_id + 同钟、页序不同或含重复页的重放，会产生同 record_id 不同字节
  → PAGE_ARTIFACT_CONFLICT 伪 receipt；该 receipt code 目前唯一的现实触发路径就是
  这一不对称。
Recommended owner: P0-03 adapter owner
Required fix: page_index（与 source_version）并入 record_identity，或对字节等价内容
  页不派生冲突 receipt；文档化重复页语义。
Acceptance evidence: 页序重排/重复页重放零 receipt、零新行的测试。
```

```text
Finding ID: S-03
Severity: BLOCKING（批次中途死亡、部分写入无 receipt）
Evidence: census/catalog.py:252-253（落地版保留：observed > ingested 的 ValueError
  在逐页循环内、首 save 之后可触发）
Problem: 页钟校验发生在已提交前序页之后，违反模块合同"no batch dies midway with
  partial writes and no receipt"；ingest_verified_market 对 naive
  source_observed_at 也会崩溃，与其"never crashes the caller"docstring 相悖。
Recommended owner: P0-03 adapter owner
Required fix: 首次写入前单趟预校验全部页钟；ValueError 仅保留给 pre-write caller misuse。
Acceptance evidence: 第二页钟非法时第一页零写入（或显式 receipt）的测试。
```

```text
Finding ID: S-04
Severity: NON_BLOCKING（边界声明为绝对，实际未完全覆盖）
Evidence: census/catalog.py:263（save_contract 已包 try，但 build_page_artifact
  本身在 try 之外；except 未含 content_sha256 对非 canonical 值抛的 TypeError）
Problem: build 期 raise（naive 钟 / 非字符串 key 的 TypeError）仍会杀死批次；
  JSON 解码输入下现实性低，但边界合同声称绝对。
Recommended owner: P0-03 adapter owner
Required fix: build_page_artifact 纳入 try → PAGE_ARTIFACT_INVALID receipt；
  raw-artifact except 元组加 TypeError。
```

```text
Finding ID: S-05
Severity: NON_BLOCKING（TEST_GAP）
Evidence: tests/.../test_gamma_catalog_p0_03.py:619,630（collect_gamma_pages 仅
  直接测试，未与 CapturedPage→ingest_pages 端到端接线）
Problem: requested_limit/termination 在 ingest 边界被静默丢弃（GammaPageArtifact
  仅存 offset，CatalogIngestResult 不携带 PaginationReceipt）；GammaPageArtifact
  的模型级 hash 重算无测试。
Recommended owner: P0-03 adapter owner
Required fix: artifact/result 携带分页窗口后补 collect→capture→ingest 测试；
  补 page sha 失配测试。
```

```text
Finding ID: S-06
Severity: FIXED_BY_INTEGRATION（记录在案）
Evidence: census/catalog.py 落地版调用 link_run_artifact 两处（page capture 与
  market payload）
Problem: v2 docstring 承诺的 run-artifact 关联无人创建——集成版已修复。
```

## 4. Verdict against the acceptance gate (review §8)

```text
all seven blocking findings closed        PARTIAL — BF-01/02/05 的 P0-02 侧已原生落地；
                                         adapter 侧 S-01 残留（BF-02 空洞）、S-02/S-03 为
                                         rework 引入/遗留的新违反
P0-03 focused tests pass                 PASS（当前树 37 tests 全绿）
entire polymarket_alpha suite passes     PASS（166 passed）
legacy Gamma regression passes           PASS（8 passed）
P0-11 no-live subset passes              PASS（含于全套）
evidence seal verifies from declared
  integration identity                   FAIL（S-07：seal 描述 v2，树为 native；
                                         generator 当前树崩溃）
```

## 5. Recommended next bounded fix round

归属 P0-03 adapter owner（GLM 可在新授权下执行，限定 adapters/census/tests/seal）：
S-01、S-02、S-03（blocking）+ S-04、S-05（顺手）+ S-07 的 seal 重建（或由
coordinator 统一 reseal）。本轮 GLM 未改动任何落地代码——两个迭代不得在无
coordinator 裁决下混写（S-07 教训）。

## 6. Review telemetry

- Round-2 reviewer: general-purpose subagent（只读；33 tool uses；~483s）
- Main-agent 独立复核：git 状态、构造器签名、repository API 行号、三类测试重跑、
  canonicalize/record_identity/页钟校验逐处 grep 验证
- 本报告文件：docs/design/polymarket_alpha_p0/glm_handoff/GLM_P0_03_ROUND2_REVIEW_REPORT.md
  （置于 glm_handoff/ 而非 sealed 目录——避免在 coordinator reseal 前混入迭代）
