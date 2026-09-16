#!/usr/bin/env python3
"""Evaluate the pre-registered Helsinki expression structural repair.

Model selection uses only the settled 2026-07-15..29 PIT market denominator.
The 2026-07-31 full-day replay is read only after selection as a diagnostic
regression and is never used to tune a candidate or an entry threshold.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

import joblib
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.analysis.reheat_risk import (  # noqa: E402
    research_helsinki_market_expression_v2 as v2,
)
from scripts.analysis.reheat_risk import (  # noqa: E402
    research_helsinki_market_offset_residual_v1 as base,
)
from scripts.analysis.versioned_artifact_output import (  # noqa: E402
    prepare_new_run_output,
    resolve_content_addressed_artifact,
    resolve_run_output,
)


ARTIFACT_FAMILY = "helsinki_market_expression_v3_structural_repair"
DIAGNOSTIC_SHA256 = "14da606a7185b43a45aeddb217fb1d515c0e4a9c332cdb5c12f0092c782a8f7c"
V2_ARTIFACT_SHA256 = "e5290f36ad033d1a526162c124a54106ecfb066e0b1538a3864ed0db1c841288"
STATES = base.REPLAY / "checkpoint_market_states_v2_v7.csv.gz"

RAW_SPLIT_FEATURES = {
    "forecast_future_peak_margin_vs_boundary_c",
    "forecast_current_innovation_c",
}
STRUCTURAL_BASE = tuple(
    name for name in v2.COMPACT_FEATURES if name not in RAW_SPLIT_FEATURES
)
MARGIN_FEATURE = "forecast_bias_corrected_future_peak_margin_vs_boundary_c"
TRANSITION_FEATURE = "log_minutes_since_bracket_transition"

# Fixed before reading the 2026-07-31 diagnostic labels.  A0 is the already
# frozen v2 challenger and is loaded/reproduced separately.
CANDIDATES: dict[str, dict[str, Any]] = {
    "p_structural_margin_r16": {
        "kind": "binary_offset",
        "features": (*STRUCTURAL_BASE, MARGIN_FEATURE),
        "ridge": 16.0,
    },
    "p_structural_margin_transition_r16": {
        "kind": "binary_offset",
        "features": (*STRUCTURAL_BASE, MARGIN_FEATURE, TRANSITION_FEATURE),
        "ridge": 16.0,
    },
    "p_structural_margin_transition_hgb": {
        "kind": "shallow_hgb",
        "features": ("market_logit", *STRUCTURAL_BASE, MARGIN_FEATURE, TRANSITION_FEATURE),
    },
}


def add_structural_features(rows: pd.DataFrame) -> pd.DataFrame:
    frame = rows.copy()
    states = pd.read_csv(STATES)
    states["source_first_seen_ts_utc"] = pd.to_datetime(
        states["source_first_seen_ts_utc"], utc=True
    )
    transitions = (
        states.groupby(["target_date", "official_running_max_c"], as_index=False)[
            "source_first_seen_ts_utc"
        ]
        .min()
        .rename(columns={"source_first_seen_ts_utc": "bracket_transition_ts_utc"})
    )
    frame = frame.merge(
        transitions,
        on=["target_date", "official_running_max_c"],
        how="left",
        validate="many_to_one",
    )
    decision = pd.to_datetime(frame["decision_ts_utc"], utc=True)
    age = (
        decision - pd.to_datetime(frame["bracket_transition_ts_utc"], utc=True)
    ).dt.total_seconds() / 60
    if age.isna().any() or (age < -1e-6).any():
        raise RuntimeError("invalid PIT bracket transition age")
    frame["minutes_since_bracket_transition"] = age.clip(lower=0)
    frame[TRANSITION_FEATURE] = np.log1p(frame["minutes_since_bracket_transition"])
    frame[MARGIN_FEATURE] = (
        frame["forecast_future_peak_margin_vs_boundary_c"]
        + frame["forecast_current_innovation_c"]
    )
    return frame


def expanding_predictions(
    rows: pd.DataFrame, v2_artifact_path: Path
) -> pd.DataFrame:
    dates = sorted(rows["target_date"].astype(str).unique())
    outputs = []
    v2_artifact = joblib.load(v2_artifact_path)
    for index, target_date in enumerate(dates):
        if index < v2.MIN_TRAIN_DATES:
            continue
        train = rows.loc[rows["target_date"].isin(dates[:index])].copy()
        test = rows.loc[rows["target_date"].eq(target_date)].copy()
        output = test.copy()
        output["p_frozen_old"] = base.fit_offset(
            base.state_entry_rows(train), test, base.FADE_FEATURES
        )
        # Refit A0 fold-wise to preserve the exact expanding OOF denominator.
        a0 = v2.fit_binary_offset(train, v2_artifact["features"], 16.0)
        output["p_v2_compact_r16"] = v2.predict_binary_offset(a0, test)
        for name, definition in CANDIDATES.items():
            artifact = v2.fit_candidate(train, definition)
            output[name], _ = v2.predict_candidate(artifact, test)
        outputs.append(output)
    return pd.concat(outputs, ignore_index=True)


def score_tables(predictions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    grains = {
        "checkpoint": predictions,
        "state_entry": base.state_entry_rows(predictions),
        "date_x_entry": v2.date_x_entries(predictions),
    }
    probabilities = (
        "market_probability",
        "p_break_v7",
        "p_frozen_old",
        "p_v2_compact_r16",
        *CANDIDATES,
    )
    scores: list[dict[str, Any]] = []
    bootstraps: list[dict[str, Any]] = []
    for grain_name, grain in grains.items():
        evidence_sets = {"all": grain}
        if grain_name in {"checkpoint", "date_x_entry"}:
            evidence_sets["active_first_after_source"] = grain.loc[
                grain["book_evidence"].eq("active_first_after_source")
            ]
            evidence_sets["full_ladder_before_source"] = grain.loc[
                grain["book_evidence"].eq("full_ladder_before_source")
            ]
        for evidence, subset in evidence_sets.items():
            for probability in probabilities:
                scores.append(
                    {
                        "grain": grain_name,
                        "evidence": evidence,
                        "model": probability,
                        **v2.binary_metrics(subset, probability),
                    }
                )
            for challenger in ("p_v2_compact_r16", *CANDIDATES):
                for baseline_probability in (
                    "market_probability",
                    "p_frozen_old",
                    "p_v2_compact_r16",
                ):
                    if challenger == baseline_probability:
                        continue
                    for result in v2.block_bootstrap(
                        subset, challenger, baseline_probability
                    ):
                        bootstraps.append(
                            {"grain": grain_name, "evidence": evidence, **result}
                        )
    return pd.DataFrame(scores), pd.DataFrame(bootstraps)


def selection_table(scores: pd.DataFrame) -> pd.DataFrame:
    indexed = scores.set_index(["grain", "evidence", "model"])
    rows = []
    for candidate in CANDIDATES:
        integrated_candidate = scores.loc[
            scores["model"].eq(candidate) & scores["evidence"].eq("all")
        ][["brier", "logloss"]].mean()
        integrated_v2 = scores.loc[
            scores["model"].eq("p_v2_compact_r16") & scores["evidence"].eq("all")
        ][["brier", "logloss"]].mean()
        date_x = indexed.loc[("date_x_entry", "all", candidate)]
        date_x_v2 = indexed.loc[("date_x_entry", "all", "p_v2_compact_r16")]
        active = indexed.loc[("date_x_entry", "active_first_after_source", candidate)]
        active_market = indexed.loc[
            ("date_x_entry", "active_first_after_source", "market_probability")
        ]
        gate_integrated = bool(
            (integrated_candidate <= integrated_v2).all()
        )
        gate_date_x = bool(
            date_x["brier"] <= date_x_v2["brier"]
            and date_x["logloss"] <= date_x_v2["logloss"]
        )
        gate_active = bool(
            active["brier"] <= active_market["brier"]
            and active["logloss"] <= active_market["logloss"]
        )
        rows.append(
            {
                "candidate": candidate,
                "integrated_brier": integrated_candidate["brier"],
                "integrated_logloss": integrated_candidate["logloss"],
                "date_x_brier": date_x["brier"],
                "date_x_logloss": date_x["logloss"],
                "active_date_x_brier": active["brier"],
                "active_date_x_logloss": active["logloss"],
                "gate_integrated_vs_v2": gate_integrated,
                "gate_date_x_vs_v2": gate_date_x,
                "gate_active_vs_market": gate_active,
                "historical_pass": gate_integrated and gate_date_x and gate_active,
            }
        )
    return pd.DataFrame(rows).sort_values(
        ["historical_pass", "date_x_brier", "date_x_logloss"],
        ascending=[False, True, True],
    )


def calibration_table(predictions: pd.DataFrame) -> pd.DataFrame:
    outputs = []
    bins = np.linspace(0, 1, 11)
    for model in ("market_probability", "p_v2_compact_r16", *CANDIDATES):
        frame = predictions[["target_date", "y_break", model]].copy()
        frame["bin"] = pd.cut(
            frame[model], bins=bins, include_lowest=True, right=True
        ).astype(str)
        for value, group in frame.groupby("bin", observed=True):
            daily = group.groupby("target_date").agg(
                predicted=(model, "mean"), observed=("y_break", "mean"), rows=("y_break", "size")
            )
            outputs.append(
                {
                    "model": model,
                    "probability_bin": value,
                    "rows": int(group.shape[0]),
                    "target_dates": int(group["target_date"].nunique()),
                    "mean_predicted_date_equal": float(daily["predicted"].mean()),
                    "observed_rate_date_equal": float(daily["observed"].mean()),
                }
            )
    return pd.DataFrame(outputs)


def slice_scores(predictions: pd.DataFrame) -> pd.DataFrame:
    frame = predictions.copy()
    local = pd.to_datetime(frame["decision_ts_utc"], utc=True).dt.tz_convert(
        "Europe/Helsinki"
    )
    frame["local_time_window"] = pd.cut(
        local.dt.hour + local.dt.minute / 60,
        bins=[-np.inf, 12, 15, 18, np.inf],
        labels=["before_12", "12_15", "15_18", "after_18"],
        right=False,
    ).astype(str)
    frame["peak_window"] = pd.cut(
        frame["forecast_peak_h"],
        bins=[-np.inf, 0, 1, 2, np.inf],
        labels=["peak_passed", "peak_0_1h", "peak_1_2h", "peak_gt_2h"],
        include_lowest=True,
    ).astype(str)
    outputs = []
    for dimension in ("path_state", "book_evidence", "local_time_window", "peak_window"):
        for value, group in frame.groupby(dimension, observed=True):
            for model in (
                "market_probability",
                "p_v2_compact_r16",
                *CANDIDATES,
            ):
                outputs.append(
                    {
                        "dimension": dimension,
                        "value": str(value),
                        "model": model,
                        **v2.binary_metrics(group, model),
                    }
                )
    return pd.DataFrame(outputs)


def diagnostic_rows(
    artifacts: dict[str, dict[str, Any]], diagnostic_path: Path
) -> pd.DataFrame:
    raw = []
    with diagnostic_path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if (
                row.get("model_id")
                != "helsinki_market_expression_v2_research_challenger"
                or row.get("evaluation_status") != "scored"
            ):
                continue
            raw.append(
                {
                    "target_date": row["target_date"],
                    "current_bracket": row["current_bracket"],
                    "decision_ts_utc": row["decision_ts_utc"],
                    "source_obs_ts_utc": row["source_obs_ts_utc"],
                    "market_probability": row["market_probability"],
                    "effective_cost": row["effective_cost_per_share"],
                    "p_v2_compact_r16": row["model_probability"],
                    **row["features"],
                }
            )
    frame = pd.DataFrame(raw)
    frame["decision_ts_utc"] = pd.to_datetime(frame["decision_ts_utc"], utc=True)
    frame["source_obs_ts_utc"] = pd.to_datetime(frame["source_obs_ts_utc"], utc=True)
    transitions = (
        frame.groupby(["target_date", "current_bracket"], as_index=False)[
            "source_obs_ts_utc"
        ]
        .min()
        .rename(columns={"source_obs_ts_utc": "bracket_transition_ts_utc"})
    )
    frame = frame.merge(
        transitions,
        on=["target_date", "current_bracket"],
        validate="many_to_one",
    )
    frame["minutes_since_bracket_transition"] = (
        (frame["decision_ts_utc"] - frame["bracket_transition_ts_utc"])
        .dt.total_seconds()
        .div(60)
        .clip(lower=0)
    )
    frame[TRANSITION_FEATURE] = np.log1p(
        frame["minutes_since_bracket_transition"]
    )
    frame[MARGIN_FEATURE] = (
        frame["forecast_future_peak_margin_vs_boundary_c"]
        + frame["forecast_current_innovation_c"]
    )
    market = np.clip(frame["market_probability"], 1e-6, 1 - 1e-6)
    frame["market_logit"] = np.log(market / (1 - market))
    for name, artifact in artifacts.items():
        frame[name], _ = v2.predict_candidate(artifact, frame)
        frame[f"{name}_would_enter"] = frame[name] > frame["effective_cost"]
    return frame


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    resolved_output = resolve_run_output(
        ARTIFACT_FAMILY,
        run_id=args.run_id,
        explicit_output=args.output_dir,
    )
    v2_artifact_path = resolve_content_addressed_artifact(V2_ARTIFACT_SHA256)
    diagnostic_path = resolve_content_addressed_artifact(DIAGNOSTIC_SHA256)
    rows, coverage = base.prepare_rows()
    rows = add_structural_features(v2.add_v2_features(rows))
    rows["target_date"] = rows["target_date"].astype(str)
    predictions = expanding_predictions(rows, v2_artifact_path)
    scores, bootstraps = score_tables(predictions)
    selection = selection_table(scores)
    calibration = calibration_table(predictions)
    slices = slice_scores(predictions)

    artifacts = {
        name: v2.fit_candidate(rows, definition)
        for name, definition in CANDIDATES.items()
    }
    diagnostic = diagnostic_rows(artifacts, diagnostic_path)
    diagnostic_pass = {}
    for name in CANDIDATES:
        by_bracket = diagnostic.groupby("current_bracket").apply(
            lambda group: bool(group[f"{name}_would_enter"].any()),
            include_groups=False,
        )
        diagnostic_pass[name] = bool(
            all(by_bracket.get(bracket, False) for bracket in (21, 22, 23, 24, 25))
            and not by_bracket.get(26, False)
        )

    selection["diagnostic_20260731_pass"] = selection["candidate"].map(
        diagnostic_pass
    )
    selection["shadow_eligible"] = (
        selection["historical_pass"] & selection["diagnostic_20260731_pass"]
    )
    shadow_candidate = selection.loc[selection["shadow_eligible"]]

    trade_models = ("p_v2_compact_r16", *CANDIDATES)
    trades = pd.concat(
        [base.route(predictions, model, shares=5) for model in trade_models],
        ignore_index=True,
    )
    trade_summaries = []
    oof_dates = sorted(predictions["target_date"].unique())
    for model in trade_models:
        model_trades = trades.loc[trades["model"].eq(model)]
        for evidence, subset in (
            ("all", model_trades),
            (
                "active_first_after_source",
                model_trades.loc[
                    model_trades["book_evidence"].eq("active_first_after_source")
                ],
            ),
            (
                "full_ladder_before_source",
                model_trades.loc[
                    model_trades["book_evidence"].eq("full_ladder_before_source")
                ],
            ),
        ):
            trade_summaries.append(
                {
                    "model": model,
                    "evidence": evidence,
                    **base.trade_summary(subset, oof_dates),
                }
            )

    output = prepare_new_run_output(resolved_output)
    artifact_manifest = []
    for name, artifact in artifacts.items():
        artifact.update(
            {
                "model_id": f"helsinki_market_expression_v3_{name}",
                "candidate_name": name,
                "train_rows_unique": int(len(rows)),
                "train_target_dates": int(rows["target_date"].nunique()),
                "train_start": str(rows["target_date"].min()),
                "train_end": str(rows["target_date"].max()),
                "clean_forward_start": "next_target_date_after_artifact_freeze",
                "diagnostic_20260731_used_for_selection": False,
                "historical_pass": bool(
                    selection.set_index("candidate").loc[name, "historical_pass"]
                ),
                "diagnostic_20260731_pass": diagnostic_pass[name],
                "shadow_eligible": bool(
                    selection.set_index("candidate").loc[name, "shadow_eligible"]
                ),
            }
        )
        path = output / f"{name}.joblib"
        joblib.dump(artifact, path)
        artifact_manifest.append(
            {
                "candidate": name,
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )

    scores.to_csv(output / "probability_scores.csv", index=False)
    bootstraps.to_csv(output / "target_date_bootstrap.csv", index=False)
    selection.to_csv(output / "candidate_selection.csv", index=False)
    calibration.to_csv(output / "calibration.csv", index=False)
    slices.to_csv(output / "slice_scores.csv", index=False)
    trades.to_csv(output / "trade_replay_5share.csv", index=False)
    pd.DataFrame(trade_summaries).to_csv(
        output / "trade_summaries.csv", index=False
    )
    predictions.to_csv(
        output / "oof_checkpoint_predictions.csv.gz",
        index=False,
        compression={"method": "gzip", "mtime": 0},
    )
    diagnostic.to_csv(output / "diagnostic_20260731.csv", index=False)
    summary = {
        "status": (
            "shadow_candidate_pass"
            if not shadow_candidate.empty
            else "structural_repair_failed_keep_existing_shadow"
        ),
        "coverage": coverage,
        "oof": {
            "rows": int(len(predictions)),
            "target_dates": int(predictions["target_date"].nunique()),
            "start": str(predictions["target_date"].min()),
            "end": str(predictions["target_date"].max()),
        },
        "candidate_count_k": len(CANDIDATES),
        "selection_rule": (
            "non-worse than v2 on integrated and date-X Brier/logloss, "
            "non-worse than market on active post-source date-X; 2026-07-31 "
            "diagnostic additionally must retain 21-25 and suppress false 26"
        ),
        "selected_for_shadow": (
            None if shadow_candidate.empty else shadow_candidate.iloc[0]["candidate"]
        ),
        "diagnostic_20260731_rows": int(len(diagnostic)),
        "artifacts": artifact_manifest,
        "config_changed": False,
        "shadow_restarted": False,
        "live_change": False,
        "orders_submitted": 0,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
