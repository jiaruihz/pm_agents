"""
backtest_weather_edge_engine_blended_entry_bands.py

Entry-band aware backtest for weather_edge_engine_blended_single_v0.

This answers: what if we keep the current live selector shape
(entry_price_window + min_edge) but replace raw model probability with the
market-anchored blended probability?

The script evaluates two selector profiles:
  - current_25_75: BUY_YES/BUY_NO entry 0.25-0.75, min_edge 0.10
  - current_side_band: BUY_YES 0.20-0.45 edge 0.20; BUY_NO 0.35-0.65 edge 0.10

This remains an opportunity-level counterfactual over fact_signal_candidates,
not actual live PnL.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd

from scripts.analysis.backtest_weather_edge_engine_blended_single import (  # noqa: E402
    INSTANCE_CASE,
    STRATEGY_ID,
    STRATEGY_SPEC,
    _current_live_actual,
    _data_self_check,
)
from scripts.analysis.eval_city_day_basket import (  # noqa: E402
    DB_DEFAULT,
    OUT_DEFAULT,
    Leg,
    _attribution,
    _summarize,
)
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
        profile_id="current_25_75",
        source_live_instance="mid_price_core_v1_25_75 / mid_price_core_v2_25_75 signal selector",
        buy_yes=SideBand(0.25, 0.75, 0.10),
        buy_no=SideBand(0.25, 0.75, 0.10),
    ),
    SelectorProfile(
        profile_id="current_side_band",
        source_live_instance="mid_price_core_v1_side_band signal selector",
        buy_yes=SideBand(0.20, 0.45, 0.20),
        buy_no=SideBand(0.35, 0.65, 0.10),
    ),
]


def _fetchall(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict]:
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _load_candidates(conn: sqlite3.Connection) -> pd.DataFrame:
    sql = """
        SELECT city, event_date, bracket, side,
               model_p_yes, market_yes_price,
               decision_entry_price, final_yes,
               live_filled, paper_ordered, eligible
        FROM fact_signal_candidates
        WHERE settlement_status = 'settled'
          AND decision_window_missing = 0
          AND model_p_yes IS NOT NULL
          AND market_yes_price IS NOT NULL
          AND decision_entry_price IS NOT NULL
          AND final_yes IS NOT NULL
    """
    return pd.read_sql_query(sql, conn)


def _band_for_side(profile: SelectorProfile, side: str) -> SideBand:
    return profile.buy_yes if side == "BUY_YES" else profile.buy_no


def _side_edge(side: str, p_yes: float, entry: float) -> float:
    if side == "BUY_YES":
        return p_yes - entry
    return (1.0 - p_yes) - entry


def _decide_profile(df: pd.DataFrame, profile: SelectorProfile, source: ProbSource) -> list[Leg]:
    blend_cfg = load_default_config()
    legs: list[Leg] = []
    for r in df.itertuples():
        side = str(r.side)
        entry = float(r.decision_entry_price)
        band = _band_for_side(profile, side)
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
        edge = _side_edge(side, p_used, entry)
        if edge < band.min_edge:
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


def _slice(df: pd.DataFrame, name: str) -> pd.DataFrame:
    if name == "full":
        return df
    if name == "holdout_from_2026_05_26":
        return df[df["event_date"] >= "2026-05-26"].reset_index(drop=True)
    if name == "recent_from_2026_06_01":
        return df[df["event_date"] >= "2026-06-01"].reset_index(drop=True)
    if name == "live_filled_only":
        return df[df["live_filled"] == 1].reset_index(drop=True)
    raise ValueError(f"unknown slice: {name}")


def _evaluate_profile(df: pd.DataFrame, profile: SelectorProfile) -> dict:
    raw_legs = _decide_profile(df, profile, "raw")
    blended_legs = _decide_profile(df, profile, "blended")
    raw_s = asdict(_summarize(f"{profile.profile_id}_raw", raw_legs))
    blended_s = asdict(_summarize(f"{profile.profile_id}_{STRATEGY_ID}", blended_legs))
    attr = _attribution(blended_legs, raw_legs)
    return {
        "profile": {
            "profile_id": profile.profile_id,
            "source_live_instance": profile.source_live_instance,
            "buy_yes": asdict(profile.buy_yes),
            "buy_no": asdict(profile.buy_no),
            "notional": profile.notional,
        },
        "raw": raw_s,
        "blended": blended_s,
        "attribution_blended_vs_raw": {
            "raw_winning_profit_missed_by_blend": attr["missed_profit_usd"],
            "raw_losing_cost_avoided_by_blend": attr["avoided_loss_usd"],
            "shared_profit": attr["shared_profit_usd"],
            "shared_loss": attr["shared_loss_usd"],
            "net_blended_vs_raw": attr["net_basket_vs_baseline_usd"],
        },
    }


def _actual_live_by_instance(conn: sqlite3.Connection) -> dict:
    rows = _fetchall(
        conn,
        f"""
        SELECT
          {INSTANCE_CASE} AS strategy_instance,
          COUNT(*) AS fills,
          SUM(cost_usd) AS cost_usd,
          SUM(pnl_usd_at_fill) AS pnl_usd,
          AVG(CAST(win_by_count AS REAL)) AS win_rate,
          MIN(target_date) AS min_target_date,
          MAX(target_date) AS max_target_date
        FROM fact_trades
        WHERE trade_class='live_real'
          AND settlement_status='settled'
        GROUP BY strategy_instance
        ORDER BY strategy_instance
        """,
    )
    for row in rows:
        cost = row.get("cost_usd") or 0.0
        row["roi"] = (row.get("pnl_usd") or 0.0) / cost if cost else 0.0
    return {"by_instance": rows}


def _write_md(report: dict, out_path: Path) -> None:
    lines = [
        "# Weather Edge Engine Blended Entry-Band Backtest",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> strategy_id: `{STRATEGY_ID}`",
        f"> strategy_spec: `{report['strategy_spec']}`",
        f"> db: `{report['db_path']}`",
        "",
        "## Target Metric",
        "",
        "`blended_entry_band_shadow` = keep the current live entry-price window and min-edge gates, but replace raw probability with blended probability for the side-aware edge.",
        "",
        "This is an opportunity-level counterfactual over `fact_signal_candidates`, not actual wallet PnL.",
        "",
        "## Selector Profiles",
        "",
        "| profile | source live instance | BUY_YES gate | BUY_NO gate |",
        "|---|---|---|---|",
    ]
    for p in report["profiles"]:
        yes = p["buy_yes"]
        no = p["buy_no"]
        lines.append(
            f"| {p['profile_id']} | {p['source_live_instance']} | "
            f"{yes['min_entry']:.2f}-{yes['max_entry']:.2f}, edge>={yes['min_edge']:.2f} | "
            f"{no['min_entry']:.2f}-{no['max_entry']:.2f}, edge>={no['min_edge']:.2f} |"
        )
    lines.extend([
        "",
        "## Data Self-Check",
        "",
        "```json",
        json.dumps(report["data_self_check"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## Backtest Summary",
        "",
        "| slice | profile | source | legs | cost | pnl | ROI | win_rate | top5 ROI |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ])
    for slice_name, block in report["slices"].items():
        for profile_id, result in block.items():
            for source in ("raw", "blended"):
                s = result[source]
                lines.append(
                    f"| {slice_name} | {profile_id} | {source} | {s['n_legs']} | "
                    f"${s['total_cost_usd']:.0f} | ${s['total_pnl_usd']:+.0f} | "
                    f"{s['roi']*100:+.2f}% | {s['win_rate']*100:.1f}% | "
                    f"{s['roi_excl_top5']*100:+.2f}% |"
                )
    lines.extend([
        "",
        "## Blended vs Raw Attribution",
        "",
        "| slice | profile | raw winning profit missed | raw losing cost avoided | net blended vs raw |",
        "|---|---|---:|---:|---:|",
    ])
    for slice_name, block in report["slices"].items():
        for profile_id, result in block.items():
            a = result["attribution_blended_vs_raw"]
            lines.append(
                f"| {slice_name} | {profile_id} | "
                f"${a['raw_winning_profit_missed_by_blend']:.0f} | "
                f"${a['raw_losing_cost_avoided_by_blend']:.0f} | "
                f"${a['net_blended_vs_raw']:+.0f} |"
            )
    lines.extend([
        "",
        "## Current Live Actual Fills",
        "",
        "These are actual settled `live_real` fills. They are not the same denominator as the opportunity replay.",
        "",
        "| strategy_instance | fills | dates | cost | pnl | ROI | win_rate |",
        "|---|---:|---|---:|---:|---:|---:|",
    ])
    for row in report["current_live_actual"]["by_instance"]:
        lines.append(
            f"| {row['strategy_instance']} | {row['fills']} | "
            f"{row['min_target_date']} -> {row['max_target_date']} | "
            f"${(row['cost_usd'] or 0):.0f} | ${(row['pnl_usd'] or 0):+.0f} | "
            f"{(row['roi'] or 0)*100:+.2f}% | {(row['win_rate'] or 0)*100:.1f}% |"
        )
    lines.extend([
        "",
        "## Read",
        "",
        "- This is the fairer comparison for PR3a: current selector gates are preserved, only the probability/edge source changes.",
        "- If blended has too few/no legs under a current gate, the blend is acting as a stricter confidence filter, not as a new execution strategy.",
        "- Production remains unchanged; this is still shadow research.",
    ])
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    db_path = DB_DEFAULT
    with sqlite3.connect(str(db_path)) as conn:
        df = _load_candidates(conn)
        slices = ["full", "holdout_from_2026_05_26", "recent_from_2026_06_01", "live_filled_only"]
        report = {
            "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "strategy_id": STRATEGY_ID,
            "strategy_spec": str(STRATEGY_SPEC.relative_to(_ROOT)),
            "db_path": str(db_path),
            "data_self_check": _data_self_check(conn),
            "profiles": [_evaluate_profile(df.iloc[:0].copy(), p) for p in PROFILES],
            "slices": {
                slice_name: {
                    p.profile_id: _evaluate_profile(_slice(df, slice_name), p)
                    for p in PROFILES
                }
                for slice_name in slices
            },
            "current_live_actual": _actual_live_by_instance(conn),
        }

    # Keep profile metadata compact in the top-level section.
    report["profiles"] = [entry["profile"] for entry in report["profiles"]]

    today = dt.date.today()
    out_dir = OUT_DEFAULT / today.strftime("%Y-%m")
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{today.isoformat()}-blended-entry-band-backtest.json"
    md_path = out_dir / f"{today.isoformat()}-blended-entry-band-backtest.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_md(report, md_path)

    for slice_name, block in report["slices"].items():
        for profile_id, result in block.items():
            raw = result["raw"]
            blended = result["blended"]
            print(
                f"{slice_name} {profile_id}: "
                f"raw n={raw['n_legs']} roi={raw['roi']*100:+.2f}% pnl={raw['total_pnl_usd']:+.2f}; "
                f"blend n={blended['n_legs']} roi={blended['roi']*100:+.2f}% pnl={blended['total_pnl_usd']:+.2f}"
            )
    print(f"JSON written to {json_path}")
    print(f"Markdown written to {md_path}")


if __name__ == "__main__":
    main()
