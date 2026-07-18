#!/usr/bin/env python3
"""Research post-update T/T+1 ladder repricing from the zero-notional collector.

T-1 NO is retained only as an information-absorption clock.  Target expressions
are current YES (stop), current NO (continuation payout), and d1 YES (one-step
stop).  The script is descriptive/research-only and never submits orders.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_COLLECTOR = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/output/source_event_ladder_repricing_shadow"
)
HORIZONS = (0, 30, 60, 120, 300)
TOLERANCE = {0: 120, 30: 20, 60: 20, 120: 30, 300: 60}
EXPRESSIONS = {
    "t_minus_1_no": ("t_minus_1", "no", "clock_only"),
    "current_yes": ("source_round", "yes", "current_stop"),
    "current_no": ("source_round", "no", "current_no_payout"),
    "d1_yes": ("source_plus_1", "yes", "d1_stop"),
    "d1_no": ("source_plus_1", "no", "d1_no_payout"),
}


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def as_float(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    size = path.stat().st_size
    with path.open("rb") as handle:
        while handle.tell() < size:
            raw = handle.readline()
            if not raw:
                break
            try:
                row = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(row, dict):
                yield row


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def median(values: Iterable[float | None]) -> float | None:
    clean = [float(x) for x in values if x is not None and math.isfinite(float(x))]
    return statistics.median(clean) if clean else None


def mean(values: Iterable[float | None]) -> float | None:
    clean = [float(x) for x in values if x is not None and math.isfinite(float(x))]
    return sum(clean) / len(clean) if clean else None


def weather_fee(price: float) -> float:
    return 0.05 * price * (1.0 - price)


def quote_part(row: dict[str, Any], rel: str, side: str) -> dict[str, Any]:
    return ((((row.get("quotes") or {}).get(rel) or {}).get(side)) or {})


def quote_metrics(part: dict[str, Any]) -> dict[str, float | None]:
    bid = as_float(part.get("fresh_best_bid"))
    ask = as_float(part.get("fresh_best_ask"))
    bid_size = as_float(part.get("fresh_bid_size"))
    ask_size = as_float(part.get("fresh_ask_size"))
    mid = (bid + ask) / 2.0 if bid is not None and ask is not None else None
    return {
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "spread": ask - bid if bid is not None and ask is not None else None,
        "bid_size": bid_size,
        "ask_size": ask_size,
    }


def supported_quote(
    rows: list[dict[str, Any]], decision: datetime, horizon: int
) -> tuple[dict[str, Any] | None, float | None]:
    target = decision + timedelta(seconds=horizon)
    candidates: list[tuple[float, datetime, dict[str, Any]]] = []
    for row in rows:
        ts = parse_dt(row.get("ts_utc"))
        if ts is None or ts < decision:
            continue
        candidates.append((abs((ts - target).total_seconds()), ts, row))
    if not candidates:
        return None, None
    gap, ts, row = min(candidates, key=lambda item: (item[0], item[1]))
    if gap > TOLERANCE[horizon]:
        return None, None
    return row, (ts - decision).total_seconds()


def bracket_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace("+", "")
    try:
        return float(text)
    except ValueError:
        return None


def load_settlements(db_path: Path) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any]]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    winners: dict[tuple[str, str], dict[str, Any]] = {}
    for city, target_date, bracket, question, final_price in conn.execute(
        "SELECT city,target_date,bracket,question,final_price FROM settlement_outcomes "
        "WHERE settlement_status='settled' AND final_price>=0.999"
    ):
        q = str(question or "").lower()
        winners[(str(city), str(target_date))] = {
            "bracket": str(bracket),
            "value": bracket_number(bracket),
            "is_lower_tail": "or below" in q,
            "is_upper_tail": "or higher" in q,
            "question": question,
        }
    meta_row = conn.execute(
        "SELECT MIN(target_date),MAX(target_date),COUNT(DISTINCT target_date) FROM settlement_outcomes"
    ).fetchone()
    fact_row = conn.execute("SELECT MAX(fact_built_at_utc),COUNT(*) FROM fact_trades").fetchone()
    conn.close()
    return winners, {
        "settlement_start": meta_row[0],
        "settlement_end": meta_row[1],
        "settlement_dates": meta_row[2],
        "fact_built_at_utc": fact_row[0],
        "fact_trades": fact_row[1],
        "db_mtime_utc": datetime.fromtimestamp(db_path.stat().st_mtime, timezone.utc).isoformat(),
    }


def event_identity(row: dict[str, Any]) -> tuple[str, str, str, str]:
    current = row.get("current_market_bracket") or row.get("current_bracket_c") or row.get("source_round_c")
    return (str(row.get("city")), str(row.get("target_date")), str(row.get("source")), str(current))


def load_snapshot_features(event: dict[str, Any], current: str) -> dict[str, Any]:
    path_value = event.get("paper_snapshot_path")
    if not path_value:
        return {"feature_status": "missing_snapshot_path"}
    path = Path(str(path_value))
    if not path.exists():
        return {"feature_status": "missing_snapshot_file"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"feature_status": "invalid_snapshot_file"}
    snapshot_ts = parse_dt(payload.get("ts_utc"))
    decision = parse_dt(event.get("source_detect_ts_utc"))
    if snapshot_ts is None or decision is None or snapshot_ts > decision:
        return {"feature_status": "non_pit_snapshot"}
    matches = [
        row for row in payload.get("records", [])
        if str(row.get("city")) == str(event.get("city"))
        and str(row.get("target_date")) == str(event.get("target_date"))
        and str(row.get("bracket")) == str(current)
    ]
    if not matches:
        return {"feature_status": "current_record_absent"}
    row = matches[0]
    peak_delta = as_float(row.get("forecast_peak_delta_hours_local"))
    ceiling_f = as_float(row.get("forecast_max_above_metar_max_f"))
    forecast_native = as_float(row.get("forecast_max_native"))
    current_value = bracket_number(current)
    # Selected first-round cities are exact 1C markets.  Remaining ceiling must
    # be measured from the newly reached source bracket T, not from the stale
    # pre-update METAR maximum; the latter would mechanically encode source lead.
    ceiling_native = (
        forecast_native - current_value
        if forecast_native is not None and current_value is not None else None
    )
    if peak_delta is None:
        peak_state = "unknown"
    elif peak_delta > 1.0:
        peak_state = "fresh_runway"
    elif peak_delta >= -1.0:
        peak_state = "near_peak"
    else:
        peak_state = "post_peak"
    if ceiling_native is None:
        ceiling_state = "unknown"
    elif ceiling_native <= 0.25:
        ceiling_state = "ceiling_exhausted"
    elif ceiling_native <= 1.25:
        ceiling_state = "ceiling_one_c_or_less"
    else:
        ceiling_state = "ceiling_above_one_c"
    return {
        "feature_status": "ok",
        "feature_snapshot_ts_utc": payload.get("ts_utc"),
        "forecast_peak_delta_hours_local": peak_delta,
        "forecast_max_above_metar_max_f": ceiling_f,
        "forecast_max_native": forecast_native,
        "forecast_ceiling_margin_vs_source_T_native": ceiling_native,
        "forecast_max_f": as_float(row.get("forecast_max_f")),
        "hours_to_settle": as_float(row.get("hours_to_settle")),
        "metar_current_max_f": as_float(row.get("metar_current_max_f")),
        "metar_latest_temp_f": as_float(row.get("metar_latest_temp_f")),
        "metar_latest_ts_utc": row.get("metar_latest_ts_utc"),
        "forecast_source": row.get("forecast_source"),
        "forecast_values_hash": row.get("forecast_values_hash"),
        "peak_state": peak_state,
        "ceiling_state": ceiling_state,
    }


def labels_for_event(event: dict[str, Any], winner: dict[str, Any] | None) -> dict[str, Any]:
    current = bracket_number(
        event.get("current_market_bracket") or event.get("current_bracket_c") or event.get("source_round_c")
    )
    if winner is None or current is None or winner.get("value") is None:
        return {
            "settlement_status": "missing",
            "final_winner_bracket": None,
            "current_stop": None,
            "overshoot": None,
            "d1_stop": None,
            "current_no_payout": None,
            "d1_no_payout": None,
        }
    final = float(winner["value"])
    exact_winner = not winner["is_lower_tail"] and not winner["is_upper_tail"]
    current_stop = int(exact_winner and abs(final - current) < 1e-9)
    d1_stop = int(exact_winner and abs(final - (current + 1.0)) < 1e-9)
    if winner["is_lower_tail"] and final >= current:
        overshoot = None
    elif winner["is_upper_tail"] and final <= current:
        overshoot = None
    else:
        overshoot = int(final > current)
    return {
        "settlement_status": "settled",
        "final_winner_bracket": winner["bracket"],
        "final_winner_is_tail": int(not exact_winner),
        "current_stop": current_stop,
        "overshoot": overshoot,
        "d1_stop": d1_stop,
        "current_no_payout": 1 - current_stop,
        "d1_no_payout": 1 - d1_stop,
    }


def oof_group_probability(
    rows: list[dict[str, Any]], row: dict[str, Any], target: str, keys: tuple[str, ...]
) -> tuple[float | None, int]:
    train = [
        other for other in rows
        if other.get("target_date") != row.get("target_date") and other.get(target) is not None
    ]
    if not train:
        return None, 0
    matched = [other for other in train if all(other.get(k) == row.get(k) for k in keys)]
    if not matched:
        matched = train
    hits = sum(int(other[target]) for other in matched)
    return (hits + 1.0) / (len(matched) + 2.0), len(matched)


def brier(y: int, p: float) -> float:
    return (float(y) - p) ** 2


def logloss(y: int, p: float) -> float:
    clipped = min(1 - 1e-6, max(1e-6, p))
    return -(y * math.log(clipped) + (1 - y) * math.log(1 - clipped))


def score_models(event_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    specs = {
        "event_only": (),
        "city_source": ("city",),
        "weather_path_source": ("city", "peak_state", "ceiling_state"),
        "weather_path_market_geometry": (
            "city", "peak_state", "ceiling_state", "current_price_bucket", "ladder_slope_bucket"
        ),
    }
    targets = {
        "current_stop": "current_yes_mid_h0",
        "overshoot": "current_no_mid_h0",
        "d1_stop": "d1_yes_mid_h0",
        "current_no_payout": "current_no_mid_h0",
    }
    predictions: list[dict[str, Any]] = []
    for row in event_rows:
        for target, market_field in targets.items():
            y = row.get(target)
            market_p = as_float(row.get(market_field))
            # Same-row comparison: every ablation below uses exactly the rows on
            # which the direct market mid and PIT path features both exist.
            if y is None or market_p is None or row.get("feature_status") != "ok":
                continue
            predictions.append({
                "event_id": row["event_id"], "city": row["city"],
                "target_date": row["target_date"], "target": target,
                "model": "market_direct_mid", "y": y, "p": market_p,
                "train_rows": "NA", "brier": brier(y, market_p),
                "logloss": logloss(y, market_p),
                "baseline_semantics": "approximate_for_overshoot" if target == "overshoot" else "exact",
            })
            for name, keys in specs.items():
                p, n = oof_group_probability(event_rows, row, target, keys)
                if p is None:
                    continue
                predictions.append({
                    "event_id": row["event_id"], "city": row["city"],
                    "target_date": row["target_date"], "target": target,
                    "model": name, "y": y, "p": p, "train_rows": n,
                    "brier": brier(y, p), "logloss": logloss(y, p),
                    "baseline_semantics": "date-block OOF beta-smoothed empirical",
                })
    summary: list[dict[str, Any]] = []
    for (target, model), rows in sorted(_group(predictions, "target", "model").items()):
        summary.append({
            "target": target, "model": model, "rows": len(rows),
            "dates": len({r["target_date"] for r in rows}),
            "cities": len({r["city"] for r in rows}),
            "mean_brier": mean(r["brier"] for r in rows),
            "mean_logloss": mean(r["logloss"] for r in rows),
            "mean_p": mean(r["p"] for r in rows),
            "base_rate": mean(r["y"] for r in rows),
        })
    return predictions, summary


def _group(rows: Iterable[dict[str, Any]], *keys: str) -> dict[tuple[Any, ...], list[dict[str, Any]]]:
    out: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        out[tuple(row.get(k) for k in keys)].append(row)
    return out


def build_analysis(args: argparse.Namespace) -> dict[str, Any]:
    collector = Path(args.collector)
    selected = [part.strip() for part in args.cities.split(",") if part.strip()]
    raw_events = list(iter_jsonl(collector / "events.jsonl"))
    raw_quotes = list(iter_jsonl(collector / "quote_snapshots.jsonl"))
    dedup: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    event_key_aliases: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)
    for row in sorted(raw_events, key=lambda x: str(x.get("source_detect_ts_utc") or "")):
        identity = event_identity(row)
        event_key_aliases[identity].add(str(row.get("event_key")))
        dedup.setdefault(identity, row)
    quotes_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in raw_quotes:
        quotes_by_key[str(row.get("event_key"))].append(row)
    for rows in quotes_by_key.values():
        rows.sort(key=lambda x: str(x.get("ts_utc") or ""))
    winners, db_meta = load_settlements(Path(args.db_path))

    event_rows: list[dict[str, Any]] = []
    panel: list[dict[str, Any]] = []
    for identity, event in dedup.items():
        city, target_date, source, current = identity
        if city not in selected:
            continue
        decision = parse_dt(event.get("source_detect_ts_utc"))
        obs_ts = parse_dt(event.get("source_obs_ts_utc"))
        if decision is None:
            continue
        qrows: list[dict[str, Any]] = []
        for alias in event_key_aliases[identity]:
            qrows.extend(quotes_by_key.get(alias, []))
        qrows.sort(key=lambda x: str(x.get("ts_utc") or ""))
        features = load_snapshot_features(event, current)
        labels = labels_for_event(event, winners.get((city, target_date)))
        event_id = "|".join(identity)
        base: dict[str, Any] = {
            "event_id": event_id, "city": city, "target_date": target_date,
            "source": source, "current_bracket": current,
            "previous_bracket": event.get("previous_market_bracket") or event.get("previous_no_bracket_c"),
            "d1_bracket": event.get("next_market_bracket") or event.get("next_no_bracket_c"),
            "source_obs_ts_utc": event.get("source_obs_ts_utc"),
            "source_first_seen_ts_utc": event.get("source_detect_ts_utc"),
            "source_obs_to_first_seen_sec": (decision - obs_ts).total_seconds() if obs_ts else None,
            "source_temp_c": as_float(event.get("source_temp_c")),
            "metar_running_max_round_c": as_float(event.get("metar_running_max_round_c")),
            "source_basis_class": event.get("source_basis_class"),
            "source_bracket_mode": event.get("source_bracket_mode"),
            **features, **labels,
        }
        supported: dict[int, tuple[dict[str, Any], float]] = {}
        for horizon in HORIZONS:
            qrow, actual = supported_quote(qrows, decision, horizon)
            if qrow is not None and actual is not None:
                supported[horizon] = (qrow, actual)
            for expression, (rel, side, label_name) in EXPRESSIONS.items():
                metrics = quote_metrics(quote_part(qrow or {}, rel, side))
                panel.append({
                    **base, "expression": expression, "label_name": label_name,
                    "label": base.get(label_name), "horizon_sec": horizon,
                    "actual_after_first_seen_sec": actual, **metrics,
                    "quote_status": quote_part(qrow or {}, rel, side).get("fresh_status") if qrow else "unsupported_horizon",
                    "quote_row_ts_utc": qrow.get("ts_utc") if qrow else None,
                })
        h0 = supported.get(0, (None, None))[0]
        base["h0_quote_after_first_seen_sec"] = supported.get(0, (None, None))[1]
        for prefix, rel, side in [
            ("previous_no", "t_minus_1", "no"),
            ("current_yes", "source_round", "yes"),
            ("current_no", "source_round", "no"),
            ("d1_yes", "source_plus_1", "yes"),
            ("d1_no", "source_plus_1", "no"),
        ]:
            metrics = quote_metrics(quote_part(h0 or {}, rel, side))
            for key, value in metrics.items():
                base[f"{prefix}_{key}_h0"] = value
        cp = as_float(base.get("current_yes_mid_h0"))
        dp = as_float(base.get("d1_yes_mid_h0"))
        base["current_price_bucket"] = (
            "low" if cp is not None and cp < 0.25 else
            "middle" if cp is not None and cp <= 0.75 else
            "high" if cp is not None else "missing"
        )
        slope = (dp - cp) if cp is not None and dp is not None else None
        base["ladder_slope_yes_d1_minus_current"] = slope
        base["ladder_slope_bucket"] = (
            "d1_above_current" if slope is not None and slope > 0.05 else
            "flat" if slope is not None and slope >= -0.05 else
            "d1_below_current" if slope is not None else "missing"
        )
        base["full_target_book_h0"] = int(all(base.get(k) is not None for k in (
            "current_yes_ask_h0", "current_no_ask_h0", "d1_yes_ask_h0"
        )))
        event_rows.append(base)

    predictions, score_summary = score_models(event_rows)

    state_summary: list[dict[str, Any]] = []
    state_specs = [
        ("current_stop", "current_yes_ask_h0"),
        ("current_no_payout", "current_no_ask_h0"),
        ("overshoot", "current_no_ask_h0"),
        ("d1_stop", "d1_yes_ask_h0"),
    ]
    state_groups = _group(event_rows, "city", "peak_state", "ceiling_state")
    for (city, peak_state, ceiling_state), rows in sorted(
        state_groups.items(), key=lambda item: tuple(str(value or "") for value in item[0])
    ):
        for target, ask_field in state_specs:
            labeled = [r for r in rows if r.get(target) is not None]
            executable = [r for r in labeled if as_float(r.get(ask_field)) is not None]
            state_summary.append({
                "city": city, "peak_state": peak_state, "ceiling_state": ceiling_state,
                "target": target, "labeled_rows": len(labeled),
                "dates": len({r["target_date"] for r in labeled}),
                "base_rate": mean(r.get(target) for r in labeled),
                "executable_rows": len(executable),
                "avg_direct_ask": mean(as_float(r.get(ask_field)) for r in executable),
            })

    prediction_index = {
        (row["event_id"], row["target"], row["model"]): row
        for row in predictions
    }
    policy_rows: list[dict[str, Any]] = []
    policy_specs = [
        ("current_yes", "current_stop", "current_yes_ask_h0", "current_yes_ask_size_h0"),
        ("current_no", "current_no_payout", "current_no_ask_h0", "current_no_ask_size_h0"),
        ("d1_yes", "d1_stop", "d1_yes_ask_h0", "d1_yes_ask_size_h0"),
    ]
    for event in event_rows:
        candidates: list[dict[str, Any]] = []
        for expression, target, ask_field, size_field in policy_specs:
            prediction = prediction_index.get((event["event_id"], target, "weather_path_source"))
            ask = as_float(event.get(ask_field))
            if prediction is None or ask is None:
                continue
            cost = ask + weather_fee(ask) + 0.01
            candidates.append({
                "expression": expression, "target": target, "p_oof": float(prediction["p"]),
                "ask": ask, "cost_with_fee_buffer": cost,
                "expected_edge": float(prediction["p"]) - cost,
                "ask_size": as_float(event.get(size_field)), "y": event.get(target),
            })
        best = max(candidates, key=lambda row: row["expected_edge"]) if candidates else None
        trade = best is not None and best["expected_edge"] > 0
        policy_rows.append({
            "event_id": event["event_id"], "city": event["city"],
            "target_date": event["target_date"], "current_bracket": event["current_bracket"],
            "peak_state": event.get("peak_state"), "ceiling_state": event.get("ceiling_state"),
            "selected_expression": best["expression"] if trade else "no_trade",
            "selected_target": best["target"] if trade else None,
            "p_oof": best["p_oof"] if trade else None,
            "direct_ask": best["ask"] if trade else None,
            "cost_with_fee_buffer": best["cost_with_fee_buffer"] if trade else None,
            "expected_edge": best["expected_edge"] if trade else (best["expected_edge"] if best else None),
            "ask_size": best["ask_size"] if trade else None,
            "label": best["y"] if trade else None,
            "realized_pnl_per_share": (
                float(best["y"]) - best["cost_with_fee_buffer"]
                if trade and best["y"] is not None else None
            ),
            "research_status": "exploratory_date_block_oof_not_frozen",
        })
    traded = [r for r in policy_rows if r["selected_expression"] != "no_trade" and r["label"] is not None]
    total_cost = sum(float(r["cost_with_fee_buffer"]) for r in traded)
    total_pnl = sum(float(r["realized_pnl_per_share"]) for r in traded)
    by_expression = Counter(r["selected_expression"] for r in traded)
    by_date_pnl = defaultdict(float)
    by_date_cost = defaultdict(float)
    for row in traded:
        by_date_pnl[row["target_date"]] += float(row["realized_pnl_per_share"])
        by_date_cost[row["target_date"]] += float(row["cost_with_fee_buffer"])
    best_date = max(by_date_pnl, key=by_date_pnl.get) if by_date_pnl else None
    policy_summary = [{
        "model": "weather_path_source", "policy": "highest_positive_OOF_edge_among_currentYES_currentNO_d1YES",
        "eligible_events": len(policy_rows), "selected_trades": len(traded),
        "dates": len({r["target_date"] for r in traded}),
        "cities": len({r["city"] for r in traded}),
        "expression_counts": json.dumps(by_expression, sort_keys=True),
        "avg_expected_edge": mean(r.get("expected_edge") for r in traded),
        "top_level_capacity_5_rows": sum((as_float(r.get("ask_size")) or 0) >= 5 for r in traded),
        "top_level_capacity_10_rows": sum((as_float(r.get("ask_size")) or 0) >= 10 for r in traded),
        "descriptive_oof_policy_roi": total_pnl / total_cost if total_cost else None,
        "best_date": best_date,
        "top_date_removed_roi": (
            (total_pnl - by_date_pnl[best_date]) / (total_cost - by_date_cost[best_date])
            if best_date and total_cost > by_date_cost[best_date] else None
        ),
        "verdict_boundary": "post-hypothesis exploratory; four global settled dates; not frozen forward",
    }]

    repricing: list[dict[str, Any]] = []
    for (city, expression, horizon), rows in sorted(_group(panel, "city", "expression", "horizon_sec").items()):
        if horizon == 0:
            continue
        h0_map = {
            row["event_id"]: row for row in panel
            if row["expression"] == expression and row["horizon_sec"] == 0
        }
        deltas = []
        for row in rows:
            h0 = h0_map.get(row["event_id"])
            if h0 and h0.get("mid") is not None and row.get("mid") is not None:
                deltas.append(float(row["mid"]) - float(h0["mid"]))
        repricing.append({
            "city": city, "expression": expression, "horizon_sec": horizon,
            "supported_rows": len(deltas), "dates": len({r["target_date"] for r in rows if r.get("mid") is not None}),
            "mean_mid_delta_vs_h0": mean(deltas), "median_mid_delta_vs_h0": median(deltas),
            "mean_abs_mid_delta": mean(abs(x) for x in deltas),
        })

    panel_index = {(r["event_id"], r["expression"], r["horizon_sec"]): r for r in panel}
    async_cases: list[dict[str, Any]] = []
    async_controls: list[dict[str, Any]] = []
    for event in event_rows:
        reaction: dict[str, int | None] = {}
        deltas_by_expr: dict[str, dict[int, float]] = defaultdict(dict)
        for expression in ("current_yes", "d1_yes"):
            h0 = panel_index.get((event["event_id"], expression, 0))
            h0_mid = as_float((h0 or {}).get("mid"))
            reaction[expression] = None
            if h0_mid is None:
                continue
            for horizon in HORIZONS[1:]:
                row = panel_index.get((event["event_id"], expression, horizon))
                mid_value = as_float((row or {}).get("mid"))
                if mid_value is None:
                    continue
                delta = mid_value - h0_mid
                deltas_by_expr[expression][horizon] = delta
                if reaction[expression] is None and abs(delta) >= 0.02:
                    reaction[expression] = horizon
        cur, d1 = reaction["current_yes"], reaction["d1_yes"]
        row = {
            "event_id": event["event_id"], "city": event["city"],
            "target_date": event["target_date"], "current_bracket": event["current_bracket"],
            "peak_state": event.get("peak_state"), "ceiling_state": event.get("ceiling_state"),
            "current_reaction_sec": cur, "d1_reaction_sec": d1,
            "previous_no_bid_h0": event.get("previous_no_bid_h0"),
            "current_stop": event.get("current_stop"), "overshoot": event.get("overshoot"),
            "d1_stop": event.get("d1_stop"),
            "current_delta_300s": deltas_by_expr["current_yes"].get(300),
            "d1_delta_300s": deltas_by_expr["d1_yes"].get(300),
        }
        if cur is not None and d1 is not None and abs(cur - d1) >= 30:
            row["async_type"] = "current_first" if cur < d1 else "d1_first"
            async_cases.append(row)
        elif (cur is None) != (d1 is None):
            row["async_type"] = "only_current_reacted" if cur is not None else "only_d1_reacted"
            async_cases.append(row)
        elif cur is not None and d1 is not None and cur == d1:
            row["control_type"] = "synchronous_same_supported_horizon"
            async_controls.append(row)
        elif cur is None and d1 is None:
            row["control_type"] = "neither_moved_2c_on_supported_horizons"
            async_controls.append(row)

    execution: list[dict[str, Any]] = []
    for expression, label_name, ask_field, size_field in [
        ("current_yes", "current_stop", "current_yes_ask_h0", "current_yes_ask_size_h0"),
        ("current_no", "current_no_payout", "current_no_ask_h0", "current_no_ask_size_h0"),
        ("d1_yes", "d1_stop", "d1_yes_ask_h0", "d1_yes_ask_size_h0"),
    ]:
        for city in selected + ["ALL_SELECTED"]:
            rows = [r for r in event_rows if (city == "ALL_SELECTED" or r["city"] == city)]
            settled = [r for r in rows if r.get(label_name) is not None and as_float(r.get(ask_field)) is not None]
            cost = 0.0
            pnl = 0.0
            for row in settled:
                ask = float(row[ask_field])
                total_cost = ask + weather_fee(ask) + 0.01
                cost += total_cost
                pnl += float(row[label_name]) - total_cost
            execution.append({
                "city": city, "expression": expression, "settled_executable_rows": len(settled),
                "dates": len({r["target_date"] for r in settled}),
                "avg_direct_ask": mean(as_float(r.get(ask_field)) for r in settled),
                "win_rate": mean(r.get(label_name) for r in settled),
                "top_level_capacity_5_rows": sum((as_float(r.get(size_field)) or 0) >= 5 for r in settled),
                "top_level_capacity_10_rows": sum((as_float(r.get(size_field)) or 0) >= 10 for r in settled),
                "descriptive_fee_buffer_roi": pnl / cost if cost else None,
                "interpretation": "all-row hindsight settlement replay; not a selected OOF policy",
            })

    physical_router_rows: list[dict[str, Any]] = []
    for event in event_rows:
        if event["city"] not in ("Helsinki", "Tokyo"):
            continue
        state = event.get("ceiling_state")
        if state == "ceiling_exhausted":
            expression, target, ask_field, size_field = (
                "current_yes", "current_stop", "current_yes_ask_h0", "current_yes_ask_size_h0"
            )
        elif state == "ceiling_above_one_c":
            expression, target, ask_field, size_field = (
                "current_no", "current_no_payout", "current_no_ask_h0", "current_no_ask_size_h0"
            )
        elif state == "ceiling_one_c_or_less":
            expression, target, ask_field, size_field = (
                "d1_yes", "d1_stop", "d1_yes_ask_h0", "d1_yes_ask_size_h0"
            )
        else:
            continue
        ask = as_float(event.get(ask_field))
        y = event.get(target)
        if ask is None or y is None:
            continue
        cost = ask + weather_fee(ask) + 0.01
        physical_router_rows.append({
            "event_id": event["event_id"], "city": event["city"],
            "target_date": event["target_date"], "peak_state": event.get("peak_state"),
            "ceiling_state": state, "forecast_ceiling_margin_vs_source_T_native": event.get("forecast_ceiling_margin_vs_source_T_native"),
            "expression": expression, "target": target, "direct_ask": ask,
            "cost_with_fee_buffer": cost, "label": y,
            "realized_pnl_per_share": float(y) - cost,
            "ask_size": as_float(event.get(size_field)),
            "research_status": "post_hypothesis_interpretable_router_not_frozen",
        })
    def summarize_physical_router(
        rows: list[dict[str, Any]], router: str, verdict_boundary: str
    ) -> dict[str, Any]:
        cost = sum(float(row["cost_with_fee_buffer"]) for row in rows)
        pnl = sum(float(row["realized_pnl_per_share"]) for row in rows)
        by_date: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
        for row in rows:
            by_date[row["target_date"]][0] += float(row["realized_pnl_per_share"])
            by_date[row["target_date"]][1] += float(row["cost_with_fee_buffer"])
        best_date = max(by_date, key=lambda date: by_date[date][0]) if by_date else None
        return {
            "router": router,
            "cities": "Helsinki,Tokyo", "rows": len(rows),
            "dates": len(by_date),
            "expression_counts": json.dumps(Counter(row["expression"] for row in rows), sort_keys=True),
            "win_rate": mean(row["label"] for row in rows),
            "fee_buffer_roi": pnl / cost if cost else None,
            "best_date": best_date,
            "top_date_removed_roi": (
                (pnl - by_date[best_date][0]) / (cost - by_date[best_date][1])
                if best_date and cost > by_date[best_date][1] else None
            ),
            "verdict_boundary": verdict_boundary,
        }

    physical_two_leg_rows = [
        row for row in physical_router_rows if row["expression"] in {"current_yes", "current_no"}
    ]
    physical_router_summary = [
        summarize_physical_router(
            physical_router_rows,
            "ceiling_exhausted=currentYES; ceiling_0.25_to_1.25C=d1YES; ceiling_above_1.25C=currentNO",
            "post-hypothesis; only four dates; reported as mechanism diagnostic, not policy",
        ),
        summarize_physical_router(
            physical_two_leg_rows,
            "ceiling_exhausted=currentYES; ceiling_0.25_to_1.25C=no_trade; ceiling_above_1.25C=currentNO",
            "clean two-leg candidate; post-hypothesis and only four dates; do not freeze or live",
        ),
    ]

    scorecard: list[dict[str, Any]] = []
    source_prior = {
        "Helsinki": ("same-station alternate feed", "exact_1c", "primary"),
        "Tokyo": ("same-airport JMA proxy", "exact_1c", "secondary"),
        "Istanbul": ("same-airport MGM proxy", "exact_1c", "negative_control"),
    }
    for city in selected:
        rows = [r for r in event_rows if r["city"] == city]
        scorecard.append({
            "city": city, "source_relation": source_prior[city][0], "market_shape": source_prior[city][1],
            "role": source_prior[city][2], "events": len(rows),
            "active_dates": len({r["target_date"] for r in rows}),
            "settled_events": sum(r["settlement_status"] == "settled" for r in rows),
            "median_obs_to_first_seen_sec": median(r.get("source_obs_to_first_seen_sec") for r in rows),
            "median_first_book_after_first_seen_sec": median(r.get("h0_quote_after_first_seen_sec") for r in rows),
            "h0_full_target_book_events": sum(r["full_target_book_h0"] for r in rows),
            "pit_weather_path_events": sum(r.get("feature_status") == "ok" for r in rows),
            "selection_basis": "source alignment + timestamps + sibling book + PIT features; never target-leg ROI",
        })

    raw_selected = [r for r in raw_events if str(r.get("city")) in selected]
    settled_rows = [r for r in event_rows if r["settlement_status"] == "settled"]
    funnels = [
        {"funnel": "signal", "stage": "raw_collector_events", "unit": "raw event rows", "rows": len(raw_events), "dates": len({r.get("target_date") for r in raw_events})},
        {"funnel": "signal", "stage": "selected_city_raw_events", "unit": "raw event rows", "rows": len(raw_selected), "dates": len({r.get("target_date") for r in raw_selected})},
        {"funnel": "signal", "stage": "first_city_date_source_T_event", "unit": "events", "rows": len(event_rows), "dates": len({r["target_date"] for r in event_rows})},
        {"funnel": "evidence", "stage": "PIT_weather_path", "unit": "events", "rows": sum(r.get("feature_status") == "ok" for r in event_rows), "dates": len({r["target_date"] for r in event_rows if r.get("feature_status") == "ok"})},
        {"funnel": "evidence", "stage": "settlement_label", "unit": "events", "rows": len(settled_rows), "dates": len({r["target_date"] for r in settled_rows})},
        {"funnel": "evidence", "stage": "full_T_YES_NO_and_d1_YES_book_h0", "unit": "events", "rows": sum(r["full_target_book_h0"] for r in event_rows), "dates": len({r["target_date"] for r in event_rows if r["full_target_book_h0"]})},
        {"funnel": "evidence", "stage": "actual_fills", "unit": "fills", "rows": 0, "dates": 0},
    ]
    horizon_counts = Counter((r["horizon_sec"] for r in panel if r["expression"] == "current_yes" and r.get("mid") is not None))
    coverage = [
        {"gap": "T_plus_2_book", "affected_rows": len(event_rows), "detail": "collector schema contains only t_minus_1/source_round/source_plus_1"},
        {"gap": "pre_event_book", "affected_rows": len(event_rows), "detail": "episode starts after source first-seen; h0 is first supported post-detect quote"},
        {"gap": "matched_non_cross_baseline", "affected_rows": len(event_rows), "detail": "collector records only update episodes; no same-city/local-time non-cross sibling panel"},
        {"gap": "rich_weather_fields", "affected_rows": len(event_rows), "detail": "cloud/rain/wind/humidity/recent path trend absent; only PIT peak clock/forecast ceiling/METAR state available"},
        {"gap": "unsettled_events", "affected_rows": len(event_rows) - len(settled_rows), "detail": f"canonical settlement ends {db_meta['settlement_end']}"},
        {"gap": "global_settled_target_dates", "affected_rows": len({r['target_date'] for r in settled_rows}), "detail": "too few dates for reliable expanding/OOF model selection"},
    ]
    for horizon in HORIZONS:
        coverage.append({
            "gap": f"supported_current_mid_h{horizon}", "affected_rows": horizon_counts[horizon],
            "detail": f"of {len(event_rows)} deduplicated selected-city events",
        })

    return {
        "meta": {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "collector": str(collector), "raw_events": len(raw_events), "raw_quotes": len(raw_quotes),
            "selected_cities": selected, **db_meta,
        },
        "scorecard": scorecard, "event_rows": event_rows, "panel": panel,
        "predictions": predictions, "score_summary": score_summary,
        "state_summary": state_summary, "policy_rows": policy_rows,
        "policy_summary": policy_summary,
        "physical_router_rows": physical_router_rows,
        "physical_router_summary": physical_router_summary,
        "repricing": repricing, "async_cases": async_cases,
        "async_controls": async_controls, "execution": execution,
        "funnels": funnels, "coverage": coverage,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collector", default=str(DEFAULT_COLLECTOR))
    parser.add_argument("--db-path", default=str(ROOT / "runtime/weather.db"))
    parser.add_argument("--cities", default="Helsinki,Tokyo,Istanbul")
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    payload = build_analysis(args)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, key in [
        ("city_scorecard.csv", "scorecard"),
        ("event_rows.csv", "event_rows"),
        ("event_expression_horizon_panel.csv", "panel"),
        ("oof_predictions.csv", "predictions"),
        ("proper_score_summary.csv", "score_summary"),
        ("state_target_summary.csv", "state_summary"),
        ("exploratory_oof_policy_rows.csv", "policy_rows"),
        ("exploratory_oof_policy_summary.csv", "policy_summary"),
        ("physical_router_rows.csv", "physical_router_rows"),
        ("physical_router_summary.csv", "physical_router_summary"),
        ("repricing_summary.csv", "repricing"),
        ("asynchronous_cases.csv", "async_cases"),
        ("asynchronous_counterexamples.csv", "async_controls"),
        ("execution_summary.csv", "execution"),
        ("funnels.csv", "funnels"),
        ("coverage_gaps.csv", "coverage"),
    ]:
        write_csv(out / name, payload[key])
    (out / "summary.json").write_text(json.dumps(payload["meta"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "meta": payload["meta"],
        "scorecard": payload["scorecard"],
        "proper_score_summary": payload["score_summary"],
        "execution": payload["execution"],
        "async_cases": len(payload["async_cases"]),
        "async_controls": len(payload["async_controls"]),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
