#!/usr/bin/env python3
"""STRICT (no-leak) mechanism split for repaired-city YES-bucket edge.

Redo the same/differ-settle split with snapshot-aligned running max (no
look-ahead). Question: does the +10pp strict edge concentrate on days where
the official station and the default(wrong) station settle to DIFFERENT
brackets? If yes -> station-mismatch mechanism. If edge is on same-settle days
too -> mechanism is NOT station.
"""
from __future__ import annotations
import glob, json, math, sys
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.observed_max.historical_artifacts import (  # noqa: E402
    m3_orderbook_best_ask_quotes,
    pm_history_winner_label,
)

def rhu(x): return math.floor(float(x) + 0.5)

# city -> (official icao, wrong icao, unit, tz)
CFG = {"Paris": ("LFPB", "LFPG", "C", "Europe/Paris"),
       "London": ("EGLC", "EGLL", "C", "Europe/London"),
       "Milan": ("LIMC", "LIML", "C", "Europe/Rome"),
       "Chicago": ("KORD", "KMDW", "F", "America/Chicago"),
       "KualaLumpur": ("WMKK", "WMSA", "C", "Asia/Kuala_Lumpur"),
       "PanamaCity": ("MPMG", "MPTO", "C", "America/Panama")}

def load(icao, unit, globs):
    for g in globs:
        fs = sorted(glob.glob(g.format(icao=icao)))
        if fs:
            df = pd.read_csv(fs[-1]); col = "tmpf" if unit == "F" else "tmpc"
            df["valid"] = pd.to_datetime(df["valid"], utc=True, errors="coerce")
            df[col] = pd.to_numeric(df[col], errors="coerce")
            return df.dropna(subset=["valid", col]).sort_values("valid"), col
    return None, None

off_obs, wrong_obs = {}, {}
for city, (oi, wi, unit, tz) in CFG.items():
    off_obs[city] = load(oi, unit, ["runtime/rule_source_research/obs_cache/iem_{icao}_*.csv"])
    wrong_obs[city] = load(wi, unit, ["runtime/rule_source_research/wrong_station_cache/iem_{icao}_*.csv"])

# wrong-station FINAL bracket per city-day (local-day max)
wrong_final = {}
for city, (oi, wi, unit, tz) in CFG.items():
    df, col = wrong_obs[city]
    if df is None: continue
    d = df.copy()
    d["ld"] = d["valid"].dt.tz_convert(ZoneInfo(tz)).dt.date.astype(str)
    for day, g in d.groupby("ld"):
        wrong_final[(city, day)] = rhu(g[col].max())

q = pd.read_csv(m3_orderbook_best_ask_quotes())
q = q[(q.outcome == "yes") & q.city.isin(CFG) & q.decision_hour_local.between(14, 16)].copy()
q["snap"] = pd.to_datetime(q["snapshot_ts_utc"], utc=True, errors="coerce")

def winner(city, day):
    return pm_history_winner_label(city, day)

# official FINAL bracket per city-day (for divergence vs wrong)
off_final = {}
for city, (oi, wi, unit, tz) in CFG.items():
    df, col = off_obs[city]
    if df is None: continue
    d = df.copy(); d["ld"] = d["valid"].dt.tz_convert(ZoneInfo(tz)).dt.date.astype(str)
    for day, g in d.groupby("ld"):
        off_final[(city, day)] = rhu(g[col].max())

rows = []
for r in q.itertuples():
    if pd.isna(r.snap): continue
    df, col = off_obs[r.city]
    if df is None: continue
    lo_t = pd.Timestamp(r.target_date + " 00:00", tz="UTC") - pd.Timedelta(hours=14)
    c = df[(df["valid"] <= r.snap) & (df["valid"] >= lo_t)]   # STRICT no-leak
    if c.empty: continue
    rv = rhu(c[col].max())
    lo = r.bracket_low if pd.notna(r.bracket_low) else -1e9
    hi = r.bracket_high if pd.notna(r.bracket_high) else 1e9
    if not (lo <= rv <= hi): continue
    if not (0.05 <= r.best_ask <= 0.95): continue
    w = winner(r.city, r.target_date)
    if w is None: continue
    offf = off_final.get((r.city, r.target_date)); wrf = wrong_final.get((r.city, r.target_date))
    if offf is None or wrf is None: continue
    rows.append(dict(city=r.city, date=r.target_date, hour=int(r.decision_hour_local),
                     bracket=str(r.bracket).strip(), ask=r.best_ask,
                     win=int(str(r.bracket).strip() == str(w).strip()),
                     differ=int(offf != wrf)))
t = pd.DataFrame(rows).sort_values("hour").drop_duplicates(["city", "date", "bracket"], keep="first")
print("=== STRICT no-leak mechanism split (repaired YES-bucket) ===")
print(f"total n={len(t)} edge={(t.win.mean()-t.ask.mean())*100:+.1f}pp  differ-days={t.differ.sum()}/{len(t)}")
for lbl, sub in [("SAME-settle (站点无关)", t[t.differ == 0]), ("DIFFER-settle (站点起作用)", t[t.differ == 1])]:
    if len(sub) == 0: print(f"{lbl}: 0"); continue
    se = math.sqrt(0.25/len(sub))*100
    print(f"{lbl}: n={len(sub)} avg_ask={sub.ask.mean():.3f} realized={sub.win.mean():.3f} "
          f"edge={(sub.win.mean()-sub.ask.mean())*100:+.1f}pp (SE~{se:.1f})")
