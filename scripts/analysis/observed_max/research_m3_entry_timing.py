#!/usr/bin/env python3
"""M3 tail-NO entry timing study (14-21h).

For each entry hour and bracket distance above the running max, compare the
market premium offered on tail NO against the physical probability that the
final max lands in that bracket, settled against official pm_history winners.

City groups:
- whitelist: cities whose WU/IEM station aligns ~100% with official settlement
  (same construction as m3_tail_no_diagnosis).
- repaired: cities whose official station differs from our wu_obs station; for
  these the running max is recomputed from the official-station IEM cache and
  alignment was separately validated at ~100% (official_resolution_source_v0).

Outputs an EV-by-entry-hour table that is the basis for the entry timing
decision. No live action is derived directly from this script.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[3]

WHITELIST_MIN_DAYS = 20
ASK_CAP = 0.97  # >= 3c premium
ASK_FLOOR = 0.005

REPAIRED_CITIES = {"Paris", "London", "Milan", "Chicago", "KualaLumpur", "PanamaCity"}


def label_num(value: object) -> float | None:
    nums = re.findall(r"-?\d+(?:\.\d+)?", str(value))
    return float(nums[0]) if nums else None


def round_half_up(x: float) -> int:
    return math.floor(float(x) + 0.5)


def load_valid_city_days(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    valid = df[df["pm_history_valid"].fillna(False) & df["winner_count"].eq(1)].copy()
    valid["winner_label"] = valid["winner_labels"].astype(str)
    return valid[["city", "target_date", "unit", "winner_label", "match_round"]]


def build_whitelist(valid: pd.DataFrame) -> set[str]:
    per_city = (
        valid.dropna(subset=["match_round"])
        .groupby("city")
        .agg(days=("match_round", "size"), match_rate=("match_round", "mean"))
    )
    return set(per_city[(per_city["match_rate"].eq(1.0)) & (per_city["days"].ge(WHITELIST_MIN_DAYS))].index)


def load_joined(paths: list[Path]) -> pd.DataFrame:
    frames = []
    for p in paths:
        if p.exists():
            frames.append(pd.read_csv(p))
        else:
            print(f"!! joined file missing, skipped: {p}")
    joined = pd.concat(frames, ignore_index=True)
    joined = joined.drop_duplicates(
        subset=["city", "target_date", "decision_hour_local", "bracket", "outcome", "snapshot_ts_utc"]
    )
    return joined


def load_bottom_brackets(pm_history_dir: Path, city_days: pd.DataFrame) -> set[tuple[str, str, str]]:
    """Set of (city, target_date, label) whose question is an 'or below' tail.

    The orderbook joined CSV only carries the bracket label, so the bottom
    bracket of each market ('24°C or below' labelled '24') is indistinguishable
    from a point bracket without the question text from pm_history.
    """
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


def load_official_running(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    # market value for C cities: round(running_c); for F (Chicago) use raw F round.
    df["official_running_value"] = df.apply(
        lambda r: round_half_up(r["running_max_raw"]) if r["unit"] == "F" else round_half_up(r["running_max_c"]),
        axis=1,
    )
    return df[["city", "target_date", "decision_hour_local", "official_running_value"]]


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
        # F brackets are 2-degree ranges
        return math.ceil((float(low) - float(rv)) / 2.0)
    return float(low) - float(rv)


def physical_jump_table(wu_detail: Path, official_detail: Path, whitelist: set[str]) -> pd.DataFrame:
    wu = pd.read_csv(wu_detail, usecols=["city", "target_date", "decision_hour_local", "running_max_c", "final_max_c"])
    wu = wu[wu["city"].isin(whitelist)].copy()
    wu["group"] = "whitelist"
    off = pd.read_csv(official_detail)
    off = off.rename(columns={"round_c_bucket_delta": "jump_pre"})
    off["group"] = "repaired"
    wu["jump"] = wu.apply(
        lambda r: round_half_up(r["final_max_c"]) - round_half_up(r["running_max_c"]), axis=1
    )
    off["jump"] = off["jump_pre"]
    allj = pd.concat(
        [wu[["group", "city", "decision_hour_local", "jump"]], off[["group", "city", "decision_hour_local", "jump"]]],
        ignore_index=True,
    )
    rows = []
    for (group, hour), g in allj.groupby(["group", "decision_hour_local"]):
        n = len(g)
        for d in (1, 2, 3):
            rows.append(
                {
                    "group": group,
                    "decision_hour_local": hour,
                    "distance": d,
                    "p_land_exact": float((g["jump"] == d).mean()),
                    "p_jump_ge": float((g["jump"] >= d).mean()),
                    "n_city_hours": n,
                }
            )
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--joined",
        nargs="+",
        default=[
            str(REPO / "docs/analysis/2026-06/generated/m3_orderbook_best_ask_v2_h14_17/m3_orderbook_best_ask_joined.csv"),
            str(REPO / "docs/analysis/2026-06/generated/m3_orderbook_best_ask_v2_h18_19/m3_orderbook_best_ask_joined.csv"),
            str(REPO / "docs/analysis/2026-06/generated/m3_orderbook_best_ask_v1/m3_orderbook_best_ask_joined.csv"),
        ],
    )
    parser.add_argument(
        "--alignment-city-days",
        default=str(REPO / "docs/analysis/2026-06/generated/m3_settlement_alignment_v1/m3_settlement_alignment_city_days.csv"),
    )
    parser.add_argument(
        "--wu-observed-detail",
        default=str(REPO / "docs/analysis/2026-06/generated/m3_observed_max_v2_h14_21/m3_observed_max_residual_detail.csv"),
    )
    parser.add_argument(
        "--official-running-detail",
        default=str(REPO / "docs/analysis/2026-06/generated/official_station_running_max_v0/official_station_running_max_detail.csv"),
    )
    parser.add_argument(
        "--pm-history-dir",
        default=str(REPO / "runtime/weather_edge_v1/market_data/cache/pm_history"),
    )
    parser.add_argument(
        "--output-dir",
        default=str(REPO / "docs/analysis/2026-06/generated/m3_entry_timing_v0"),
    )
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    valid = load_valid_city_days(Path(args.alignment_city_days))
    whitelist = build_whitelist(valid)
    joined = load_joined([Path(p) for p in args.joined])

    official_running = load_official_running(Path(args.official_running_detail))
    joined = joined.merge(
        official_running, on=["city", "target_date", "decision_hour_local"], how="left"
    )

    joined["group"] = None
    joined.loc[joined["city"].isin(whitelist), "group"] = "whitelist"
    joined.loc[joined["city"].isin(REPAIRED_CITIES), "group"] = "repaired"
    joined = joined[joined["group"].notna()].copy()

    # running value: whitelist uses wu-based running_market_value from the join;
    # repaired uses the official-station running value (drop rows without it).
    joined["running_value"] = joined["running_market_value"]
    rep_mask = joined["group"].eq("repaired")
    joined.loc[rep_mask, "running_value"] = joined.loc[rep_mask, "official_running_value"]
    joined = joined[joined["running_value"].notna()].copy()

    # settle against official winners only
    joined = joined.merge(
        valid[["city", "target_date", "winner_label"]],
        on=["city", "target_date"],
        how="inner",
    )

    bottoms = load_bottom_brackets(Path(args.pm_history_dir), joined)
    joined["is_bottom_bracket"] = joined.apply(
        lambda r: (str(r["city"]), str(r["target_date"]), str(r["bracket"]).strip()) in bottoms,
        axis=1,
    )

    no = joined[joined["outcome"].eq("no") & ~joined["is_bottom_bracket"]].copy()
    no["distance"] = no.apply(tail_distance, axis=1)
    tail = no[no["distance"].notna() & no["distance"].ge(1)].copy()
    tail["distance"] = tail["distance"].clip(upper=3).astype(int)
    tail = tail[(tail["best_ask"] >= ASK_FLOOR) & (tail["best_ask"] <= ASK_CAP)].copy()

    tail["lose"] = tail["winner_label"].astype(str).str.strip().eq(tail["bracket"].astype(str).str.strip())
    tail["entry_cost"] = tail["best_ask"]
    tail["pnl"] = (1.0 - tail["best_ask"]).where(~tail["lose"], -tail["best_ask"])
    tail["premium_c"] = 1.0 - tail["best_ask"]
    tail["notional"] = tail["best_ask"] * tail["best_ask_size"].fillna(0)

    summary = (
        tail.groupby(["group", "decision_hour_local", "distance"])
        .agg(
            trades=("pnl", "size"),
            city_days=("target_date", lambda s: s.size),
            cities=("city", "nunique"),
            avg_ask=("best_ask", "mean"),
            med_ask=("best_ask", "median"),
            win_rate=("lose", lambda s: 1.0 - s.mean()),
            cost=("entry_cost", "sum"),
            pnl=("pnl", "sum"),
            top_book_notional=("notional", "sum"),
        )
        .reset_index()
    )
    summary["roi"] = summary["pnl"] / summary["cost"]

    by_hour = (
        tail.groupby(["group", "decision_hour_local"])
        .agg(
            trades=("pnl", "size"),
            cities=("city", "nunique"),
            avg_ask=("best_ask", "mean"),
            win_rate=("lose", lambda s: 1.0 - s.mean()),
            cost=("entry_cost", "sum"),
            pnl=("pnl", "sum"),
            top_book_notional=("notional", "sum"),
        )
        .reset_index()
    )
    by_hour["roi"] = by_hour["pnl"] / by_hour["cost"]

    by_city = (
        tail.groupby(["group", "city"])
        .agg(
            trades=("pnl", "size"),
            avg_ask=("best_ask", "mean"),
            win_rate=("lose", lambda s: 1.0 - s.mean()),
            cost=("entry_cost", "sum"),
            pnl=("pnl", "sum"),
        )
        .reset_index()
    )
    by_city["roi"] = by_city["pnl"] / by_city["cost"]

    jump_table = physical_jump_table(
        Path(args.wu_observed_detail), Path(args.official_running_detail), whitelist
    )

    ev = summary.merge(jump_table, on=["group", "decision_hour_local", "distance"], how="left")
    ev["fair_ask"] = 1.0 - ev["p_land_exact"]
    ev["edge_per_dollar"] = ev["roi"]

    tail_out = out_dir / "m3_entry_timing_tail_no_trades.csv"
    tail.to_csv(tail_out, index=False)
    summary.to_csv(out_dir / "m3_entry_timing_summary_by_hour_distance.csv", index=False)
    by_hour.to_csv(out_dir / "m3_entry_timing_summary_by_hour.csv", index=False)
    by_city.to_csv(out_dir / "m3_entry_timing_summary_by_city.csv", index=False)
    ev.to_csv(out_dir / "m3_entry_timing_ev_table.csv", index=False)
    jump_table.to_csv(out_dir / "m3_entry_timing_physical_jump_table.csv", index=False)

    manifest = {
        "experiment": "m3_entry_timing_v0",
        "ask_cap": ASK_CAP,
        "whitelist_size": len(whitelist),
        "whitelist": sorted(whitelist),
        "repaired": sorted(REPAIRED_CITIES),
        "joined_inputs": args.joined,
        "tail_no_quotes": int(len(tail)),
        "notes": [
            "Settlement uses official pm_history single-winner labels only.",
            "Repaired cities use official-station running max (IEM, validated ~100% alignment).",
            "NO on bracket loses iff that bracket is the official winner.",
            "No fees/queue/latency modeling; top-of-book asks only.",
        ],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    pd.set_option("display.width", 250)
    print("=== tail NO by entry hour (ask<=0.97) ===")
    print(by_hour.to_string(index=False))
    print("\n=== EV table (hour x distance) ===")
    cols = [
        "group", "decision_hour_local", "distance", "trades", "avg_ask",
        "win_rate", "roi", "p_land_exact", "fair_ask", "top_book_notional",
    ]
    print(ev[cols].to_string(index=False))


if __name__ == "__main__":
    main()
