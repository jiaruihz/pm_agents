#!/usr/bin/env python3
"""PIT evaluation of Boxoffice Pro Wednesday forecasts versus Polymarket."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
from scipy.stats import norm


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.box_office_repeat_weekend.industry import (  # noqa: E402
    BoxOfficeProClient,
    TheNumbersClient,
)

import research_repeat_weekend_v0 as v0  # noqa: E402
import research_repeat_weekend_v1 as v1  # noqa: E402


DEFAULT_OUT = ROOT / "docs/analysis/2026-08/generated/box_office_repeat_weekend_v3"
V2_OUT = ROOT / "docs/analysis/2026-08/generated/box_office_repeat_weekend_v2"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2026, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 8, 9))
    return parser.parse_args()


def norm_title(value: str) -> str:
    value = value.replace("&", " and ")
    value = re.sub(r"\bthe\b", " ", value, flags=re.I)
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def match_forecasts_actuals(
    forecasts: list[dict[str, Any]], actuals: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    actual_by_key = {
        (row["target_friday"], norm_title(row["movie"])): row for row in actuals
    }
    rows = []
    for forecast in forecasts:
        actual = actual_by_key.get(
            (forecast["target_friday"], norm_title(forecast["movie"]))
        )
        if actual is None:
            continue
        mid = float(forecast["forecast_mid_m"])
        gross = float(actual["actual_gross_m"])
        rows.append(
            {
                **forecast,
                "actual_movie": actual["movie"],
                "actual_gross_m": gross,
                "ape": abs(mid - gross) / gross,
                "log_residual": math.log(gross / mid),
                "range_covered": int(
                    float(forecast["forecast_low_m"])
                    <= gross
                    <= float(forecast["forecast_high_m"])
                ),
            }
        )
    return rows


def empirical_probs(
    labels: list[str], forecast_mid_m: float, prior_residuals: np.ndarray
) -> list[float]:
    robust_sigma = (
        np.median(np.abs(prior_residuals - np.median(prior_residuals))) * 1.4826
    )
    bandwidth = max(0.04, 0.18 * robust_sigma)
    probabilities = []
    for label in labels:
        lo, hi = v0.parse_bracket(label)
        lo_residual = (
            -math.inf if math.isinf(lo) else math.log(max(lo / forecast_mid_m, 1e-9))
        )
        hi_residual = (
            math.inf if math.isinf(hi) else math.log(max(hi / forecast_mid_m, 1e-9))
        )
        probabilities.append(
            float(
                np.mean(
                    norm.cdf((hi_residual - prior_residuals) / bandwidth)
                    - norm.cdf((lo_residual - prior_residuals) / bandwidth)
                )
            )
        )
    total = sum(probabilities)
    return [value / total for value in probabilities]


def blend(industry: list[float], market: list[float], beta: float) -> list[float]:
    left = np.clip(np.asarray(industry, dtype=float), 1e-9, 1)
    right = np.clip(np.asarray(market, dtype=float), 1e-9, 1)
    mixed = np.exp(beta * np.log(left) + (1.0 - beta) * np.log(right))
    return (mixed / mixed.sum()).tolist()


def fit_beta(rows: list[dict[str, Any]]) -> float:
    def loss(beta: float) -> float:
        return float(
            np.mean(
                [
                    -math.log(
                        max(
                            blend(row["industry_probs"], row["market_probs"], beta)[
                                row["winner"]
                            ],
                            1e-12,
                        )
                    )
                    for row in rows
                ]
            )
        )

    return float(minimize_scalar(loss, bounds=(0.0, 1.0), method="bounded").x)


def score_market(
    market: pd.DataFrame, matched: list[dict[str, Any]], *, min_prior: int = 8
) -> list[dict[str, Any]]:
    forecast_by_key = {
        (
            row["target_friday"],
            norm_title(row["movie"]),
            int(row["release_week"]),
        ): row
        for row in matched
        if row.get("release_week") is not None
    }
    scored = []
    for source in market.to_dict("records"):
        key = (
            str(source["target_friday"]),
            norm_title(str(source["movie"])),
            int(source["week"]),
        )
        forecast = forecast_by_key.get(key)
        if forecast is None:
            continue
        cutoff = datetime.fromisoformat(str(source["market_cutoff"]).replace("Z", "+00:00"))
        published = datetime.fromisoformat(
            str(forecast["published_at_utc"]).replace("Z", "+00:00")
        )
        if published > cutoff:
            continue
        prior = np.asarray(
            [
                row["log_residual"]
                for row in matched
                if row["target_friday"] < forecast["target_friday"]
                and row.get("release_week") is not None
            ],
            dtype=float,
        )
        if len(prior) < min_prior:
            continue
        labels = v1.load_literal(source["brackets"])
        industry = empirical_probs(labels, forecast["forecast_mid_m"], prior)
        market_probs = [float(x) for x in v1.load_literal(source["market_probs"])]
        winner = int(source["winner"])
        scored.append(
            {
                "event_id": str(source["event_id"]),
                "movie": source["movie"],
                "week": int(source["week"]),
                "target_friday": source["target_friday"],
                "published_at_utc": forecast["published_at_utc"],
                "market_cutoff": source["market_cutoff"],
                "forecast_mid_m": forecast["forecast_mid_m"],
                "actual_gross_m": forecast["actual_gross_m"],
                "prior_forecast_rows": len(prior),
                "brackets": labels,
                "winner": winner,
                "industry_probs": industry,
                "market_raw": v1.load_literal(source["market_raw"]),
                "market_probs": market_probs,
                "industry_log_loss": -math.log(max(industry[winner], 1e-12)),
                "market_log_loss": -math.log(max(market_probs[winner], 1e-12)),
                "industry_brier": sum(
                    (value - float(index == winner)) ** 2
                    for index, value in enumerate(industry)
                ),
                "market_brier": sum(
                    (value - float(index == winner)) ** 2
                    for index, value in enumerate(market_probs)
                ),
            }
        )
    outputs = []
    for row in sorted(scored, key=lambda value: (value["target_friday"], value["event_id"])):
        prior = [
            value for value in scored if value["target_friday"] < row["target_friday"]
        ]
        beta = fit_beta(prior) if len(prior) >= 10 else 0.0
        final = blend(row["industry_probs"], row["market_probs"], beta)
        winner = row["winner"]
        outputs.append(
            {
                **row,
                "beta_industry": beta,
                "beta_train_partitions": len(prior),
                "final_probs": final,
                "final_log_loss": -math.log(max(final[winner], 1e-12)),
                "final_brier": sum(
                    (value - float(index == winner)) ** 2
                    for index, value in enumerate(final)
                ),
            }
        )
    return outputs


def mean(rows: list[dict[str, Any]], field: str) -> float | None:
    return float(np.mean([row[field] for row in rows])) if rows else None


def ledger_summary(ledger: list[dict[str, Any]]) -> dict[str, Any]:
    if not ledger:
        return {
            "trades": 0,
            "target_weekends": 0,
            "wins": 0,
            "hit_rate": None,
            "median_entry_price": None,
            "capital": 0.0,
            "pnl": 0.0,
            "roi": None,
            "pnl_quantiles": None,
            "entry_price_bins": [],
            "contribution_by_movie": [],
        }
    capital = sum(row["cost"] for row in ledger)
    pnl = sum(row["pnl"] for row in ledger)
    bins = []
    for label, lo, hi in (
        ("0-10c", 0.0, 0.10),
        ("10-25c", 0.10, 0.25),
        ("25-50c", 0.25, 0.50),
        ("50c+", 0.50, 1.01),
    ):
        subset = [
            row for row in ledger if lo <= row["historical_price_proxy"] < hi
        ]
        sub_capital = sum(row["cost"] for row in subset)
        sub_pnl = sum(row["pnl"] for row in subset)
        bins.append(
            {
                "bin": label,
                "trades": len(subset),
                "wins": sum(row["won"] for row in subset),
                "capital": sub_capital,
                "pnl": sub_pnl,
                "roi": sub_pnl / sub_capital if sub_capital else None,
            }
        )
    by_movie = []
    for movie in sorted({row["movie"] for row in ledger}):
        subset = [row for row in ledger if row["movie"] == movie]
        by_movie.append(
            {
                "movie": movie,
                "trades": len(subset),
                "wins": sum(row["won"] for row in subset),
                "pnl": sum(row["pnl"] for row in subset),
            }
        )
    return {
        "trades": len(ledger),
        "target_weekends": len({row["target_friday"] for row in ledger}),
        "wins": sum(row["won"] for row in ledger),
        "hit_rate": float(np.mean([row["won"] for row in ledger])),
        "median_entry_price": float(
            np.median([row["historical_price_proxy"] for row in ledger])
        ),
        "capital": capital,
        "pnl": pnl,
        "roi": pnl / capital,
        "pnl_quantiles": {
            str(quantile): float(np.quantile([row["pnl"] for row in ledger], quantile))
            for quantile in (0.0, 0.25, 0.5, 0.75, 1.0)
        },
        "entry_price_bins": bins,
        "contribution_by_movie": sorted(
            by_movie, key=lambda row: row["pnl"], reverse=True
        ),
        "bootstrap_target_weekend": v1.bootstrap_trade_roi(
            ledger, "target_friday"
        ),
    }


def main() -> None:
    args = parse_args()
    out = args.out.resolve()
    cache = out / "cache"
    out.mkdir(parents=True, exist_ok=True)
    print("collecting Boxoffice Pro forecasts", flush=True)
    forecast_objects = BoxOfficeProClient(cache_dir=cache / "boxofficepro").collect(
        args.start, args.end
    )
    forecasts = [row.to_dict() for row in forecast_objects]
    targets = sorted(
        {
            date.fromisoformat(row["target_friday"])
            for row in forecasts
            if date.fromisoformat(row["target_friday"]) < args.end
        }
    )
    print(f"collecting {len(targets)} final weekend charts", flush=True)
    charts = TheNumbersClient(cache_dir=cache / "the_numbers")
    actuals = []
    actual_failures = []
    for index, target in enumerate(targets, 1):
        try:
            actuals.extend(charts.weekend_chart(target))
        except (RuntimeError, ValueError) as exc:
            actual_failures.append(
                {"target_friday": target.isoformat(), "reason": str(exc)}
            )
        if index % 8 == 0 or index == len(targets):
            print(f"charts {index}/{len(targets)}", flush=True)
    matched = match_forecasts_actuals(forecasts, actuals)
    repeat = [row for row in matched if row.get("release_week") is not None]

    market = pd.read_csv(V2_OUT / "market_v2_comparison.csv")
    cutoff = pd.read_csv(v1.DEFAULT_OUT / "market_full_audit.csv")[[
        "event_id",
        "market_cutoff",
    ]].drop_duplicates("event_id")
    market["event_id"] = market["event_id"].astype(str)
    cutoff["event_id"] = cutoff["event_id"].astype(str)
    market = market.merge(cutoff, on="event_id", how="left", validate="one_to_one")
    scored = score_market(market, repeat)
    industry_view = [{**row, "model_probs": row["industry_probs"]} for row in scored]
    final_view = [{**row, "model_probs": row["final_probs"]} for row in scored]
    industry_primary = v1.trade_ledger(industry_view, 0.10, 0.02)
    final_primary = v1.trade_ledger(final_view, 0.10, 0.02)
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "forecast_source": "Boxoffice Pro Wednesday Weekend Preview public WordPress payload",
        "actual_source": "The Numbers final domestic weekend chart",
        "forecast_posts": len({row["source_post_id"] for row in forecasts}),
        "forecast_rows_all_top3": len(forecasts),
        "actual_chart_failures": actual_failures,
        "forecast_rows_matched_actual": len(matched),
        "repeat_forecast_rows_matched_actual": len(repeat),
        "repeat_forecast_target_weekends": len({row["target_friday"] for row in repeat}),
        "repeat_point_mape": mean(repeat, "ape"),
        "repeat_range_coverage": mean(repeat, "range_covered"),
        "market_partitions_scored": len(scored),
        "market_target_weekends_scored": len({row["target_friday"] for row in scored}),
        "industry_log_loss": mean(scored, "industry_log_loss"),
        "market_log_loss": mean(scored, "market_log_loss"),
        "final_log_loss": mean(scored, "final_log_loss"),
        "industry_brier": mean(scored, "industry_brier"),
        "market_brier": mean(scored, "market_brier"),
        "final_brier": mean(scored, "final_brier"),
        "walk_forward_beta_last": scored[-1]["beta_industry"] if scored else None,
        "industry_primary_10pp_02c": ledger_summary(industry_primary),
        "final_primary_10pp_02c": ledger_summary(final_primary),
        "industry_trade_grid": v1.trade_grid(industry_view),
        "final_trade_grid": v1.trade_grid(final_view),
        "decision": (
            "no_directional_trade_industry_weight_zero"
            if scored and (scored[-1]["beta_industry"] < 0.01)
            else "insufficient_overlap"
        ),
    }
    v1.write_csv(out / "industry_forecasts.csv", forecasts)
    v1.write_csv(out / "industry_forecast_actuals.csv", matched)
    v1.write_csv(out / "industry_market_comparison.csv", scored)
    v1.write_csv(out / "industry_primary_ledger.csv", industry_primary)
    v1.write_csv(out / "final_primary_ledger.csv", final_primary)
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
