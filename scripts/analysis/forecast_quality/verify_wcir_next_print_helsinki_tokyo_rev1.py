#!/usr/bin/env python3
"""Offline verifier for the Helsinki/Tokyo WCIR rev1 evidence seal."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import zipfile

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REVIEW = ROOT / "reviews/wcir_next_official_print_helsinki_tokyo_rev1"
SUPPORT = np.arange(-10, 11, dtype=int)
EPS = 1e-7
CORE_FEATURES = [
    "latest_fast_native_value", "last_official_native_value", "official_running_max",
    "fast_minus_last_official", "fast_minus_running_max", "recent_slope",
    "recent_acceleration", "path_volatility", "running_fast_max",
    "time_since_high_minutes", "pullback_depth", "reheat_strength",
    "distance_to_up_native_boundary", "distance_to_down_native_boundary",
    "local_time_sin", "local_time_cos",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_manifest(root: Path) -> None:
    full_manifest = root / "EVIDENCE_MANIFEST.json"
    manifest_path = full_manifest if full_manifest.is_file() else root / "COMPACT_EVIDENCE_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {row["path"]: row for row in manifest["entries"]}
    actual = {
        str(path.relative_to(root)): path for path in root.rglob("*")
        if path.is_file() and path.name != manifest_path.name and not path.name.endswith((".zip", ".zip.sha256"))
    }
    if set(expected) != set(actual):
        raise RuntimeError(f"manifest entry-set drift missing={sorted(set(expected)-set(actual))} extra={sorted(set(actual)-set(expected))}")
    for relative, path in actual.items():
        row = expected[relative]
        if path.stat().st_size != row["size_bytes"] or sha256(path) != row["sha256"]:
            raise RuntimeError(f"manifest identity drift: {relative}")


def dependency_weights(frame: pd.DataFrame) -> np.ndarray:
    date_group_count = frame.groupby("target_date")["official_print_group_id"].transform("nunique").to_numpy(float)
    row_count = frame.groupby(["target_date", "official_print_group_id"])["decision_vintage_id"].transform("size").to_numpy(float)
    return (1.0 / date_group_count) / row_count


def metric_rows(frame: pd.DataFrame, pmf: np.ndarray) -> pd.DataFrame:
    labels = frame["next_official_delta_native_tick"].to_numpy(int)
    indices = np.searchsorted(SUPPORT, labels)
    cdf = np.cumsum(pmf, axis=1)
    outcome_cdf = (SUPPORT[None, :] >= labels[:, None]).astype(float)
    rps = np.sum((cdf[:, :-1] - outcome_cdf[:, :-1]) ** 2, axis=1) / (len(SUPPORT) - 1)
    logloss = -np.log(np.clip(pmf[np.arange(len(frame)), indices], EPS, 1.0))
    return pd.DataFrame({
        "target_date": frame["target_date"].to_numpy(),
        "official_print_group_id": frame["official_print_group_id"].to_numpy(),
        "rps": rps, "logloss": logloss,
    })


def score(frame: pd.DataFrame, pmf: np.ndarray) -> tuple[dict[str, float], pd.DataFrame]:
    rows = metric_rows(frame, pmf)
    groups = rows.groupby(["target_date", "official_print_group_id"], as_index=False).mean(numeric_only=True)
    daily = groups.groupby("target_date").mean(numeric_only=True)
    return {"rps": float(daily["rps"].mean()), "logloss": float(daily["logloss"].mean())}, rows


def bootstrap_delta(candidate: pd.DataFrame, baseline: pd.DataFrame, reps: int = 2000) -> dict[str, object]:
    left = candidate.groupby(["target_date", "official_print_group_id"])["rps"].mean().groupby("target_date").mean()
    right = baseline.groupby(["target_date", "official_print_group_id"])["rps"].mean().groupby("target_date").mean()
    dates = sorted(set(left.index) & set(right.index))
    delta = np.array([left[day] - right[day] for day in dates], dtype=float)
    rng = np.random.default_rng(20260829)
    samples = np.array([rng.choice(delta, len(delta), replace=True).mean() for _ in range(reps)])
    return {
        "candidate_minus_baseline": float(delta.mean()),
        "ci95": [float(np.quantile(samples, 0.025)), float(np.quantile(samples, 0.975))],
    }


def verify_scores(root: Path, city: str) -> dict[str, object]:
    model = json.loads((root / f"MODEL_COMPARISON_{city.upper()}.json").read_text(encoding="utf-8"))
    historical = pd.read_csv(root / f"evidence/ROW_LEVEL_HISTORICAL_PANEL_{city.upper()}.csv.gz")
    captured = pd.read_csv(root / f"evidence/ROW_LEVEL_CAPTURED_PANEL_{city.upper()}.csv.gz")
    oof = pd.read_csv(root / f"evidence/ROW_LEVEL_OOF_PREDICTIONS_{city.upper()}.csv.gz")
    heldout = pd.read_csv(root / f"evidence/ROW_LEVEL_HELDOUT_AND_CAPTURED_PREDICTIONS_{city.upper()}.csv.gz")
    model_ids = {
        "B2": "B2_latest_fast_rounded",
        "M1": "ams_next_print_m1_ordinal_logit",
        "M2": "ams_next_print_m2_monotonic_additive",
    }
    cases = {
        "historical_expanding_oof_all_checkpoints": (
            historical.loc[historical["decision_vintage_id"].isin(oof["decision_vintage_id"])], oof
        ),
        "historical_expanding_oof_opportunity_matched": (
            historical.loc[
                historical["decision_vintage_id"].isin(oof["decision_vintage_id"])
                & historical["opportunity_matched"].astype(bool)
            ], oof.loc[oof["decision_vintage_id"].isin(
                historical.loc[historical["opportunity_matched"].astype(bool), "decision_vintage_id"]
            )],
        ),
        "historical_untouched_outer_20_dates": (
            historical.loc[historical["decision_vintage_id"].isin(
                heldout.loc[heldout["fold"].astype(str).eq("historical_outer"), "decision_vintage_id"]
            )], heldout.loc[heldout["fold"].astype(str).eq("historical_outer")],
        ),
        "captured_pit": (
            captured, heldout.loc[heldout["fold"].astype(str).eq("captured_pit")]
        ),
    }
    output: dict[str, object] = {"denominators": {}}
    for denominator, (panel, predictions) in cases.items():
        denominator_output: dict[str, object] = {"rows": len(panel), "models": {}}
        metric_by_model: dict[str, pd.DataFrame] = {}
        for short, model_id in model_ids.items():
            selected_raw = predictions.loc[predictions["model_id"].eq(model_id)]
            if selected_raw["decision_vintage_id"].duplicated().any() or len(selected_raw) != len(panel):
                raise RuntimeError(f"{city} {denominator} {short} prediction identity drift")
            selected = selected_raw.set_index("decision_vintage_id")
            selected = selected.loc[panel["decision_vintage_id"]]
            pmf = selected[[f"p_delta_{value:+d}" for value in SUPPORT]].to_numpy(float)
            if not np.isfinite(pmf).all() or (pmf < 0).any() or (pmf > 1).any() or not np.allclose(pmf.sum(axis=1), 1.0, atol=1e-10):
                raise RuntimeError(f"{city} {denominator} {short} invalid PMF")
            calculated, metric_by_model[short] = score(panel, pmf)
            expected = model["denominators"][denominator][short]
            for metric in ("rps", "logloss"):
                if not np.isclose(calculated[metric], expected[metric], atol=1e-12, rtol=0):
                    raise RuntimeError(
                        f"{city} {denominator} {short} {metric} drift "
                        f"{calculated[metric]} != {expected[metric]}"
                    )
            denominator_output["models"][short] = calculated
        for candidate in ("M1", "M2"):
            calculated_delta = bootstrap_delta(metric_by_model[candidate], metric_by_model["B2"])
            expected_delta = model["denominators"][denominator][f"{candidate}_minus_B2"]
            if not np.isclose(calculated_delta["candidate_minus_baseline"], expected_delta["candidate_minus_baseline"], atol=1e-12, rtol=0):
                raise RuntimeError(f"{city} {denominator} {candidate} bootstrap mean drift")
            if not np.allclose(calculated_delta["ci95"], expected_delta["ci95"], atol=1e-12, rtol=0):
                raise RuntimeError(f"{city} {denominator} {candidate} bootstrap CI drift")
        output["denominators"][denominator] = denominator_output
    return output


def stable_hash(value: object) -> str:
    def default(item: object) -> object:
        if isinstance(item, np.generic):
            return item.item()
        if pd.isna(item):
            return None
        return str(item)
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=default, allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def verify_feature_and_clock_contracts(root: Path, city: str) -> dict[str, int]:
    panel = pd.read_csv(root / f"evidence/ROW_LEVEL_CAPTURED_PANEL_{city.upper()}.csv.gz")
    audit = pd.read_csv(root / f"evidence/FEATURE_PARITY_ROW_AUDIT_{city.upper()}.csv")
    lineage = pd.read_csv(root / f"evidence/FEATURE_LINEAGE_{city.upper()}.csv.gz")
    eligible = audit.loc[audit["eligibility_status"].eq("OK")].copy()
    detect = pd.to_datetime(eligible["source_detect_ts_utc"], utc=True)
    decision = pd.to_datetime(eligible["decision_ready_at_utc"], utc=True)
    official = pd.to_datetime(eligible["official_first_seen_at_utc"], utc=True)
    report_gap = pd.to_numeric(eligible["next_official_report_gap_seconds"])
    if not ((detect <= decision) & (decision < official) & report_gap.gt(0) & report_gap.le(1200)).all():
        raise RuntimeError(f"{city} eligible causal/matching clock violation")
    if not eligible["frozen_archive_prefix_complete"].astype(bool).all():
        raise RuntimeError(f"{city} eligible incomplete frozen archive prefix")
    if not pd.to_datetime(eligible["source_path_start"], utc=True).eq(
        pd.to_datetime(eligible["expected_source_path_start"], utc=True)
    ).all():
        raise RuntimeError(f"{city} eligible source path start contract violation")
    expected_station = {"Helsinki": "EFHK", "Tokyo": "RJTT"}[city]
    if not panel["official_station"].astype(str).eq(expected_station).all():
        raise RuntimeError(f"{city} captured official station contract violation")
    if not panel["official_source"].astype(str).eq("aviationweather_metar").all():
        raise RuntimeError(f"{city} captured official source contract violation")
    if panel[["official_raw_row_hash", "official_raw_payload_hash", "official_raw_source_path"]].isna().any().any():
        raise RuntimeError(f"{city} captured official raw lineage missing")
    panel_by_id = panel.set_index("decision_vintage_id")
    audit_by_id = eligible.set_index("event_id")
    for decision_id, row in panel_by_id.iterrows():
        vector = json.loads(audit_by_id.loc[decision_id, "feature_vector_json"])
        if stable_hash(vector) != audit_by_id.loc[decision_id, "feature_vector_hash"]:
            raise RuntimeError(f"{city} feature vector hash drift: {decision_id}")
        for name in CORE_FEATURES:
            expected = vector[name]
            actual = row[name]
            if expected is None and pd.isna(actual):
                continue
            if expected is None or not np.isclose(float(actual), float(expected), atol=1e-12, rtol=0):
                raise RuntimeError(f"{city} panel/feature-vector drift: {decision_id} {name}")
    eligible_lineage = lineage.loc[lineage["decision_vintage_id"].astype(str).isin(panel["decision_vintage_id"].astype(str))]
    counts = eligible_lineage.groupby("decision_vintage_id")["feature_name"].nunique()
    if len(counts) != len(panel) or not counts.eq(len(CORE_FEATURES)).all():
        raise RuntimeError(f"{city} feature lineage cardinality drift")
    missing = eligible_lineage.loc[eligible_lineage["missing_reason"].eq("SOURCE_FIELD_MISSING")]
    if len(missing):
        raise RuntimeError(f"{city} eligible feature lineage has SOURCE_FIELD_MISSING")
    return {"eligible_rows": len(eligible), "lineage_rows": len(eligible_lineage)}


def verify_market_and_zero_notional(root: Path, city: str) -> dict[str, int]:
    market = pd.read_csv(root / f"evidence/MARKET_IDENTITY_RECONCILIATION_{city.upper()}.csv")
    components = [
        "event_id_archive_exact", "market_id_archive_exact", "condition_id_archive_exact",
        "token_id_archive_exact", "checkpoint_timestamp_archive_exact",
    ]
    expected_full = market[components].astype(bool).all(axis=1)
    if not expected_full.eq(market["full_archive_native_identity_reconciled"].astype(bool)).all():
        raise RuntimeError(f"{city} market full identity flag drift")
    if market.loc[market["evidence_tier"].ne("TIER_D_UNUSABLE"), "checkpoint_timestamp"].isna().any():
        raise RuntimeError(f"{city} usable market tier missing checkpoint")
    raw_path = root / f"evidence/MARKET_PRIMARY_RAW_EVIDENCE_{city.upper()}.jsonl.gz"
    with __import__("gzip").open(raw_path, "rt", encoding="utf-8") as handle:
        raw_rows = sum(1 for line in handle if line.strip())
    if raw_rows != len(market):
        raise RuntimeError(f"{city} market primary raw evidence cardinality drift")
    zero = json.loads((root / "ZERO_NOTIONAL_AUDIT.json").read_text(encoding="utf-8"))
    ledger = json.loads((root / "EXECUTION_LEDGER_RECONCILIATION.json").read_text(encoding="utf-8"))
    execution_fields = ("signals", "plans", "orders", "fills", "posted_notional_usd", "fill_cost_usd")
    if any(zero[key] != 0 for key in execution_fields) or zero["scope"] != ledger["scope"] or any(
        zero[key] != ledger["counts"][key] for key in execution_fields
    ) or any(
        zero[key] is not False for key in (
            "production_config_changed", "collector_changed", "selector_or_threshold_changed",
            "position_policy_changed", "order_path_changed", "live_or_shadow_behavior_changed",
            "frozen_forward_started",
        )
    ):
        raise RuntimeError("zero-notional audit violation")
    identity = ledger.get("canonical_db_identity") or {}
    if not identity.get("resolved_path") or not identity.get("inode") or not identity.get("size_bytes"):
        raise RuntimeError("canonical execution ledger identity missing")
    return {"events": len(market), "full_archive_native_identity_events": int(expected_full.sum())}


def verify_frozen_inputs(root: Path) -> int:
    frozen = json.loads((root / "FROZEN_INPUT_MANIFEST.json").read_text(encoding="utf-8"))
    identities: list[dict[str, object]] = []
    def walk(value: object) -> None:
        if isinstance(value, dict):
            if "sealed_relative_path" in value:
                identities.append(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(frozen)
    for row in identities:
        path = root / str(row["sealed_relative_path"])
        if not path.is_file() or path.stat().st_size != row["size_bytes"] or sha256(path) != row["sha256"]:
            raise RuntimeError(f"frozen input identity drift: {row['sealed_relative_path']}")
    return len(identities)


def verify_review(root: Path) -> dict[str, object]:
    verify_manifest(root)
    has_row_level = all(
        (root / f"evidence/ROW_LEVEL_CAPTURED_PANEL_{city.upper()}.csv.gz").is_file()
        for city in ("Helsinki", "Tokyo")
    )
    if has_row_level:
        result = {city: {
            "scores": verify_scores(root, city),
            "features_and_clocks": verify_feature_and_clock_contracts(root, city),
            "market_and_zero_notional": verify_market_and_zero_notional(root, city),
        } for city in ("Helsinki", "Tokyo")}
        result["frozen_input_identities"] = verify_frozen_inputs(root)
    else:
        result = {"score_recomputation": "NOT_INCLUDED_IN_COMPACT_REVIEW_PACKET"}
    result["manifest"] = "PASS"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--package", type=Path, help="verify ZIP, SHA256 sidecar, CRC, then extracted strict manifest/evidence")
    args = parser.parse_args()
    if args.package is None:
        result = verify_review(args.review.resolve())
    else:
        package = args.package.resolve()
        sidecar = package.with_suffix(package.suffix + ".sha256")
        if not package.is_file() or not sidecar.is_file():
            raise RuntimeError("package or SHA256 sidecar missing")
        fields = sidecar.read_text(encoding="utf-8").strip().split()
        if len(fields) != 2 or fields[1] != package.name or fields[0] != sha256(package):
            raise RuntimeError("package SHA256 sidecar drift")
        with zipfile.ZipFile(package) as archive:
            bad_member = archive.testzip()
            if bad_member is not None:
                raise RuntimeError(f"package CRC failure: {bad_member}")
            with tempfile.TemporaryDirectory(prefix="wcir_ht_package_verify_") as temporary:
                archive.extractall(temporary)
                result = verify_review(Path(temporary))
        result["package"] = {
            "path": str(package), "sha256": fields[0], "sidecar": "PASS", "zip_crc": "PASS"
        }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
