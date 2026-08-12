#!/usr/bin/env python3
"""Score a frozen Helsinki WCIR replay as a 5-share exact-bracket strategy."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score


SEED = 20260812
SHARES = 5.0


def fee_per_share(price: float) -> float:
    return round(0.05 * price * (1.0 - price), 5)


def sweep_asks(levels: list[dict[str, Any]], shares: float = SHARES) -> dict[str, float] | None:
    remaining = shares
    notional = fee = 0.0
    for level in sorted(levels, key=lambda row: float(row.get("price", 2.0))):
        price = float(level.get("price", 0.0))
        size = float(level.get("size", 0.0))
        if not 0 < price < 1 or size <= 0:
            continue
        take = min(remaining, size)
        notional += take * price
        fee += take * fee_per_share(price)
        remaining -= take
        if remaining <= 1e-9:
            return {
                "entry_vwap": notional / shares,
                "fee_per_share": fee / shares,
                "effective_cost": (notional + fee) / shares,
                "cash_cost": notional + fee,
            }
    return None


def load_winners(db_path: Path, dates: list[str]) -> dict[str, str]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    placeholders = ",".join("?" for _ in dates)
    rows = conn.execute(
        f"""
        SELECT target_date, bracket
        FROM settlement_outcomes
        WHERE city = 'Helsinki' AND final_price >= 0.999
          AND target_date IN ({placeholders})
        """,
        dates,
    ).fetchall()
    conn.close()
    return {str(date): str(bracket) for date, bracket in rows}


def metrics(rows: pd.DataFrame, probability: str) -> dict[str, Any]:
    frame = rows.copy()
    y = frame["y_no"].to_numpy(float)
    p = np.clip(frame[probability].to_numpy(float), 1e-6, 1 - 1e-6)
    frame["brier"] = (p - y) ** 2
    frame["logloss"] = -y * np.log(p) - (1 - y) * np.log(1 - p)
    daily = frame.groupby("target_date")[["brier", "logloss"]].mean()
    return {
        "rows": int(len(frame)),
        "target_dates": int(frame["target_date"].nunique()),
        "brier": float(daily["brier"].mean()),
        "logloss": float(daily["logloss"].mean()),
        "auc": float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else None,
        "calibration_bias": float(frame.groupby("target_date").apply(
            lambda group: (group[probability] - group["y_no"]).mean(),
            include_groups=False,
        ).mean()),
    }


def probability_delta_bootstrap(
    rows: pd.DataFrame, draws: int
) -> dict[str, dict[str, float]]:
    frame = rows.copy()
    y = frame["y_no"].to_numpy(float)
    model = np.clip(frame["model_no"].to_numpy(float), 1e-6, 1 - 1e-6)
    market = np.clip(frame["market_no"].to_numpy(float), 1e-6, 1 - 1e-6)
    frame["brier_delta"] = (model - y) ** 2 - (market - y) ** 2
    frame["logloss_delta"] = (
        -y * np.log(model)
        - (1 - y) * np.log(1 - model)
        + y * np.log(market)
        + (1 - y) * np.log(1 - market)
    )
    daily = frame.groupby("target_date")[["brier_delta", "logloss_delta"]].mean()
    rng = np.random.default_rng(SEED + 1)
    values = {"brier": [], "logloss": []}
    for _ in range(draws):
        sample = daily.iloc[rng.integers(0, len(daily), len(daily))]
        values["brier"].append(float(sample["brier_delta"].mean()))
        values["logloss"].append(float(sample["logloss_delta"].mean()))
    return {
        metric: {
            "model_minus_market": float(daily[f"{metric}_delta"].mean()),
            "ci_low": float(np.quantile(samples, 0.025)),
            "ci_high": float(np.quantile(samples, 0.975)),
            "probability_model_better": float(np.mean(np.asarray(samples) < 0)),
        }
        for metric, samples in values.items()
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", required=True)
    parser.add_argument("--db", default="runtime/weather.db")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--strategy-id",
        default="helsinki_bounded_market_residual_c015_symmetric_v1",
    )
    parser.add_argument("--logit-cap", type=float, default=0.15)
    parser.add_argument("--bootstrap-draws", type=int, default=10000)
    args = parser.parse_args()
    replay_root = Path(args.replay_root)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)

    raw_rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for day_dir in sorted(path for path in replay_root.iterdir() if path.is_dir()):
        summary_path = day_dir / "replay_summary.json"
        if summary_path.is_file():
            summaries.append(json.loads(summary_path.read_text(encoding="utf-8")))
        evaluations = day_dir / "evaluations.jsonl"
        if not evaluations.is_file():
            continue
        with evaluations.open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                if row.get("evaluation_status") == "scored":
                    raw_rows.append(row)
    if not raw_rows:
        raise RuntimeError("no scored frozen-forward evaluations")

    dates = sorted({str(row["target_date"]) for row in raw_rows})
    winners = load_winners(Path(args.db).resolve(), dates)
    missing_settlement = sorted(set(dates) - set(winners))

    opportunity_rows: list[dict[str, Any]] = []
    probability_rows: list[dict[str, Any]] = []
    no_seen: set[str] = set()
    for row in raw_rows:
        date = str(row["target_date"])
        bracket = str(row["current_bracket"])
        side = str(row["market_side"]).lower()
        decision = str(row["decision_ts_utc"])
        asks = ((row.get("market") or {}).get("raw") or {}).get("asks") or []
        cost = sweep_asks(asks)
        if cost is None:
            continue
        probability = float(row["model_probability"])
        opportunity_rows.append(
            {
                "target_date": date,
                "bracket": bracket,
                "decision_ts_utc": decision,
                "side": side,
                "model_probability": probability,
                **cost,
                "edge_after_fee": probability - cost["effective_cost"],
                "quote_state": (row.get("market") or {}).get("quote_state"),
                "condition_id": (row.get("market") or {}).get("condition_id"),
            }
        )
        if side == "no" and row["evaluation_id"] not in no_seen and date in winners:
            no_seen.add(row["evaluation_id"])
            probability_rows.append(
                {
                    "target_date": date,
                    "decision_ts_utc": decision,
                    "bracket": bracket,
                    "model_no": probability,
                    "market_no": float(row["market_probability"]),
                    "y_no": float(bracket != winners[date]),
                }
            )
    opportunities = pd.DataFrame(opportunity_rows).sort_values("decision_ts_utc")
    probabilities = pd.DataFrame(probability_rows).drop_duplicates(
        ["target_date", "decision_ts_utc", "bracket"]
    )

    # Fixed strategy grain: first positive-EV state for each target_date × bracket.
    # YES and NO compete on the same checkpoint; row ordering cannot select the side.
    entries: list[pd.Series] = []
    for (_date, _bracket), group in opportunities.groupby(
        ["target_date", "bracket"], sort=False
    ):
        selected = None
        for _, checkpoint in group.groupby("decision_ts_utc", sort=True):
            candidate = checkpoint.sort_values("edge_after_fee", ascending=False).iloc[0]
            if candidate["edge_after_fee"] > 0:
                selected = candidate
                break
        if selected is not None:
            entries.append(selected)
    trades = pd.DataFrame(entries).sort_values(["target_date", "decision_ts_utc"])
    trades["winner_bracket"] = trades["target_date"].map(winners)
    trades["settled"] = trades["winner_bracket"].notna()
    trades["won"] = np.where(
        trades["side"].eq("yes"),
        trades["bracket"].eq(trades["winner_bracket"]),
        trades["bracket"].ne(trades["winner_bracket"]),
    ) & trades["settled"]
    trades["payout"] = np.where(trades["won"], SHARES, 0.0)
    trades["pnl"] = np.where(trades["settled"], trades["payout"] - trades["cash_cost"], np.nan)
    trades["local_hour"] = pd.to_datetime(trades["decision_ts_utc"], utc=True).dt.tz_convert(
        "Europe/Helsinki"
    ).dt.hour
    trades["price_band"] = pd.cut(
        trades["effective_cost"],
        [-1, 0.01, 0.20, 0.40, 0.60, 0.80, 0.99, 2],
        labels=["<=1%", "1-20%", "20-40%", "40-60%", "60-80%", "80-99%", ">=99%"],
    )
    settled = trades.loc[trades["settled"]].copy()

    def trade_summary(frame: pd.DataFrame) -> dict[str, Any]:
        cash = float(frame["cash_cost"].sum())
        pnl = float(frame["pnl"].sum())
        return {
            "trades": int(len(frame)),
            "target_dates": int(frame["target_date"].nunique()),
            "wins": int(frame["won"].sum()),
            "win_rate": float(frame["won"].mean()) if len(frame) else None,
            "cash_cost": cash,
            "pnl": pnl,
            "roi": pnl / cash if cash else None,
        }

    daily = settled.groupby("target_date")[["pnl", "cash_cost"]].sum()
    rng = np.random.default_rng(SEED)
    bootstrap = []
    for _ in range(args.bootstrap_draws):
        sample = daily.iloc[rng.integers(0, len(daily), len(daily))]
        bootstrap.append(float(sample["pnl"].sum() / sample["cash_cost"].sum()))

    score_model = metrics(probabilities, "model_no")
    score_market = metrics(probabilities, "market_no")
    result = {
        "status": "frozen_forward_strategy_scored",
        "strategy_id": args.strategy_id,
        "strategy": {
            "probability": (
                f"logit(market_no)+{args.logit_cap:g}*tanh("
                f"(logit(weather_no)-logit(market_no))/{args.logit_cap:g})"
            ),
            "expression": "first positive 5-share fee-adjusted EV per target_date x bracket; YES/NO choose larger EV",
            "extra_entry_threshold": 0.0,
            "shares": SHARES,
            "orders_submitted": 0,
        },
        "coverage": {
            "target_dates": dates,
            "settlement_dates": sorted(winners),
            "missing_settlement_dates": missing_settlement,
            "scored_side_rows": len(raw_rows),
            "probability_rows": len(probabilities),
            "executable_side_rows": len(opportunities),
            "source_checkpoints": sum(int(row.get("source_checkpoints", 0)) for row in summaries),
            "replayed_checkpoints": sum(int(row.get("replayed_checkpoints", 0)) for row in summaries),
            "blocked_source_checkpoints": sum(int(row.get("blocked_source_checkpoints", 0)) for row in summaries),
        },
        "probability_scores_same_rows_date_equal": {
            "model": score_model,
            "market": score_market,
            "model_minus_market": {
                "brier": score_model["brier"] - score_market["brier"],
                "logloss": score_model["logloss"] - score_market["logloss"],
            },
        },
        "probability_model_minus_market_target_date_bootstrap": probability_delta_bootstrap(
            probabilities, args.bootstrap_draws
        ),
        "trading_primary_all_prices": trade_summary(settled),
        "trading_diagnostic_1_to_99_percent": trade_summary(
            settled.loc[settled["effective_cost"].between(0.01, 0.99, inclusive="neither")]
        ),
        "target_date_block_bootstrap_roi": {
            "draws": args.bootstrap_draws,
            "ci_low": float(np.quantile(bootstrap, 0.025)),
            "median": float(np.quantile(bootstrap, 0.5)),
            "ci_high": float(np.quantile(bootstrap, 0.975)),
        },
    }
    result["by_side"] = {
        str(key): trade_summary(group) for key, group in settled.groupby("side")
    }
    result["by_price_band"] = {
        str(key): trade_summary(group)
        for key, group in settled.groupby("price_band", observed=False)
    }

    opportunities.to_csv(output / "signal_opportunities_5share.csv", index=False)
    probabilities.to_csv(output / "probability_same_rows.csv", index=False)
    trades.to_csv(output / "strategy_trades_5share.csv", index=False)
    (output / "summary.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
