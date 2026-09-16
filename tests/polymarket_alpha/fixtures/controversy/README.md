# Controversy recall frozen fixtures (P0-06C)

Frozen dispute-corpus payloads consumed by
`tests/polymarket_alpha/test_controversy_recall_p0_06c.py`. Files are inputs
only; tests never mutate them in place.

| File | Role |
|---|---|
| `corpus_v1.json` | Main frozen corpus. Four cases; `case-004` deliberately references unmapped market ref `unlisted-index`. |
| `corpus_v1_truncated.json` | Same declared identity/artifact hash as `corpus_v1.json` but `artifact_text` truncated before `[case-003]`; triggers `SOURCE_HASH_MISMATCH`. |
| `corpus_offset_overflow.json` | Self-consistent corpus (hash matches its bytes) that adds `case-005` with `quote_end` beyond artifact length; triggers per-case `SOURCE_INCOMPLETE`. |

Shared frozen values:

- schema descriptor: `polymarket_alpha.controversy.corpus.v1`
- `schema_sha256`: `4765c01c5d8db4af5d466877041df39f866c32b4bb2f28f2d41c1a6d5722fdfb`
- `artifact_sha256` (full corpus bytes): `47ea498ce9fdf3d8a93f64322700c690c5af3cd4d2a1afec3ed69dc583c1c77a`

Corpus revision used by tests (frozen caller input):
`corpus_revision_sha256 = sha256("polymarket_alpha.controversy.corpus_revision.v1")`.
