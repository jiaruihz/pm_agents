# P0-06D Self-Review Report

reviewer: 执行 GLM(P0-06D 实现者)按 work order「不得启动subagent」约束执行的只读自查
review scope: owned files only — `src/polymarket_alpha/recall/wallet.py`、`tests/polymarket_alpha/test_wallet_recall_p0_06d.py`、`tests/polymarket_alpha/fixtures/wallet_recall/**`
review date: 2026-08-27
review 边界: 正确性、边界条件、幂等/append-only 语义、方向隐私红线、测试覆盖缺口、明显性能与可读性问题

## 结论

复查通过,无未修复代码阻断项。实现过程中发现并已修复 3 个缺陷;本次复查追加 6 组边界探针,全部按预期 fail-closed 或正确放行;2 项非阻断观察作为设计取舍记录在案。Codex 后续发现本报告的跨-run identity 文字落后于协调器已合入的 append-only 修复：当前合同是 same-run exact retry byte-identical、cross-run attempt-scoped record ids、P0-06A 负责业务语义去重。修订后 focused 41、06C+06D focused 58、全 Alpha 371、P0-11 audit(violations=0)复跑全绿。

## 实现期已修复的缺陷(seal 前发现并修复,留档)

| # | 缺陷 | 修复 |
|---|---|---|
| F1 | `_build_hit` 对无 alias 地址直接下标访问 `alias_states[address]` 抛 KeyError,未按显式拒绝路径处理 | 改为 `alias_states.get(address, _NO_ALIAS)`,无 alias 地址正确产生无归属 hit |
| F2 | public fingerprint 预镜像中保留了 fact 的 `token_id`;binary market 的 YES/NO token 腿编码方向,属于方向侧信道 | 引入 `PUBLIC_FACT_EXCLUDED_FIELDS = PRIVATE_FACT_FIELDS | {"token_id"}`,public 视图统一剔除 |
| F3 | public features 携带 `source_snapshot_id`/`source_captured_at`(provenance 已含,属冗余公共面),且调用方 id 的偶然子串(如 "out**side**"、"**token**-ambiguous")会误触词法扫描器导致整市场误拒 | 从 features 移除这两个字段;公共 features 收窄为 地址/计数/entity 归属/freshness 配置;调用方 id 仅保留在 provenance(不在扫描范围,语义为 lineage 而非方向文本) |

## 本次复查边界探针结果(既有测试未直接覆盖)

| # | 探针 | 结果 | 判定 |
|---|---|---|---|
| P1 | `max_source_age_seconds=0`:fact 恰在 as_of 观察 | current hit 放行;1 秒前的 fact 因 receipt 窗口 [as_of, as_of] 不覆盖而 `FACT_OUTSIDE_SNAPSHOT_WINDOW` 显式拒绝,无静默降级 | ✓ |
| P2 | 空 facts + 合法 receipt | 空 outcome(hits/historical/rejections 均空),双指纹仍可计算,结构合法 | ✓ |
| P3 | 同 address+entity、confidence 0.9 与 0.85 冲突 | 视为冲突证据 → `AMBIGUOUS_ADDRESS_ALIAS` fail closed(与双 entity 多义同路径) | ✓ |
| P4 | naive datetime(无 tz)输入 | `ensure_utc` 抛 ValidationError,与仓库契约一致,不产生歧义时间 | ✓ |
| P5 | float confidence 输入 | `AlphaContract.reject_float_inputs` 拒绝,canonical 层无浮点 | ✓ |
| P6 | 含重音字符(NFC 敏感)的 alias provenance 全链路 | canonical JSON 归一化后正常出 hit,泄漏扫描零违规 | ✓ |

## 非阻断观察(设计取舍,不改代码)

1. **public fingerprint 对被拒 alias 文本的包含**:public 输入视图按"剔除方向字段后的输入"构造,被 `ALIAS_PUBLIC_TEXT_UNSAFE` 拒绝的 alias 原文仍进入 public 指纹的 SHA-256 预镜像。风险可忽略:单向哈希不暴露原文,且指纹算法包含盲测方不可得的全部 facts,不构成可探测 oracle;保持"public 视图 = 脱敏输入视图"的语义更利于重放审计。
2. **batch 级拒绝 first-fail-wins**:source mismatch > truncated > incomplete > window 缺失,一次只报首个原因。单一显式原因足够定位;如需完整诊断可由调用方修复后重放逐层暴露。

## 逐条对照 work order 复核

- 纯函数性:模块 import 仅 re/datetime/decimal/enum/typing/pydantic/内部 contracts+registry;无 I/O、无全局可变状态(`_NO_ALIAS` 为只读单例);AST audit 与 in-test audit 双重通过。✓
- 幂等/append-only:provider 无写入;same-run exact retry 输出逐字节一致;record id 包含 run envelope，跨 run 使用不同 attempt-scoped ids，避免同 id/不同 canonical bytes 的 repository 冲突；P0-06A `recall_dedupe_key` 排除 retry envelope，负责跨-run 业务语义去重。✓
- R1–R5 与测试映射:见 `MANIFEST.md`;复查确认 reason codes 闭集由构造保证(仅两个模块常量可进入),`raw_score` 为固定配置常量,extensions 恒空。✓
- 隐私红线复查:direction 五字段(side/position_direction/notional_usdc/size/token_id)在 public features、reason codes、extensions、record identity 中均不可达;发射前递归扫描是构造 allowlist 之外的第二道防线;BUY↔SELL flip 测试在指纹与 record id 两层验证不变性。✓

## 测试覆盖缺口评估(接受并留档)

- `max_source_age_seconds=0`、空 facts、同 entity 冲突 confidence 三个路径此前无直接测试——本次已由探针 P1–P3 验证行为正确;如后续改代码,建议将 P1–P3 固化为回归测试。
- aggregator 层的跨 batch 重试去重直接依赖 06A 已测语义(dedupe key),本包只断言 key 相等,不重复覆盖 06A 范围。
- 词法扫描器对"任意改写散文"的语义级拦截不在承诺范围(Limitations 已声明)。

## 性能与可读性

- `_alias_states` 每 request 恰计算一次;指纹构造为 O(n log n) 排序 + 两次哈希;无重复遍历。规模受调用方快照约束,P0 离线阶段无瓶颈。
- 模块约 630 行,单一职责分区(models → scanner → provider),注释聚焦约束而非流水账。

## 给 Codex review 的重点建议

1. 确认 `PUBLIC_FACT_EXCLUDED_FIELDS` 将 `token_id` 视为方向承载的口径是否符合 P0-08A Blind 侧预期(当前口径:binary leg token 编码 YES/NO,一律不公开)。
2. 已确认 record id 包含 run envelope、跨 run 使用不同 `recall_hit` ids，并由 06A `recall_dedupe_key` 收敛业务语义；这与 append-only 存储层预期一致。
3. 确认 stale 历史库 fixture 的"整批 fail closed(零 historical)"口径即为预期(替代口径是放行 historical;当前选择更保守,因为旧窗口 receipt 无法证明完备性)。
