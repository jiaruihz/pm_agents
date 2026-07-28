#!/usr/bin/env python3
"""Temporal falsification of a rain/convective selector for HeadA tickets.

The historical weather labels are hypothesis-generation evidence only because
their forecast vintages are not fully PIT.  The current HeadA shadow rows are
decision-time PIT and remain the frozen forward denominator.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
INPUT_DIR = (
    ROOT
    / "docs/analysis/2026-07/generated/heada_multisource_city_regime_v1"
)
OUTPUT_DIR = (
    ROOT
    / "docs/analysis/2026-07/generated/heada_rain_convective_shadow_v1"
)
HISTORICAL_CSV = INPUT_DIR / "historical_enriched.csv"
CURRENT_CSV = INPUT_DIR / "current_enriched.csv"
LIFECYCLE_CSV = (
    ROOT
    / "docs/analysis/2026-07/generated/heada_market_anchored_lifecycle_v1"
    / "exact_ticket_checkpoint_rows.csv"
)
SEED = 20260728
BOOTSTRAP_DRAWS = 20_000


def roi(frame: pd.DataFrame) -> float:
    cost = float(frame["cost_eval"].sum())
    return float(frame["pnl_eval"].sum() / cost) if cost > 0 else float("nan")


def score_row(block: str, cohort: str, frame: pd.DataFrame, all_dates: int) -> dict:
    return {
        "block": block,
        "cohort": cohort,
        "rows": int(len(frame)),
        "active_dates": int(frame["target_date"].nunique()),
        "all_block_dates": int(all_dates),
        "rows_per_all_block_date": float(len(frame) / all_dates),
        "wins": int(frame["win"].sum()),
        "win_rate": float(frame["win"].mean()) if len(frame) else float("nan"),
        "cost": float(frame["cost_eval"].sum()),
        "pnl": float(frame["pnl_eval"].sum()),
        "roi": roi(frame),
        "avg_ask": float(frame["ask"].mean()) if len(frame) else float("nan"),
    }


def block_bootstrap(frame: pd.DataFrame, block: str, rng: np.random.Generator) -> dict:
    tagged = frame.assign(selected=frame["weather_regime"].eq("rain_convective"))
    daily = (
        tagged.groupby(["target_date", "selected"])[["cost_eval", "pnl_eval"]]
        .sum()
        .unstack("selected", fill_value=0.0)
    )
    for metric in ("cost_eval", "pnl_eval"):
        for selected in (False, True):
            if (metric, selected) not in daily:
                daily[(metric, selected)] = 0.0
    values = np.column_stack(
        [
            daily[("cost_eval", True)],
            daily[("pnl_eval", True)],
            daily[("cost_eval", False)],
            daily[("pnl_eval", False)],
        ]
    )
    indices = rng.integers(0, len(values), size=(BOOTSTRAP_DRAWS, len(values)))
    sampled = values[indices].sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        selected_roi = sampled[:, 1] / sampled[:, 0]
        complement_roi = sampled[:, 3] / sampled[:, 2]
        uplift = selected_roi - complement_roi
    selected_roi = selected_roi[np.isfinite(selected_roi)]
    uplift = uplift[np.isfinite(uplift)]
    return {
        "block": block,
        "draws": BOOTSTRAP_DRAWS,
        "target_dates": int(len(values)),
        "selected_roi_ci_low": float(np.quantile(selected_roi, 0.025)),
        "selected_roi_ci_high": float(np.quantile(selected_roi, 0.975)),
        "roi_uplift_vs_complement_ci_low": float(np.quantile(uplift, 0.025)),
        "roi_uplift_vs_complement_ci_high": float(np.quantile(uplift, 0.975)),
    }


def bootstrap_date_mean(
    frame: pd.DataFrame,
    value_column: str,
    rng: np.random.Generator,
) -> tuple[float, float]:
    daily = frame.groupby("target_date")[value_column].mean().to_numpy()
    indices = rng.integers(0, len(daily), size=(BOOTSTRAP_DRAWS, len(daily)))
    sampled = daily[indices].mean(axis=1)
    return float(np.quantile(sampled, 0.025)), float(np.quantile(sampled, 0.975))


def market_anchor_scorecard(rng: np.random.Generator) -> pd.DataFrame:
    if not LIFECYCLE_CSV.exists():
        return pd.DataFrame()
    rows = pd.read_csv(LIFECYCLE_CSV)
    rows = rows[rows["weather_regime"].eq("rain_convective")].copy()
    score_rows: list[dict] = []
    for checkpoint_hour, frame in rows.groupby("checkpoint_hour_local"):
        frame = frame.dropna(
            subset=["win", "proxy_price_t", "weather_probability_delta"]
        ).copy()
        outcome = frame["win"].astype(float).to_numpy()
        market = frame["proxy_price_t"].to_numpy()
        candidate = np.clip(
            market + frame["weather_probability_delta"].to_numpy(), 0.001, 0.999
        )
        frame["brier_delta"] = (candidate - outcome) ** 2 - (market - outcome) ** 2
        frame["logloss_delta"] = (
            -(outcome * np.log(candidate) + (1.0 - outcome) * np.log(1.0 - candidate))
            + outcome * np.log(market)
            + (1.0 - outcome) * np.log(1.0 - market)
        )
        brier_ci = bootstrap_date_mean(frame, "brier_delta", rng)
        logloss_ci = bootstrap_date_mean(frame, "logloss_delta", rng)
        base = {
            "checkpoint_hour_local": int(checkpoint_hour),
            "metric": "terminal_probability_gamma_1",
            "horizon_min": 0,
            "rows": int(len(frame)),
            "dates": int(frame["target_date"].nunique()),
            "tickets": int(frame["_row_id"].nunique()),
            "wins": int(frame["win"].sum()),
            "brier_delta": float(frame["brier_delta"].mean()),
            "brier_delta_ci_low": brier_ci[0],
            "brier_delta_ci_high": brier_ci[1],
            "logloss_delta": float(frame["logloss_delta"].mean()),
            "logloss_delta_ci_low": logloss_ci[0],
            "logloss_delta_ci_high": logloss_ci[1],
            "mean_signed_move": float("nan"),
            "signed_move_ci_low": float("nan"),
            "signed_move_ci_high": float("nan"),
        }
        score_rows.append(base)
        for horizon in (60, 180):
            price_column = f"proxy_price_plus_{horizon}m"
            lead = frame.dropna(subset=[price_column]).copy()
            lead["signed_move"] = np.sign(lead["weather_probability_delta"]) * (
                lead[price_column] - lead["proxy_price_t"]
            )
            move_ci = bootstrap_date_mean(lead, "signed_move", rng)
            score_rows.append(
                {
                    "checkpoint_hour_local": int(checkpoint_hour),
                    "metric": "price_lead_lag",
                    "horizon_min": horizon,
                    "rows": int(len(lead)),
                    "dates": int(lead["target_date"].nunique()),
                    "tickets": int(lead["_row_id"].nunique()),
                    "wins": int(lead["win"].sum()),
                    "brier_delta": float("nan"),
                    "brier_delta_ci_low": float("nan"),
                    "brier_delta_ci_high": float("nan"),
                    "logloss_delta": float("nan"),
                    "logloss_delta_ci_low": float("nan"),
                    "logloss_delta_ci_high": float("nan"),
                    "mean_signed_move": float(lead["signed_move"].mean()),
                    "signed_move_ci_low": move_ci[0],
                    "signed_move_ci_high": move_ci[1],
                }
            )
    return pd.DataFrame(score_rows)


def main() -> None:
    historical = pd.read_csv(HISTORICAL_CSV)
    current = pd.read_csv(CURRENT_CSV)
    historical_dates = sorted(historical["target_date"].unique())
    split = len(historical_dates) // 2
    blocks = {
        "historical_discovery_non_pit": historical[
            historical["target_date"].isin(historical_dates[:split])
        ].copy(),
        "historical_replication_non_pit": historical[
            historical["target_date"].isin(historical_dates[split:])
        ].copy(),
        "current_frozen_pit": current.copy(),
    }

    score_rows: list[dict] = []
    source_rows: list[dict] = []
    bootstrap_rows: list[dict] = []
    rng = np.random.default_rng(SEED)
    for block, frame in blocks.items():
        selected = frame[frame["weather_regime"].eq("rain_convective")]
        complement = frame[~frame["weather_regime"].eq("rain_convective")]
        all_dates = int(frame["target_date"].nunique())
        score_rows.extend(
            [
                score_row(block, "all_heada", frame, all_dates),
                score_row(block, "rain_convective", selected, all_dates),
                score_row(block, "complement", complement, all_dates),
            ]
        )
        for source, source_frame in selected.groupby("source"):
            row = score_row(block, f"rain_convective_{source}", source_frame, all_dates)
            row["source"] = source
            source_rows.append(row)
        bootstrap_rows.append(block_bootstrap(frame, block, rng))

    scorecard = pd.DataFrame(score_rows)
    source_scorecard = pd.DataFrame(source_rows)
    bootstrap = pd.DataFrame(bootstrap_rows)
    market_anchor = market_anchor_scorecard(rng)
    current_selected = blocks["current_frozen_pit"][
        blocks["current_frozen_pit"]["weather_regime"].eq("rain_convective")
    ]
    daily_rows: list[dict] = []
    for target_date, daily in blocks["current_frozen_pit"].groupby("target_date"):
        rain = daily[daily["weather_regime"].eq("rain_convective")]
        daily_rows.append(
            {
                "target_date": target_date,
                "all_rows": int(len(daily)),
                "all_wins": int(daily["win"].sum()),
                "all_win_rate": float(daily["win"].mean()),
                "all_roi": roi(daily),
                "rain_rows": int(len(rain)),
                "rain_wins": int(rain["win"].sum()),
                "rain_win_rate": float(rain["win"].mean()) if len(rain) else float("nan"),
                "rain_roi": roi(rain),
            }
        )
    current_daily = pd.DataFrame(daily_rows)
    largest_rain_day = (
        current_selected.groupby("target_date").size().sort_values(ascending=False).index[0]
    )
    leave_largest_day_out = current_selected[
        current_selected["target_date"].ne(largest_rain_day)
    ]
    capacity = {
        "rows": int(len(current_selected)),
        "direct_best_ask_rows": int(current_selected["best_ask"].notna().sum()),
        "median_best_ask_size_shares": float(current_selected["best_ask_size"].median()),
        "median_fresh_spread": float(current_selected["fresh_spread"].median()),
        "max_rows_on_one_date": int(current_selected.groupby("target_date").size().max()),
        "largest_rain_day": str(largest_rain_day),
        "leave_largest_rain_day_out_roi": roi(leave_largest_day_out),
    }
    summary = {
        "generated_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "selector": {
            "name": "heada_rain_convective_shadow_v1",
            "rule": "weather_regime == rain_convective",
            "weather_definition": "decision-time D-1 peak-window precipitation probability >= 50%",
            "execution": "fee-adjusted taker evaluation; zero-notional shadow only",
        },
        "denominator": {
            block: {
                "rows": int(len(frame)),
                "dates": int(frame["target_date"].nunique()),
                "start": str(frame["target_date"].min()),
                "end": str(frame["target_date"].max()),
                "weather_vintage": (
                    "decision_time_pit"
                    if block == "current_frozen_pit"
                    else "posthoc_archive_non_pit"
                ),
            }
            for block, frame in blocks.items()
        },
        "current_capacity": capacity,
        "verdict": {
            "status": "forward_shadow_candidate",
            "live_action": "none",
            "reason": (
                "Temporal direction replicates, but historical weather vintages are "
                "non-PIT and the current PIT target-date bootstrap CI crosses zero."
            ),
        },
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    scorecard.to_csv(OUTPUT_DIR / "temporal_scorecard.csv", index=False)
    source_scorecard.to_csv(OUTPUT_DIR / "source_scorecard.csv", index=False)
    bootstrap.to_csv(OUTPUT_DIR / "target_date_bootstrap.csv", index=False)
    market_anchor.to_csv(OUTPUT_DIR / "market_anchor_scorecard.csv", index=False)
    current_daily.to_csv(OUTPUT_DIR / "current_daily_scorecard.csv", index=False)
    (OUTPUT_DIR / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
