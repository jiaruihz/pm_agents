# P1 Resolution Intake and Research Brief — Evidence Seal

Disposition: `COMPLETE_OFFLINE_SEAMS`

This seal closes two offline integration gaps without adding a model client,
network fetcher, scheduler, or current-database migration.

## Delivered

- `resolution_intake.py` binds caller-captured bytes, exact RuleContract source
  and precedence labels, parser assertion, condition identity, outcome,
  adjudication state and clocks into a replayable SourceArtifact and
  MarketResolution. The raw hash/length and every Pydantic input are rebuilt at
  the intake boundary. Resolution-source and precedence labels may be distinct;
  both are exact normalized matches, never substring guesses.
- `brief.py` renders deterministic provider-neutral Blind and Market briefs.
  Blind briefs expose only the released allowlist packet and prohibit venue,
  price/book, position and wallet sources. Market briefs require and hash-check
  the exact accepted Blind result from packet provenance, so the external
  researcher receives the real blind baseline rather than an unstated side
  input.
- Brief payloads are stored internally as immutable canonical JSON strings.
  Public payload properties return detached copies, so post-build dictionary
  mutation cannot alter the prompt or brief hash.

## Verification

```text
focused_learning_and_research=80 passed
full_alpha=578 passed
resolution_intake_security_audit=PASS (0 violations)
research_brief_security_audit=PASS (0 violations)
compileall=PASS
git_diff_check=PASS
```

## Review closure

The independent reviewer found one High issue: frozen dataclass fields still
contained mutable dictionaries, allowing post-build Blind prompt injection.
The implementation now retains only immutable canonical JSON strings and
returns detached decoded copies. An adversarial mutation fixture proves the
prompt and identity remain unchanged.

Coordinator review also corrected one contract assumption: `resolution_sources`
and `source_precedence` are separate RuleContract vocabularies and no longer
must share one label. Both exact declarations are persisted in the artifact's
hash-bound intake extension together with the parser assertion.

## Limitations

- The intake seam verifies capture and assertion lineage; it does not understand
  arbitrary rule prose or independently prove the caller's parser conclusion.
- No official-resolution network adapter or batch scheduler is included.
- Research briefs make manual/external-agent execution reproducible, but Alpha
  still owns no GPT/API/browser executor.
- No operational pilot or production activation is authorized.
