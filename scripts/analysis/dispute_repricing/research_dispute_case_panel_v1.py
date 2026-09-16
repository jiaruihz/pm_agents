#!/usr/bin/env python3
"""Build a structured dispute case panel and expanding-window OOS baselines."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
import sys
from typing import Any, Iterable

import numpy as np
import joblib
from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.pipeline import Pipeline


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.rule_lawyer.dispute import (  # noqa: E402
    MODEL_CATEGORICAL_FIELDS,
    MODEL_NUMERIC_FIELDS,
    build_model_feature_row,
    classify_dispute_case,
    first_clear_non_p4_rows,
    modeled_taker_fee_per_share,
    opportunity_cluster_id,
)


FEE_RATE_BY_TOPIC = {
    "sports_esports": 0.05,
    "crypto": 0.07,
    "economics_finance": 0.05,
    "politics": 0.04,
    "awards_media": 0.05,
    "other": 0.05,
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open() as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def build_case(row: dict[str, Any]) -> dict[str, Any]:
    issue = classify_dispute_case(row).to_dict()
    domains = issue.pop("source_domains")
    case = {
        "case_id": row["signal_id"],
        "market_id": row["market_id"],
        "slug": row.get("slug") or "",
        "title": row.get("title") or "",
        "dispute_ts": int(row["dispute_ts"]),
        "dispute_utc": row["dispute_utc"],
        "dispute_month": row["dispute_utc"][:7],
        "market_theme_v1": row.get("market_theme_v1") or "other",
        "proposed_binary": int(row["proposed_binary"]),
        "proposed_side": "YES" if int(row["proposed_binary"]) == 1 else "NO",
        "label_flip": 1 if row["request_settlement_class"] == "binary_flip" else 0,
        "request_settlement_class": row["request_settlement_class"],
        "entry_price": row.get("entry_price"),
        "pre_dispute_price": row.get("pre_dispute_price"),
        "entry_delay_minutes": row.get("entry_delay_minutes"),
        "proposal_to_dispute_minutes": row.get("proposal_to_dispute_minutes"),
        "log_dispute_latency": math.log1p(max(0.0, float(row.get("proposal_to_dispute_minutes") or 0))),
        "source_domains": domains,
        "top_source_domain": domains[0] if domains else "none",
        "topic": row.get("topic") or "other",
        **issue,
    }
    case["opportunity_cluster_id"] = opportunity_cluster_id(case)
    return case


def feature_matrix(rows: list[dict[str, Any]], *, include_market: bool) -> list[dict[str, Any]]:
    return [
        build_model_feature_row(
            row,
            market_price=float(row["entry_price"]) if include_market else None,
        )
        for row in rows
    ]


def build_model(*, include_market: bool) -> Pipeline:
    return Pipeline(
        [
            ("transform", DictVectorizer(sparse=True)),
            ("model", LogisticRegression(C=0.25, max_iter=2000, class_weight=None)),
        ]
    )


def expanding_oos(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    priced = sorted(
        (row for row in cases if row.get("entry_price") is not None),
        key=lambda row: (row["dispute_ts"], row["case_id"]),
    )
    months = sorted({row["dispute_month"] for row in priced if row["dispute_month"] >= "2026-01"})
    predictions: list[dict[str, Any]] = []
    for month in months:
        train = [row for row in priced if row["dispute_month"] < month]
        test = [row for row in priced if row["dispute_month"] == month]
        if len(train) < 150 or len({row["label_flip"] for row in train}) < 2 or not test:
            continue
        rules_model = build_model(include_market=False)
        combined_model = build_model(include_market=True)
        labels = [row["label_flip"] for row in train]
        rules_model.fit(feature_matrix(train, include_market=False), labels)
        combined_model.fit(feature_matrix(train, include_market=True), labels)
        rules_prob = rules_model.predict_proba(feature_matrix(test, include_market=False))[:, 1]
        combined_prob = combined_model.predict_proba(feature_matrix(test, include_market=True))[:, 1]
        for row, p_rules, p_combined in zip(test, rules_prob, combined_prob):
            price = float(row["entry_price"])
            fee_rate = FEE_RATE_BY_TOPIC.get(row["topic"], 0.05)
            fee = modeled_taker_fee_per_share(price, fee_rate)
            predictions.append(
                {
                    **row,
                    "train_markets": len(train),
                    "p_market": price,
                    "p_rules_oos": float(p_rules),
                    "p_market_rules_oos": float(p_combined),
                    "modeled_fee_per_share": fee,
                    "market_public_print_pnl": row["label_flip"] - price - fee,
                    "rules_signal_edge": float(p_rules) - price - fee,
                    "combined_signal_edge": float(p_combined) - price - fee,
                }
            )
    return predictions


def metric_block(rows: list[dict[str, Any]], probability_field: str) -> dict[str, Any]:
    labels = np.array([row["label_flip"] for row in rows], dtype=float)
    probabilities = np.clip(np.array([row[probability_field] for row in rows], dtype=float), 1e-6, 1 - 1e-6)
    return {
        "markets": len(rows),
        "brier": float(brier_score_loss(labels, probabilities)),
        "logloss": float(log_loss(labels, probabilities, labels=[0, 1])),
        "mean_probability": float(probabilities.mean()),
        "flip_rate": float(labels.mean()),
    }


def date_block_delta_ci(
    rows: list[dict[str, Any]], challenger: str, baseline: str, *, samples: int = 5000
) -> dict[str, list[float]]:
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_date[row["dispute_utc"][:10]].append(row)
    dates = sorted(by_date)
    rng = random.Random(20260812)
    brier_deltas: list[float] = []
    logloss_deltas: list[float] = []
    for _ in range(samples):
        replay = [item for _ in dates for item in by_date[rng.choice(dates)]]
        brier_delta = 0.0
        logloss_delta = 0.0
        for row in replay:
            y = float(row["label_flip"])
            pc = min(max(float(row[challenger]), 1e-6), 1 - 1e-6)
            pb = min(max(float(row[baseline]), 1e-6), 1 - 1e-6)
            brier_delta += (pc - y) ** 2 - (pb - y) ** 2
            logloss_delta += -(y * math.log(pc) + (1 - y) * math.log(1 - pc))
            logloss_delta -= -(y * math.log(pb) + (1 - y) * math.log(1 - pb))
        brier_deltas.append(brier_delta / len(replay))
        logloss_deltas.append(logloss_delta / len(replay))
    result: dict[str, list[float]] = {}
    for name, values in (("brier", brier_deltas), ("logloss", logloss_deltas)):
        values.sort()
        result[name] = [values[int((samples - 1) * q)] for q in (0.025, 0.5, 0.975)]
    return result


def date_block_mean_ci(rows: list[dict[str, Any]], field: str, *, samples: int = 5000) -> list[float] | None:
    if not rows:
        return None
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_date[row["dispute_utc"][:10]].append(row)
    dates = sorted(by_date)
    rng = random.Random(20260812)
    means: list[float] = []
    for _ in range(samples):
        replay = [item for _ in dates for item in by_date[rng.choice(dates)]]
        means.append(sum(float(row[field]) for row in replay) / len(replay))
    means.sort()
    return [means[int((samples - 1) * q)] for q in (0.025, 0.5, 0.975)]


def select_one_per_cluster(rows: list[dict[str, Any]], *, edge_field: str) -> list[dict[str, Any]]:
    """Keep the highest-edge expression from each correlated event cluster."""
    selected: dict[str, dict[str, Any]] = {}
    for row in rows:
        cluster_id = str(row["opportunity_cluster_id"])
        current = selected.get(cluster_id)
        if current is None or (float(row[edge_field]), str(row["case_id"])) > (
            float(current[edge_field]), str(current["case_id"])
        ):
            selected[cluster_id] = row
    return sorted(selected.values(), key=lambda row: (row["dispute_ts"], row["case_id"]))


def cluster_policy_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cost = sum(float(row["entry_price"]) + float(row["modeled_fee_per_share"]) for row in rows)
    pnl = sum(float(row["market_public_print_pnl"]) for row in rows)
    return {
        "selected_expressions": len(rows),
        "independent_opportunity_clusters": len({row["opportunity_cluster_id"] for row in rows}),
        "independent_utc_dates": len({row["dispute_utc"][:10] for row in rows}),
        "flip_rate": sum(row["label_flip"] for row in rows) / len(rows) if rows else None,
        "mean_public_print_net_pnl": pnl / len(rows) if rows else None,
        "public_print_roi_on_cost_plus_fee": pnl / cost if cost else None,
        "public_print_net_pnl_date_block_ci": date_block_mean_ci(rows, "market_public_print_pnl"),
    }


def oos_group_metrics(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row[field])].append(row)
    result: dict[str, Any] = {}
    for key, items in sorted(groups.items(), key=lambda pair: (-len(pair[1]), pair[0])):
        result[key] = {
            "market": metric_block(items, "p_market"),
            "rules_only": metric_block(items, "p_rules_oos"),
            "market_plus_rules": metric_block(items, "p_market_rules_oos"),
            "market_plus_rules_minus_market_date_block_ci": date_block_delta_ci(
                items, "p_market_rules_oos", "p_market"
            ),
        }
    return result


def signal_policy_diagnostics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for threshold in (0.0, 0.02, 0.05, 0.10, 0.15, 0.20):
        selected = [row for row in rows if float(row["combined_signal_edge"]) >= threshold]
        cost = sum(float(row["entry_price"]) + float(row["modeled_fee_per_share"]) for row in selected)
        pnl = sum(float(row["market_public_print_pnl"]) for row in selected)
        result[f"edge_gte_{int(threshold * 100):02d}c"] = {
            "markets": len(selected),
            "independent_utc_dates": len({row["dispute_utc"][:10] for row in selected}),
            "mean_predicted_edge": (
                sum(float(row["combined_signal_edge"]) for row in selected) / len(selected)
                if selected else None
            ),
            "mean_public_print_net_pnl": pnl / len(selected) if selected else None,
            "public_print_roi_on_cost_plus_fee": pnl / cost if cost else None,
            "public_print_net_pnl_date_block_ci": date_block_mean_ci(
                selected, "market_public_print_pnl"
            ),
        }
    return result


def fixed_candidate_policy(rows: list[dict[str, Any]]) -> dict[str, Any]:
    market_selected = [
        row for row in rows
        if row["track"] == "mechanical" and float(row["combined_signal_edge"]) >= 0.10
    ]
    selected = select_one_per_cluster(market_selected, edge_field="combined_signal_edge")
    return {
        "policy_id": "mechanical_reverse_taker_25share_v1",
        "status": "frozen_for_zero_notional_forward",
        "eligibility": "track == mechanical and model_edge_at_25share_executable_ask >= 0.10; take highest edge only per opportunity_cluster_id",
        "market_level_candidates_before_cluster_cap": len(market_selected),
        **cluster_policy_metrics(selected),
        "important_limit": "Historical expression uses first public print; forward expression must use fresh 25-share executable ask.",
    }


def slice_summary(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row[field])].append(row)
    result: dict[str, Any] = {}
    for key, items in sorted(groups.items(), key=lambda pair: (-len(pair[1]), pair[0])):
        priced = [row for row in items if row.get("entry_price") is not None]
        result[key] = {
            "cases": len(items),
            "priced": len(priced),
            "flip_rate": sum(row["label_flip"] for row in items) / len(items),
            "mean_public_print": (
                sum(float(row["entry_price"]) for row in priced) / len(priced) if priced else None
            ),
            "gross_public_print_edge": (
                sum(row["label_flip"] - float(row["entry_price"]) for row in priced) / len(priced)
                if priced else None
            ),
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--signals",
        default="runtime/dispute_repricing/dispute_repricing_v1/signals.jsonl",
    )
    parser.add_argument(
        "--out-dir",
        default="runtime/dispute_repricing/dispute_case_panel_v1",
    )
    args = parser.parse_args()

    signal_path = Path(args.signals)
    out_dir = Path(args.out_dir)
    clear = first_clear_non_p4_rows(read_jsonl(signal_path))
    cases = [build_case(row) for row in clear]
    predictions = expanding_oos(cases)
    final_train = [row for row in cases if row.get("entry_price") is not None]
    final_model = build_model(include_market=True)
    final_model.fit(
        feature_matrix(final_train, include_market=True),
        [row["label_flip"] for row in final_train],
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / "market_plus_rules_model.joblib"
    joblib.dump(final_model, model_path)

    summary = {
        "schema_version": "dispute_case_panel_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "denominator_scope": {
            "input": str(signal_path.resolve()),
            "unit": "first clear non-P4 dispute per unique Polymarket market",
            "cases": len(cases),
            "priced_cases": sum(row.get("entry_price") is not None for row in cases),
            "oos_method": "expanding monthly; first test month 2026-01; structured PIT-safe features",
            "oos_cases": len(predictions),
            "risk_unit": "one selected expression per conservative underlying opportunity cluster",
        },
        "case_routes": dict(Counter(row["track"] for row in cases)),
        "by_track": slice_summary(cases, "track"),
        "by_mechanism": slice_summary(cases, "mechanism"),
        "oos_2026": {
            "market": metric_block(predictions, "p_market") if predictions else None,
            "rules_only": metric_block(predictions, "p_rules_oos") if predictions else None,
            "market_plus_rules": metric_block(predictions, "p_market_rules_oos") if predictions else None,
            "market_plus_rules_minus_market_date_block_ci": (
                date_block_delta_ci(predictions, "p_market_rules_oos", "p_market") if predictions else None
            ),
            "by_track": oos_group_metrics(predictions, "track") if predictions else {},
            "by_mechanism": oos_group_metrics(predictions, "mechanism") if predictions else {},
            "signal_policy_diagnostics": signal_policy_diagnostics(predictions) if predictions else {},
            "fixed_candidate_policy": fixed_candidate_policy(predictions) if predictions else {},
        },
        "model_artifact": {
            "path": str(model_path.resolve()),
            "training_cases": len(final_train),
            "training_max_dispute_utc": max(row["dispute_utc"] for row in final_train),
            "categorical_fields": list(MODEL_CATEGORICAL_FIELDS),
            "numeric_fields": list(MODEL_NUMERIC_FIELDS) + ["market_logit"],
        },
        "limitations": [
            "Entry price is the first public print, not an executable ask.",
            "Historical rules/source snapshots were reconstructed after the fact and are not a complete PIT evidence archive.",
            "The classifier creates research routes, not adjudication truth labels.",
            "Semantic evidence and UMA rationale are not yet available historically, so this is a structured-feature baseline only.",
            "Sports sibling markets are clustered by the Gamma slug prefix through YYYY-MM-DD; themes without a stable event identifier remain market-scoped.",
        ],
    }

    write_jsonl(out_dir / "cases.jsonl", cases)
    write_jsonl(out_dir / "predictions_oos.jsonl", predictions)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
