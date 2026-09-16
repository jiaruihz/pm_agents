"""Append-only, same-opportunity comparison ledger for Korea airport forwards.

The forward evaluators already serialize the model and market observations that
they consumed.  This adapter preserves every serialized observation and makes
the identity and evidence gaps explicit.  It deliberately does *not* turn a
rule admission decision into a probability: a missing historical CrossNO rule
journal remains missing rule evidence.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import shlex
from typing import Any, Iterable, Mapping, Sequence

from weather_clock_contract import parse_utc, utc_text
from .korea_city_distribution import CLASSES


LEDGER_SCHEMA_VERSION = "korea_crossno_comparison_ledger_v1"
_SEOUL_CLASSES = CLASSES
_IDENTITY_FIELDS = ("condition_id", "market_id", "token_id", "bracket", "side")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _finite_probability(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and 0.0 <= result <= 1.0 else None


def _text(value: Any) -> str | None:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    value = str(value or "").strip()
    return value or None


def _base_row(record: Mapping[str, Any], *, probability_target: str, expression: str,
              model_probability: Any, market_probability: Any, label: Any,
              source_row_index: int, source_path: Path) -> dict[str, Any]:
    decision = _text(record.get("decision_ts_utc"))
    market_decision = _text(record.get("market_decision_ts_utc")) or decision
    token = _text(record.get("token_id"))
    market_token = _text(record.get("market_token_id")) or token
    row = {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "source_city": _text(record.get("city")),
        "source_prediction_path": str(source_path),
        "source_prediction_row": source_row_index,
        "source_event_key": _text(record.get("source_event_key")),
        "checkpoint_id": _text(record.get("checkpoint_id")),
        "decision_ts_utc": decision,
        "market_decision_ts_utc": market_decision,
        "target_date": _text(record.get("target_date")),
        "horizon": _text(record.get("horizon")) or "target_date_settlement",
        "probability_target": probability_target,
        "expression": expression,
        "condition_id": _text(record.get("condition_id")),
        "market_id": _text(record.get("market_id")),
        "token_id": token,
        "market_token_id": market_token,
        "bracket": _text(record.get("bracket")),
        "side": _text(record.get("side")),
        "model_probability": _finite_probability(model_probability),
        "market_probability": _finite_probability(market_probability),
        "settlement_label": _finite_probability(label) if _finite_probability(label) in (0.0, 1.0) else None,
        # A rule is a decision baseline.  It is never a fabricated probability.
        "incumbent_rule_decision": None,
        "incumbent_rule_probability": None,
        "incumbent_rule_status": "missing_exact_rule_evidence",
        "forward_same_row_status": _text(record.get("same_row_score_status")),
        "source_candidate_status": _text(record.get("candidate_status")),
        "market_join_status": _text(record.get("market_join_status")),
        "market_decision_id": _text(record.get("market_decision_id")),
        "distribution_snapshot_id": _text(record.get("feature_book_snapshot_id_distribution")),
        "routine_rung": record.get("routine_rung"),
        "source_observation_ts_utc": record.get("source_observation_ts_utc"),
        "source_first_seen_ts_utc": record.get("source_first_seen_ts_utc"),
        "source_available_at_utc": record.get("source_available_at_utc"),
        "collector_emitted_at_utc": record.get("collector_emitted_at_utc"),
        "feature_book_available_at_utc": (
            record.get("market_available_at_utc") or decision
            if record.get("city") == "Seoul" else record.get("feature_book_available_at_utc")
        ),
        "market_clock_basis": "full_ladder_completion_decision_contract" if record.get("city") == "Seoul" else "serialized_feature_book_availability",
        "declared_probability_target": record.get("probability_target"),
    }
    return row


def _seoul_rows(record: Mapping[str, Any], *, source_row_index: int,
                source_path: Path) -> list[dict[str, Any]]:
    model = record.get("posterior_distribution")
    market = record.get("market_distribution")
    label_index = record.get("label_index")
    if not isinstance(model, list):
        model = []
    if not isinstance(market, list):
        market = []
    try:
        value = float(label_index)
        label_index = int(value) if math.isfinite(value) and value.is_integer() and 0 <= value < len(CLASSES) else None
    except (TypeError, ValueError):
        label_index = None
    rows = []
    for index, name in enumerate(_SEOUL_CLASSES):
        row = _base_row(
            record,
            probability_target="remaining_heat_settlement_class",
            expression=f"remaining_heat:{name}",
            model_probability=model[index] if index < len(model) else None,
            market_probability=market[index] if index < len(market) else None,
            label=1.0 if label_index == index else 0.0 if label_index is not None else None,
            source_row_index=source_row_index,
            source_path=source_path,
        )
        row["seoul_distribution_class_index"] = index
        row["distribution_valid"] = all(
            len(pmf) == len(CLASSES)
            and all(_finite_probability(p) is not None for p in pmf)
            and math.isclose(sum(float(p) for p in pmf), 1.0, abs_tol=1e-9)
            for pmf in (model, market)
        )
        rows.append(row)
    return rows


def _busan_row(record: Mapping[str, Any], *, source_row_index: int,
               source_path: Path) -> dict[str, Any]:
    return _base_row(
        record,
        probability_target="final_exact_bracket_no_settlement",
        expression="exact_bracket_no",
        model_probability=record.get("model_p_no"),
        market_probability=record.get("market_p_no"),
        label=record.get("label_no"),
        source_row_index=source_row_index,
        source_path=source_path,
    )


def _comparison_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(
        row.get(key)
        for key in (
            "source_city", "source_event_key", "checkpoint_id", "decision_ts_utc",
            "target_date", "horizon", "probability_target", "expression",
            "condition_id", "market_id", "token_id", "bracket", "side",
            "market_decision_id", "distribution_snapshot_id", "routine_rung",
        )
    )


def _finalize_rows(rows: list[dict[str, Any]], rule_records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    # Equivalent ISO spellings must not create distinct opportunity keys.
    for row in rows:
        for field in ("decision_ts_utc", "market_decision_ts_utc"):
            try:
                row[field] = utc_text(row[field])
            except ValueError:
                pass
    key_counts = Counter(_comparison_key(row) for row in rows)
    rules: dict[tuple, list[Mapping[str, Any]]] = {}
    for rule in rule_records:
        rule = dict(rule)
        try:
            rule["decision_ts_utc"] = utc_text(rule.get("decision_ts_utc"))
        except ValueError:
            continue
        rules.setdefault(_comparison_key(rule), []).append(rule)
    for row in rows:
        blockers: list[str] = []
        if not row["source_event_key"] or not row["checkpoint_id"] or not row["decision_ts_utc"]:
            blockers.append("missing_event_checkpoint_or_decision_identity")
        if row["decision_ts_utc"] != row["market_decision_ts_utc"]:
            blockers.append("market_decision_time_mismatch")
        if row["token_id"] != row["market_token_id"]:
            blockers.append("market_token_identity_mismatch")
        identity_fields = (
            ("market_decision_id", "distribution_snapshot_id", "routine_rung")
            if row["source_city"] == "Seoul" else _IDENTITY_FIELDS
        )
        missing_identity = [key for key in identity_fields if row.get(key) in (None, "")]
        if missing_identity:
            blockers.append("missing_market_expression_identity:" + ",".join(missing_identity))
        if row["source_city"] == "Seoul" and (not row.get("distribution_valid") or row["market_join_status"] != "scorable"):
            blockers.append("invalid_or_unavailable_market_distribution")
        if row["horizon"] != "target_date_settlement" or row["declared_probability_target"] not in (None, row["probability_target"]):
            blockers.append("probability_target_or_horizon_mismatch")
        try:
            clocks = {key: parse_utc(row.get(key), field=key) for key in (
                "source_observation_ts_utc", "source_first_seen_ts_utc", "source_available_at_utc",
                "collector_emitted_at_utc", "feature_book_available_at_utc", "decision_ts_utc",
            )}
            if not (clocks["source_observation_ts_utc"] <= clocks["source_first_seen_ts_utc"] <= clocks["source_available_at_utc"] <= clocks["collector_emitted_at_utc"] <= clocks["decision_ts_utc"]
                    and clocks["feature_book_available_at_utc"] <= clocks["decision_ts_utc"]):
                blockers.append("noncausal_source_or_book_clock")
        except ValueError:
            blockers.append("missing_or_invalid_evidence_clock")
        if row["model_probability"] is None or row["market_probability"] is None:
            blockers.append("model_or_market_probability_missing")
        if row["settlement_label"] is None:
            blockers.append("settlement_label_missing")
        if row["forward_same_row_status"] not in (None, "scorable"):
            blockers.append("forward_same_row_status:" + str(row["forward_same_row_status"]))
        if key_counts[_comparison_key(row)] > 1:
            blockers.append("duplicate_or_conflicting_comparison_key")
        row["model_market_status"] = "scorable" if not blockers else "blocked"
        row["model_market_blockers"] = blockers
        matched = rules.get(_comparison_key(row), [])
        if len(matched) == 1 and matched[0].get("policy_artifact_sha256") and matched[0].get("rule_evidence_id") and matched[0].get("incumbent_rule_decision") in ("enter", "abstain"):
            row["incumbent_rule_decision"] = matched[0]["incumbent_rule_decision"]
            row["incumbent_rule_status"] = "exact_identity_matched"
            row["incumbent_rule_evidence_id"] = matched[0]["rule_evidence_id"]
            row["incumbent_rule_policy_sha256"] = matched[0]["policy_artifact_sha256"]
        elif matched:
            row["incumbent_rule_status"] = "conflicting_or_incomplete_rule_evidence"
        row["three_arm_status"] = (
            "scorable" if not blockers and row["incumbent_rule_decision"] is not None
            else "blocked_missing_incumbent_rule_evidence" if not blockers
            else "blocked_model_market_or_identity"
        )
        row["comparison_key_sha256"] = _stable_hash(list(_comparison_key(row)))
        row["ledger_row_id"] = _stable_hash(
            [row["comparison_key_sha256"], row["source_prediction_path"], row["source_prediction_row"]]
        )
    return rows


def build_korea_comparison_ledger(
    prediction_paths: Sequence[Path],
    *, rule_records: Sequence[Mapping[str, Any]] = (),
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Normalize forward prediction files without joining or dropping rows."""
    rows: list[dict[str, Any]] = []
    input_files = []
    source_rows = 0
    for path in prediction_paths:
        path = Path(path)
        data = path.read_bytes()
        input_files.append({"path": str(path), "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)})
        with gzip.open(io.BytesIO(data), "rt", encoding="utf-8") as handle:
            for source_row_index, line in enumerate(handle, start=1):
                source_rows += 1
                record = json.loads(line)
                city = _text(record.get("city"))
                if city == "Seoul":
                    rows.extend(_seoul_rows(record, source_row_index=source_row_index, source_path=path))
                elif city == "Busan":
                    rows.append(_busan_row(record, source_row_index=source_row_index, source_path=path))
                else:
                    rows.append(_base_row(record, probability_target="unknown", expression="unknown",
                                          model_probability=None, market_probability=None, label=None,
                                          source_row_index=source_row_index, source_path=path))
    rows = _finalize_rows(rows, rule_records)
    summary = {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "input_files": input_files,
        "source_rows": source_rows,
    }
    summary["ledger_rows"] = len(rows)
    summary["model_market_status_counts"] = dict(Counter(row["model_market_status"] for row in rows))
    summary["three_arm_status_counts"] = dict(Counter(row["three_arm_status"] for row in rows))
    summary["rule_evidence_status_counts"] = dict(Counter(row["incumbent_rule_status"] for row in rows))
    summary["blocker_counts"] = dict(Counter(blocker for row in rows for blocker in row["model_market_blockers"]))
    summary["grain_by_city"] = {"Seoul": "full_ladder_completion_x_remaining_heat_class", "Busan": "serialized_exact_no_candidate"}
    summary["by_city"] = {city: dict(Counter(r["model_market_status"] for r in rows if r["source_city"] == city)) for city in sorted({r["source_city"] for r in rows if r["source_city"]})}
    return rows, summary


def _write_jsonl_gz(rows: Iterable[Mapping[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as zipped:
            with io.TextIOWrapper(zipped, encoding="utf-8") as handle:
                for row in rows:
                    handle.write(_stable_json(row) + "\n")


def write_korea_comparison_ledger(
    *, prediction_paths: Sequence[Path], output_dir: Path,
    forward_summary_path: Path | None = None,
    rule_evidence_path: Path | None = None,
) -> dict[str, Any]:
    """Write a ledger, input hashes, and compact summary to an empty-or-new dir."""
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"comparison ledger output must be empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    rule_records = []
    if rule_evidence_path is not None:
        with Path(rule_evidence_path).open() as handle:
            rule_records = [json.loads(line) for line in handle if line.strip()]
    rows, summary = build_korea_comparison_ledger(prediction_paths, rule_records=rule_records)
    if rule_evidence_path is not None:
        summary["input_files"].append({"path": str(rule_evidence_path), "sha256": _sha256_file(rule_evidence_path), "role": "exact_incumbent_rule_evidence"})
    if forward_summary_path is not None:
        forward_summary_path = Path(forward_summary_path)
        forward_summary = json.loads(forward_summary_path.read_text(encoding="utf-8"))
        summary["forward_summary_input"] = {
            "path": str(forward_summary_path),
            "sha256": _sha256_file(forward_summary_path),
        }
        summary["legacy_forward_coverage"] = {
            city: {
                "comparison_input_artifact_present": bool(
                    (city_summary.get("artifacts") or {}).get("comparison_input_path")
                ),
                "serialized_input_scope": (
                    "all_full_ladder_decisions_including_missing_labels"
                    if (city_summary.get("artifacts") or {}).get("comparison_input_path")
                    else "all_serialized_candidate_rows"
                    if city == "Busan"
                    else "legacy_same_row_scorable_prediction_slice"
                ),
                "pit_source_rows": (city_summary.get("evidence_funnel") or {}).get("pit_source_rows"),
                "same_row_scorable_rows": (city_summary.get("evidence_funnel") or {}).get("same_row_scorable_rows"),
            }
            for city, city_summary in (forward_summary.get("cities") or {}).items()
        }
        seoul_coverage = summary["legacy_forward_coverage"].get("Seoul") or {}
        # Source checkpoints and ladder decisions are different grains.
        # Only subtract the same ladder-decision denominator.
        ladder_rows = (forward_summary.get("cities", {}).get("Seoul", {}).get("evidence_funnel") or {}).get("complete_ladder_decision_rows")
        score_rows = seoul_coverage.get("same_row_scorable_rows")
        if not seoul_coverage.get("comparison_input_artifact_present") and isinstance(ladder_rows, int) and isinstance(score_rows, int):
            summary["legacy_unrepresented_opportunity_rows"] = {
                "Seoul": ladder_rows - score_rows
            }
    ledger_path = output_dir / "comparison_ledger.jsonl.gz"
    _write_jsonl_gz(rows, ledger_path)
    input_path = output_dir / "input_hashes.json"
    input_path.write_text(json.dumps(summary["input_files"], indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary.update({
        "observed_at_utc": datetime.now(tz=UTC).isoformat(),
        "ledger_path": str(ledger_path),
        "ledger_sha256": _sha256_file(ledger_path),
        "input_hashes_path": str(input_path),
        "input_hashes_sha256": _sha256_file(input_path),
        "remaining_gap": (
            "Rule coverage is reported row by row; no rule probability is fabricated. "
            + (
                "The legacy Seoul sealed run retained only its same-row scorable slice; "
                f"{summary['legacy_unrepresented_opportunity_rows']['Seoul']} ladder-decision opportunities "
                "(ladder-decision grain) are not reconstructable from its prediction artifact."
                if "legacy_unrepresented_opportunity_rows" in summary
                else ""
            )
        ),
        "reproduce_command": (
            ".venv/bin/python -m weather_model_evaluation.korea_comparison_ledger "
            + " ".join(f"--prediction {shlex.quote(str(path))}" for path in prediction_paths)
            + (f" --forward-summary {shlex.quote(str(forward_summary_path))}" if forward_summary_path else "")
            + (f" --rule-evidence {shlex.quote(str(rule_evidence_path))}" if rule_evidence_path else "")
            + f" --output-dir {shlex.quote(str(output_dir))}"
        ),
    })
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary["summary_path"] = str(summary_path)
    summary["summary_sha256"] = _sha256_file(summary_path)
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prediction", action="append", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--forward-summary", type=Path)
    parser.add_argument("--rule-evidence", type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(write_korea_comparison_ledger(prediction_paths=args.prediction, output_dir=args.output_dir, forward_summary_path=args.forward_summary, rule_evidence_path=args.rule_evidence), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
