#!/usr/bin/env python3
"""NWP-locked NO test on whitelist cities (user hypothesis).

Hypothesis: combining decision-time NWP forecast (the day's forecast max from
live ECMWF, recorded in production paper snapshots) with METAR exhaustion
identifies city-days where the day max is locked; buying d1/d2 NO there should
be +EV even in whitelist cities if the market does not fully use NWP.

Data: production half-hourly paper snapshots (2026-05-05..), each record has
decision-time forecast_max_f, metar running max/current temp, and the NO-side
top of book — a complete no-lookahead decision dataset.

Variants per quote (city-local hour 13-17, d1/d2 NO, ask in [0.005, 0.97]):
  A metar_exh   : decline >= 1.0C                       (baseline, was negative)
  B nwp_locked  : forecast_max < d-bracket low          (NWP says cannot reach)
  C both        : A and B
Settlement: official pm_history single winner.
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from weather_data_feed.production_paths import historical_strategy_snapshots  # noqa: E402

WHITELIST_MIN_DAYS = 20
HOURS = set(range(13, 18))
ASK_MIN, ASK_MAX = 0.005, 0.97
WIN_THRESHOLD = 0.99


def round_half_up(x: float) -> int:
    return math.floor(float(x) + 0.5)


def f_to_c(f: float) -> float:
    return (f - 32.0) * 5.0 / 9.0


def load_whitelist(path: Path) -> set[str]:
    df = pd.read_csv(path)
    valid = df[df["pm_history_valid"].fillna(False) & df["winner_count"].eq(1)].dropna(subset=["match_round"])
    pc = valid.groupby("city").agg(days=("match_round", "size"), rate=("match_round", "mean"))
    return set(pc[(pc["rate"].eq(1.0)) & (pc["days"].ge(WHITELIST_MIN_DAYS))].index)


def load_winners(pm_history_dir: Path, city_days: set[tuple[str, str]]) -> dict[tuple[str, str], str]:
    winners: dict[tuple[str, str], str] = {}
    for city, day in city_days:
        f = pm_history_dir / f"{city}_{day}.json"
        if not f.exists():
            continue
        try:
            data = json.loads(f.read_text())
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        ws = [
            str(b.get("label") or "").strip()
            for b in (data.get("brackets") or [])
            if (b.get("final_price") or 0) >= WIN_THRESHOLD
        ]
        if len(ws) == 1:
            winners[(city, day)] = ws[0]
    return winners


def parse_bracket_low(label: str, question: str) -> tuple[float | None, bool]:
    """Return (low, is_bottom)."""
    lab = str(label).replace("°", "").strip()
    q = str(question).lower()
    nums = re.findall(r"-?\d+(?:\.\d+)?", lab)
    if not nums:
        return None, False
    if "or below" in q or "or lower" in q:
        return None, True
    return float(nums[0]), False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--snapshots-glob",
        default=str(historical_strategy_snapshots() / "snapshot_*.json"),
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
        default=str(REPO / "docs/analysis/2026-06/generated/m3_nwp_locked_no_v0"),
    )
    args = parser.parse_args()
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    whitelist = load_whitelist(Path(args.alignment_city_days))

    rows = []
    files = sorted(glob.glob(args.snapshots_glob))
    for fp in files:
        try:
            snap = json.loads(Path(fp).read_text())
        except (json.JSONDecodeError, OSError):
            continue
        for r in snap.get("records") or []:
            city = r.get("city")
            if city not in whitelist:
                continue
            if r.get("record_type") != "edge_signal":
                continue
            ts_local = r.get("ts_local")
            if not ts_local:
                continue
            try:
                hour = datetime.fromisoformat(ts_local).hour
            except ValueError:
                continue
            if hour not in HOURS:
                continue
            fx = r.get("forecast_max_f")
            ask = r.get("no_best_ask")
            if fx is None or ask is None:
                continue
            event_date = r.get("event_date")
            # only same-day markets (target date == local decision date)
            if not event_date or ts_local[:10] != event_date:
                continue
            unit = r.get("unit") or "C"
            low, is_bottom = parse_bracket_low(r.get("bracket") or "", r.get("question") or "")
            if low is None or is_bottom:
                continue
            forecast_v = float(fx) if unit == "F" else f_to_c(float(fx))
            ask = float(ask)
            if not (ASK_MIN <= ask <= ASK_MAX):
                continue
            rows.append(
                {
                    "city": city,
                    "target_date": event_date,
                    "hour_local": hour,
                    "ts_utc": r.get("ts_utc"),
                    "bracket": str(r.get("bracket")).strip(),
                    "unit": unit,
                    "bracket_low": low,
                    "no_best_ask": ask,
                    "no_ask_size": r.get("no_ask_size"),
                    "no_depth_ask_10c": r.get("no_depth_ask_10c"),
                    "forecast_v": round(forecast_v, 2),
                    "model_prob": r.get("model_prob"),
                }
            )

    q = pd.DataFrame(rows)
    if q.empty:
        print("no quotes extracted")
        return

    # METAR running max / decline at decision hour from wu_obs residual detail
    # (same decision-time-only construction as the exhaustion study).
    wu = pd.read_csv(
        REPO / "docs/analysis/2026-06/generated/m3_observed_max_v3_h10_21/m3_observed_max_residual_detail.csv",
        usecols=["city", "target_date", "decision_hour_local", "running_max_c", "running_max_f", "current_temp_c"],
    )
    wu["decline_c"] = wu["running_max_c"] - wu["current_temp_c"]
    q = q.merge(
        wu.rename(columns={"decision_hour_local": "hour_local"}),
        on=["city", "target_date", "hour_local"],
        how="inner",
    )
    q["running_value"] = q.apply(
        lambda r: round_half_up(r["running_max_f"]) if r["unit"] == "F" else round_half_up(r["running_max_c"]),
        axis=1,
    )
    q = q[q["bracket_low"] > q["running_value"]].copy()
    q["step"] = q["unit"].map({"F": 2, "C": 1})
    q["distance"] = ((q["bracket_low"] - q["running_value"]) / q["step"]).apply(math.ceil)
    q = q[q["distance"].le(2)].copy()
    q["nwp_locked"] = q["forecast_v"] < q["bracket_low"]
    q["nwp_margin"] = (q["bracket_low"] - q["forecast_v"]).round(2)
    if q.empty:
        print("no quotes after merge")
        return
    winners = load_winners(
        Path(args.pm_history_dir), set(zip(q["city"], q["target_date"], strict=True))
    )
    q["winner"] = q.apply(lambda r: winners.get((r["city"], r["target_date"])), axis=1)
    q = q[q["winner"].notna()].copy()
    q["lose"] = q["winner"].astype(str).str.strip().eq(q["bracket"])
    q["pnl"] = (1.0 - q["no_best_ask"]).where(~q["lose"], -q["no_best_ask"])
    q.to_csv(out_dir / "nwp_locked_no_quotes.csv", index=False)

    def run(name: str, mask: pd.Series) -> dict | None:
        sel = q[mask].sort_values("ts_utc").drop_duplicates(
            subset=["city", "target_date", "bracket"], keep="first"
        )
        if sel.empty:
            return None
        daily = sel.groupby("target_date")["pnl"].sum()
        return {
            "variant": name,
            "trades": len(sel),
            "cities": sel["city"].nunique(),
            "days": len(daily),
            "pos_days": int((daily > 0).sum()),
            "avg_ask": round(float(sel["no_best_ask"].mean()), 3),
            "win_rate": round(float(1 - sel["lose"].mean()), 3),
            "cost": round(float(sel["no_best_ask"].sum()), 2),
            "pnl": round(float(sel["pnl"].sum()), 2),
            "roi": round(float(sel["pnl"].sum() / sel["no_best_ask"].sum()), 4),
            "daily_tstat": round(
                float(daily.mean() / daily.std() * math.sqrt(len(daily)))
                if len(daily) > 1 and daily.std() > 0
                else float("nan"),
                2,
            ),
        }

    exh = q["decline_c"].ge(1.0)
    locked0 = q["nwp_locked"]
    locked_m = q["nwp_margin"].ge(1.0)  # forecast at least 1 unit below bracket
    results = [
        run("A metar_exh>=1.0", exh),
        run("B nwp_locked", locked0),
        run("B+ nwp_locked margin>=1", locked_m),
        run("C exh & nwp_locked", exh & locked0),
        run("C+ exh & margin>=1", exh & locked_m),
        run("anti: not locked", ~locked0),
        run("all", pd.Series(True, index=q.index)),
    ]
    res = pd.DataFrame([r for r in results if r])
    res.to_csv(out_dir / "nwp_locked_no_results.csv", index=False)

    # market-vs-NWP calibration: ask by nwp_margin bucket
    q["margin_bucket"] = pd.cut(q["nwp_margin"], [-10, -1, 0, 0.5, 1, 2, 10])
    calib = (
        q.groupby("margin_bucket", observed=True)
        .agg(
            n=("lose", "size"),
            realized_win=("lose", lambda s: 1 - s.mean()),
            avg_ask=("no_best_ask", "mean"),
        )
        .round(3)
    )
    calib.to_csv(out_dir / "nwp_margin_calibration.csv")

    manifest = {
        "experiment": "m3_nwp_locked_no_v0",
        "snapshot_files": len(files),
        "quotes": int(len(q)),
        "notes": [
            "All signals from the same decision-time snapshot record (no lookahead).",
            "forecast_max_f is the live ECMWF day-max forecast recorded in production.",
            "NO entries on d1/d2 brackets above METAR running value; official settlement.",
        ],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    pd.set_option("display.width", 220)
    print(f"quotes: {len(q)} over {q['target_date'].nunique()} days, {q['city'].nunique()} cities")
    print("\n=== market ask vs NWP margin (does market already price NWP?) ===")
    print(calib.to_string())
    print("\n=== variants ===")
    print(res.to_string(index=False))


if __name__ == "__main__":
    main()
