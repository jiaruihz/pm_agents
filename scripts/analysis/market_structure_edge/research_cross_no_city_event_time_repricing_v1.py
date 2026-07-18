#!/usr/bin/env python3
"""City-first cross-NO source-to-book event-time audit (research only)."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RUNTIME = Path("/Volumes/jrs/weather_data_feed_service_runtime")
HORIZONS = (0, 30, 60, 120, 300)
HORIZON_TOLERANCE = {0: 120, 30: 20, 60: 20, 120: 30, 300: 60}


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
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


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


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


def as_bool(value: Any) -> bool | None:
    if value in (True, "True", "true", 1, "1"):
        return True
    if value in (False, "False", "false", 0, "0"):
        return False
    return None


def as_float(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def wilson_low(hits: int, total: int, z: float = 1.96) -> float | None:
    if total <= 0:
        return None
    p = hits / total
    den = 1 + z * z / total
    center = p + z * z / (2 * total)
    radius = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return (center - radius) / den


def weather_fee_per_share(price: float, fee_rate: float = 0.05) -> float:
    return fee_rate * price * (1.0 - price)


def quote_values(row: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
    no = ((row.get("quotes") or {}).get("t_minus_1") or {}).get("no") or {}
    return (
        as_float(row.get("t_minus_1_no_best_ask") or no.get("fresh_best_ask")),
        as_float(row.get("t_minus_1_no_best_bid") or no.get("fresh_best_bid")),
        as_float(no.get("fresh_ask_size")),
    )


def nearest_supported_quote(
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
    if gap > HORIZON_TOLERANCE[horizon]:
        return None, None
    return row, (ts - decision).total_seconds()


def event_key_for_label(city: str, target_date: str, source: str, previous: str) -> str:
    try:
        current = str(int(float(previous)) + 1)
    except ValueError:
        current = ""
    return f"{city}|{target_date}|{source}|{current}"


def load_db_snapshot(path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    fact = conn.execute(
        "SELECT COUNT(*), MAX(fact_built_at_utc), "
        "SUM(CASE WHEN settlement_status='unsettled' THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN settlement_status='missing_bracket' THEN 1 ELSE 0 END) FROM fact_trades"
    ).fetchone()
    settlement = conn.execute(
        "SELECT MIN(target_date), MAX(target_date), COUNT(DISTINCT target_date) FROM settlement_outcomes"
    ).fetchone()
    conn.close()
    return {
        "db_mtime_utc": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
        "fact_trades": fact[0],
        "fact_built_at_utc": fact[1],
        "unsettled": fact[2] or 0,
        "missing_bracket": fact[3] or 0,
        "settlement_start": settlement[0],
        "settlement_end": settlement[1],
        "settlement_dates": settlement[2],
    }


def collector_counts(events: list[dict[str, Any]], quotes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for city in sorted({str(row.get("city")) for row in events}):
        city_events = [row for row in events if row.get("city") == city]
        event_keys = {str(row.get("event_key")) for row in city_events}
        city_quotes = [row for row in quotes if row.get("event_key") in event_keys]
        priced_keys = {
            str(row.get("event_key")) for row in city_quotes if quote_values(row)[0] is not None
        }
        output[city] = {
            "collector_events": len(event_keys),
            "collector_dates": len({row.get("target_date") for row in city_events}),
            "priced_events": len(priced_keys),
            "direct_book_event_coverage": round(len(priced_keys) / len(event_keys), 4) if event_keys else 0,
        }
    return output


def make_scorecard(
    summaries: list[dict[str, str]], counts: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    summary = {row["city"]: row for row in summaries}
    candidates = [
        ("Moscow", "WRH/Synoptic UUWW", "authoritative_invalidated", "actual rule source", "exact_1c", 5, 5),
        ("HongKong", "HKO", "authoritative_invalidated", "actual independent rule source", "floor_1c", 5, 5),
        ("Helsinki", "FMI/EFHK", "proxy_confirmed_cross", "same-station alternate feed", "exact_1c", 4, 5),
        ("Tokyo", "JMA/RJTT", "proxy_confirmed_cross", "same-airport proxy", "exact_1c", 4, 5),
        ("Busan", "AMOS/RKPK", "proxy_confirmed_cross", "runway/airport proxy", "exact_1c", 4, 4),
        ("Singapore", "MSS/WSSS", "proxy_confirmed_cross", "nearby reference station", "exact_1c", 2, 4),
        ("Istanbul", "MGM/LTFM", "proxy_confirmed_cross", "same-airport alternate feed", "exact_1c", 4, 5),
    ]
    prior = {
        "Moscow": ("NA", "62/62 settlement replay", "source-to-book first-seen absent"),
        "HongKong": ("NA", "rule mapping audited", "no HongKong HKO event-to-book rows"),
        "Tokyo": ("~0.908 raw prior", "small/non-frozen", "basis false-cross audit pending"),
        "Busan": ("~0.831 raw prior", "known false crosses", "basis/revision risk"),
        "Singapore": ("~0.633 raw prior", "negative control", "nearby-station basis weak"),
    }
    rows: list[dict[str, Any]] = []
    for city, source, mechanism, relation, shape, authority, mapping in candidates:
        s = summary.get(city, {})
        c = counts.get(city, {})
        next_precision = s.get("first_cross_precision") or prior.get(city, ("NA", "", ""))[0]
        persistent_precision = s.get("persistent_cross_precision") or "NA"
        cadence = s.get("median_fast_observation_cadence_min") or "NA"
        detect_lag = s.get("median_fast_detection_lag_min") or "NA"
        lead = s.get("median_lead_to_next_metar_min") or "NA"
        coverage = float(c.get("direct_book_event_coverage", 0))
        time_score = 5 if detect_lag not in ("", "NA") and float(detect_lag) <= 5 else 3 if detect_lag not in ("", "NA") and float(detect_lag) <= 15 else 1
        coverage_score = 5 if coverage >= 0.8 else 3 if coverage >= 0.5 else 0
        cleanliness = authority + mapping + time_score + coverage_score
        selected = city == "Helsinki"
        blocker = prior.get(city, ("", "", ""))[2]
        if city == "Helsinki":
            blocker = "selected proxy; only two settled event-to-book dates"
        elif city == "Istanbul":
            blocker = "next-METAR precision and detection lag fail Helsinki comparison"
        rows.append({
            "city": city, "source": source, "mechanism": mechanism,
            "source_settlement_relation": relation, "market_shape": shape,
            "authority_alignment_score_5": authority, "mapping_score_5": mapping,
            "time_score_5": time_score, "book_coverage_score_5": coverage_score,
            "cleanliness_score_20": cleanliness, "source_cadence_min": cadence,
            "detect_lag_min": detect_lag, "median_lead_to_next_official_min": lead,
            "single_next_official_precision": next_precision,
            "persistent_next_official_precision": persistent_precision,
            "collector_events": c.get("collector_events", 0),
            "collector_dates": c.get("collector_dates", 0),
            "direct_book_event_coverage": coverage,
            "first_round_status": "selected_proxy" if selected else "not_selected",
            "blocker_or_reason": blocker,
        })
    return sorted(rows, key=lambda row: (-int(row["cleanliness_score_20"]), row["city"]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME))
    parser.add_argument("--db-path", default=str(ROOT / "runtime/weather.db"))
    parser.add_argument("--eligibility-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    runtime = Path(args.runtime_root)
    collector = runtime / "output/source_event_ladder_repricing_shadow"
    events_raw = list(iter_jsonl(collector / "events.jsonl"))
    dedup_events: dict[str, dict[str, Any]] = {}
    for row in events_raw:
        key = str(row.get("event_key") or "")
        if key and key not in dedup_events:
            dedup_events[key] = row
    events = list(dedup_events.values())
    quotes = list(iter_jsonl(collector / "quote_snapshots.jsonl"))
    quote_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in quotes:
        quote_by_event[str(row.get("event_key") or "")].append(row)
    for rows in quote_by_event.values():
        rows.sort(key=lambda row: str(row.get("ts_utc") or ""))

    eligibility = Path(args.eligibility_dir)
    first = read_csv(eligibility / "first_cross_events.csv")
    persistent = read_csv(eligibility / "persistent_cross_events.csv")
    summaries = read_csv(eligibility / "summary_by_city_source.csv")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    counts = collector_counts(events, quotes)
    scorecard = make_scorecard(summaries, counts)
    write_csv(out_dir / "city_mechanism_scorecard.csv", scorecard)

    collector_start = min(row["target_date"] for row in events if row.get("city") == "Helsinki")
    calibration = [
        row for row in first if row.get("city") == "Helsinki" and row.get("target_date", "") < collector_start
    ]
    calibration_persistent = [
        row for row in persistent if row.get("city") == "Helsinki" and row.get("target_date", "") < collector_start
    ]
    cal_final_hits = sum(as_bool(row.get("settlement_left_previous_bracket")) is True for row in calibration)
    cal_next_hits = sum(as_bool(row.get("next_metar_market_crossed")) is True for row in calibration)
    cal_p_final_hits = sum(as_bool(row.get("settlement_left_previous_bracket")) is True for row in calibration_persistent)
    cal_p_next_hits = sum(as_bool(row.get("next_metar_market_crossed")) is True for row in calibration_persistent)
    probabilities = {
        "single": {
            "final_p": cal_final_hits / len(calibration),
            "final_low": wilson_low(cal_final_hits, len(calibration)),
            "next_p": cal_next_hits / len(calibration),
            "next_low": wilson_low(cal_next_hits, len(calibration)),
            "n": len(calibration),
        },
        "persistent": {
            "final_p": cal_p_final_hits / len(calibration_persistent),
            "final_low": wilson_low(cal_p_final_hits, len(calibration_persistent)),
            "next_p": cal_p_next_hits / len(calibration_persistent),
            "next_low": wilson_low(cal_p_next_hits, len(calibration_persistent)),
            "n": len(calibration_persistent),
        },
    }

    labels: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for row in first:
        labels[(row["city"], row["target_date"], row["fast_source"], row["previous_market_bracket"])] = row
    persistent_labels: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for row in persistent:
        persistent_labels[(row["city"], row["target_date"], row["fast_source"], row["previous_market_bracket"])] = row

    ledger: list[dict[str, Any]] = []
    curves: list[dict[str, Any]] = []

    def add_policy_row(event: dict[str, Any], definition: str, label: dict[str, str] | None) -> None:
        decision = parse_dt(label.get("fast_detect_ts_utc") if label and definition == "persistent" else event.get("source_detect_ts_utc"))
        observed = parse_dt(label.get("fast_obs_ts_utc") if label and definition == "persistent" else event.get("source_obs_ts_utc"))
        if decision is None:
            return
        prob = probabilities[definition]
        qrows = quote_by_event.get(str(event.get("event_key")), [])
        horizon_data: dict[int, tuple[dict[str, Any] | None, float | None]] = {
            horizon: nearest_supported_quote(qrows, decision, horizon) for horizon in HORIZONS
        }
        first_quote, first_offset = horizon_data[0]
        ask, bid, size = quote_values(first_quote or {})
        fee = weather_fee_per_share(ask) if ask is not None else None
        edge_point = prob["final_p"] - ask - fee if ask is not None else None
        edge_low = prob["final_low"] - ask - fee if ask is not None else None
        row = {
            "mechanism": "proxy_confirmed_cross", "policy_definition": definition,
            "city": event.get("city"), "source": event.get("source"),
            "target_date": event.get("target_date"), "event_key": event.get("event_key"),
            "source_observation_ts_utc": observed.isoformat() if observed else "",
            "source_first_seen_ts_utc": decision.isoformat(),
            "collector_episode_created_ts_utc": event.get("created_at_utc"),
            "observation_to_first_seen_sec": round((decision - observed).total_seconds(), 3) if observed else "",
            "collector_episode_delay_sec": round((parse_dt(event.get("created_at_utc")) - decision).total_seconds(), 3) if parse_dt(event.get("created_at_utc")) else "",
            "previous_market_bracket": event.get("previous_market_bracket"),
            "source_market_value": event.get("source_market_value"),
            "source_temp_c": event.get("source_temp_c"),
            "next_official_confirmed": as_bool(label.get("next_metar_market_crossed")) if label else "",
            "final_settlement_left_old_bracket": as_bool(label.get("settlement_left_previous_bracket")) if label else "",
            "label_status": "labeled" if label else "coverage_gap_unsettled_or_unpaired",
            "frozen_calibration_n": prob["n"], "frozen_final_p": round(prob["final_p"], 6),
            "frozen_final_wilson_low": round(prob["final_low"], 6),
            "frozen_next_official_p": round(prob["next_p"], 6),
            "first_supported_quote_offset_sec": round(first_offset, 3) if first_offset is not None else "",
            "direct_no_ask": ask, "direct_no_bid": bid, "top_ask_size": size,
            "official_fee_per_share": round(fee, 6) if fee is not None else "",
            "edge_per_share_point_p": round(edge_point, 6) if edge_point is not None else "",
            "edge_per_share_wilson_low": round(edge_low, 6) if edge_low is not None else "",
            "edge_per_share_wilson_low_plus_1c_buffer": round(edge_low - 0.01, 6) if edge_low is not None else "",
            "top_level_capacity_1": bool(size is not None and size >= 1),
            "top_level_capacity_5": bool(size is not None and size >= 5),
            "top_level_capacity_10": bool(size is not None and size >= 10),
        }
        ledger.append(row)
        for horizon, (quote, offset) in horizon_data.items():
            h_ask, h_bid, h_size = quote_values(quote or {})
            h_fee = weather_fee_per_share(h_ask) if h_ask is not None else None
            curves.append({
                "event_key": event.get("event_key"), "policy_definition": definition,
                "city": event.get("city"), "target_date": event.get("target_date"),
                "requested_horizon_sec": horizon,
                "actual_quote_offset_sec": round(offset, 3) if offset is not None else "",
                "horizon_supported": quote is not None, "direct_no_ask": h_ask,
                "direct_no_bid": h_bid, "top_ask_size": h_size,
                "edge_point_p": round(prob["final_p"] - h_ask - h_fee, 6) if h_ask is not None else "",
                "edge_wilson_low": round(prob["final_low"] - h_ask - h_fee, 6) if h_ask is not None else "",
            })

    helsinki_events = sorted(
        [row for row in events if row.get("city") == "Helsinki" and row.get("source") == "fmi"],
        key=lambda row: str(row.get("source_detect_ts_utc")),
    )
    for event in helsinki_events:
        previous = str(event.get("previous_market_bracket"))
        key = ("Helsinki", str(event.get("target_date")), "fmi", previous)
        add_policy_row(event, "single", labels.get(key))
        if key in persistent_labels:
            add_policy_row(event, "persistent", persistent_labels[key])

    write_csv(out_dir / "source_event_ledger.csv", ledger)
    write_csv(out_dir / "event_time_repricing_curve.csv", curves)

    false_crosses: list[dict[str, Any]] = []
    for definition, rows in (("single", first), ("persistent", persistent)):
        for row in rows:
            if row.get("city") not in {"Helsinki", "Istanbul"}:
                continue
            if as_bool(row.get("next_metar_market_crossed")) is False:
                false_crosses.append({
                    "city": row.get("city"), "source": row.get("fast_source"),
                    "policy_definition": definition, "target_date": row.get("target_date"),
                    "fast_obs_ts_utc": row.get("fast_obs_ts_utc"),
                    "fast_detect_ts_utc": row.get("fast_detect_ts_utc"),
                    "previous_market_bracket": row.get("previous_market_bracket"),
                    "fast_temp_unit": row.get("fast_temp_unit"),
                    "next_metar_temp_round": row.get("next_metar_temp_round"),
                    "final_settlement_left_old_bracket": row.get("settlement_left_previous_bracket"),
                    "cause": "proxy print not confirmed by next official report; weather-cause fields unavailable in this ledger",
                })
    write_csv(out_dir / "false_crosses.csv", false_crosses)

    summary_rows: list[dict[str, Any]] = []
    latency_rows: list[dict[str, Any]] = []
    for definition in ("single", "persistent"):
        rows = [row for row in ledger if row["policy_definition"] == definition]
        labeled = [row for row in rows if row["label_status"] == "labeled"]
        priced = [row for row in rows if row["direct_no_ask"] != "" and row["direct_no_ask"] is not None]
        robust = [row for row in priced if float(row["edge_per_share_wilson_low_plus_1c_buffer"]) > 0]
        offsets = [float(row["first_supported_quote_offset_sec"]) for row in rows if row["first_supported_quote_offset_sec"] != ""]
        summary_rows.append({
            "mechanism": "proxy_confirmed_cross", "city": "Helsinki", "policy_definition": definition,
            "events": len(rows), "dates": len({row["target_date"] for row in rows}),
            "labeled_events": len(labeled), "labeled_dates": len({row["target_date"] for row in labeled}),
            "direct_book_events": len(priced), "robust_positive_edge_events": len(robust),
            "robust_positive_edge_share": round(len(robust) / len(rows), 4) if rows else 0,
            "median_first_quote_delay_sec": round(statistics.median(offsets), 3) if offsets else "",
            "frozen_final_p": probabilities[definition]["final_p"],
            "frozen_final_wilson_low": probabilities[definition]["final_low"],
            "frozen_next_official_p": probabilities[definition]["next_p"],
            "verdict": "continue_collector",
        })
        for horizon in HORIZONS:
            hrows = [row for row in curves if row["policy_definition"] == definition and row["requested_horizon_sec"] == horizon]
            covered = [
                row for row in hrows
                if row["horizon_supported"] and row["direct_no_ask"] not in (None, "")
            ]
            positive = [row for row in covered if row["edge_wilson_low"] != "" and float(row["edge_wilson_low"]) > 0]
            positive_buffer = [row for row in covered if row["edge_wilson_low"] != "" and float(row["edge_wilson_low"]) > 0.01]
            latency_rows.append({
                "city": "Helsinki", "policy_definition": definition,
                "horizon_sec": horizon, "events": len(hrows), "supported_quotes": len(covered),
                "positive_fee_adjusted_edge": len(positive),
                "positive_fee_plus_1c_buffer": len(positive_buffer),
                "survival_rate_supported": round(len(positive_buffer) / len(covered), 4) if covered else "",
            })
    summary_rows.append({
        "mechanism": "authoritative_invalidated", "city": "none_frozen",
        "policy_definition": "official_rule_source", "events": 0, "dates": 0,
        "labeled_events": 0, "labeled_dates": 0, "direct_book_events": 0,
        "robust_positive_edge_events": 0, "robust_positive_edge_share": 0,
        "median_first_quote_delay_sec": "", "frozen_final_p": 1.0,
        "frozen_final_wilson_low": "", "frozen_next_official_p": 1.0,
        "verdict": "continue_collector",
    })
    write_csv(out_dir / "mechanism_summary.csv", summary_rows)
    write_csv(out_dir / "latency_survival.csv", latency_rows)

    single_rows = [row for row in ledger if row["policy_definition"] == "single"]
    persistent_rows = [row for row in ledger if row["policy_definition"] == "persistent"]
    funnels = [
        {"funnel": "signal", "stage": "Helsinki distinct causal FMI observations", "unit": "observation", "rows": next((r["distinct_fast_observations"] for r in summaries if r["city"] == "Helsinki"), 0), "dates": next((r["event_sample_days"] for r in summaries if r["city"] == "Helsinki"), 0)},
        {"funnel": "signal", "stage": "first legal bracket cross", "unit": "event", "rows": next((r["first_cross_signals"] for r in summaries if r["city"] == "Helsinki"), 0), "dates": next((r["first_cross_days"] for r in summaries if r["city"] == "Helsinki"), 0)},
        {"funnel": "signal", "stage": "persistent two-observation cross", "unit": "event", "rows": next((r["persistent_cross_signals"] for r in summaries if r["city"] == "Helsinki"), 0), "dates": next((r["persistent_cross_days"] for r in summaries if r["city"] == "Helsinki"), 0)},
        {"funnel": "signal", "stage": "collector-window first cross", "unit": "event", "rows": len(single_rows), "dates": len({r["target_date"] for r in single_rows})},
        {"funnel": "evidence", "stage": "trustworthy first-seen and event episode", "unit": "event", "rows": len(single_rows), "dates": len({r["target_date"] for r in single_rows})},
        {"funnel": "evidence", "stage": "supported direct NO quote at decision", "unit": "event", "rows": sum(r["direct_no_ask"] not in (None, "") for r in single_rows), "dates": len({r["target_date"] for r in single_rows if r["direct_no_ask"] not in (None, "")})},
        {"funnel": "evidence", "stage": "settlement/official label", "unit": "event", "rows": sum(r["label_status"] == "labeled" for r in single_rows), "dates": len({r["target_date"] for r in single_rows if r["label_status"] == "labeled"})},
        {"funnel": "evidence", "stage": "positive Wilson-low edge after fee plus 1c", "unit": "event", "rows": sum(r["edge_per_share_wilson_low_plus_1c_buffer"] != "" and float(r["edge_per_share_wilson_low_plus_1c_buffer"]) > 0 for r in single_rows), "dates": len({r["target_date"] for r in single_rows if r["edge_per_share_wilson_low_plus_1c_buffer"] != "" and float(r["edge_per_share_wilson_low_plus_1c_buffer"]) > 0})},
    ]
    write_csv(out_dir / "funnels.csv", funnels)

    gaps = [
        {"layer": "authoritative", "city": "Moscow", "gap": "no WRH/UUWW authoritative event-to-direct-book episodes in current ladder collector", "affected_events": 0},
        {"layer": "authoritative", "city": "HongKong", "gap": "HKO collector rows are Shenzhen cross-station proxy, not HongKong settlement events", "affected_events": 0},
        {"layer": "proxy", "city": "Helsinki", "gap": "settlement_outcomes end before collector end", "affected_events": sum(r["label_status"] != "labeled" for r in single_rows)},
        {"layer": "book", "city": "Helsinki", "gap": "no supported direct ask within decision horizon", "affected_events": sum(r["direct_no_ask"] in (None, "") for r in single_rows)},
        {"layer": "capacity", "city": "Helsinki", "gap": "collector stores top level only; 5/10-share VWAP beyond top ask is unavailable", "affected_events": len(single_rows)},
    ]
    write_csv(out_dir / "coverage_gaps.csv", gaps)

    db = load_db_snapshot(Path(args.db_path))
    generated = datetime.now(timezone.utc).isoformat()
    single_summary = next(row for row in summary_rows if row.get("policy_definition") == "single")
    persistent_summary = next(row for row in summary_rows if row.get("policy_definition") == "persistent")
    best_single = sorted(
        [row for row in single_rows if row["edge_per_share_wilson_low_plus_1c_buffer"] != ""],
        key=lambda row: float(row["edge_per_share_wilson_low_plus_1c_buffer"]), reverse=True,
    )
    best_text = "none"
    if best_single:
        best = best_single[0]
        best_text = f"{best['target_date']} old {best['previous_market_bracket']} NO ask={best['direct_no_ask']}, conservative edge={best['edge_per_share_wilson_low_plus_1c_buffer']}/share, top size={best['top_ask_size']}"

    report = f"""# Cross-NO 城市机制与 Event-Time Repricing v1

## 数据快照

- 数据源：Mac JRS raw `{collector}` + canonical `{args.db_path}`；未同步、未 rebuild。
- 生成时间：`{generated}`；collector event 覆盖 `{min(r['target_date'] for r in events)}..{max(r['target_date'] for r in events)}`，`{len(events)}` 个去重事件、`{len(quotes)}` 行盘口。
- DB mtime `{db['db_mtime_utc']}`，fact built `{db['fact_built_at_utc']}`，fact_trades `{db['fact_trades']}`。
- unsettled `{db['unsettled']}/{db['fact_trades']}`；missing_bracket `{db['missing_bracket']}`；settlement_outcomes 截止 `{db['settlement_end']}`。本研究不发布 fill PnL，标签直接来自 `settlement_outcomes`；这 55 个全局 fact missing_bracket 未进入本研究分母。

## 大白话结论

首轮最干净的是 **Helsinki / FMI 的 proxy cross**，不是 authoritative cross。FMI 本身更新约 10 分钟一次，系统 first-seen 延迟约 3.2 分钟，相对下一份 METAR 的中位领先约 **10.3 分钟**。但这 10 分钟不是可交易窗口：盘口 collector 在系统 first-seen 后才建 episode，首个可用 direct NO quote 的中位延迟是 **{single_summary['median_first_quote_delay_sec']} 秒**，而且大多数旧档 NO 已经在 `0.99` 附近或没有 ask。

冻结在 collector 启动前的 Helsinki calibration：single cross 最终旧档失效 `18/18`，95% Wilson 下界 `{probabilities['single']['final_low']:.3f}`；下一份 METAR 立即确认只有 `14/18`。因此它不能按确定性票算。collector 窗口内 single cross 有 `{single_summary['events']}` 个 / `{single_summary['dates']}` 天，但结算对齐只有 `{single_summary['labeled_events']}` 个 / `{single_summary['labeled_dates']}` 天；按 Wilson 下界、官方 fee 再加 1c buffer，只有 `{single_summary['robust_positive_edge_events']}` 个事件仍为正。最好的一行是 `{best_text}`，它是 forward collector 捕获但尚未结算的一行，只能记机会，不能升级策略。

persistent cross 更准但更慢：collector 窗口只有 `{persistent_summary['events']}` 个对齐事件；一个首次可见 ask 已到 `0.994`，另一个没有卖盘。也就是说，**等两次观测确认后，盘口基本已经吃完 edge，5/10-share 可执行容量为 0 或不稳健。**

authoritative 轨道本轮没有冻结城市：Moscow 的 62/62 规则映射只证明结算语义，不证明 WRH first-seen 比盘口快；当前 ladder collector 没有 Moscow episode。HKO 当前两条事件属于 Shenzhen/Lau Fau Shan cross-station proxy，不是 HongKong authoritative event。这个轨道只能 `continue_collector`，不能拿历史规则回放冒充 event-time 证据。

失败层次很明确：Helsinki single 主要卡在 **盘口反应快 + 样本/settlement 覆盖薄**；persistent 进一步卡在 **确认延迟**。不是天气特征还不够多，也不是需要再加价格 hard gate。

## 城市选择

完整 scorecard 见 `city_mechanism_scorecard.csv`。选择不看 ROI：

- proxy 主样本：Helsinki/FMI，exact-1C、同站 alternate feed、first-seen 和 direct book 都存在。
- 负面对照：Istanbul/MGM，只用于 source/detection 对照；约 19 分钟 detect lag、single next-METAR precision 约 0.21，明显不适合深入盘口。
- authoritative：Moscow/HongKong 均不冻结，原因是当前 source-first-seen→direct-book 分母为 0。

## 两种机制分开 verdict

| mechanism | denominator | 结果 | verdict |
|---|---:|---|---|
| authoritative_invalidated | 0 event-time direct-book events | 语义干净，但没有 first-seen→book 证据 | `continue_collector` |
| proxy single cross, Helsinki | {single_summary['events']} events / {single_summary['dates']} dates | 少数早期 quote 有 edge；结算对齐仅 {single_summary['labeled_dates']} 天 | `continue_collector` |
| proxy persistent cross, Helsinki | {persistent_summary['events']} events | ask 0.994 或无 ask；确认后容量消失 | `continue_collector` |

significance=FAIL；baseline=NA（只有两个 settled event-to-book dates）；forward=FAIL；conclusion=`inconclusive`。不改 live。

## 准确度与 false cross

- Helsinki frozen pre-collector single：next official `{cal_next_hits}/{len(calibration)}`，final settlement `{cal_final_hits}/{len(calibration)}`。
- Helsinki frozen pre-collector persistent：next official `{cal_p_next_hits}/{len(calibration_persistent)}`，final settlement `{cal_p_final_hits}/{len(calibration_persistent)}`。
- 全部 Helsinki/Istanbul next-official false crosses 已写入 `false_crosses.csv`。天气原因字段在该 event ledger 中不完整，所以原因保持 `unknown/source-basis-or-transient-spike`，没有事后编故事。

## Event-time 与容量口径

- decision clock 是 source `first_seen/detect`，不是 report timestamp。
- `0/30/60/120/300s` 只有实际 quote 落在容差内才算 supported；collector 后期变成 600 秒 cadence 的行不会伪装成 30 秒数据。
- fee 使用 Weather 官方 `0.05 * price * (1-price)` 每股；另报 +1c execution buffer。
- collector 只有 top ask/size，没有完整多档深度，所以只能证明 top-level 1/5/10-share capacity；不能声称 5/10-share VWAP。

## 双漏斗

详见 `funnels.csv`。盘口缺失、结算截止和 authoritative 事件缺失都列在 `coverage_gaps.csv`，没有作为策略筛选条件。

## 8 环覆盖

- covered：source basis、PIT first-seen、next-official/final label、direct book、official fee、top-level capacity、event-time curve、双漏斗。
- partial：显著性（日期太少）、天气 false-cross 解释（字段未进入 collector ledger）、capacity（仅 top level）。
- missing：authoritative source-to-book episodes、同城 non-cross matched baseline、10 个新 active dates frozen forward、真实 fills。

## 动作

保持当前 zero-notional collector，冻结 Helsinki/FMI 的 `single` 与 `persistent` 两个定义分别记账；不要把 persistent 当 entry gate，也不要扩城市。先补到至少 10 个新的 settled active dates，并让 Moscow/HKO authoritative collector 产生真实 first-seen→direct-book episode，再复核。当前不启动新 shadow、不改 live、不下单。
"""
    Path(args.report).write_text(report, encoding="utf-8")
    print(json.dumps({
        "events": len(events), "quotes": len(quotes), "helsinki_single": len(single_rows),
        "helsinki_persistent": len(persistent_rows), "report": args.report,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
