#!/usr/bin/env python3
"""Temperature-exhaustion BUY-NO strategy study (10-21h).

Hypothesis: the hour-of-day entry rule is a crude proxy. The real signal is
*exhaustion* — how far the current temperature has fallen below the day's
running max at decision time (decline_from_max). Once a station has clearly
rolled over, the probability of the final max jumping to a higher bracket
should collapse, regardless of clock hour. If the market still prices residual
uncertainty into above-running-max brackets at that moment, buying NO collects
it ("temperature theta").

Layers:
1. physical: P(bucket jump >= 1/2) by (group, hour, decline bucket)
2. market: realized tail-NO ROI by (group, decline bucket, hour, distance)
3. strategy: concrete entry rules, one entry per (city, day, bracket) at the
   earliest qualifying quote, daily PnL series + t-stat.

Settlement: official pm_history single-winner labels only. Whitelist cities use
wu_obs running max (station == official, validated); repaired cities use
official-station IEM running max.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[3]

WHITELIST_MIN_DAYS = 20
REPAIRED_CITIES = {"Paris", "London", "Milan", "Chicago", "KualaLumpur", "PanamaCity"}

DECLINE_BUCKETS = [
    (-99.0, 0.5, "<0.5"),
    (0.5, 1.0, "0.5-1.0"),
    (1.0, 2.0, "1.0-2.0"),
    (2.0, 99.0, ">=2.0"),
]


def decline_bucket(x: float) -> str | None:
    for lo, hi, name in DECLINE_BUCKETS:
        if lo <= x < hi:
            return name
    return None


def round_half_up(x: float) -> int:
    return math.floor(float(x) + 0.5)


def load_valid_city_days(path: Path) -> tuple[pd.DataFrame, set[str]]:
    df = pd.read_csv(path)
    valid = df[df["pm_history_valid"].fillna(False) & df["winner_count"].eq(1)].copy()
    valid["winner_label"] = valid["winner_labels"].astype(str)
    per_city = (
        valid.dropna(subset=["match_round"])
        .groupby("city")
        .agg(days=("match_round", "size"), match_rate=("match_round", "mean"))
    )
    whitelist = set(
        per_city[(per_city["match_rate"].eq(1.0)) & (per_city["days"].ge(WHITELIST_MIN_DAYS))].index
    )
    return valid[["city", "target_date", "winner_label"]], whitelist


def load_observed(wu_detail: Path, official_detail: Path, whitelist: set[str]) -> pd.DataFrame:
    wu = pd.read_csv(
        wu_detail,
        usecols=[
            "city", "target_date", "decision_hour_local",
            "running_max_c", "final_max_c", "current_temp_c", "running_max_f",
        ],
    )
    wu = wu[wu["city"].isin(whitelist)].copy()
    wu["group"] = "whitelist"
    # whitelist market value: F cities use F rounding (unit resolved at join time)
    off = pd.read_csv(official_detail)
    off["group"] = "repaired"
    off["running_max_f"] = off.apply(
        lambda r: r["running_max_raw"] if r["unit"] == "F" else r["running_max_c"] * 9 / 5 + 32,
        axis=1,
    )
    cols = [
        "group", "city", "target_date", "decision_hour_local",
        "running_max_c", "final_max_c", "current_temp_c", "running_max_f",
    ]
    obs = pd.concat([wu[cols], off[cols]], ignore_index=True)
    obs["decline"] = obs["running_max_c"] - obs["current_temp_c"]
    obs["decline_bucket"] = obs["decline"].apply(decline_bucket)
    obs["jump_c"] = obs.apply(
        lambda r: round_half_up(r["final_max_c"]) - round_half_up(r["running_max_c"]), axis=1
    )
    return obs


def physical_table(obs: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (group, hour, db), g in obs.groupby(["group", "decision_hour_local", "decline_bucket"]):
        rows.append(
            {
                "group": group,
                "decision_hour_local": hour,
                "decline_bucket": db,
                "n": len(g),
                "p_jump_ge1": float((g["jump_c"] >= 1).mean()),
                "p_jump_ge2": float((g["jump_c"] >= 2).mean()),
            }
        )
    return pd.DataFrame(rows)


def load_bottom_brackets(pm_history_dir: Path, city_days: pd.DataFrame) -> set[tuple[str, str, str]]:
    bottoms: set[tuple[str, str, str]] = set()
    for city, day in city_days[["city", "target_date"]].drop_duplicates().itertuples(index=False):
        f = pm_history_dir / f"{city}_{day}.json"
        if not f.exists():
            continue
        try:
            data = json.loads(f.read_text())
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        for b in data.get("brackets") or []:
            q = str(b.get("question") or "").lower()
            if "or below" in q or "or lower" in q:
                bottoms.add((city, day, str(b.get("label") or "").strip()))
    return bottoms


def tail_distance(row: pd.Series) -> float | None:
    low = row["bracket_low"]
    if pd.isna(low):
        return None
    rv = row["running_value"]
    if pd.isna(rv):
        return None
    if float(low) <= float(rv):
        return None
    if row["unit"] == "F":
        return math.ceil((float(low) - float(rv)) / 2.0)
    return float(low) - float(rv)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--quotes",
        nargs="+",
        default=[
            str(REPO / "docs/analysis/2026-06/generated/m3_orderbook_best_ask_v3_h10_13/m3_orderbook_best_ask_quotes.csv"),
            str(REPO / "docs/analysis/2026-06/generated/m3_orderbook_best_ask_v2_h14_17/m3_orderbook_best_ask_quotes.csv"),
            str(REPO / "docs/analysis/2026-06/generated/m3_orderbook_best_ask_v2_h18_19/m3_orderbook_best_ask_quotes.csv"),
            str(REPO / "docs/analysis/2026-06/generated/m3_orderbook_best_ask_v1/m3_orderbook_best_ask_quotes.csv"),
        ],
    )
    parser.add_argument(
        "--wu-detail",
        default=str(REPO / "docs/analysis/2026-06/generated/m3_observed_max_v3_h10_21/m3_observed_max_residual_detail.csv"),
    )
    parser.add_argument(
        "--official-detail",
        default=str(REPO / "docs/analysis/2026-06/generated/official_station_running_max_v0/official_station_running_max_detail.csv"),
    )
    parser.add_argument(
        "--alignment-city-days",
        default=str(REPO / "docs/analysis/2026-06/generated/m3_settlement_alignment_v1/m3_settlement_alignment_city_days.csv"),
    )
    parser.add_argument(
        "--pm-history-dir",
        default=str(REPO / "runtime/weather_edge_v1/market_data/cache/pm_history"),
    )
    parser.add_argument(
        "--output-dir",
        default=str(REPO / "docs/analysis/2026-06/generated/m3_exhaustion_no_v0"),
    )
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    valid, whitelist = load_valid_city_days(Path(args.alignment_city_days))
    obs = load_observed(Path(args.wu_detail), Path(args.official_detail), whitelist)

    phys = physical_table(obs)
    phys.to_csv(out_dir / "exhaustion_physical_table.csv", index=False)

    quotes = pd.concat(
        [pd.read_csv(p) for p in args.quotes if Path(p).exists()], ignore_index=True
    ).drop_duplicates(subset=["city", "target_date", "decision_hour_local", "bracket", "outcome"])

    q = quotes.merge(
        obs, on=["city", "target_date", "decision_hour_local"], how="inner"
    ).merge(valid, on=["city", "target_date"], how="inner")

    # group-specific running market value
    q["running_value"] = np.where(
        q["unit"].eq("F"),
        q["running_max_f"].apply(lambda x: round_half_up(x) if pd.notna(x) else np.nan),
        q["running_max_c"].apply(lambda x: round_half_up(x) if pd.notna(x) else np.nan),
    )

    bottoms = load_bottom_brackets(Path(args.pm_history_dir), q)
    q["is_bottom"] = q.apply(
        lambda r: (str(r["city"]), str(r["target_date"]), str(r["bracket"]).strip()) in bottoms,
        axis=1,
    )

    no = q[q["outcome"].eq("no") & ~q["is_bottom"]].copy()
    no["distance"] = no.apply(tail_distance, axis=1)
    tail = no[no["distance"].notna() & no["distance"].ge(1)].copy()
    tail["distance"] = tail["distance"].clip(upper=3).astype(int)
    tail = tail[(tail["best_ask"] >= 0.005) & (tail["best_ask"] <= 0.97)].copy()
    tail["lose"] = tail["winner_label"].astype(str).str.strip().eq(
        tail["bracket"].astype(str).str.strip()
    )
    tail["pnl"] = (1.0 - tail["best_ask"]).where(~tail["lose"], -tail["best_ask"])
    tail["notional"] = tail["best_ask"] * tail["best_ask_size"].fillna(0)
    tail.to_csv(out_dir / "exhaustion_tail_no_quotes.csv", index=False)

    # market cells
    cells = (
        tail.groupby(["group", "decline_bucket", "distance"])
        .agg(
            trades=("pnl", "size"),
            cities=("city", "nunique"),
            avg_ask=("best_ask", "mean"),
            win_rate=("lose", lambda s: 1 - s.mean()),
            cost=("best_ask", "sum"),
            pnl=("pnl", "sum"),
            notional=("notional", "sum"),
        )
        .reset_index()
    )
    cells["roi"] = cells["pnl"] / cells["cost"]
    cells.to_csv(out_dir / "exhaustion_market_cells.csv", index=False)

    cells_hour = (
        tail.groupby(["group", "decline_bucket", "decision_hour_local"])
        .agg(
            trades=("pnl", "size"),
            avg_ask=("best_ask", "mean"),
            win_rate=("lose", lambda s: 1 - s.mean()),
            cost=("best_ask", "sum"),
            pnl=("pnl", "sum"),
        )
        .reset_index()
    )
    cells_hour["roi"] = cells_hour["pnl"] / cells_hour["cost"]
    cells_hour.to_csv(out_dir / "exhaustion_market_cells_by_hour.csv", index=False)

    # strategy rules: one entry per (city, day, bracket), earliest qualifying hour
    def run_rule(name: str, df: pd.DataFrame, mask: pd.Series) -> dict | None:
        sel = df[mask].sort_values("decision_hour_local")
        sel = sel.drop_duplicates(subset=["city", "target_date", "bracket"], keep="first")
        if sel.empty:
            return None
        daily = sel.groupby("target_date")["pnl"].sum()
        cost = sel["best_ask"].sum()
        tstat = (
            float(daily.mean() / daily.std() * math.sqrt(len(daily)))
            if len(daily) > 1 and daily.std() > 0
            else float("nan")
        )
        return {
            "rule": name,
            "trades": len(sel),
            "city_days": sel.groupby(["city", "target_date"]).ngroups,
            "cities": sel["city"].nunique(),
            "days": len(daily),
            "pos_days": int((daily > 0).sum()),
            "avg_ask": float(sel["best_ask"].mean()),
            "win_rate": float(1 - sel["lose"].mean()),
            "cost": float(cost),
            "pnl": float(sel["pnl"].sum()),
            "roi": float(sel["pnl"].sum() / cost),
            "daily_tstat": tstat,
            "top_book_notional": float(sel["notional"].sum()),
        }

    exhausted = tail["decline"].ge(1.0)
    fresh = tail["decline"].lt(0.5)
    rules = []
    for group in ("whitelist", "repaired"):
        g = tail["group"].eq(group)
        rules.append(run_rule(f"{group}|exh>=1.0 h10-17 d>=1", tail, g & exhausted & tail["decision_hour_local"].between(10, 17)))
        rules.append(run_rule(f"{group}|exh>=1.0 h10-17 d>=2", tail, g & exhausted & tail["decision_hour_local"].between(10, 17) & tail["distance"].ge(2)))
        rules.append(run_rule(f"{group}|exh>=2.0 h10-17 d>=1", tail, g & tail["decline"].ge(2.0) & tail["decision_hour_local"].between(10, 17)))
        rules.append(run_rule(f"{group}|baseline h15-17 d>=1 (no exh)", tail, g & tail["decision_hour_local"].between(15, 17)))
        rules.append(run_rule(f"{group}|fresh(<0.5) h10-17 d>=1 (anti)", tail, g & fresh & tail["decision_hour_local"].between(10, 17)))
        rules.append(run_rule(f"{group}|exh>=1.0 h10-14 d>=1 (early only)", tail, g & exhausted & tail["decision_hour_local"].between(10, 14)))
    rules_df = pd.DataFrame([r for r in rules if r])
    rules_df.to_csv(out_dir / "exhaustion_strategy_rules.csv", index=False)

    manifest = {
        "experiment": "m3_exhaustion_no_v0",
        "whitelist_size": len(whitelist),
        "repaired": sorted(REPAIRED_CITIES),
        "decline_buckets": [b[2] for b in DECLINE_BUCKETS],
        "tail_quotes": int(len(tail)),
        "notes": [
            "decline = running_max_c - current_temp_c at decision time (C for all cities).",
            "Settlement: official pm_history single-winner label equality.",
            "One entry per (city, day, bracket) at earliest qualifying hour in rule tests.",
            "Top-of-book asks only; no fees/queue/slippage.",
        ],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    pd.set_option("display.width", 250)
    print("=== physical: P(jump>=1) by decline bucket (pooled hours 12-17) ===")
    pool = obs[obs["decision_hour_local"].between(12, 17)]
    pt = pool.groupby(["group", "decline_bucket"]).agg(
        n=("jump_c", "size"), p_jump_ge1=("jump_c", lambda s: (s >= 1).mean())
    )
    print(pt.round(4).to_string())
    print("\n=== market cells (group x decline x distance) ===")
    print(cells.round(3).to_string(index=False))
    print("\n=== strategy rules ===")
    print(rules_df.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
