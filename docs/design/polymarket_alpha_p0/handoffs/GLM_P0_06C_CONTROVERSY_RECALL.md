# GLM Work Order — P0-06C Controversy Recall

```text
Task ID: P0-06C-CONTROVERSY_RECALL
Phase: P0_OFFLINE_IMPLEMENTATION_ONLY
Completion target: READY_FOR_CODEX_REVIEW
```

你只实现 controversy/dispute Recall provider。你不是仓库唯一工作者；不得
回滚或格式化他人改动，不得启动subagent。

Owned files only:

```text
src/polymarket_alpha/recall/controversy.py
tests/polymarket_alpha/test_controversy_recall_p0_06c.py
tests/polymarket_alpha/fixtures/controversy/**
docs/design/polymarket_alpha_p0/P0_06C-evidence-seal/**
```

Read-only inputs:

- released `RecallHit`, `RecallerType.CONTROVERSY` and provider registry APIs;
- existing dispute/corpus code and tests only as reference;
- frozen fixture payloads supplied in the owned fixture directory.

Required behavior:

1. Pure provider API: caller passes source identity, as-of cutoff, corpus/case
   revisions and market mapping. Provider performs no DB open and no network.
2. Every accepted hit binds absolute source path identity (path/device/inode or
   explicit fixture identity), schema/corpus revision hash, source artifact id,
   PIT effective time and exact case/source offsets.
3. Reject post-cutoff evidence, identity mismatch, incomplete/truncated source,
   blank/ambiguous market mapping and duplicate logical cases.
4. Missing source returns a typed provider skip/receipt, not a global failure.
5. Emit deterministic `RecallHit` with `CONTROVERSY` reason codes only. It may
   state that a rule/dispute is contested or clarification-dependent; it may not
   estimate probability, price or fair value.

Explicitly out of scope:

- opening current/canonical dispute DB;
- copying or migrating dispute tables;
- RuleContract changes;
- Candidate/state-machine writes;
- book/network/order/signing/private-key code.

Tests must cover: exact PIT boundary, post-cutoff rejection, source identity
mismatch, duplicate case, missing source, incomplete source, input reordering,
retry/cross-run identity and provider isolation. Run focused tests, the complete
`tests/polymarket_alpha` suite and P0-11 source audit over the owned module.

Handback: changed files, requirement-to-test map, exact commands/results, golden
hit hashes, limitations, out-of-scope confirmation and available model/effort/
token telemetry. Do not commit; Codex owns review and merge.
