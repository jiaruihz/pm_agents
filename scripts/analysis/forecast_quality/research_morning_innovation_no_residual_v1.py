#!/usr/bin/env python3
"""Market-anchored executable NO strategy from same-day forecast innovation.

The probability model is fitted separately at 09:00 and 12:00 local.  Every
test target date is scored using only earlier target dates.  The candidate
adds physically signed observation-minus-model innovation to the same-row
market + forecast-distance baseline, then buys at most one direct NO ask per
city/checkpoint when official-fee-adjusted edge is at least two cents.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.market_brackets import parse_market_bracket


DEFAULT_DB = ROOT / "runtime/weather.db"
DEFAULT_INPUT = (
    ROOT
    / "docs/analysis/2026-07/generated/forecast_innovation_morning_v1/checkpoint_rows.csv"
)
DEFAULT_OUT = (
    ROOT
    / "docs/analysis/2026-07/generated/morning_innovation_no_residual_v1"
)
DEFAULT_REPORT = (
    ROOT
    / "docs/analysis/2026-07/2026-07-28-morning-innovation-no-residual-v1.md"
)
CHECKPOINTS = (9, 12)
MIN_TRAIN_DATES = 5
REGULARIZATION_C = 0.10
EDGE_BUFFER = 0.02
SHARES = 5.0
MIN_ASK_SIZE = 5.0
WEATHER_FEE_RATE = 0.05
BOOTSTRAP_SAMPLES = 5_000
BOOTSTRAP_SEED = 20260728

FEATURES = {
    "market_raw": [],
    "market_calibrated": ["market_yes_logit"],
    "market_plus_distance": [
        "market_yes_logit",
        "distance_to_bracket_f",
        "distance_to_bracket_sq",
        "is_bottom",
        "is_top",
    ],
    "market_plus_innovation": [
        "market_yes_logit",
        "distance_to_bracket_f",
        "distance_to_bracket_sq",
        "is_bottom",
        "is_top",
        "forecast_innovation_f",
        "innovation_sq",
        "innovation_x_distance",
        "innovation_x_bottom",
        "innovation_x_top",
    ],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    return parser.parse_args()


def connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=2000")
    return conn


def load_quotes(conn: sqlite3.Connection, ladder_ids: list[str]) -> pd.DataFrame:
    chunks: list[pd.DataFrame] = []
    for start in range(0, len(ladder_ids), 500):
        ids = ladder_ids[start : start + 500]
        placeholders = ",".join("?" for _ in ids)
        chunks.append(
            pd.read_sql_query(
                f"""
                SELECT
                    ladder_snapshot_id,
                    absolute_bracket_identity AS rung,
                    question AS rung_question,
                    no_direct_bid,
                    no_direct_ask,
                    no_direct_ask_size,
                    no_book_fetched_at_utc
                FROM tmax_v2_ladder_rung_quotes
                WHERE ladder_snapshot_id IN ({placeholders})
                """,
                conn,
                params=ids,
            )
        )
    return pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()


def native_to_f(value: float, unit: str) -> float:
    return value if str(unit).upper() == "F" else value * 9.0 / 5.0 + 32.0


def build_expression_rows(states: pd.DataFrame, quotes: pd.DataFrame) -> pd.DataFrame:
    rows = states.merge(
        quotes,
        on="ladder_snapshot_id",
        how="inner",
        validate="one_to_many",
    )
    rows = rows[
        rows["no_direct_bid"].notna()
        & rows["no_direct_ask"].notna()
        & rows["no_direct_ask_size"].notna()
    ].copy()
    rows["no_direct_bid"] = rows["no_direct_bid"].astype(float)
    rows["no_direct_ask"] = rows["no_direct_ask"].astype(float)
    rows["no_direct_ask_size"] = rows["no_direct_ask_size"].astype(float)
    rows = rows[
        rows["no_direct_bid"].between(0.001, 0.999)
        & rows["no_direct_ask"].between(0.001, 0.999)
        & rows["no_direct_bid"].le(rows["no_direct_ask"])
    ].copy()

    parsed: list[dict[str, Any] | None] = []
    for row in rows.itertuples(index=False):
        bracket = parse_market_bracket(
            str(row.rung), str(row.rung_question or "")
        )
        if bracket is None:
            parsed.append(None)
            continue
        if bracket.low is not None and bracket.high is not None:
            center = (float(bracket.low) + float(bracket.high)) / 2.0
        elif bracket.low is not None:
            center = float(bracket.low)
        elif bracket.high is not None:
            center = float(bracket.high)
        else:
            parsed.append(None)
            continue
        parsed.append(
            {
                "bracket_center_f": native_to_f(center, str(row.market_unit)),
                "is_bottom": int(bracket.bottom),
                "is_top": int(bracket.top),
            }
        )
    parsed_frame = pd.DataFrame(parsed, index=rows.index)
    rows = pd.concat([rows, parsed_frame], axis=1)
    rows = rows[rows["bracket_center_f"].notna()].copy()

    rows["label_yes"] = rows["rung"].astype(str).eq(rows["bracket"].astype(str)).astype(int)
    rows["label_no"] = 1 - rows["label_yes"]
    rows["market_no_mid"] = (
        rows["no_direct_bid"] + rows["no_direct_ask"]
    ) / 2.0
    rows["market_yes_mid"] = 1.0 - rows["market_no_mid"]
    clipped_market = rows["market_yes_mid"].clip(0.001, 0.999)
    rows["market_yes_logit"] = np.log(clipped_market / (1.0 - clipped_market))
    rows["distance_to_bracket_f"] = (
        rows["bracket_center_f"] - rows["forecast_max_f"]
    )
    rows["distance_to_bracket_sq"] = rows["distance_to_bracket_f"] ** 2
    rows["innovation_sq"] = rows["forecast_innovation_f"] ** 2
    rows["innovation_x_distance"] = (
        rows["forecast_innovation_f"] * rows["distance_to_bracket_f"]
    )
    rows["innovation_x_bottom"] = (
        rows["forecast_innovation_f"] * rows["is_bottom"]
    )
    rows["innovation_x_top"] = rows["forecast_innovation_f"] * rows["is_top"]
    rows["state_weight"] = 1.0 / rows.groupby("tmax_state_id")[
        "tmax_state_id"
    ].transform("size")
    rows["fee_per_share"] = (
        WEATHER_FEE_RATE
        * rows["no_direct_ask"]
        * (1.0 - rows["no_direct_ask"])
    )
    rows["effective_cost_per_share"] = (
        rows["no_direct_ask"] + rows["fee_per_share"]
    )
    return rows


def model_pipeline() -> Pipeline:
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            (
                "model",
                LogisticRegression(
                    C=REGULARIZATION_C,
                    solver="lbfgs",
                    max_iter=3_000,
                    random_state=BOOTSTRAP_SEED,
                ),
            ),
        ]
    )


def expanding_oof(rows: pd.DataFrame) -> pd.DataFrame:
    records: list[pd.DataFrame] = []
    for checkpoint, checkpoint_rows in rows.groupby("checkpoint_hour_local"):
        dates = sorted(checkpoint_rows["target_date"].unique())
        for index, test_date in enumerate(dates):
            train_dates = dates[:index]
            if len(train_dates) < MIN_TRAIN_DATES:
                continue
            train = checkpoint_rows[
                checkpoint_rows["target_date"].isin(train_dates)
            ].copy()
            test = checkpoint_rows[
                checkpoint_rows["target_date"].eq(test_date)
            ].copy()
            if train["label_yes"].nunique() < 2:
                continue
            scored = test[
                [
                    "tmax_state_id",
                    "city",
                    "target_date",
                    "checkpoint_hour_local",
                    "region",
                    "rung",
                    "label_yes",
                    "label_no",
                    "market_yes_mid",
                    "market_no_mid",
                    "state_weight",
                    "no_direct_ask",
                    "no_direct_ask_size",
                    "fee_per_share",
                    "effective_cost_per_share",
                    "forecast_innovation_f",
                    "distance_to_bracket_f",
                    "is_bottom",
                    "is_top",
                ]
            ].copy()
            scored["train_dates"] = len(train_dates)
            scored["train_through_date"] = max(train_dates)
            scored["pred_market_raw_yes"] = test["market_yes_mid"].to_numpy()
            for variant, features in FEATURES.items():
                if variant == "market_raw":
                    continue
                model = model_pipeline()
                model.fit(
                    train[features],
                    train["label_yes"],
                    model__sample_weight=train["state_weight"],
                )
                scored[f"pred_{variant}_yes"] = model.predict_proba(
                    test[features]
                )[:, 1]
            records.append(scored)
    return pd.concat(records, ignore_index=True) if records else pd.DataFrame()


def date_block_ci(
    frame: pd.DataFrame,
    value_column: str,
    *,
    samples: int = BOOTSTRAP_SAMPLES,
) -> tuple[float, float]:
    daily = frame.groupby("target_date")[value_column].mean().to_numpy(dtype=float)
    if not len(daily):
        return math.nan, math.nan
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    draws = rng.choice(daily, size=(samples, len(daily)), replace=True).mean(axis=1)
    return float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))


def probability_scorecard(oof: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    candidate = "market_plus_innovation"
    baseline = "market_plus_distance"
    for checkpoint, group in oof.groupby("checkpoint_hour_local"):
        losses: dict[str, pd.DataFrame] = {}
        for variant in FEATURES:
            probability = group[f"pred_{variant}_yes"].clip(1e-6, 1 - 1e-6)
            scored = group[
                ["tmax_state_id", "target_date", "state_weight"]
            ].copy()
            scored["logloss"] = -(
                group["label_yes"] * np.log(probability)
                + group["label_no"] * np.log(1.0 - probability)
            )
            scored["brier"] = (probability - group["label_yes"]) ** 2
            scored["weighted_logloss"] = scored["logloss"] * scored["state_weight"]
            scored["weighted_brier"] = scored["brier"] * scored["state_weight"]
            state = (
                scored.groupby(["tmax_state_id", "target_date"], as_index=False)
                .agg(
                    logloss=("weighted_logloss", "sum"),
                    brier=("weighted_brier", "sum"),
                )
            )
            losses[variant] = state
        base = losses[baseline].rename(
            columns={"logloss": "base_logloss", "brier": "base_brier"}
        )
        for variant in FEATURES:
            state = losses[variant].merge(
                base,
                on=["tmax_state_id", "target_date"],
                validate="one_to_one",
            )
            state["logloss_delta_vs_distance"] = (
                state["logloss"] - state["base_logloss"]
            )
            state["brier_delta_vs_distance"] = state["brier"] - state["base_brier"]
            log_low, log_high = date_block_ci(
                state, "logloss_delta_vs_distance"
            )
            brier_low, brier_high = date_block_ci(
                state, "brier_delta_vs_distance"
            )
            records.append(
                {
                    "checkpoint_hour_local": int(checkpoint),
                    "variant": variant,
                    "expressions": len(group),
                    "states": int(group["tmax_state_id"].nunique()),
                    "dates": int(group["target_date"].nunique()),
                    "date_equal_logloss": float(
                        state.groupby("target_date")["logloss"].mean().mean()
                    ),
                    "date_equal_brier": float(
                        state.groupby("target_date")["brier"].mean().mean()
                    ),
                    "logloss_delta_vs_distance": float(
                        state["logloss_delta_vs_distance"].mean()
                    ),
                    "logloss_delta_ci_low": log_low,
                    "logloss_delta_ci_high": log_high,
                    "brier_delta_vs_distance": float(
                        state["brier_delta_vs_distance"].mean()
                    ),
                    "brier_delta_ci_low": brier_low,
                    "brier_delta_ci_high": brier_high,
                    "is_primary_candidate": variant == candidate,
                }
            )
    return pd.DataFrame(records)


def select_trades(oof: pd.DataFrame, variant: str) -> pd.DataFrame:
    rows = oof.copy()
    rows["p_no"] = 1.0 - rows[f"pred_{variant}_yes"]
    rows["fee_adjusted_edge"] = (
        rows["p_no"] - rows["effective_cost_per_share"]
    )
    rows = rows[rows["no_direct_ask_size"].ge(MIN_ASK_SIZE)].copy()
    rows = (
        rows.sort_values(
            ["tmax_state_id", "fee_adjusted_edge", "rung"],
            ascending=[True, False, True],
        )
        .drop_duplicates("tmax_state_id", keep="first")
        .copy()
    )
    rows["eligible"] = rows["fee_adjusted_edge"].ge(EDGE_BUFFER)
    rows = rows[rows["eligible"]].copy()
    rows["shares"] = SHARES
    rows["cost"] = SHARES * rows["no_direct_ask"]
    rows["fee"] = SHARES * rows["fee_per_share"]
    rows["payout"] = SHARES * rows["label_no"]
    rows["pnl"] = rows["payout"] - rows["cost"] - rows["fee"]
    rows["variant"] = variant
    return rows


def roi_ci(trades: pd.DataFrame) -> tuple[float, float]:
    if trades.empty:
        return math.nan, math.nan
    daily = trades.groupby("target_date", as_index=False).agg(
        pnl=("pnl", "sum"),
        capital=("cost", "sum"),
    )
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    indices = rng.integers(0, len(daily), size=(BOOTSTRAP_SAMPLES, len(daily)))
    pnl = daily["pnl"].to_numpy()[indices].sum(axis=1)
    capital = daily["capital"].to_numpy()[indices].sum(axis=1)
    roi = np.divide(pnl, capital, out=np.full_like(pnl, np.nan), where=capital > 0)
    return float(np.nanquantile(roi, 0.025)), float(np.nanquantile(roi, 0.975))


def trade_scorecard(oof: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    trade_frames: list[pd.DataFrame] = []
    records: list[dict[str, Any]] = []
    for checkpoint in CHECKPOINTS:
        checkpoint_rows = oof[oof["checkpoint_hour_local"].eq(checkpoint)]
        for variant in FEATURES:
            trades = select_trades(checkpoint_rows, variant)
            trade_frames.append(trades)
            low, high = roi_ci(trades)
            capital = float(trades["cost"].sum()) if len(trades) else 0.0
            records.append(
                {
                    "checkpoint_hour_local": checkpoint,
                    "variant": variant,
                    "opportunity_states": int(
                        checkpoint_rows["tmax_state_id"].nunique()
                    ),
                    "trades": len(trades),
                    "dates": int(trades["target_date"].nunique()) if len(trades) else 0,
                    "cities": int(trades["city"].nunique()) if len(trades) else 0,
                    "wins": int(trades["label_no"].sum()) if len(trades) else 0,
                    "mean_ask": float(trades["no_direct_ask"].mean())
                    if len(trades)
                    else math.nan,
                    "capital": capital,
                    "fee": float(trades["fee"].sum()) if len(trades) else 0.0,
                    "pnl": float(trades["pnl"].sum()) if len(trades) else 0.0,
                    "roi": float(trades["pnl"].sum() / capital)
                    if capital
                    else math.nan,
                    "roi_ci_low": low,
                    "roi_ci_high": high,
                }
            )
    detail = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    return detail, pd.DataFrame(records)


def paired_trade_delta(
    trades: pd.DataFrame,
    *,
    candidate: str = "market_plus_innovation",
    baseline: str = "market_plus_distance",
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for checkpoint in CHECKPOINTS:
        daily = (
            trades[
                trades["checkpoint_hour_local"].eq(checkpoint)
                & trades["variant"].isin([candidate, baseline])
            ]
            .groupby(["target_date", "variant"], as_index=False)
            .agg(pnl=("pnl", "sum"))
            .pivot(index="target_date", columns="variant", values="pnl")
            .fillna(0.0)
        )
        if candidate not in daily or baseline not in daily:
            continue
        daily["pnl_delta"] = daily[candidate] - daily[baseline]
        low, high = date_block_ci(
            daily.reset_index(), "pnl_delta"
        )
        records.append(
            {
                "checkpoint_hour_local": checkpoint,
                "dates": len(daily),
                "candidate_pnl": float(daily[candidate].sum()),
                "baseline_pnl": float(daily[baseline].sum()),
                "pnl_delta": float(daily["pnl_delta"].sum()),
                "date_mean_pnl_delta": float(daily["pnl_delta"].mean()),
                "date_mean_delta_ci_low": low,
                "date_mean_delta_ci_high": high,
            }
        )
    return pd.DataFrame(records)


def markdown_table(frame: pd.DataFrame, columns: list[str]) -> str:
    def cell(value: Any) -> str:
        if value is None or (isinstance(value, float) and not math.isfinite(value)):
            return "NA"
        if isinstance(value, (float, np.floating)):
            return f"{float(value):.4f}"
        return str(value)

    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in frame.reindex(columns=columns).to_dict("records"):
        lines.append("| " + " | ".join(cell(row[column]) for column in columns) + " |")
    return "\n".join(lines)


def write_report(
    path: Path,
    *,
    states: pd.DataFrame,
    expressions: pd.DataFrame,
    oof: pd.DataFrame,
    probability: pd.DataFrame,
    trades: pd.DataFrame,
    trade_summary: pd.DataFrame,
    paired: pd.DataFrame,
) -> None:
    candidate_probability = probability[
        probability["variant"].eq("market_plus_innovation")
    ]
    candidate_trades = trade_summary[
        trade_summary["variant"].eq("market_plus_innovation")
    ]
    lines = [
        "# Morning innovation NO residual v1",
        "",
        "## 结论与动作",
        "",
        "本策略把 forecast innovation 落成同刻 market-anchored exact-bracket NO："
        "每个 city-day/checkpoint 只买 fee-adjusted EV 最大的一档 direct NO。"
        "它是 retrospective expanding OOF，不是 actual fills；三门与动作见末尾。",
        "",
        "## Frozen contract",
        "",
        f"- checkpoints：每个城市当地 `{CHECKPOINTS[0]:02d}:00` / `{CHECKPOINTS[1]:02d}:00`，"
        "IANA timezone 已由上游 checkpoint artifact 验证。",
        "- probability：同刻 NO midpoint 为 market prior；baseline 加 forecast bracket distance；"
        "candidate 再加 `innovation × signed bracket distance`、innovation² 与 open-tail interaction。",
        f"- train：严格 prior target dates，至少 {MIN_TRAIN_DATES} 天；"
        f"`LogisticRegression C={REGULARIZATION_C}`，不含 city/region selector。",
        f"- execution：direct NO ask、ask size≥{MIN_ASK_SIZE:g}、官方 Weather fee "
        f"`{WEATHER_FEE_RATE:.2f}*p*(1-p)`、edge≥{EDGE_BUFFER:.2f}、"
        f"每 state 一档、{SHARES:g} shares、hold to settlement。",
        "",
        "## Signal funnel",
        "",
        f"- settled checkpoint states：{states['tmax_state_id'].nunique():,} states / "
        f"{states['target_date'].nunique()} dates。",
        f"- direct two-sided NO expression rows：{len(expressions):,} expressions / "
        f"{expressions['tmax_state_id'].nunique():,} states。",
        f"- expanding OOF：{len(oof):,} expressions / "
        f"{oof['tmax_state_id'].nunique():,} states / {oof['target_date'].nunique()} dates。",
        "",
        "## Evidence funnel",
        "",
        f"- direct NO bid+ask：{len(expressions):,} expression rows。",
        f"- top ask depth≥5：{int(expressions['no_direct_ask_size'].ge(MIN_ASK_SIZE).sum()):,} rows。",
        f"- candidate selected replay：{len(trades[trades['variant'].eq('market_plus_innovation')]):,} "
        "research trades；actual fills=0。",
        "",
        "## Probability quality（state/date equal）",
        "",
        markdown_table(
            probability,
            [
                "checkpoint_hour_local",
                "variant",
                "expressions",
                "states",
                "dates",
                "date_equal_logloss",
                "date_equal_brier",
                "logloss_delta_vs_distance",
                "logloss_delta_ci_low",
                "logloss_delta_ci_high",
            ],
        ),
        "",
        "主检验是 candidate 相对完全同 rows 的 `market_plus_distance`；delta<0 才表示 improvement。",
        "",
        "## Fee-adjusted executable replay",
        "",
        markdown_table(
            trade_summary,
            [
                "checkpoint_hour_local",
                "variant",
                "opportunity_states",
                "trades",
                "dates",
                "wins",
                "mean_ask",
                "capital",
                "fee",
                "pnl",
                "roi",
                "roi_ci_low",
                "roi_ci_high",
            ],
        ),
        "",
        "## Candidate vs no-innovation paired daily PnL",
        "",
        markdown_table(
            paired,
            [
                "checkpoint_hour_local",
                "dates",
                "candidate_pnl",
                "baseline_pnl",
                "pnl_delta",
                "date_mean_pnl_delta",
                "date_mean_delta_ci_low",
                "date_mean_delta_ci_high",
            ],
        ),
        "",
        "## Three gates",
        "",
    ]
    probability_pass = bool(
        len(candidate_probability)
        and candidate_probability["logloss_delta_ci_high"].lt(0).all()
    )
    execution_pass = bool(
        len(candidate_trades)
        and candidate_trades["roi_ci_low"].gt(0).all()
    )
    lines.extend(
        [
            f"- significance/probability：{'PASS' if probability_pass else 'FAIL'}。",
            f"- fee-adjusted execution：{'PASS' if execution_pass else 'FAIL'}。",
            "- fresh frozen forward：NA。",
            "",
            "```text",
            f"status={'shadow_candidate' if probability_pass and execution_pass else 'inconclusive'}",
            "live_action=none",
            "actual_fill_class=research_replay",
            "```",
            "",
            "无论历史点估如何，本轮不按 region/city/price 继续切片造 gate；"
            "只有 probability 与 execution 同时过门，才允许进入完整分母 zero-notional forward。",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    states = pd.read_csv(args.input)
    states = states[
        states["checkpoint_hour_local"].isin(CHECKPOINTS)
        & states["forecast_innovation_f"].notna()
        & states["forecast_max_f"].notna()
    ].copy()
    conn = connect_ro(args.db)
    quotes = load_quotes(
        conn, states["ladder_snapshot_id"].astype(str).drop_duplicates().tolist()
    )
    conn.close()
    expressions = build_expression_rows(states, quotes)
    oof = expanding_oof(expressions)
    if oof.empty:
        raise RuntimeError("expanding OOF produced zero expression rows")
    probability = probability_scorecard(oof)
    trades, trade_summary = trade_scorecard(oof)
    paired = paired_trade_delta(trades)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    expressions.to_csv(args.output_dir / "expression_rows.csv", index=False)
    oof.to_csv(args.output_dir / "oof_expression_predictions.csv", index=False)
    probability.to_csv(args.output_dir / "probability_scorecard.csv", index=False)
    trades.to_csv(args.output_dir / "selected_trades.csv", index=False)
    trade_summary.to_csv(args.output_dir / "trade_scorecard.csv", index=False)
    paired.to_csv(args.output_dir / "paired_trade_delta.csv", index=False)
    summary = {
        "contract": {
            "checkpoints_local": list(CHECKPOINTS),
            "min_train_dates": MIN_TRAIN_DATES,
            "regularization_c": REGULARIZATION_C,
            "edge_buffer": EDGE_BUFFER,
            "shares": SHARES,
            "min_ask_size": MIN_ASK_SIZE,
            "weather_fee_rate": WEATHER_FEE_RATE,
        },
        "counts": {
            "states": int(states["tmax_state_id"].nunique()),
            "state_dates": int(states["target_date"].nunique()),
            "expression_rows": len(expressions),
            "quote_states": int(expressions["tmax_state_id"].nunique()),
            "oof_expression_rows": len(oof),
            "oof_states": int(oof["tmax_state_id"].nunique()),
            "oof_dates": int(oof["target_date"].nunique()),
        },
        "probability_scorecard": probability.to_dict("records"),
        "trade_scorecard": trade_summary.to_dict("records"),
        "paired_trade_delta": paired.to_dict("records"),
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_report(
        args.report,
        states=states,
        expressions=expressions,
        oof=oof,
        probability=probability,
        trades=trades,
        trade_summary=trade_summary,
        paired=paired,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
