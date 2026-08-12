#!/usr/bin/env python3
"""Decisive look-ahead check for the station-basis YES-bucket edge.

Hypothesis: the +14pp "edge" is inflated because running max was computed
through the END of the decision hour while the market quote is near the START.
Test: recompute running max with obs strictly <= the quote's snapshot timestamp
(no leak) vs <= snapshot+1h (mimics end-of-hour leak). If the edge collapses
under the strict cutoff, it was look-ahead, not a market edge.
"""
from __future__ import annotations
import glob, json, math, sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.observed_max.historical_artifacts import (  # noqa: E402
    m3_orderbook_best_ask_quotes,
    pm_history_winner_label,
)

def rhu(x): return math.floor(float(x) + 0.5)

CFG = {"Paris": ("LFPB", "C"), "London": ("EGLC", "C"), "Milan": ("LIMC", "C"),
       "Chicago": ("KORD", "F"), "KualaLumpur": ("WMKK", "C"), "PanamaCity": ("MPMG", "C")}

obs = {}
for city, (icao, unit) in CFG.items():
    fs = sorted(glob.glob(f"runtime/rule_source_research/obs_cache/iem_{icao}_*.csv"))
    df = pd.read_csv(fs[-1])
    col = "tmpf" if unit == "F" else "tmpc"
    df["valid"] = pd.to_datetime(df["valid"], utc=True, errors="coerce")
    df[col] = pd.to_numeric(df[col], errors="coerce")
    obs[city] = (df.dropna(subset=["valid", col]).sort_values("valid"), col, unit)

q = pd.read_csv(m3_orderbook_best_ask_quotes())
q = q[(q.outcome == "yes") & q.city.isin(CFG) & q.decision_hour_local.between(14, 16)].copy()
q["snap"] = pd.to_datetime(q["snapshot_ts_utc"], utc=True, errors="coerce")

def winner(city, day):
    return pm_history_winner_label(city, day)

def calc(strict: bool):
    rows = []
    for r in q.itertuples():
        if pd.isna(r.snap):
            continue
        df, col, unit = obs[r.city]
        cut = r.snap if strict else r.snap + pd.Timedelta(hours=1)
        lo_t = pd.Timestamp(r.target_date + " 00:00", tz="UTC") - pd.Timedelta(hours=14)
        c = df[(df["valid"] <= cut) & (df["valid"] >= lo_t)]
        if c.empty:
            continue
        rv = rhu(c[col].max())
        lo = r.bracket_low if pd.notna(r.bracket_low) else -1e9
        hi = r.bracket_high if pd.notna(r.bracket_high) else 1e9
        if not (lo <= rv <= hi):
            continue
        if not (0.05 <= r.best_ask <= 0.95):
            continue
        w = winner(r.city, r.target_date)
        if w is None:
            continue
        rows.append(dict(city=r.city, date=r.target_date, hour=int(r.decision_hour_local),
                         bracket=str(r.bracket).strip(), ask=r.best_ask,
                         win=int(str(r.bracket).strip() == str(w).strip())))
    return pd.DataFrame(rows).sort_values("hour").drop_duplicates(["city", "date", "bracket"], keep="first")

for strict, lbl in [(True, "STRICT obs<=snapshot_ts (no leak)"),
                    (False, "LEAKY obs<=snapshot+1h (end-of-hour)")]:
    t = calc(strict)
    print(f"{lbl}: n={len(t)} avg_ask={t.ask.mean():.3f} realized={t.win.mean():.3f} "
          f"edge_pp={(t.win.mean()-t.ask.mean())*100:+.1f}")
