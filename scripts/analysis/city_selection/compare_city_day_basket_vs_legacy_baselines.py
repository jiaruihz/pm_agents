"""
compare_city_day_basket_vs_legacy_baselines.py

Fair baseline comparison for city-day basket research.

Question:
  Is the basket/optimizer improving over the legacy per-bucket independent
  selector, or are both just inheriting the same weather-model problem?

Method:
  - Use the same fact_signal_candidates representative decision snapshot
    used by prior PR2/PR2b research.
  - Compare against live-like legacy raw selector profiles, not only headline ROI.
  - Evaluate basket variants on:
      1. the same entry-band universe;
      2. the exact legacy raw-triggered opportunity universe.

Offline only. No N100/live behavior changed.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd

from scripts.analysis.city_selection.eval_city_day_basket import (  # noqa: E402
    DB_DEFAULT,
    OUT_DEFAULT,
    Leg,
    _attribution,
    _decide_basket,
    _summarize,
)
from scripts.analysis.city_selection.research_city_day_basket_optimizer import (  # noqa: E402
    _decide_combo_optimizer,
)
from weather_dashboard.basket import BasketConfig  # noqa: E402
from weather_dashboard.blend import blend_probability, load_default_config  # noqa: E402

ProbSource = Literal["raw", "blended"]


@dataclass(frozen=True)
class SideBand:
    min_entry: float
    max_entry: float
    min_edge: float


@dataclass(frozen=True)
class SelectorProfile:
    profile_id: str
    source_live_instance: str
    buy_yes: SideBand
    buy_no: SideBand
    notional: float = 5.0


PROFILES = [
    SelectorProfile(
        profile_id="legacy_25_75_e10",
        source_live_instance="mid_price_core_v1/v2 25-75 style",
        buy_yes=SideBand(0.25, 0.75, 0.10),
        buy_no=SideBand(0.25, 0.75, 0.10),
    ),
    SelectorProfile(
        profile_id="legacy_side_band",
        source_live_instance="mid_price_core_v1 side-band style",
        buy_yes=SideBand(0.20, 0.45, 0.20),
        buy_no=SideBand(0.35, 0.65, 0.10),
    ),
]


def _load_rows(db_path: Path) -> pd.DataFrame:
    sql = """
        SELECT city, event_date, bracket, side,
               model_p_yes, market_yes_price,
               decision_entry_price, final_yes,
               decision_hours_to_settle, decision_snapshot_ts_utc,
               live_filled, paper_ordered, eligible
        FROM fact_signal_candidates
        WHERE settlement_status = 'settled'
          AND decision_window_missing = 0
          AND model_p_yes IS NOT NULL
          AND market_yes_price IS NOT NULL
          AND decision_entry_price IS NOT NULL
          AND final_yes IS NOT NULL
    """
    with sqlite3.connect(str(db_path)) as conn:
        return pd.read_sql_query(sql, conn)


def _side_band(profile: SelectorProfile, side: str) -> SideBand:
    return profile.buy_yes if side == "BUY_YES" else profile.buy_no


def _side_edge(side: str, p_yes: float, entry: float) -> float:
    if side == "BUY_YES":
        return p_yes - entry
    return (1.0 - p_yes) - entry


def _slice(df: pd.DataFrame, name: str) -> pd.DataFrame:
    if name == "full":
        return df.reset_index(drop=True)
    if name == "holdout_from_2026_05_26":
        return df[df["event_date"] >= "2026-05-26"].reset_index(drop=True)
    if name == "recent_from_2026_06_01":
        return df[df["event_date"] >= "2026-06-01"].reset_index(drop=True)
    if name == "live_filled_only":
        return df[df["live_filled"] == 1].reset_index(drop=True)
    raise ValueError(f"unknown slice: {name}")


def _entry_band_universe(df: pd.DataFrame, profile: SelectorProfile) -> pd.DataFrame:
    mask = []
    for r in df.itertuples():
        band = _side_band(profile, str(r.side))
        entry = float(r.decision_entry_price)
        mask.append(band.min_entry <= entry < band.max_entry)
    return df[pd.Series(mask, index=df.index)].reset_index(drop=True)


def _decide_legacy_profile(
    df: pd.DataFrame,
    profile: SelectorProfile,
    source: ProbSource,
) -> list[Leg]:
    blend_cfg = load_default_config()
    legs: list[Leg] = []
    for r in df.itertuples():
        side = str(r.side)
        entry = float(r.decision_entry_price)
        band = _side_band(profile, side)
        if entry < band.min_entry or entry >= band.max_entry:
            continue
        raw_p = float(r.model_p_yes)
        if source == "raw":
            p_used = raw_p
        else:
            p_used = blend_probability(
                city=str(r.city),
                model_p_yes_raw=raw_p,
                market_implied_p_yes=float(r.market_yes_price),
                config=blend_cfg,
            ).p_yes_used
        if _side_edge(side, p_used, entry) < band.min_edge:
            continue
        legs.append(
            Leg(
                city=str(r.city),
                target_date=str(r.event_date),
                bracket=str(r.bracket),
                side=side,
                entry_price=entry,
                notional=profile.notional,
                final_yes=float(r.final_yes),
                p_yes_raw=raw_p,
                p_yes_used=p_used,
            )
        )
    return legs


def _legacy_trigger_universe(df: pd.DataFrame, legacy_legs: list[Leg]) -> pd.DataFrame:
    keys = {leg.key for leg in legacy_legs}
    rows = [
        (str(r.city), str(r.event_date), str(r.bracket), str(r.side)) in keys
        for r in df.itertuples()
    ]
    return df[pd.Series(rows, index=df.index)].reset_index(drop=True)


def _basket_config() -> BasketConfig:
    return BasketConfig(
        single_leg_notional_small=3.0,
        single_leg_notional_normal=8.0,
        city_day_notional_cap=15.0,
        max_no_legs_per_city_day=4,
        edge_small_threshold=0.03,
        edge_normal_threshold=0.06,
        prefer_no_over_yes=False,
    )


def _timing_summary(df: pd.DataFrame) -> dict:
    if df.empty:
        return {}
    hours = df["decision_hours_to_settle"].dropna()
    return {
        "n_rows": int(len(df)),
        "date_range": [str(df["event_date"].min()), str(df["event_date"].max())],
        "snapshot_ts_min": str(df["decision_snapshot_ts_utc"].min()),
        "snapshot_ts_max": str(df["decision_snapshot_ts_utc"].max()),
        "hours_to_settle_p10": float(hours.quantile(0.10)) if len(hours) else None,
        "hours_to_settle_median": float(hours.median()) if len(hours) else None,
        "hours_to_settle_p90": float(hours.quantile(0.90)) if len(hours) else None,
    }


def _summarize_rule(rule: str, legs: list[Leg], baseline: list[Leg] | None) -> dict:
    summary = asdict(_summarize(rule, legs))
    attr = None if baseline is None else _attribution(legs, baseline)
    return {
        "summary": summary,
        "vs_legacy_raw": attr,
    }


def _evaluate_profile(df: pd.DataFrame, profile: SelectorProfile) -> dict:
    blend_cfg = load_default_config()
    basket_cfg = _basket_config()

    legacy_raw = _decide_legacy_profile(df, profile, "raw")
    legacy_blended = _decide_legacy_profile(df, profile, "blended")

    entry_universe = _entry_band_universe(df, profile)
    raw_trigger_universe = _legacy_trigger_universe(df, legacy_raw)

    basket_entry = _decide_basket(entry_universe, blend_cfg, basket_cfg)
    basket_raw_triggers = _decide_basket(raw_trigger_universe, blend_cfg, basket_cfg)
    combo_entry = _decide_combo_optimizer(entry_universe, mode="market_risk")
    combo_raw_triggers = _decide_combo_optimizer(raw_trigger_universe, mode="market_risk")

    rules = {
        "legacy_raw_independent": _summarize_rule("legacy_raw_independent", legacy_raw, None),
        "legacy_blended_independent": _summarize_rule("legacy_blended_independent", legacy_blended, legacy_raw),
        "basket_pr2b_same_entry_band": _summarize_rule("basket_pr2b_same_entry_band", basket_entry, legacy_raw),
        "basket_pr2b_legacy_raw_triggers": _summarize_rule("basket_pr2b_legacy_raw_triggers", basket_raw_triggers, legacy_raw),
        "combo_market_risk_same_entry_band": _summarize_rule("combo_market_risk_same_entry_band", combo_entry, legacy_raw),
        "combo_market_risk_legacy_raw_triggers": _summarize_rule("combo_market_risk_legacy_raw_triggers", combo_raw_triggers, legacy_raw),
    }
    return {
        "profile": asdict(profile),
        "timing": _timing_summary(df),
        "entry_band_universe_rows": int(len(entry_universe)),
        "legacy_raw_trigger_rows": int(len(raw_trigger_universe)),
        "rules": rules,
    }


def _actual_live(conn: sqlite3.Connection) -> dict:
    conn.row_factory = sqlite3.Row
    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT
              COALESCE(strategy_id, strategy_name, execution_policy, 'unknown') AS strategy_instance,
              COUNT(*) AS fills,
              SUM(cost_usd) AS cost_usd,
              SUM(pnl_usd_at_fill) AS pnl_usd,
              AVG(CAST(win_by_count AS REAL)) AS win_rate,
              MIN(target_date) AS min_target_date,
              MAX(target_date) AS max_target_date
            FROM fact_trades
            WHERE trade_class='live_real'
              AND settlement_status='settled'
            GROUP BY COALESCE(strategy_id, strategy_name, execution_policy, 'unknown')
            ORDER BY strategy_instance
            """
        ).fetchall()
    ]
    for row in rows:
        cost = row.get("cost_usd") or 0.0
        row["roi"] = (row.get("pnl_usd") or 0.0) / cost if cost else 0.0
    return {"by_instance": rows}


def _write_markdown(report: dict, out_path: Path) -> None:
    lines = [
        "# City-Day Basket vs Legacy Bucket Baselines — 2026-06-06",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> DB: `{report['db_path']}`",
        "> Scope: offline fair-baseline diagnostic only; no N100/live behavior changed.",
        "",
        "## Target Question",
        "",
        "Does city-day basket selection improve over the legacy per-bucket independent selector on the same live-like decision timing and entry universe?",
        "",
        "This report separates two failure modes:",
        "",
        "- If both legacy and basket lose on the same slice, the weather/probability model or market regime is likely the first problem.",
        "- If legacy wins but basket loses, the city-day grouping/objective is likely the first problem.",
        "",
        "## Timing",
        "",
        "The source table is `fact_signal_candidates` with `decision_window_missing = 0`, i.e. the existing T-22~24h representative decision snapshot. It is close to the old live timing but is not a full 30-minute snapshot replay.",
        "",
        "## Rules",
        "",
        "- `legacy_raw_independent`: old-style per bucket raw model gate with live-like entry band and min edge.",
        "- `legacy_blended_independent`: same per bucket gate, replacing raw probability with blend.",
        "- `basket_pr2b_same_entry_band`: PR2b basket over the same entry-band universe.",
        "- `basket_pr2b_legacy_raw_triggers`: PR2b basket restricted to rows the legacy raw rule would have triggered.",
        "- `combo_market_risk_same_entry_band`: combo optimizer over the same entry-band universe.",
        "- `combo_market_risk_legacy_raw_triggers`: combo optimizer restricted to legacy raw triggered rows.",
        "",
        "## Backtest Summary",
        "",
        "| slice | profile | rule | n | cost | pnl | ROI | top5 ROI | missed vs legacy | avoided vs legacy |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for slice_name, slice_data in report["slices"].items():
        for profile_id, profile_data in slice_data.items():
            for rule, block in profile_data["rules"].items():
                s = block["summary"]
                attr = block["vs_legacy_raw"] or {}
                lines.append(
                    f"| {slice_name} | {profile_id} | {rule} | {s['n_legs']} | "
                    f"${s['total_cost_usd']:.0f} | ${s['total_pnl_usd']:+.0f} | "
                    f"{s['roi']*100:+.2f}% | {s['roi_excl_top5']*100:+.2f}% | "
                    f"${attr.get('missed_profit_usd', 0.0):.0f} | "
                    f"${attr.get('avoided_loss_usd', 0.0):.0f} |"
                )
    lines.extend([
        "",
        "## Actual Live Reference",
        "",
        "This is actual settled `live_real` PnL by recorded instance; it is not forced to the same representative snapshot, so use it as context, not a strict counterfactual.",
        "",
        "| instance | fills | date range | cost | pnl | ROI | win_rate |",
        "|---|---:|---|---:|---:|---:|---:|",
    ])
    for row in report["actual_live"]["by_instance"]:
        lines.append(
            f"| {row['strategy_instance']} | {row['fills']} | "
            f"{row['min_target_date']} -> {row['max_target_date']} | "
            f"${(row.get('cost_usd') or 0.0):.0f} | "
            f"${(row.get('pnl_usd') or 0.0):+.0f} | "
            f"{(row.get('roi') or 0.0)*100:+.2f}% | "
            f"{(row.get('win_rate') or 0.0)*100:.1f}% |"
        )
    lines.extend([
        "",
        "## Quant Read",
        "",
        "- Judge basket against `legacy_raw_independent` first; ROI alone is not enough.",
        "- `basket_pr2b_legacy_raw_triggers` isolates the grouping/sizing effect on the exact old-triggered rows.",
        "- `same_entry_band` variants test whether basket finds better opportunities when given the same live-like price universe.",
        "- Negative top5-removed ROI remains a tail-dependency warning even if headline ROI is positive.",
        "",
        "Production remains unchanged.",
    ])
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    df = _load_rows(DB_DEFAULT)
    slice_names = [
        "full",
        "holdout_from_2026_05_26",
        "recent_from_2026_06_01",
        "live_filled_only",
    ]
    slices = {}
    for slice_name in slice_names:
        sdf = _slice(df, slice_name)
        slices[slice_name] = {
            profile.profile_id: _evaluate_profile(sdf, profile)
            for profile in PROFILES
        }

    with sqlite3.connect(str(DB_DEFAULT)) as conn:
        actual_live = _actual_live(conn)

    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "db_path": str(DB_DEFAULT),
        "slices": slices,
        "actual_live": actual_live,
    }

    today = dt.date.today()
    out_dir = OUT_DEFAULT / today.strftime("%Y-%m")
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{today.isoformat()}-city-day-basket-vs-legacy-baselines.json"
    md_path = out_dir / f"{today.isoformat()}-city-day-basket-vs-legacy-baselines.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _write_markdown(report, md_path)

    for slice_name, slice_data in slices.items():
        print(f"\n== {slice_name} ==")
        for profile_id, profile_data in slice_data.items():
            print(f"-- {profile_id}")
            for rule, block in profile_data["rules"].items():
                s = block["summary"]
                print(
                    f"{rule:40s} n={s['n_legs']:4d} "
                    f"pnl={s['total_pnl_usd']:+8.2f} "
                    f"roi={s['roi']*100:+7.2f}% "
                    f"top5={s['roi_excl_top5']*100:+7.2f}%"
                )
    print(f"\nJSON written to {json_path}")
    print(f"Markdown written to {md_path}")


if __name__ == "__main__":
    main()
