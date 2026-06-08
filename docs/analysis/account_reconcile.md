# Account Reconcile

> Living doc for module [5]: wallet balance, CLOB fills, cashflow, submitted notional, and DB/fact reconciliation.
> Current status: `current-reference` only when the reconcile script and CLOB coverage gate pass.
> Last updated: 2026-06-09.

## Current Conclusion

Account reconciliation is not the same as strategy PnL. Wallet questions must use fill-date cashflow and authenticated/live CLOB data where available, then reconcile DB fact rows against raw live order/fill evidence. `target_date` and `order_date_bj` cannot be used as wallet cashflow dates.

## Canonical Command

```bash
python3 scripts/analysis/weather_live_account_reconcile.py --start YYYY-MM-DD --end YYYY-MM-DD --date-field fill_date_bj --group-by instance,selected_date
python3 scripts/analysis/weather_clob_fill_coverage_gate.py
```

## Evidence Map

| Evidence | Window | What It Says | Status |
|---|---|---|---|
| `docs/analysis/2026-06/2026-06-06-live-account-reconcile-near-binary-fix.md` | 2026-06 | near-binary fix and account reconciliation | snapshot |
| `docs/analysis/2026-06/2026-06-06-polymarket-ui-account-loss-reconciliation.md` | 2026-06 | UI loss vs DB/fact/account views | snapshot |
| `docs/analysis/2026-06/2026-06-06-account-equity-replay.md` | 2026-06 | public activity + raw live order + DB alignment context | snapshot |

## Required Gates Before Publishing

| Gate | Required Evidence |
|---|---|
| Fill ID reconciliation | Report `db_live_real_distinct_fills`, `raw_clob_distinct_fills`, `db_not_in_raw`, `raw_not_in_db` |
| Coverage | Run `weather_clob_fill_coverage_gate.py`; if `gate_pass=false`, do not publish live_real PnL/ROI |
| Date field | Use `fill_date_bj` for wallet cashflow; do not use `order_date_bj` |
| Cost split | Separate submitted, posted, actual fill cost, open cost, realized PnL, and MTM |
| Source caveat | Public Polymarket activity is fallback only, not authoritative order-level fill truth |

## Open Work

1. Keep account-reconcile conclusions out of live-performance tables unless source and date fields are explicit.
2. Add a short checklist for future “余额少了” investigations.
