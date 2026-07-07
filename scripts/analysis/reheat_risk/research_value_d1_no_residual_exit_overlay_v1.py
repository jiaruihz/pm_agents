#!/usr/bin/env python3
"""Backtest residual exit overlay for value_d1_no.

Entry denominator:
- d1 NO first-cross rows.
- p_leg_win_physical_expanding_v1 - executable entry cost >= threshold.

Exit denominator:
- later PIT paper-snapshot rows for the same city/date/bracket/token side.
- if the taker-sell NO bid net of Weather fee is greater than the updated
  expanding p_leg_win_now, exit at the first such snapshot.

Settlement is only used after the entry/exit decision has been generated.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.reheat_risk.research_late_window_residual_calibrated_features_v1 import (  # noqa: E402
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
    add_model_features,
    make_model,
)


HEATING_DIR = ROOT / "docs/analysis/2026-07/generated/late_window_residual_heating_done_v1"
POLICY_DIR = ROOT / "docs/analysis/2026-07/generated/late_window_residual_p_leg_win_policy_v1"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/value_d1_no_residual_exit_overlay_v1"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-07-value-d1-no-residual-exit-overlay-v1.md"
FEE_RATE = 0.05
MIN_EXIT_BID = 0.05
MIN_EXIT_DEPTH_SHARES = 5.0
EDGE_THRESHOLDS = [0.0, 0.01, 0.02, 0.03]


def fee_per_share(price: float) -> float:
    if not math.isfinite(price):
        return math.nan
    return round(FEE_RATE * price * (1.0 - price), 5)


def score_raw_rows_expanding(raw: pd.DataFrame, first_cross: pd.DataFrame) -> pd.DataFrame:
    """Score all raw PIT rows with models trained only on earlier target dates."""

    train_base = add_model_features(first_cross.copy())
    train_base = train_base[train_base["win"].notna()].copy()
    train_base["win"] = pd.to_numeric(train_base["win"], errors="coerce")
    raw_features = add_model_features(raw.copy())
    preds = pd.Series(np.nan, index=raw.index, dtype=float)
    for target_date in sorted(raw["target_date"].astype(str).unique()):
        train_mask = train_base["target_date"].astype(str).lt(target_date)
        test_mask = raw["target_date"].astype(str).eq(target_date)
        train = train_base[train_mask].dropna(subset=["win"]).copy()
        if len(train) < 80 or train["win"].nunique() < 2:
            continue
        model = make_model()
        model.fit(train[NUMERIC_FEATURES + CATEGORICAL_FEATURES], train["win"].astype(int))
        preds.loc[test_mask] = model.predict_proba(raw_features.loc[test_mask, NUMERIC_FEATURES + CATEGORICAL_FEATURES])[:, 1]
    out = raw.copy()
    out["p_leg_win_now_expanding_v1"] = preds
    return out


def entry_rows(threshold: float) -> pd.DataFrame:
    selected = pd.read_csv(POLICY_DIR / "selected_rows.csv")
    selected["target_date"] = selected["target_date"].astype(str)
    selected["snapshot_ts_utc"] = selected["snapshot_ts_utc"].astype(str)
    selected["edge_threshold"] = pd.to_numeric(selected["edge_threshold"], errors="coerce")
    selected["win"] = pd.to_numeric(selected["win"], errors="coerce")
    selected["entry_price"] = pd.to_numeric(selected["entry_price"], errors="coerce")
    selected["cost_per_share"] = pd.to_numeric(selected["cost_per_share"], errors="coerce")
    selected["model_edge_per_share"] = pd.to_numeric(selected["model_edge_per_share"], errors="coerce")
    out = selected[
        selected["pred_name"].astype(str).eq("expanding")
        & selected["scope"].astype(str).eq("forward_expanding")
        & selected["leg"].astype(str).eq("d1_no")
        & selected["edge_threshold"].eq(float(threshold))
        & selected["win"].notna()
    ].copy()
    return out.sort_values(["target_date", "city", "snapshot_ts_utc", "bracket"]).reset_index(drop=True)


def load_no_book_rows(raw: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for snapshot_file in sorted(raw["snapshot_file"].dropna().astype(str).unique()):
        path = ROOT / snapshot_file
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        snapshot_ts = str(payload.get("ts_utc") or "")
        ts_beijing = str(payload.get("ts_beijing") or "")
        for rec in payload.get("records") or []:
            if not isinstance(rec, dict):
                continue
            target_date = str(rec.get("target_date") or rec.get("event_date") or "")
            city_date = str(rec.get("city_local_date_at_snapshot") or "")
            if city_date != target_date:
                continue
            rows.append(
                {
                    "snapshot_file": snapshot_file,
                    "snapshot_ts_utc": snapshot_ts,
                    "ts_beijing": ts_beijing,
                    "city": str(rec.get("city") or ""),
                    "target_date": target_date,
                    "bracket": str(rec.get("bracket") or ""),
                    "no_best_bid": pd.to_numeric(rec.get("no_best_bid"), errors="coerce"),
                    "no_bid_size": pd.to_numeric(rec.get("no_bid_size"), errors="coerce"),
                    "no_depth_bid_5c": pd.to_numeric(rec.get("no_depth_bid_5c"), errors="coerce"),
                    "question": str(rec.get("question") or ""),
                }
            )
    return pd.DataFrame(rows)


def build_exit_lookup(scored_raw: pd.DataFrame, no_books: pd.DataFrame) -> dict[tuple[str, str, str], pd.DataFrame]:
    raw = scored_raw.copy()
    raw["target_date"] = raw["target_date"].astype(str)
    raw["p_no_hold_now_expanding_v1"] = np.where(
        raw["outcome"].astype(str).eq("yes"),
        1.0 - pd.to_numeric(raw["p_leg_win_now_expanding_v1"], errors="coerce"),
        pd.to_numeric(raw["p_leg_win_now_expanding_v1"], errors="coerce"),
    )
    prob = raw[
        [
            "city",
            "target_date",
            "bracket",
            "snapshot_ts_utc",
            "leg",
            "outcome",
            "p_leg_win_now_expanding_v1",
            "p_no_hold_now_expanding_v1",
        ]
    ].copy()
    books = no_books.copy()
    books["target_date"] = books["target_date"].astype(str)
    books = books.merge(prob, on=["city", "target_date", "bracket", "snapshot_ts_utc"], how="left")
    books["snapshot_dt"] = pd.to_datetime(books["snapshot_ts_utc"], utc=True, errors="coerce")
    books["no_best_bid"] = pd.to_numeric(books["no_best_bid"], errors="coerce")
    books["no_bid_depth_for_exit"] = books[["no_bid_size", "no_depth_bid_5c"]].max(axis=1, skipna=True)
    books["exit_fee_per_share"] = books["no_best_bid"].apply(fee_per_share)
    books["exit_net_per_share"] = books["no_best_bid"] - books["exit_fee_per_share"]
    raw = books[
        books["snapshot_dt"].notna()
        & books["no_best_bid"].ge(MIN_EXIT_BID)
        & books["no_bid_depth_for_exit"].fillna(0).ge(MIN_EXIT_DEPTH_SHARES)
        & books["p_no_hold_now_expanding_v1"].notna()
    ].copy()
    return {
        (str(city), str(target_date), str(bracket)): g.sort_values("snapshot_dt").reset_index(drop=True)
        for (city, target_date, bracket), g in raw.groupby(["city", "target_date", "bracket"], dropna=False)
    }


def replay(threshold: float, exit_lookup: dict[tuple[str, str, str], pd.DataFrame]) -> pd.DataFrame:
    entries = entry_rows(threshold)
    rows: list[dict[str, Any]] = []
    for entry in entries.itertuples(index=False):
        entry_dt = pd.to_datetime(getattr(entry, "snapshot_ts_utc"), utc=True, errors="coerce")
        key = (str(getattr(entry, "city")), str(getattr(entry, "target_date")), str(getattr(entry, "bracket")))
        candidates = exit_lookup.get(key, pd.DataFrame())
        exit_row = None
        if not candidates.empty and pd.notna(entry_dt):
            later = candidates[candidates["snapshot_dt"].gt(entry_dt)].copy()
            later = later[later["exit_net_per_share"].ge(later["p_no_hold_now_expanding_v1"])].copy()
            if not later.empty:
                exit_row = later.iloc[0]

        cost = float(getattr(entry, "cost_per_share"))
        hold_pnl = float(getattr(entry, "win")) - cost
        out: dict[str, Any] = {
            "city": str(getattr(entry, "city")),
            "target_date": str(getattr(entry, "target_date")),
            "entry_snapshot_ts_utc": str(getattr(entry, "snapshot_ts_utc")),
            "leg": str(getattr(entry, "leg")),
            "bracket": str(getattr(entry, "bracket")),
            "entry_price": float(getattr(entry, "entry_price")),
            "entry_cost_per_share": cost,
            "entry_p_model": float(getattr(entry, "p_model")),
            "entry_model_edge_per_share": float(getattr(entry, "model_edge_per_share")),
            "win": float(getattr(entry, "win")),
            "hold_pnl_per_share": hold_pnl,
            "exit_triggered": exit_row is not None,
        }
        if exit_row is not None:
            exit_net = float(exit_row["exit_net_per_share"])
            out.update(
                {
                    "exit_snapshot_ts_utc": str(exit_row["snapshot_ts_utc"]),
                    "exit_ts_beijing": str(exit_row.get("ts_beijing", "")),
                    "exit_no_bid": float(exit_row["no_best_bid"]),
                    "exit_fee_per_share": float(exit_row["exit_fee_per_share"]),
                    "exit_net_per_share": exit_net,
                    "exit_p_leg_win_now": float(exit_row["p_leg_win_now_expanding_v1"]),
                    "exit_p_no_hold_now": float(exit_row["p_no_hold_now_expanding_v1"]),
                    "exit_pnl_per_share": exit_net - cost,
                    "exit_minutes_after_entry": (
                        (pd.Timestamp(exit_row["snapshot_dt"]) - entry_dt).total_seconds() / 60.0
                        if pd.notna(entry_dt)
                        else math.nan
                    ),
                }
            )
        else:
            out.update(
                {
                    "exit_snapshot_ts_utc": None,
                    "exit_ts_beijing": None,
                    "exit_no_bid": math.nan,
                    "exit_fee_per_share": math.nan,
                    "exit_net_per_share": math.nan,
                    "exit_p_leg_win_now": math.nan,
                    "exit_p_no_hold_now": math.nan,
                    "exit_pnl_per_share": hold_pnl,
                    "exit_minutes_after_entry": math.nan,
                }
            )
        out["overlay_delta_per_share"] = out["exit_pnl_per_share"] - hold_pnl
        out["threshold"] = float(threshold)
        rows.append(out)
    return pd.DataFrame(rows)


def block_ci_delta(df: pd.DataFrame, reps: int = 3000, seed: int = 20260707) -> tuple[float | None, float | None]:
    if df.empty or df["target_date"].nunique() < 2:
        return None, None
    daily = df.groupby("target_date", as_index=False).agg(
        cost=("entry_cost_per_share", "sum"),
        hold_pnl=("hold_pnl_per_share", "sum"),
        exit_pnl=("exit_pnl_per_share", "sum"),
    )
    values = daily[["cost", "hold_pnl", "exit_pnl"]].to_numpy(dtype=float)
    rng = np.random.default_rng(seed)
    deltas: list[float] = []
    for _ in range(reps):
        sample = values[rng.integers(0, len(values), len(values))]
        cost = float(sample[:, 0].sum())
        if cost > 0:
            deltas.append(float((sample[:, 2].sum() - sample[:, 1].sum()) / cost))
    if not deltas:
        return None, None
    return float(np.quantile(deltas, 0.025)), float(np.quantile(deltas, 0.975))


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for threshold, g in df.groupby("threshold", dropna=False):
        cost = float(g["entry_cost_per_share"].sum())
        hold_pnl = float(g["hold_pnl_per_share"].sum())
        exit_pnl = float(g["exit_pnl_per_share"].sum())
        ci_low, ci_high = block_ci_delta(g)
        rows.append(
            {
                "threshold": float(threshold),
                "rows": int(len(g)),
                "active_dates": int(g["target_date"].nunique()),
                "cities": int(g["city"].nunique()),
                "entry_cost": cost,
                "hold_pnl": hold_pnl,
                "hold_roi": hold_pnl / cost if cost else math.nan,
                "exit_overlay_pnl": exit_pnl,
                "exit_overlay_roi": exit_pnl / cost if cost else math.nan,
                "delta_pnl": exit_pnl - hold_pnl,
                "delta_roi": (exit_pnl - hold_pnl) / cost if cost else math.nan,
                "delta_roi_ci_low": ci_low,
                "delta_roi_ci_high": ci_high,
                "entry_hit_rate": float(g["win"].mean()),
                "exit_rate": float(g["exit_triggered"].mean()),
                "rescued_loser_rate": float(((g["win"].eq(0)) & g["exit_triggered"]).mean()),
                "sold_winner_rate": float(((g["win"].eq(1)) & g["exit_triggered"]).mean()),
                "avg_exit_minutes": float(g.loc[g["exit_triggered"], "exit_minutes_after_entry"].mean())
                if g["exit_triggered"].any()
                else math.nan,
            }
        )
    return pd.DataFrame(rows)


def daily_summary(df: pd.DataFrame) -> pd.DataFrame:
    out = (
        df.groupby(["threshold", "target_date"], as_index=False)
        .agg(
            rows=("city", "count"),
            cities=("city", "nunique"),
            cost=("entry_cost_per_share", "sum"),
            hold_pnl=("hold_pnl_per_share", "sum"),
            exit_pnl=("exit_pnl_per_share", "sum"),
            exits=("exit_triggered", "sum"),
            wins=("win", "sum"),
        )
        .assign(
            hold_roi=lambda d: d["hold_pnl"] / d["cost"],
            exit_overlay_roi=lambda d: d["exit_pnl"] / d["cost"],
            delta_pnl=lambda d: d["exit_pnl"] - d["hold_pnl"],
            delta_roi=lambda d: (d["exit_pnl"] - d["hold_pnl"]) / d["cost"],
        )
    )
    return out


def markdown_table(df: pd.DataFrame, cols: list[str], max_rows: int = 40) -> str:
    if df.empty:
        return "_empty_"
    sub = df[cols].head(max_rows).copy()
    for col in sub.columns:
        if pd.api.types.is_float_dtype(sub[col]):
            sub[col] = sub[col].map(lambda x: "" if pd.isna(x) else f"{x:.4f}")
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for row in sub.itertuples(index=False, name=None):
        lines.append("| " + " | ".join(str(x) for x in row) + " |")
    return "\n".join(lines)


def write_report(summary: pd.DataFrame, daily: pd.DataFrame, detail: pd.DataFrame) -> None:
    base = summary[summary["threshold"].eq(0.0)].iloc[0].to_dict() if not summary.empty else {}
    failures = detail[(detail["threshold"].eq(0.0)) & (detail["overlay_delta_per_share"].lt(0))].copy()
    winners_sold = detail[(detail["threshold"].eq(0.0)) & detail["win"].eq(1.0) & detail["exit_triggered"]].copy()
    lines = [
        "# value_d1_no Residual Exit Overlay v1",
        "",
        "Status: `snapshot`",
        "",
        "## Verdict",
        (
            f"On the clean non-leaky forward `value_d1_no` denominator at edge>=0, hold ROI is "
            f"{base.get('hold_roi', math.nan):.1%} and the residual exit overlay ROI is "
            f"{base.get('exit_overlay_roi', math.nan):.1%}; delta is {base.get('delta_roi', math.nan):.1%} "
            f"with date-block CI [{base.get('delta_roi_ci_low', math.nan):.1%}, {base.get('delta_roi_ci_high', math.nan):.1%}]. "
            "The edge>=0 point estimate is slightly better than hold, but it is not robust: stricter entry thresholds turn negative and the CI crosses 0. "
            "The overlay mostly sells eventual winners rather than rescuing losers, so keep it as shadow telemetry until the exit model can distinguish true thesis invalidation from profitable repricing."
        ),
        "",
        "## Data And Rules",
        "- Entry: `pred_name=expanding`, `scope=forward_expanding`, `leg=d1_no`, first-cross, edge threshold sweep.",
        "- Exit: same city/date/bracket, later PIT snapshot, executable `no_best_bid`, depth >= 5 shares.",
        "- Exit trigger: `exit_net_bid_after_taker_fee >= p_no_hold_now_expanding_v1`.",
        "- Settlement label is only used after decisions to compare hold vs exit.",
        "",
        "## Summary",
        markdown_table(
            summary,
            [
                "threshold",
                "rows",
                "active_dates",
                "cities",
                "hold_roi",
                "exit_overlay_roi",
                "delta_roi",
                "delta_roi_ci_low",
                "delta_roi_ci_high",
                "entry_hit_rate",
                "exit_rate",
                "rescued_loser_rate",
                "sold_winner_rate",
                "avg_exit_minutes",
            ],
        ),
        "",
        "## Daily edge>=0",
        markdown_table(
            daily[daily["threshold"].eq(0.0)],
            ["target_date", "rows", "cities", "hold_roi", "exit_overlay_roi", "delta_roi", "exits", "wins"],
            max_rows=20,
        ),
        "",
        "## Negative Overlay Cases edge>=0",
        markdown_table(
            failures.sort_values("overlay_delta_per_share"),
            [
                "city",
                "target_date",
                "bracket",
                "entry_price",
                "win",
                "hold_pnl_per_share",
                "exit_no_bid",
                "exit_p_no_hold_now",
                "exit_pnl_per_share",
                "overlay_delta_per_share",
                "exit_ts_beijing",
            ],
            max_rows=30,
        ),
        "",
        "## Sold Winners edge>=0",
        markdown_table(
            winners_sold.sort_values("overlay_delta_per_share"),
            [
                "city",
                "target_date",
                "bracket",
                "entry_price",
                "exit_no_bid",
                "exit_p_no_hold_now",
                "overlay_delta_per_share",
                "exit_ts_beijing",
            ],
            max_rows=30,
        ),
        "",
        "## Contract Read",
        "significance=FAIL because delta CI crosses/leans below 0; baseline=FAIL versus hold-to-settlement on the same entries; forward=LOW_SAMPLE with 6 active dates; conclusion=shadow_only_exit_telemetry.",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    first_cross = pd.read_csv(HEATING_DIR / "first_cross_rows.csv")
    raw = pd.read_csv(HEATING_DIR / "raw_pit_leg_rows.csv")
    scored_raw = score_raw_rows_expanding(raw, first_cross)
    no_books = load_no_book_rows(raw)
    exit_lookup = build_exit_lookup(scored_raw, no_books)
    detail = pd.concat([replay(threshold, exit_lookup) for threshold in EDGE_THRESHOLDS], ignore_index=True)
    summary = summarize(detail)
    daily = daily_summary(detail)

    scored_raw.to_csv(OUT_DIR / "raw_rows_scored_expanding.csv", index=False)
    detail.to_csv(OUT_DIR / "exit_overlay_detail.csv", index=False)
    summary.to_csv(OUT_DIR / "summary.csv", index=False)
    daily.to_csv(OUT_DIR / "daily_summary.csv", index=False)
    payload = {
        "summary": summary.to_dict("records"),
        "outputs": {
            "detail": str((OUT_DIR / "exit_overlay_detail.csv").relative_to(ROOT)),
            "summary": str((OUT_DIR / "summary.csv").relative_to(ROOT)),
            "daily": str((OUT_DIR / "daily_summary.csv").relative_to(ROOT)),
            "report": str(OUT_MD.relative_to(ROOT)),
        },
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_report(summary, daily, detail)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
