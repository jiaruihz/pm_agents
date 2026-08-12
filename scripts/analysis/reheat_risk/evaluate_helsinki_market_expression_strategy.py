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


def sweep_bids(levels: list[dict[str, Any]], shares: float = SHARES) -> dict[str, float] | None:
    remaining = shares
    notional = fee = 0.0
    for level in sorted(levels, key=lambda row: float(row.get("price", 0.0)), reverse=True):
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
                "exit_vwap": notional / shares,
                "exit_fee_per_share": fee / shares,
                "exit_net_per_share": (notional - fee) / shares,
                "exit_cash_proceeds": notional - fee,
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


def build_trade_timelines(
    opportunities: pd.DataFrame,
    trades: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Attach every later FMI checkpoint to each selected exact-bracket entry.

    The model only scores the *current* official bracket.  Once the running
    maximum leaves an entry bracket, the old expression is no longer scored;
    the timeline therefore records the physical terminal state instead of
    inventing a held-position probability.
    """
    timeline_rows: list[dict[str, Any]] = []
    case_rows: list[dict[str, Any]] = []
    if trades.empty:
        return pd.DataFrame(), pd.DataFrame()

    frame = opportunities.copy()
    frame["decision_dt"] = pd.to_datetime(frame["decision_ts_utc"], utc=True)
    checkpoint_state = (
        frame.sort_values(["target_date", "decision_dt", "side"])
        .drop_duplicates(["target_date", "decision_ts_utc"])
    )
    for trade_number, trade in enumerate(trades.itertuples(index=False), start=1):
        entry_dt = pd.Timestamp(trade.decision_ts_utc)
        if entry_dt.tzinfo is None:
            entry_dt = entry_dt.tz_localize("UTC")
        else:
            entry_dt = entry_dt.tz_convert("UTC")
        entry_bracket = int(trade.bracket)
        side = str(trade.side)
        date_rows = checkpoint_state.loc[
            checkpoint_state["target_date"].eq(str(trade.target_date))
            & checkpoint_state["decision_dt"].ge(entry_dt)
        ].copy()
        held_rows = frame.loc[
            frame["target_date"].eq(str(trade.target_date))
            & frame["bracket"].astype(str).eq(str(trade.bracket))
            & frame["side"].eq(side)
            & frame["decision_dt"].ge(entry_dt)
        ].set_index("decision_ts_utc")
        first_transition_ts: str | None = None
        for state in date_rows.itertuples(index=False):
            current_bracket = int(state.bracket)
            if current_bracket > entry_bracket:
                physical_state = "terminal_won" if side == "no" else "terminal_lost"
                if first_transition_ts is None:
                    first_transition_ts = str(state.decision_ts_utc)
            else:
                physical_state = "still_open_same_bracket"
            held = None
            if str(state.decision_ts_utc) in held_rows.index:
                held = held_rows.loc[str(state.decision_ts_utc)]
                if isinstance(held, pd.DataFrame):
                    held = held.iloc[0]
            timeline_rows.append(
                {
                    "trade_number": trade_number,
                    "target_date": trade.target_date,
                    "entry_bracket": trade.bracket,
                    "entry_side": side,
                    "entry_decision_ts_utc": trade.decision_ts_utc,
                    "checkpoint_ts_utc": state.decision_ts_utc,
                    "minutes_since_entry": (
                        pd.Timestamp(state.decision_dt) - entry_dt
                    ).total_seconds()
                    / 60.0,
                    "current_official_bracket": current_bracket,
                    "physical_position_state": physical_state,
                    "held_model_probability": None if held is None else held["model_probability"],
                    "held_market_probability": None if held is None else held["market_probability"],
                    "held_effective_cost": None if held is None else held["effective_cost"],
                    "held_edge_after_fee": None if held is None else held["edge_after_fee"],
                    "exit_vwap": None if held is None else held["exit_vwap"],
                    "exit_net_per_share": None if held is None else held["exit_net_per_share"],
                    "counterfactual_exit_pnl": (
                        None
                        if held is None or pd.isna(held["exit_cash_proceeds"])
                        else held["exit_cash_proceeds"] - trade.cash_cost
                    ),
                    "value_exit": (
                        False
                        if held is None or pd.isna(held["exit_net_per_share"])
                        else held["exit_net_per_share"] > held["model_probability"]
                    ),
                    "weather_no_probability": state.weather_no_probability,
                    "path_state": state.path_state,
                    "source_lattice_anchor": state.source_lattice_anchor,
                    "official_lattice_anchor": state.official_lattice_anchor,
                    "source_obs_ts_utc": state.source_obs_ts_utc,
                    "source_first_seen_at_utc": state.source_first_seen_at_utc,
                    "source_to_book_lag_seconds": state.source_to_book_lag_seconds,
                    "temp_delta_10m": state.temp_delta_10m,
                    "temp_slope_30m_cph": state.temp_slope_30m_cph,
                    "plateau_duration_min": state.plateau_duration_min,
                    "pullback_depth_c": state.pullback_depth_c,
                    "forecast_future_peak_margin_vs_running_c": state.forecast_future_peak_margin_vs_running_c,
                    "forecast_minutes_to_future_peak": state.forecast_minutes_to_future_peak,
                }
            )
        same_bracket = frame.loc[
            frame["target_date"].eq(str(trade.target_date))
            & frame["bracket"].astype(str).eq(str(trade.bracket))
            & frame["side"].eq(side)
            & frame["decision_dt"].ge(entry_dt)
        ].sort_values("decision_dt")
        transition_minutes = None
        if first_transition_ts is not None:
            transition_minutes = (
                pd.Timestamp(first_transition_ts) - entry_dt
            ).total_seconds() / 60.0
        case_rows.append(
            {
                "trade_number": trade_number,
                "target_date": trade.target_date,
                "bracket": trade.bracket,
                "side": side,
                "entry_decision_ts_utc": trade.decision_ts_utc,
                "entry_effective_cost": trade.effective_cost,
                "entry_edge_after_fee": trade.edge_after_fee,
                "winner_bracket": trade.winner_bracket,
                "won": trade.won,
                "pnl": trade.pnl,
                "later_checkpoints": max(0, len(date_rows) - 1),
                "first_official_bracket_transition_ts_utc": first_transition_ts,
                "minutes_to_official_bracket_transition": transition_minutes,
                "last_same_bracket_ts_utc": (
                    same_bracket.iloc[-1]["decision_ts_utc"] if len(same_bracket) else None
                ),
                "last_same_bracket_model_probability": (
                    same_bracket.iloc[-1]["model_probability"] if len(same_bracket) else None
                ),
                "last_same_bracket_market_probability": (
                    same_bracket.iloc[-1]["market_probability"] if len(same_bracket) else None
                ),
                "last_same_bracket_edge_after_fee": (
                    same_bracket.iloc[-1]["edge_after_fee"] if len(same_bracket) else None
                ),
            }
        )
    return pd.DataFrame(timeline_rows), pd.DataFrame(case_rows)


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
        bids = ((row.get("market") or {}).get("raw") or {}).get("bids") or []
        cost = sweep_asks(asks)
        if cost is None:
            continue
        exit_value = sweep_bids(bids)
        probability = float(row["model_probability"])
        opportunity_rows.append(
            {
                "target_date": date,
                "bracket": bracket,
                "decision_ts_utc": decision,
                "side": side,
                "model_probability": probability,
                "market_probability": float(row["market_probability"]),
                **cost,
                **(
                    exit_value
                    if exit_value is not None
                    else {
                        "exit_vwap": None,
                        "exit_fee_per_share": None,
                        "exit_net_per_share": None,
                        "exit_cash_proceeds": None,
                    }
                ),
                "edge_after_fee": probability - cost["effective_cost"],
                "quote_state": (row.get("market") or {}).get("quote_state"),
                "condition_id": (row.get("market") or {}).get("condition_id"),
                "source_obs_ts_utc": row.get("source_obs_ts_utc"),
                "source_first_seen_at_utc": (row.get("lineage") or {}).get(
                    "source_first_seen_at_utc"
                ),
                "source_to_book_lag_seconds": (row.get("lineage") or {}).get(
                    "source_to_book_lag_seconds"
                ),
                "weather_no_probability": (row.get("lineage") or {}).get(
                    "weather_probability"
                ),
                "path_state": (row.get("lineage") or {}).get("path_state"),
                "source_lattice_anchor": (row.get("lineage") or {}).get(
                    "source_lattice_anchor"
                ),
                "official_lattice_anchor": (row.get("lineage") or {}).get(
                    "official_lattice_anchor"
                ),
                "feature_coverage": row.get("feature_coverage"),
                "temp_delta_10m": (row.get("features") or {}).get("temp_delta_10m"),
                "temp_slope_30m_cph": (row.get("features") or {}).get(
                    "temp_slope_30m_cph"
                ),
                "plateau_duration_min": (row.get("features") or {}).get(
                    "plateau_duration_min"
                ),
                "pullback_depth_c": (row.get("features") or {}).get(
                    "pullback_depth_c"
                ),
                "forecast_future_peak_margin_vs_running_c": (
                    row.get("features") or {}
                ).get("forecast_future_peak_margin_vs_running_c"),
                "forecast_minutes_to_future_peak": (row.get("features") or {}).get(
                    "forecast_minutes_to_future_peak"
                ),
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
    timelines, case_summary = build_trade_timelines(opportunities, trades)

    exit_rows: list[dict[str, Any]] = []
    for trade in settled.itertuples(index=False):
        rows = timelines.loc[
            timelines["target_date"].eq(trade.target_date)
            & timelines["entry_bracket"].astype(str).eq(str(trade.bracket))
            & timelines["entry_side"].eq(trade.side)
            & timelines["minutes_since_entry"].gt(0)
            & timelines["value_exit"].eq(True)
        ].sort_values("checkpoint_ts_utc")
        if len(rows):
            chosen = rows.iloc[0]
            pnl = float(chosen["counterfactual_exit_pnl"])
            exit_rows.append(
                {
                    "trade_number": int(chosen["trade_number"]),
                    "target_date": trade.target_date,
                    "bracket": trade.bracket,
                    "side": trade.side,
                    "action": "exit",
                    "action_ts_utc": chosen["checkpoint_ts_utc"],
                    "pnl": pnl,
                    "hold_pnl": float(trade.pnl),
                    "pnl_delta_vs_hold": pnl - float(trade.pnl),
                }
            )
        else:
            exit_rows.append(
                {
                    "trade_number": int(
                        case_summary.loc[
                            case_summary["target_date"].eq(trade.target_date)
                            & case_summary["bracket"].astype(str).eq(str(trade.bracket))
                            & case_summary["side"].eq(trade.side),
                            "trade_number",
                        ].iloc[0]
                    ),
                    "target_date": trade.target_date,
                    "bracket": trade.bracket,
                    "side": trade.side,
                    "action": "hold_to_settlement",
                    "action_ts_utc": None,
                    "pnl": float(trade.pnl),
                    "hold_pnl": float(trade.pnl),
                    "pnl_delta_vs_hold": 0.0,
                }
            )
    exit_diagnostic = pd.DataFrame(exit_rows).sort_values("trade_number")

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
        "generic_fmi_value_exit_diagnostic": {
            "policy": "at first later FMI checkpoint, exit 5 shares at actual bids minus official fee when net bid exceeds updated model holding value",
            "research_status": "retrospective_diagnostic_not_frozen",
            "trades": int(len(exit_diagnostic)),
            "exits": int(exit_diagnostic["action"].eq("exit").sum()),
            "pnl": float(exit_diagnostic["pnl"].sum()),
            "pnl_delta_vs_hold": float(exit_diagnostic["pnl_delta_vs_hold"].sum()),
            "roi_on_original_entry_cost": float(
                exit_diagnostic["pnl"].sum() / settled["cash_cost"].sum()
            ),
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
    timelines.to_csv(output / "strategy_trade_timelines.csv", index=False)
    case_summary.to_csv(output / "strategy_trade_case_summary.csv", index=False)
    exit_diagnostic.to_csv(output / "strategy_value_exit_diagnostic.csv", index=False)
    (output / "summary.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
