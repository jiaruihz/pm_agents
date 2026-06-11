# Account Reconcile

> Living doc for module [5]: wallet balance, CLOB fills, cashflow, submitted notional, and DB/fact reconciliation.
> Current status: `current-reference` only when the reconcile script and CLOB coverage gate pass.
> Last updated: 2026-06-09 Phase 4C pilot.

## Current Conclusion

Account reconciliation is not the same as strategy PnL. Wallet questions must use fill-date cashflow and authenticated/live CLOB data where available, then reconcile DB fact rows against raw live order/fill evidence. `target_date` and `order_date_bj` cannot be used as wallet cashflow dates.

Phase 4C absorbed the 2026-06-06 account snapshots into the current rule:

1. **UI account loss is an account-equity question, not a settled-PnL question.** A screenshot like Polymarket `1 week -$305` cannot be accepted or rejected using only `fact_trades.pnl_usd_at_fill`.
2. **Use `fill_date_bj` for cash usage.** `target_date` is for strategy/weather attribution; `fill_date_bj` is the date money actually left the account.
3. **Separate cashflow, realized PnL, open cost, and MTM.** Open cost is risk capital, not realized loss. MTM must include valuation timestamp and can be stale or incomplete.
4. **Public activity is diagnostic/fallback.** The 2026-06-06 replay showed public activity could explain UI magnitude, but final order-level fill truth must come from raw live/CLOB order and fill recovery with reconciliation.
5. **Old `missing_bracket` and partial-fill reports are failure history.** Current account publication requires near-binary settlement normalization and CLOB coverage gate pass.

## Canonical Command

```bash
python3 scripts/analysis/account_reconcile/weather_live_account_reconcile.py --start YYYY-MM-DD --end YYYY-MM-DD --date-field fill_date_bj --group-by instance,selected_date
python3 scripts/analysis/execution_quality/weather_clob_fill_coverage_gate.py
```

## Evidence Map

| Evidence | Window | What It Says | Status |
|---|---|---|---|
| `docs/analysis/2026-06/2026-06-06-live-account-reconcile-near-binary-fix.md` | 2026-06 | near-binary fix and account reconciliation | snapshot |
| `docs/archive/analysis/2026-06/2026-06-06-polymarket-ui-account-loss-reconciliation.md` | 2026-06 | UI loss vs DB/fact/account views | snapshot |
| `docs/archive/analysis/2026-06/2026-06-06-account-equity-replay.md` | 2026-06 | public activity + raw live order + DB alignment context | snapshot |
| `docs/analysis/2026-06/2026-06-07-fill-recovery-and-performance-recalc.md` | 2026-06 | post-fix fill coverage gate and external account activity gap separation | snapshot |
| `docs/analysis/2026-06/2026-06-07-live-strategy-period-slice-current.md` | 2026-06 | fill-date cashflow vs target-date strategy attribution after fill fix | snapshot |

## Absorbed Historical Claims

| Claim | Current handling |
|---|---|
| Near-binary settlement `0.9995/0.0005` must normalize to `1/0`; old `missing_bracket=725/734/28` reports are stale | Treat old missing-bracket PnL/rank as invalidated unless rebuilt |
| UI `1 week` loss can be a real account-equity number even when settled strategy PnL is smaller | Account-equity replay or cashflow/position reconstruction required |
| Public activity showed the right account-loss magnitude but was not authoritative order-level fill truth | Use only as fallback/diagnostic; reconcile to raw CLOB/order fills |
| Raw live posted notional, submitted/posted notional, actual fill cost, open cost, and realized PnL answer different questions | Always report separately |
| DB/fill internal consistency is necessary but not sufficient if fill recovery logic is stale | Run coverage gate and report fill-id reconciliation before PnL |

## Investigation Checklist

Use this order for any future "余额少了 / account loss / wallet mismatch" question:

1. Run the five SQL preflight checks from `WEATHER_ANALYSIS_CONTRACT.md`.
2. Run `weather_clob_fill_coverage_gate.py`; stop if `gate_pass=false`.
3. Run `weather_live_account_reconcile.py` with `--date-field fill_date_bj`.
4. Report submitted notional, posted notional, actual fill cost, open cost, realized PnL, and MTM separately.
5. Report fill-id reconciliation: DB distinct fills, raw CLOB distinct fills, DB-not-in-raw, raw-not-in-DB.
6. If UI account equity is the target, say explicitly that it requires account-equity replay: cash + positions value + redeemable/claimable, adjusted for deposits/withdrawals.

## Required Gates Before Publishing

| Gate | Required Evidence |
|---|---|
| Fill ID reconciliation | Report `db_live_real_distinct_fills`, `raw_clob_distinct_fills`, `db_not_in_raw`, `raw_not_in_db` |
| Coverage | Run `weather_clob_fill_coverage_gate.py`; if `gate_pass=false`, do not publish live_real PnL/ROI |
| Date field | Use `fill_date_bj` for wallet cashflow; do not use `order_date_bj` |
| Cost split | Separate submitted, posted, actual fill cost, open cost, realized PnL, and MTM |
| Source caveat | Public Polymarket activity is fallback only, not authoritative order-level fill truth |
| UI target metric | If comparing to UI, define whether the metric is cashflow, realized PnL, current positions, or account equity delta |

## Open Work

1. Keep account-reconcile conclusions out of live-performance tables unless source and date fields are explicit.
2. Keep the checklist above aligned with `WEATHER_ANALYSIS_CONTRACT.md` and `weather-live-account-reconcile` skill.
