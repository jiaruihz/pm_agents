# GLM P0 Read-only Prep Handoff — GLM-PREP-01..04 + Self Review

Stage: `GLM_READ_ONLY_PREP_AND_SELF_REVIEW`
Generated: 2026-08-26 (UTC+8)
Repository HEAD: `e0ad0c6b41bfd949f47fa4e187a497e4c7d793be`

## 1. Executive disposition

```text
COMPLETE_WITH_LIMITATIONS
```

理由：四个 PREP 包全部完成，每个 required case 均有 evidence path 或明确 MISSING；指定 11 个测试文件 32/32 pass。限制项（不隐瞒）：

1. Prompt owned-scope 路径 `src/strategies/dispute_repricing/` 不存在（详见 F-01）。dispute 实际代码位于 `src/strategies/rule_lawyer/dispute*.py`、`src/strategies/rule_lawyer/contract_corpus.py`、`src/strategies/rule_lawyer/canonical.py` 与 `scripts/analysis/dispute_repricing/`；本轮以这些实际路径作为 PREP-02 证据。
2. 48–79 天 wallet 陈旧结论引用 `DISCOVERY_REPORT.md:114`（设计期发现记录），本轮禁止访问 runtime DB，未做独立复测；该数字按“已记录设计事实”引用。
3. PREP-04 引用一个 owned-scope 模块（`src/platform/market_data/ws_incremental_book.py`）的直接对应测试 `tests/pmm_tests/test_weather_ws_incremental_book.py`（14/14 pass）作为 adjacent evidence；该文件不在 prompt 指定测试清单内，但属于 `src/platform/market_data/` 的直接对应 tests，且只读运行、无网络/DB。
4. Telemetry：平台未提供精确 token 计数；本轮为只读准备，无 golden fixture hash 输出（矩阵中的“recommended minimal fixture”为建议，未生成文件）。

## 2. Scope confirmation

| 项 | 确认 |
|---|---|
| 是否修改代码 | NO |
| 是否修改 authoritative 设计（8 份设计文件 + 本目录 7 份分配/映射文档） | NO |
| 是否联网（Gamma/CLOB/Polymarket 或任何外部 API） | NO |
| 是否访问 runtime/production/JRS/任何 SQLite store | NO |
| 是否触及 order/signing/private key/copy-trade execution | NO（`scripts/copy_trade/` 仅列目录结构，未读执行代码） |
| 新增文件清单 | 仅本文档：`docs/design/polymarket_alpha_p0/glm_handoff/GLM_P0_PREP_HANDOFF.md` |

## 3. Repository evidence snapshot

git HEAD `e0ad0c6b41bfd949f47fa4e187a497e4c7d793be`（branch `codex/market-ladder-kink-v1`，worktree 在会话开始时已 dirty，属 owner 既有状态，本轮未触碰任何已跟踪文件）。

实际读取路径与 SHA256 前 16 位：

| Path | SHA16 |
|---|---|
| docs/design/polymarket_alpha_p0/{P0_WORK_ALLOCATION_GLM_CODEX, SUBAGENT_WORK_PACKAGES, CURRENT_TO_TARGET_MAPPING, GAP_AND_DECISION_LOG, DEPENDENCY_GRAPH, P0_IMPLEMENTATION_PLAN, RISK_REGISTER}.md | 按序通读（设计文档，未改） |
| docs/design/polymarket_alpha_p0/DISCOVERY_REPORT.md | 仅精确检索 §wallet staleness（L114 附近） |
| src/platform/clients/gamma.py | 5281afeabeb42834 |
| src/models/market.py | 5e266e779c37d29b |
| src/strategies/rule_lawyer/contract_corpus.py | d09bb1497bc04ef4 |
| src/strategies/rule_lawyer/canonical.py | 708dbb70654f86da |
| src/strategies/rule_lawyer/services/smart_wallets.py | 4861ffc060076068 |
| src/workflows/research/market_intel_workflow.py | ea5746e298335fe1 |
| src/platform/market_data/capture_contract.py | e29aa44a2bc978c5 |
| src/platform/market_data/market_group.py | c1faadc9ed231cb8 |
| src/platform/market_data/ws_incremental_book.py | 5d35d8c826b43150 |
| src/platform/clients/clob.py | cf343eb72bfe8629 |
| scripts/analysis/wallet_weather/collect_external_wallet_weather_history_v1.py | e6d2164838ea5b23（grep 精确检索 caps/truncated，未通读） |
| 11 个指定测试文件 + tests/pmm_tests/test_weather_ws_incremental_book.py（仅运行） | 见下 |

测试命令与结果（唯一两类运行，均离线 fixture）：

```text
PYTHONPATH=. .venv/bin/pytest -q \
  tests/research_tests/test_gamma_normalize_models.py \
  tests/research_tests/test_gamma_pagination.py \
  tests/research_tests/polymarket/test_market_resolver.py \
  tests/research_tests/test_dispute_contract_corpus.py \
  tests/research_tests/test_dispute_canonical.py \
  tests/research_tests/test_dispute_artifacts.py \
  tests/research_tests/test_external_wallet_persist.py \
  tests/research_tests/test_external_wallet_history_collector.py \
  tests/research_tests/test_external_wallet_full_ladder_conversion.py \
  tests/test_platform_market_data_contracts.py \
  tests/research_tests/polymarket/test_clob_orderbook_sorting.py
→ 32 passed in 1.30s

PYTHONPATH=. .venv/bin/pytest -q tests/pmm_tests/test_weather_ws_incremental_book.py
→ 14 passed in 0.13s   （adjacent，见 §1 限制 3）
```

## 4. GLM-PREP-01 — Gamma fixture matrix

状态定义：EXISTING=有专门 fixture/test 断言目标行为；PARTIAL=相关代码/合同存在但无目标断言 fixture 或仅覆盖部分子案例；MISSING=代码合同与 fixture 均不存在。

| fixture_id | status | evidence path | input shape | identity/revision 条件 | expected normalized | expected fail-closed | owner_task | recommended minimal fixture |
|---|---|---|---|---|---|---|---|---|
| G01-multi-market-event | PARTIAL | src/platform/clients/gamma.py:110-133; src/models/market.py:58-90 | 一个 event 含多个 market；一个 market 引用多个 event | market→event 方向存在（event_ids 列表）；event→markets 方向不存在（Event 无 markets 字段） | 双向 join 显式建表，title 不参与 | 缺 event_id 的 market 可收录但不进 research stage | P0-03 | event payload 带 3 markets（含 1 个缺 condition_id），断言 join 行数与 null pending |
| G02-missing-duplicate-condition-id | MISSING | src/models/market.py:11-31（无字段）; gamma.py:266-289（fetch by condition 存在但 normalize 丢弃） | market 无 conditionId；两个 market 同 conditionId | condition_id 应为 unique alternate key | 缺失→显式 null/pending；重复→quarantine 不进 book/research | 重复 condition_id fail closed（R-003） | P0-03 | 两个 market 同 condition_id fixture，断言第二个被隔离并留 receipt |
| G03-token-order-reversal | MISSING | gamma.py:107-111（positional）; test_gamma_normalize_models.py:30-31（仅断言顺序保持） | outcomes=["No","Yes"] 与 clobTokenIds 顺序反转 | yes/no 必须按规范化 outcome label 校验映射，绝不假定 index 0/1 | label 校验后映射 yes/no token | label 缺失/非二值/长度不齐 fail closed | P0-03 | 4 组 permutation（正常/反转/缺 label/3 token），断言映射与拒绝 |
| G04-title-slug-not-identity | PARTIAL | gamma.py:135（market_id 优先但 Optional）; tests/research_tests/polymarket/test_market_resolver.py:8-32（slug/URL/condition 解析）; smart_wallets.py:81-82（现状以 slug 作 key） | 同 market 改 title/slug；不同 market 同 slug | market_id 主键；slug/title 仅 alias（带 effective interval） | alias 表记录变更区间 | market_id 缺失时不得用 slug 冒充 identity | P0-03/P0-01 | 同 slug 指向两个不同 market_id 的 fixture，断言 alias 不合并 identity |
| G05-one-char-rule-change | MISSING | src/models/market.py:33-55（current-row overwrite 形态，GAP-002） | rule 文本变化 1 字符 | rule_hash 变化→新 immutable revision + change event | 新 revision；旧行保留 | 同 id 内容 hash 变化不得静默覆盖 | P0-03/P0-04 | 同一 market 两个 raw snapshot，rule 相差 1 字符，断言两个 revision + rule_hash 不同 |
| G06-whitespace-rule-change | MISSING | gamma 路径无规范化合同；可复用先例 contract_corpus.py:41-42 normalized_contract_text | rule 仅空白/换行差异（Unicode NFC 后等价） | 非语义规范化后 hash 不变→no-op revision | NFC+换行+尾空白规范化后判定 no-op | 不得因空白差异发 change event | P0-03/P0-04 | whitespace-only diff fixture，断言无新 logical change event |
| G07-lifecycle | PARTIAL | gamma.py:141-142; market.py:18-19,66（active/resolved/closed，bool 静默默认）；无 superseded/uma status | new→closed→resolved→superseded 序列 | lifecycle 变化产生 versioned change event | 保留 raw value + normalized UTC + observed clock | 缺 lifecycle 字段不得默认 active=True | P0-03/P0-04 | 4 阶段序列 fixture；另加“缺 active 字段”子例断言 fail closed 而非默认值 |
| G08-pagination-anomalies | PARTIAL | gamma.py:51-101（offset 翻页；空页/短页终止）；test_gamma_pagination.py:22-36（正常收集+短页终止） | duplicate page（重复返回同页）；empty page；cursor 终止 | 翻页终止必须可判定 | 空页/短页终止（现有）；重复页去重终止 | 服务端重复全页时不得无限循环（现 max_pages=None 会死循环，见 F-02） | P0-03 | 重复全页 fixture（mock 恒返回同页）断言终止；空首页 fixture |
| G09-field-drift | PARTIAL | gamma.py:105-107,26-27（.get 链式回退、_to_utc_iso 吞错返回 None、active 默认 True） | missing/extra/unknown 源字段 | unknown 字段进 raw artifact 保留 | schema-drift receipt 记录新增字段 | 关键字段缺失 fail closed，不得默认值掩盖 | P0-03 | 删除 endDate/updatedAt、新增未知字段的 fixture，断言 receipt 与 fail-closed 字段 |
| G10-dual-clock | PARTIAL | gamma.py:153-154; market.py:30-31（updated_at_utc 可选 + last_synced_at_utc=now） | 缺 source observed clock（updatedAt 为空/不可解析） | source_observed_at 与 ingest clock 分列 | 双 clock 保留 | 缺 source observed clock→fail closed 或标 historical，不得用 ingest clock 冒充（R-025） | P0-03 | updatedAt 缺失/非法字符串 fixture，断言 fail closed 而非 None 静默 |

## 5. GLM-PREP-02 — Dispute/controversy PIT fixture matrix

| fixture_id | status | evidence path | input revision / as-of | 预期 hit/no-hit | source lineage | 后见污染检查 | owner_task | recommended minimal fixture |
|---|---|---|---|---|---|---|---|---|
| D01-absolute-identity | PARTIAL | canonical.py:848（watermark 用 path.resolve() 绝对路径）; test_dispute_canonical.py:186-193 | dispute.db 绝对路径 + device/inode/schema | identity 不符→no-hit + 隔离 | source_path 绝对化已存在 | 无 device/inode 证据，GAP-014 未闭 | P0-06C/P0-07 | canonical manifest 不匹配 fixture（inode 不同），断言拒读并退回 fixture 模式 |
| D02-hash-separation | PARTIAL | contract_corpus.py:271-279（contract_corpus_sha256 独立）；market 级 rule_hash 尚不存在 | rule_hash 与 corpus hash 分列 | 任一 hash 变化→对应 revision | fragment content_sha256 已有 | 不得用 corpus hash 覆盖 rule hash（R-006） | P0-07 | 同 case 两个 fixture：仅 corpus 变 vs 仅 rule 文本变，断言两个 hash 独立变化 |
| D03-pit-cutoff | EXISTING | contract_corpus.py:148-150; test_dispute_contract_corpus.py:29-64 | observed_at_ts=200；bulletin update ts=150 | ts≤200 计入、ts>250 排除（future_update_count_excluded=1） | fragment.observed/effective/publisher | 已断言后到更新不进快照 | P0-06C | 沿用现有测试作为 provider 输入契约；补 as-of 早于全部 update 的 no-hit 例 |
| D04-late-adjudication | EXISTING | 同 D03（ts=250 更新被排除并计数） | adjudication 在 decision as-of 之后到达 | 事前 Recall 不得回填命中 | future 计数进 binding_map | 已防回填；Recall 层回填禁止需 P0-06A 事件序 | P0-06C/P0-06A | corpus 已含 late update + decision 早于 update，断言 RecallHit 不产生 |
| D05-duplicate-case | PARTIAL | contract_corpus.py:73（fragment_id=sha256(origin\|content) 确定性）; test_dispute_canonical.py:147-154（重跑 0 行增量） | 同 case/fragment 重复输入 | 重复输入→幂等，无重复 logical row | source_path+offset 去重依据已录 | 同 case_id 不同内容应产生新 revision 而非覆盖 | P0-06C | 同 case_id 重复投递 + 同 id 内容变化两个 fixture |
| D06-missing-source | PARTIAL | test_dispute_canonical.py:150-154（source_path/byte_offset 落表）, 175-193（空源建 prefix、prefix 变化 fail closed） | corpus/source 缺失或被改写 | 缺 source offset→可定位；prefix 变化→RuntimeError | 增量 watermark 完整 | 无 ancillary_text 全缺的 fixture | P0-06C | 完全缺 ancillary/bulletin 的 case，断言 corpus 降级标记而非崩溃或伪造 |
| D07-precedence | PARTIAL | contract_corpus.py:104-176（legal_role + adjudication_use= binding/excluded/review_required）; test_dispute_contract_corpus.py:68-132（additional-context fail closed、operational notice excluded、correction 需 precedence review） | initial/final、binding/non-binding 混合 | excluded 不参与命中；review_required 产生 blocker 命中 | fragment origin/publisher | generated_summary_nonbinding 分类无 fixture；initial/final 无显式字段 | P0-06C/P0-07 | AI-generated summary fixture 断言 nonbinding；contract-correction 优先级例已有 |
| D08-source-unavailable-route | MISSING | 无 provider 级 route-isolation fixture | dispute source 不可用 | 只跳过 P0-06C；其余 provider + Candidate 照常 | — | 需证明不阻断 | P0-06C/P0-06A | dispute source 缺失 + 其余 provider 命中的集成 fixture，断言 Candidate 仍生成 |

## 6. GLM-PREP-03 — Wallet freshness/privacy fixture matrix

Private source fields（进入 RecallHit 前必须可 redact 的全集，来自当前 artifact 实际字段）：`wallet address、name、pseudonym、side(BUY/SELL)、outcome、size、price、usdcSize、token_convictions、buy_ratio、win_rate（方向性用法）、discovery market_slugs、transactionHash、conditionId、timestamp`。允许进入 RecallHit：脱敏后的 provenance/freshness/历史标记与 provider 版本。允许进入 BlindCandidateProjection：none（零字段）。

| fixture_id | status | evidence path | private source fields | 允许进 RecallHit | 允许进 Blind | 预期失败方式 | owner_task | recommended minimal fixture |
|---|---|---|---|---|---|---|---|---|
| W01-freshness-boundary | MISSING | smart_wallets.py 全文无 observed_at/freshness；设计事实 DISCOVERY_REPORT.md:114 | trade timestamp | observed_at + TTL gate | none | 缺 observed_at→fail closed，不产 current hit | P0-06D | observed_at 恰好在 TTL 边界±ε 三例（fresh/stale/missing） |
| W02-stale-historical-only | MISSING | DISCOVERY_REPORT.md:114（48/79 天，设计期记录）；ADR-007 | 全部 | 仅 historical 标签 | none | 陈旧源生成 current hit 即测试失败 | P0-06D | 固定 as-of + 48/79 天陈旧 snapshot，断言只出 historical RecallHit |
| W03-lowercase-address | PARTIAL | smart_wallets.py:71,98,360（strip().lower() 已实现）；无 mixed-case fixture | address | 归一后 address 作 identity | none | 大小写不同不得视为两个 wallet | P0-06D | 同一地址 3 种大小写写法，断言单 identity |
| W04-address-not-entity | MISSING | smart_wallets.py:27-39（candidates 仅按 address key；name/pseudonym 非 identity） | name/pseudonym | address 级 hit + entity provenance 待建 | none | 多 address 合并为 entity 需 provenance，否则隔离 | P0-06D/P0-01 | 同 entity 2 地址 fixture，断言产生 2 个 address hit 且不自动合并（R-013） |
| W05-proxy-alias-change | MISSING | 无任何 alias/proxy 合同 | address/proxy | alias interval 记录 | none | alias 变化不得静默改 identity | P0-06D | 同 address 换 proxy 地址 fixture，断言 alias 表区间记录 |
| W06-pagination-truncation | PARTIAL | collector:364,553,639（10,000 行 cap + truncated flag）; collector:44,148,166（ACTIVITY_MAX_ROWS=5,500）; test_external_wallet_history_collector.py:27-59（cap→truncated True；短页→False）；smart_wallets.py:363-380（positions/trades max_rows cap 无 truncated flag） | 全部行级字段 | coverage receipt + incomplete flag | none | truncated 时不得生成 current RecallHit（R-014） | P0-06D | cap 触发 fixture 已有；补 positions/trades 侧 truncated flag fixture |
| W07-source-unavailable-route | MISSING | 无 route-isolation fixture | — | — | none | wallet 源不可用只跳过 P0-06D，其余 provider 照常 | P0-06D/P0-06A | wallet source 缺失 + dispute 命中的集成 fixture，断言 Candidate 仍生成 |
| W08-recallhit-only | MISSING | 反向现状证据：dispute canonical 已含 trade_intents/paper_plans/paper_fills（canonical.py:208-262; test_dispute_canonical.py:99-137） | 全部 | 仅 RecallHit | none | wallet adapter import 图出现 order/intent/execution 即失败 | P0-06D/P0-11 | wallet provider 模块 import graph 断言（无 order/signing/execution 可达） |
| W09-no-wallet-in-blind | MISSING | market_intel_workflow.py:39-79（现状 wallet scores/audits 直接写入 summary/report） | 全部（见 W 清单） | — | none | 任何 wallet-derived 字段出现在 BlindCandidateProjection 即构建失败 | P0-08/P0-01 | 从现有 smart_wallets.json 形状生成 Candidate→projection diff fixture，断言零 wallet 字段泄漏 |
| W10-wallet-hint-nested | MISSING | 无 adversarial fixture | wallet hint 文本 | — | none | hint 嵌入 question/reason/nested string 任意深度即构建失败 | P0-08 | 3 个 adversarial：hint 在 question 模板参数、reason text、嵌套 dict 第三层字符串 |

## 7. GLM-PREP-04 — Book structural anomaly fixture matrix

输出语义约束：全部 case 仅允许 `STRUCTURAL_ANOMALY` 语义（价格区间/spread/depth/单调性/family consistency 描述）；禁止 fair value、mispriced、edge、交易建议。

| fixture_id | status | evidence path | paired capture 要求 | freshness | coverage denominator | 预期 RecallHit | route isolation | owner_task | recommended minimal fixture |
|---|---|---|---|---|---|---|---|---|---|
| B01-no-book | PARTIAL | ws_incremental_book.py:673-676（缺 baseline→BookReconstructionError）; market_group.py:149-155（book_snapshot_missing blocker） | 同 capture group 配对 | — | 缺失计入分母 | no-hit，仅本 route 跳过 | provider 级隔离 fixture 缺失 | P0-06E | 无任何 book fixture + pre-book provider 命中，断言 Candidate 照常（即 A05） |
| B02-stale-book | PARTIAL | capture_contract.py:105-131（classify_orderbook_clock：legacy 缺钟→event_time_pit_scorable=False + blockers） | 双腿各自 clock | 双钟合同已有；TTL 阈值 fixture 缺失 | stale 计入分母 | no-hit 或标本 route | 现有分类 fail-closed，无 TTL 边界 fixture | P0-05/P0-06E | response clock 早于 config TTL 边界±ε 三例 |
| B03-one-sided | PARTIAL | clob.py:129-139（best_bid/ask 各自取极值；mid 仅双边存在才计，不伪造）；adjacent: test_weather_ws_incremental_book.py:266（空侧 sentinel） | 单腿或单侧 book | — | one-sided 计入分母 | no-hit + typed 状态 | — | P0-06E | YES 有 book NO 无 book fixture，断言 batch_complete=False 且不产 mid |
| B04-missing-leg | PARTIAL | market_group.py:149-155,169-171（book_snapshot_missing:{token} + batch_complete=False 合同存在）；listed tests 仅断言 complete=True（test_platform_market_data_contracts.py:114-145） | 缺一腿 snapshot ref | — | 缺腿计入分母 | no-hit + blocker 保留 | — | P0-05/P0-06E | 两腿缺一 fixture，断言 batch_complete=False 与 blocker 命名 |
| B05-insufficient-depth | PARTIAL | ws_incremental_book.py:738-746（depth_status="insufficient_depth"）；adjacent: test_weather_ws_incremental_book.py:22（five_share_executable） | 目标 size 双向可成交 | — | insufficient 计入分母 | no-hit + typed 状态 | — | P0-06E | requested_shares 超过盘口深度 fixture，断言 insufficient_depth 不产 VWAP |
| B06-crossed-ladder | MISSING | clob.py:129-132（取 best 但无 bid≥ask crossed 检测） | crossed 档位 | — | crossed 计入分母 | no-hit + 拒绝 snapshot | — | P0-05/P0-06E | bid≥ask fixture，断言 fail closed 不产 mid/spread |
| B07-spread-depth-anomaly | PARTIAL | clob.py:134-139（spread/spread_pct_mid 已计算）；无 STRUCTURAL_ANOMALY 阈值语义 | paired + threshold config | — | 命中计入分母 | 结构性 hit（区间描述） | — | P0-06E | spread 阶梯 fixture 断言 hit 仅含区间/spread 描述字段 |
| B08-threshold-monotonicity | MISSING | 无 | 同上 | — | — | 阈值单调：更宽阈值不得减少命中 | — | P0-06E | 同一 book 跑 3 档阈值，断言命中集单调 |
| B09-family-consistency | PARTIAL | market_group.py:225-285（condition_market_group_snapshot 支持 ladder/ordinal 多档 family） | 同 family 同 batch | — | family 全档入分母 | family 内结构不一致 hit | — | P0-06E | weather ladder 缺 1 档/档位重叠 fixture，断言 family anomaly hit |
| B10-dup-out-of-order | PARTIAL | ws_incremental_book.py:427-431（duplicate_frame_ignored+计数）, 432-438（out_of_order_receive_clock 阻断）, 458-464（exchange ts 回退阻断）；adjacent tests: test_weather_ws_incremental_book.py:351,398 | 同 epoch frame 链 | receive clock 链 | — | 重复帧幂等；乱序阻断该 token | — | P0-05/P0-06E | 重复 snapshot id 投递 fixture，断言幂等无重复 logical hit |
| B11-missing-book-route | MISSING | 无 provider 级隔离 fixture | — | — | — | 只跳过 P0-06E，不阻断 P0-06A~D 与 Candidate | 必需 | P0-06E/P0-06A | book 全缺 + 其余 provider 命中，断言 Candidate 仍生成（与 B01 同一集成面，建议合并为 A05 场景） |
| B12-semantics-only | MISSING | 无 forbid-scan fixture | — | — | — | 输出含 fair value/mispriced/edge/建议字样即测试失败 | — | P0-06E/P0-12 | provider 输出 reason-code allowlist 扫描 fixture（对应 A08 的 book 侧） |

## 8. Coverage summary

| prep_id | required_cases | existing_cases | partial_cases | missing_cases | tests_covering | future_owner_task |
|---|---:|---:|---:|---:|---|---|
| GLM-PREP-01 | 10 | 0 | 6 | 4 | test_gamma_normalize_models.py, test_gamma_pagination.py, test_market_resolver.py（32 例中占 8） | P0-03（identity/pagination/drift）、P0-04（revision diff）、P0-01（identity 合同） |
| GLM-PREP-02 | 8 | 2 | 5 | 1 | test_dispute_contract_corpus.py, test_dispute_canonical.py, test_dispute_artifacts.py | P0-06C（provider 化）、P0-07（hash 分列与 precedence） |
| GLM-PREP-03 | 10 | 0 | 2 | 8 | test_external_wallet_persist.py, test_external_wallet_history_collector.py, test_external_wallet_full_ladder_conversion.py | P0-06D（freshness/identity）、P0-08+P0-01（Blind 隔离与 deny 枚举） |
| GLM-PREP-04 | 12 | 0 | 8 | 4 | test_platform_market_data_contracts.py, test_clob_orderbook_sorting.py + adjacent test_weather_ws_incremental_book.py | P0-06E（结构异动 provider）、P0-05（配对/stale/crossed 合同） |
| 合计 | 40 | 2 | 21 | 17 | 11 个指定文件 32 passed；adjacent 14 passed | — |

计数口径：EXISTING=已有专门 fixture 断言目标行为；PARTIAL=代码/合同存在但无目标断言 fixture 或仅覆盖子案例；MISSING=两者皆无。PREP-04 的 B10 类在 owned-scope 模块内已有 adjacent 测试，但按指定清单口径仍计 PARTIAL。

## 9. Findings requiring Codex review

```text
Finding ID: F-01
Severity: NON_BLOCKING
Evidence path: 本文档 §1 限制 1；ls src/strategies/（无 dispute_repricing 目录）
Problem: prompt owned-scope 路径 src/strategies/dispute_repricing/ 不存在；dispute 代码实际在
  src/strategies/rule_lawyer/{dispute,dispute_verdict,dispute_strategy,dispute_forward,contract_corpus,canonical}.py
  与 scripts/analysis/dispute_repricing/。后续正式 P0-06C prompt 若沿用旧路径会引导 worker 找不到 owned scope。
Recommended owner: Codex（任务分配文档 owner）
Required contract decision: 更新 SUBAGENT_WORK_PACKAGES.md / 后续 GLM prompt 中的 dispute owned-scope 路径映射。
Acceptance evidence: 修订后的路径清单与本报告一致。
```

```text
Finding ID: F-02
Severity: NON_BLOCKING
Evidence path: src/platform/clients/gamma.py:57-69（fetch_paginated：终止仅靠空页/短页；max_pages 默认 None）
Problem: 服务端持续返回同一满页时（重复页），循环无终止条件，max_pages=None 时为无限循环；重复页内容会重复进入
  results 且无去重。这是现有代码行为事实，直接构成 P0-03 "pagination termination / duplicate pages" 必测输入。
Recommended owner: P0-03（Gamma Catalog Adapter，GLM）
Required contract decision: P0-03 需决定重复页检测语义（offset+内容指纹判重终止）并纳入 required tests；本轮不改代码。
Acceptance evidence: P0-03 交付的 duplicate-page fixture 与终止断言。
```

```text
Finding ID: F-03
Severity: BLOCKING_FOR_FUTURE_TASK（阻塞 P0-01 Blind 合同 release 与 P0-08 adversarial fixture 完整性）
Evidence path: smart_wallets.py:385-406（WalletSignal 字段全集）; market_intel_workflow.py:39-79（wallet 分数直接进研究文本）;
  本文档 §6 private source fields 清单
Problem: ADR-008 规定"全部 wallet-derived payload"禁止进入 BlindCandidateProjection，但当前仓库没有任何地方枚举
  wallet-derived 字段全集；现有 market_intel artifact（summary.json/report.md）把 wallet score/name/pseudonym/slug
  直接写入研究文本，是 P0-08 嵌套字符串泄漏的真实来源形状。
Recommended owner: Codex（P0-01 合同 owner）
Required contract decision: 在 P0-01 BlindCandidateProjection 的 deny/allowlist 测试中显式枚举 §6 清单字段
  （wallet、name、pseudonym、side、outcome、size、price、usdcSize、token_convictions、buy_ratio、
  discovery market_slugs、transactionHash、conditionId），并把嵌套字符串递归扫描的 adversarial 用例
  （W09/W10）纳入 P0-08 required tests。
Acceptance evidence: P0-01 合同测试中的 wallet-derived deny 枚举 + P0-08 的 W09/W10 adversarial fixtures。
```

```text
Finding ID: F-04
Severity: NON_BLOCKING
Evidence path: src/models/market.py:18-19,66（仅 active/resolved/closed）；CURRENT_TO_TARGET_MAPPING 4.2 lifecycle 行
Problem: P0-04 要求 superseded lifecycle change event，但 Gamma 当前模型无任何 superseded/uma_resolution_status 来源字段；
  fixture 设计时必须先决定 superseded 的 source-of-truth（Gamma 字段名或推导规则），否则 P0-04 fixture 无法落地。
Recommended owner: Codex（P0-03/P0-04 合同裁决）
Required contract decision: 明确 superseded 的 source 字段或显式推导规则（并写进 P0-03 field matrix）。
Acceptance evidence: P0-03 交付的 lifecycle 字段映射含 superseded 定义。
```

```text
Finding ID: F-05
Severity: NON_BLOCKING
Evidence path: src/platform/clients/gamma.py:211-220,249-259（_extract_market_payload 取列表首元素，不校验返回项 id/slug
  与请求匹配；HTTP 错误一律 continue）
Problem: fetch_market_by_id_or_slug/fetch_event_by_id_or_slug 在查询返回多元素列表时取第一个，未验证其 id/slug 与请求
  一致，存在张冠李戴的 identity 错配风险（R-003 邻域）；网络错误静默返回 None。
Recommended owner: P0-03
Required contract decision: P0-03 fixture 矩阵应包含"按 id 查询返回错误 market"用例，断言 id 匹配校验 fail closed。
Acceptance evidence: P0-03 的 wrong-market-returned fixture。
```

## 10. GLM self-review

1. 是否误把历史报告当当前能力？ NO。每个 EXISTING/PARTIAL 判定绑定 HEAD `e0ad0c6b` 上的代码行号或当轮通过的测试；`DISCOVERY_REPORT.md:114` 的 48/79 天陈旧数字明确标注为“设计期记录事实”，未当作本轮运行时测量，亦未据此宣称 wallet 能力现状。
2. 是否使用了后到数据作为 PIT 输入？ NO。PREP-02 矩阵中唯一涉 PIT 的 EXISTING 用例（D03/D04）正是断言后到更新被排除；无任何 case 把 as-of 之后的数据当输入。
3. 是否让 pre-book Recall 依赖 book？ NO。PREP-01/02/03 无一 case 引入 book 输入；三包各含一条 route-isolation 用例（D08/W07/B11）且如实标 MISSING。
4. 是否让 wallet 进入 Blind？ NO。§6 明确“允许进入 Blind = none”，W09/W10 把隔离失败定义为构建失败；同时以 F-03 提请 Codex 在合同层固化枚举。
5. 是否在 book anomaly 中声称 fair value/edge？ NO。§7 顶部语义约束禁止 fair value/mispriced/edge/交易建议；B07/B12 的 expected 行为只含结构区间描述与 reason-code allowlist。
6. 是否建议了第二 collector/tracker/rule lawyer/orchestrator？ NO。所有 recommended fixture 均指向现有 owner：现有 capture owner（P0-05 只提交 demand）、唯一 RuleContractCompiler（P0-07）、现有 harness（P0-10）、单一 migration owner（P0-02）；未提议任何新服务/新 owner。
7. 是否遗漏 failure/idempotency/route-isolation 案例？ 已覆盖：failure/fail-closed 行存在于 G02/G07/G09/G10、D01/D06、W01/W02/W06、B02/B04/B06；idempotency 存在于 D05、W06、B10；route-isolation 存在于 D08、W07、B11（均显式列出，MISSING 处已标注）。
8. 是否存在没有 evidence path 支撑的结论？ 每个矩阵行与 finding 均有 path:line 证据。两处非代码声明已显式标注来源与限制：48/79 天（DISCOVERY_REPORT.md:114，未复测）与 adjacent 测试（§1 限制 3，已运行确认 14 pass）。

## 11. Exact Codex handoff block

```text
----- BEGIN_CODEX_HANDOFF -----
Task: Independently review GLM P0 read-only prep outputs.
Repository: /Users/deepsleep/projects/pm_agents
Authoritative GLM report: docs/design/polymarket_alpha_p0/glm_handoff/GLM_P0_PREP_HANDOFF.md
Stage: REVIEW_ONLY
Implementation authorized: NO
Production/network/DB/order actions authorized: NO

Review requirements:
1. Verify GLM changed only the allowed handoff file.
2. Recheck evidence paths and targeted test claims.
3. Audit all four fixture matrices for missing canonical identity, PIT, freshness, Blind privacy, route-isolation and failure cases.
4. Reject any proposal that creates a parallel collector, wallet tracker, Rule Lawyer or orchestrator.
5. Map accepted cases to P0-03, P0-04, P0-06B, P0-06C, P0-06D and P0-06E.
6. Return disposition: ACCEPT / ACCEPT_WITH_FIXES / REWORK.
7. Do not implement code in the review turn.
----- END_CODEX_HANDOFF -----
```
