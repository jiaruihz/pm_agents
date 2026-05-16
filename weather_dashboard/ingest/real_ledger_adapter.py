"""
real_ledger_adapter.py

Adapts real weather-strategy CSV rows (both ledger and snapshot-replay formats)
to the canonical row dict expected by ingest_ledger_csv / ingest_settlement_rows.

Real CSVs have richer columns but some expected fields may be absent or named
differently. This layer normalises them without touching the core ingest logic.

Canonical fields consumed downstream:
    snapshot_file, snapshot_ts_utc, city, bracket, side, model, model_prob,
    market_yes_price, edge, abs_edge, event_date, shares, cost_usd, entry_price,
    mode, order_id, created_at_utc, settlement_status
    (+ final_yes for settlements)
"""

from __future__ import annotations


def adapt_row(raw: dict, default_mode: str = "snapshot_replay") -> dict:
    """
    Normalise a single raw CSV row to the canonical ingest format.

    Parameters
    ----------
    raw          : dict from csv.DictReader (all values are strings)
    default_mode : execution mode to use when 'mode' is absent in the CSV
    """
    def _get(*keys: str, fallback: str = "") -> str:
        for k in keys:
            v = raw.get(k, "").strip()
            if v:
                return v
        return fallback

    # event_date: prefer explicit field, fall back to settle_utc date portion
    event_date = _get("event_date") or _get("settle_utc")[:10] if _get("settle_utc") else ""

    # final_yes: real CSV stores '0.0'/'1.0'/''  → normalise to '0'/'1'/''
    raw_final_yes = _get("final_yes")
    if raw_final_yes in ("1.0", "1", "True", "true"):
        final_yes = "1"
    elif raw_final_yes in ("0.0", "0", "False", "false"):
        final_yes = "0"
    else:
        final_yes = ""

    return {
        "snapshot_file":     _get("snapshot_file"),
        "snapshot_ts_utc":   _get("snapshot_ts_utc"),
        "city":              _get("city"),
        "bracket":           _get("bracket"),
        "side":              _get("side"),
        "model":             _get("model"),
        "model_prob":        _get("model_prob"),
        "market_yes_price":  _get("market_yes_price"),
        "edge":              _get("edge"),
        "abs_edge":          _get("abs_edge"),
        "event_date":        event_date,
        "shares":            _get("shares"),
        "cost_usd":          _get("cost_usd"),
        "entry_price":       _get("entry_price"),
        "mode":              _get("mode", fallback=default_mode),
        "order_id":          _get("order_id"),
        "created_at_utc":    _get("created_at_utc", "snapshot_ts_utc"),
        "settlement_status": _get("settlement_status"),
        "final_yes":         final_yes,
    }


def adapt_rows(
    raw_rows: list[dict],
    default_mode: str = "snapshot_replay",
) -> list[dict]:
    """Adapt a list of raw CSV rows."""
    return [adapt_row(r, default_mode) for r in raw_rows]
