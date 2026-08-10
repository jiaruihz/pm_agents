"""Readiness and PIT contract for pooled weather-driven ladder transport.

This module intentionally refuses to infer a full probability ladder from a
binary current-bracket model, to treat a three-rung hot strip as a full ladder,
or to substitute a 15 second REST capture for the registered 10 second target.
It is the stable input gate for the Amsterdam/Helsinki/Tokyo pooled experiment;
Busan and Seoul are source-basis negative controls only.  The registered
repricing horizons are the collector-native 15/30/60 seconds.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from src.strategies.runtime.production import load_production_spec


SCHEMA_VERSION = "weather_pooled_ladder_transport_readiness_v1"
MODEL_ID = "market_structure_edge_ladder_transport_v1"
POOLED_CITIES = ("Amsterdam", "Helsinki", "Tokyo")
NEGATIVE_CONTROL_CITIES = ("Busan", "Seoul")
HORIZONS_SEC = (15, 30, 60)
REQUIRED_SLOTS = ("pre", "t0", "+15s", "+30s", "+60s")
FROZEN_FORWARD_START = "2026-08-11"
FROZEN_FORWARD_END = "2026-08-17"
CONSERVATION_TOLERANCE = 1e-8


def default_input_paths() -> dict[str, Path]:
    production = load_production_spec()
    output_root = production.data_feed_output_root()
    return {
        "knmi_events": output_root / "knmi_first_seen_ladder_v1" / "events.jsonl",
        "knmi_captures": output_root
        / "knmi_first_seen_ladder_v1"
        / "captures.jsonl",
        "high_frequency_observations": production.live_cross_observations_root()
        / "high_frequency_observations.jsonl",
        "ws_feature_root": production.data_feed_runtime_root
        / "market_books"
        / "ws_event_ladder_features",
        "ws_epoch_root": production.data_feed_runtime_root
        / "market_books"
        / "ws_incremental"
        / "subscription_epochs",
        "weather_delta": production.data_feed_runtime_root
        / "output"
        / "city_probability_runtime"
        / "ladder_weather_delta.jsonl",
        "output_dir": production.research_artifact_root
        / "market_structure_edge"
        / "market_structure_edge_ladder_transport_v1"
        / "readiness_15_30_60_2026-08-10",
    }


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def _iter_jsonl_root(root: Path) -> Iterable[dict[str, Any]]:
    if root.is_file():
        yield from _iter_jsonl(root)
        return
    if not root.exists():
        return
    for path in sorted(root.glob("*.jsonl*")):
        yield from _iter_jsonl(path)


def _sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def project_probability_simplex(values: Sequence[float]) -> np.ndarray:
    """Euclidean projection onto the unit simplex.

    Projecting the before and after vectors separately makes their difference
    an exactly mass-conserving transport vector (up to floating point error).
    """

    vector = np.asarray(values, dtype=float)
    if vector.ndim != 1 or vector.size == 0 or not np.isfinite(vector).all():
        raise ValueError("probability vector must be finite and one-dimensional")
    ordered = np.sort(vector)[::-1]
    cumulative = np.cumsum(ordered) - 1.0
    indexes = np.arange(1, vector.size + 1)
    support = np.nonzero(ordered - cumulative / indexes > 0)[0]
    if support.size == 0:
        raise ValueError("simplex projection has empty support")
    rho = int(support[-1])
    theta = cumulative[rho] / float(rho + 1)
    projected = np.maximum(vector - theta, 0.0)
    projected /= projected.sum()
    return projected


def conservative_weather_transport(
    probability_before: Sequence[float],
    raw_probability_after: Sequence[float],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return normalized before/after ladders and their conserved delta."""

    before = project_probability_simplex(probability_before)
    after = project_probability_simplex(raw_probability_after)
    delta = after - before
    if abs(float(delta.sum())) > CONSERVATION_TOLERANCE:
        raise AssertionError("weather transport does not conserve probability mass")
    return before, after, delta


def _event_identity(row: Mapping[str, Any]) -> str:
    direct = str(row.get("information_event_id") or row.get("content_key") or "")
    if direct:
        return direct
    payload = {
        "city": row.get("city"),
        "source": row.get("source"),
        "observation_time_utc": row.get("observation_time_utc")
        or row.get("source_event_ts_utc"),
        "payload_hash": row.get("payload_hash") or row.get("raw_payload_hash"),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _source_event_census(args: argparse.Namespace) -> dict[str, Any]:
    by_city: dict[str, dict[str, Any]] = {}
    events: dict[str, set[str]] = defaultdict(set)
    dates: dict[str, set[str]] = defaultdict(set)
    sources: dict[str, Counter[str]] = defaultdict(Counter)
    unclassified: Counter[str] = Counter()

    for row in _iter_jsonl(args.knmi_events):
        if row.get("city") != "Amsterdam":
            continue
        if row.get("event_role") != "new_content" or not row.get("material_state_change"):
            continue
        city = "Amsterdam"
        events[city].add(_event_identity(row))
        source = str(row.get("source") or "unknown")
        sources[city][source] += 1
        first_seen = str(row.get("first_seen_at_utc") or "")
        target_date = str(row.get("target_date") or first_seen[:10])
        if target_date:
            dates[city].add(target_date)

    relevant = set(POOLED_CITIES) | set(NEGATIVE_CONTROL_CITIES)
    for row in _iter_jsonl(args.high_frequency_observations):
        city = str(row.get("city") or "")
        if city not in relevant or city == "Amsterdam":
            continue
        role = row.get("event_role")
        status = row.get("information_event_status")
        if role is None and status is None:
            unclassified[city] += 1
            continue
        if role != "new_content" or status != "material":
            continue
        events[city].add(_event_identity(row))
        sources[city][str(row.get("source") or "unknown")] += 1
        target_date = str(row.get("target_date") or "")
        if target_date:
            dates[city].add(target_date)

    for city in (*POOLED_CITIES, *NEGATIVE_CONTROL_CITIES):
        by_city[city] = {
            "role": "pooled" if city in POOLED_CITIES else "source_basis_negative_control",
            "material_first_seen_events": len(events[city]),
            "target_dates": len(dates[city]),
            "date_min": min(dates[city]) if dates[city] else None,
            "date_max": max(dates[city]) if dates[city] else None,
            "sources": dict(sorted(sources[city].items())),
            "legacy_unclassified_rows_excluded": unclassified[city],
        }
    return by_city


def _ws_epoch_census(root: Path) -> dict[str, Any]:
    epochs: Counter[str] = Counter()
    dates: dict[str, set[str]] = defaultdict(set)
    rung_counts: dict[str, list[int]] = defaultdict(list)
    selector_versions: dict[str, set[str]] = defaultdict(set)
    for row in _iter_jsonl_root(root):
        by_city: dict[str, set[str]] = defaultdict(set)
        for metadata in (row.get("token_rows") or {}).values():
            city = str(metadata.get("city") or "")
            bracket = str(metadata.get("bracket") or "")
            if city and bracket:
                by_city[city].add(bracket)
                event_date = str(
                    metadata.get("event_date") or metadata.get("target_date") or ""
                )
                if event_date:
                    dates[city].add(event_date)
        for city, brackets in by_city.items():
            epochs[city] += 1
            rung_counts[city].append(len(brackets))
            selector_versions[city].add(str(row.get("selector_version") or ""))
    result: dict[str, Any] = {}
    for city in sorted(set(epochs) | set(POOLED_CITIES) | set(NEGATIVE_CONTROL_CITIES)):
        counts = rung_counts[city]
        result[city] = {
            "epochs": epochs[city],
            "target_dates": len(dates[city]),
            "date_values": sorted(dates[city]),
            "min_rungs": min(counts) if counts else 0,
            "max_rungs": max(counts) if counts else 0,
            "selector_versions": sorted(value for value in selector_versions[city] if value),
        }
    return result


def _ws_feature_census(root: Path) -> dict[str, Any]:
    slots: dict[tuple[str, str], set[str]] = defaultdict(set)
    full_slots: dict[tuple[str, str], set[str]] = defaultdict(set)
    dates: dict[str, set[str]] = defaultdict(set)
    rung_counts: dict[str, list[int]] = defaultdict(list)
    scopes: dict[str, Counter[str]] = defaultdict(Counter)
    rows = Counter()
    blocked = Counter()
    for row in _iter_jsonl_root(root):
        city = str(row.get("city") or "")
        event_id = str(row.get("information_event_id") or "")
        slot = str(row.get("slot") or "")
        if not city or not event_id or not slot:
            continue
        key = (city, event_id)
        rows[city] += 1
        slots[key].add(slot)
        scope = str(row.get("ladder_scope") or "missing")
        scopes[city][scope] += 1
        if scope == "full_ladder":
            full_slots[key].add(slot)
        rung_counts[city].append(int(row.get("rung_count") or 0))
        if row.get("scorable_status") != "scorable":
            blocked[city] += 1
        target_date = str(row.get("target_date") or "")
        if target_date:
            dates[city].add(target_date)
    result: dict[str, Any] = {}
    all_cities = set(POOLED_CITIES) | set(NEGATIVE_CONTROL_CITIES) | set(rows)
    for city in sorted(all_cities):
        city_events = [key for key in slots if key[0] == city]
        complete = sum(set(REQUIRED_SLOTS).issubset(slots[key]) for key in city_events)
        full_complete = sum(
            set(REQUIRED_SLOTS).issubset(full_slots[key]) for key in city_events
        )
        counts = rung_counts[city]
        result[city] = {
            "rows": rows[city],
            "events": len(city_events),
            "events_with_all_exact_slots": complete,
            "full_ladder_events_with_all_exact_slots": full_complete,
            "target_dates": len(dates[city]),
            "date_values": sorted(dates[city]),
            "min_rungs": min(counts) if counts else 0,
            "max_rungs": max(counts) if counts else 0,
            "ladder_scopes": dict(sorted(scopes[city].items())),
            "blocked_rows": blocked[city],
        }
    return result


def _knmi_capture_census(path: Path) -> dict[str, Any]:
    offsets: Counter[int] = Counter()
    dates: set[str] = set()
    events: set[str] = set()
    full_ladder = 0
    rows = 0
    for row in _iter_jsonl(path):
        rows += 1
        offset = row.get("scheduled_offset_seconds")
        if offset is not None:
            offsets[int(offset)] += 1
        target_date = str(row.get("target_date") or "")
        if target_date:
            dates.add(target_date)
        event_id = str(row.get("source_event_id") or "")
        if event_id:
            events.add(event_id)
        if int(row.get("ladder_market_count") or 0) >= 3:
            full_ladder += 1
    return {
        "rows": rows,
        "events": len(events),
        "target_dates": len(dates),
        "date_values": sorted(dates),
        "scheduled_offsets_sec": {str(k): v for k, v in sorted(offsets.items())},
        "captures_with_ladder": full_ladder,
        "registered_horizons_available": {
            str(horizon): offsets[horizon] > 0 for horizon in HORIZONS_SEC
        },
        "all_registered_horizons_available": all(
            offsets[horizon] > 0 for horizon in HORIZONS_SEC
        ),
    }


def _weather_delta_census(path: Path) -> dict[str, Any]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    malformed = 0
    for row in _iter_jsonl(path):
        city = str(row.get("city") or "")
        event_id = str(row.get("information_event_id") or "")
        if not city or not event_id:
            malformed += 1
            continue
        groups[(city, event_id)].append(row)
    by_city: dict[str, dict[str, Any]] = {}
    for city in POOLED_CITIES:
        city_groups = [rows for (row_city, _), rows in groups.items() if row_city == city]
        conserved = 0
        invalid = 0
        dates: set[str] = set()
        for rows in city_groups:
            before = [_finite(row.get("p_weather_before")) for row in rows]
            after = [_finite(row.get("p_weather_after")) for row in rows]
            delta = [_finite(row.get("delta_p_weather")) for row in rows]
            if any(value is None for value in (*before, *after, *delta)):
                invalid += 1
                continue
            before_values = np.asarray(before, dtype=float)
            after_values = np.asarray(after, dtype=float)
            delta_values = np.asarray(delta, dtype=float)
            valid = (
                abs(float(before_values.sum()) - 1.0) <= CONSERVATION_TOLERANCE
                and abs(float(after_values.sum()) - 1.0) <= CONSERVATION_TOLERANCE
                and abs(float(delta_values.sum())) <= CONSERVATION_TOLERANCE
                and np.allclose(
                    delta_values,
                    after_values - before_values,
                    atol=CONSERVATION_TOLERANCE,
                    rtol=0.0,
                )
            )
            if valid:
                conserved += 1
            else:
                invalid += 1
            target_date = str(rows[0].get("target_date") or "")
            if target_date:
                dates.add(target_date)
        by_city[city] = {
            "events": len(city_groups),
            "conserved_full_ladder_events": conserved,
            "invalid_events": invalid,
            "target_dates": len(dates),
            "date_values": sorted(dates),
        }
    return {
        "path": str(path),
        "exists": path.exists(),
        "malformed_rows": malformed,
        "by_city": by_city,
    }


def _model_contract() -> dict[str, Any]:
    return {
        "model_id": MODEL_ID,
        "pooled_universe": list(POOLED_CITIES),
        "negative_controls": list(NEGATIVE_CONTROL_CITIES),
        "event_grain": "source adapter material event first-seen receipt clock",
        "targets": {
            "primary": [f"{value}s_rung_relative_repricing" for value in HORIZONS_SEC],
            "secondary": "terminal_exact_bracket_settlement",
            "separate_heads": True,
        },
        "weather_transport": {
            "input": "full native settlement-lattice p_weather_before/p_weather_after",
            "delta": "p_weather_after - p_weather_before",
            "constraint": "sum(delta_p_weather over full ladder) == 0",
            "normalization": "Euclidean simplex projection before differencing",
        },
        "fixed_comparisons_same_rows": {
            "M0_market_level": [
                "market_probability",
                "spread",
                "depth",
                "rank",
                "signed_distance_to_mode",
                "lifecycle",
            ],
            "M1_weather_only_increment": ["M0", "delta_p_weather", "weather_source_age"],
            "M2_ladder_only_dynamics": [
                "M0",
                "neighbor_probability_ratio",
                "local_curvature",
                "pre_event_relative_movement",
                "neighbor_lead_lag",
            ],
            "M3_weather_x_ladder": [
                "M1",
                "M2",
                "delta_p_weather_x_signed_mode_distance",
                "delta_p_weather_x_neighbor_lead_lag",
                "weather_transport_x_market_transport_residual",
            ],
        },
        "validation": {
            "development_max_date": "2026-08-10",
            "expanding_date_oof": True,
            "frozen_forward": [FROZEN_FORWARD_START, FROZEN_FORWARD_END],
            "leave_one_city_out": list(POOLED_CITIES),
            "city_allowlist_selection": False,
            "selector": "none; score every contract-valid rung",
        },
        "forbidden_substitutions": [
            "binary current-bracket probability as a full ladder",
            "subscription hot strip as a full ladder",
            "nearest available capture substituted for a registered horizon",
            "Busan/Seoul in pooled fitting",
            "max/min selector reuse",
        ],
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    source_events = _source_event_census(args)
    ws_epochs = _ws_epoch_census(args.ws_epoch_root)
    ws_features = _ws_feature_census(args.ws_feature_root)
    weather_delta = _weather_delta_census(args.weather_delta)
    knmi_captures = _knmi_capture_census(args.knmi_captures)

    blockers: list[str] = []
    for city in POOLED_CITIES:
        if source_events[city]["material_first_seen_events"] == 0:
            blockers.append(f"{city}:material_event_first_seen_missing")
        delta_city = weather_delta["by_city"][city]
        if delta_city["conserved_full_ladder_events"] == 0:
            blockers.append(f"{city}:conserved_full_ladder_delta_p_weather_missing")
        feature_city = ws_features.get(city, {})
        if feature_city.get("full_ladder_events_with_all_exact_slots", 0) == 0:
            blockers.append(f"{city}:full_ladder_pre_t0_15_30_60s_market_panel_missing")
    if not knmi_captures["all_registered_horizons_available"]:
        missing = [
            value
            for value, available in knmi_captures["registered_horizons_available"].items()
            if not available
        ]
        blockers.append(
            "Amsterdam:REST_event_capture_missing_registered_horizons="
            + ",".join(missing)
        )

    pooled_ws_dates = set()
    for city in POOLED_CITIES:
        pooled_ws_dates.update(ws_features.get(city, {}).get("date_values", ()))
    if len(pooled_ws_dates) < 2:
        blockers.append("expanding_date_oof_unavailable:fewer_than_two_joint_market_dates")
    frozen_dates = {
        value
        for value in pooled_ws_dates
        if FROZEN_FORWARD_START <= value <= FROZEN_FORWARD_END
    }
    if not frozen_dates:
        blockers.append("frozen_forward_2026-08-11_2026-08-17_not_yet_observed")

    result = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "status": "BLOCKED_DATA" if blockers else "READY_TO_FIT",
        "model_contract": _model_contract(),
        "readiness": {
            "source_event_first_seen": source_events,
            "ws_subscription_epochs": ws_epochs,
            "ws_event_ladder_features": ws_features,
            "weather_ladder_delta": weather_delta,
            "amsterdam_rest_full_ladder_context": knmi_captures,
        },
        "blockers": blockers,
        "decision": (
            "Do not fit or report markout/settlement performance until the fixed pooled "
            "denominator exists; do not change live collection in this run."
            if blockers
            else "Run the pre-registered pooled expanding-date evaluator once."
        ),
        "orders_submitted": 0,
        "live_configuration_changed": False,
        "input_identity": {
            "knmi_events": {
                "path": str(args.knmi_events),
                "sha256": _sha256(args.knmi_events),
            },
            "knmi_captures": {
                "path": str(args.knmi_captures),
                "sha256": _sha256(args.knmi_captures),
            },
            "high_frequency_observations": {
                "path": str(args.high_frequency_observations),
                "size_bytes": (
                    args.high_frequency_observations.stat().st_size
                    if args.high_frequency_observations.exists()
                    else None
                ),
            },
            "ws_feature_root": str(args.ws_feature_root),
            "ws_epoch_root": str(args.ws_epoch_root),
        },
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / "readiness.json"
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "weather_research_run_manifest_v1",
        "run_id": args.run_id,
        "model_id": MODEL_ID,
        "entrypoint": str(args.entrypoint_path),
        "entrypoint_sha256": _sha256(args.entrypoint_path),
        "evaluator_path": str(Path(__file__).resolve()),
        "evaluator_sha256": _sha256(Path(__file__).resolve()),
        "status": result["status"],
        "artifact": str(output_path),
        "model_contract": result["model_contract"],
        "orders_submitted": 0,
        "notional_usd": 0.0,
    }
    (args.output_dir / "run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def add_cli_arguments(parser: argparse.ArgumentParser) -> None:
    defaults = default_input_paths()
    parser.add_argument(
        "--transport-run-id", default="pooled_transport_15_30_60_readiness_20260810"
    )
    parser.add_argument("--transport-knmi-events", type=Path, default=defaults["knmi_events"])
    parser.add_argument(
        "--transport-knmi-captures", type=Path, default=defaults["knmi_captures"]
    )
    parser.add_argument(
        "--transport-high-frequency-observations",
        type=Path,
        default=defaults["high_frequency_observations"],
    )
    parser.add_argument(
        "--transport-ws-feature-root", type=Path, default=defaults["ws_feature_root"]
    )
    parser.add_argument(
        "--transport-ws-epoch-root", type=Path, default=defaults["ws_epoch_root"]
    )
    parser.add_argument(
        "--transport-weather-delta", type=Path, default=defaults["weather_delta"]
    )
    parser.add_argument(
        "--transport-output-dir", type=Path, default=defaults["output_dir"]
    )


def namespace_from_cli(args: argparse.Namespace, *, entrypoint_path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        run_id=args.transport_run_id,
        knmi_events=args.transport_knmi_events,
        knmi_captures=args.transport_knmi_captures,
        high_frequency_observations=args.transport_high_frequency_observations,
        ws_feature_root=args.transport_ws_feature_root,
        ws_epoch_root=args.transport_ws_epoch_root,
        weather_delta=args.transport_weather_delta,
        output_dir=args.transport_output_dir,
        entrypoint_path=entrypoint_path,
    )
