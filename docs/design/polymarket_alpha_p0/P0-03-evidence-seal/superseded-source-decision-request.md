# F-04 Decision Record — `SUPERSEDED` source-of-truth for Gamma lifecycle

Status: **CLOSED for P0** — decided by the Codex integration coordinator in
`P0_03_CODEX_INDEPENDENT_REVIEW.md` §5:

```text
SUPERSEDED_P0_POLICY=DEFERRED_UNREACHABLE
```

P0-03 and P0-04 must preserve relevant raw Gamma fields and drift evidence
but must not infer or emit `SUPERSEDED`. No inference may be made from UMA
state, `negRisk`, event membership, slug changes, or disappearance alone.
The lifecycle transition may be reopened only in the read-only operational
pilot or P1 after an authoritative source/transition rule is demonstrated.

## How this is enforced in the P0-03 delivery

- `lifecycle_status()` (`src/polymarket_alpha/adapters/gamma_normalize.py`)
  maps only ACTIVE / CLOSED / RESOLVED and has no code path that can produce
  `MarketStatus.SUPERSEDED`;
- payloads carrying candidate supersession fields (`umaResolutionStatus`,
  `negRisk*`, unknown future flags) are preserved inside raw artifacts and
  surfaced via drift receipts, never interpreted;
- locked by `test_superseded_is_never_derived`.

The original evidence-graded signal table from the request phase is retained
below for the future pilot/P1 reopening.

---

## Original request (historical, 2026-08-26)

Observable candidate source signals (evidence-graded at request time):

| Candidate signal | Evidence status in this repository |
|---|---|
| `active` / `closed` / `resolved` booleans on the market payload | Confirmed — parsed by `src/platform/clients/gamma.py` and `src/models/market.py` |
| `closed` on the *event* payload | Confirmed — parsed by `normalize_event`; event-level, not market-level |
| `umaResolutionStatus` | Not observed in any repo fixture or parser; whitelisted in `KNOWN_GAMMA_MARKET_FIELDS` so it is preserved raw without drift noise; value vocabulary unverified offline |
| `negRisk` / `negRiskMarketID` / `negRiskRequestID` group linkage | Whitelisted for raw preservation; no repo code derives lifecycle from them |
| Title/slug/URL heuristics | Forbidden by ADR-003 / F-04 — never a supersession source |

No derivation rule beyond the table exists in this repository, and none may
be invented at ingest time.
