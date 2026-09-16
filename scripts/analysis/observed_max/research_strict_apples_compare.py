#!/usr/bin/env python3
"""Apples-to-apples strict no-leak comparison: repaired vs whitelist.

Both groups: running max from raw IEM, cutoff strictly <= quote snapshot_ts
(no look-ahead), YES on the running-max bracket, settle via pm_history.
Resolves whether the 6 "repaired" cities retain any edge over whitelist once
data source and look-ahead are controlled identically.
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

REPAIRED = {"Paris": ("LFPB", "C"), "London": ("EGLC", "C"), "Milan": ("LIMC", "C"),
            "Chicago": ("KORD", "F"), "KualaLumpur": ("WMKK", "C"), "PanamaCity": ("MPMG", "C")}
# whitelist subset with cached IEM (raw), unit per city
WHITELIST = {"Chengdu": ("ZUUU", "C"), "Madrid": ("LEMD", "C"), "Chongqing": ("ZUCK", "C"),
             "BuenosAires": ("SAEZ", "C"), "Atlanta": ("KATL", "F"), "Wuhan": ("ZHHH", "C"),
             "Amsterdam": ("EHAM", "C"), "Ankara": ("LTAC", "C"), "Guangzhou": ("ZGGG", "C"),
             "Warsaw": ("EPWA", "C"), "CapeTown": ("FACT", "C"), "Helsinki": ("EFHK", "C")}

def load_obs(icao, unit, globs):
    for g in globs:
        fs = sorted(glob.glob(g.format(icao=icao)))
        if fs:
            df = pd.read_csv(fs[-1])
            col = "tmpf" if unit == "F" else "tmpc"
            df["valid"] = pd.to_datetime(df["valid"], utc=True, errors="coerce")
            df[col] = pd.to_numeric(df[col], errors="coerce")
            return df.dropna(subset=["valid", col]).sort_values("valid"), col
    return None, None

q = pd.read_csv(m3_orderbook_best_ask_quotes())
q = q[(q.outcome == "yes") & q.decision_hour_local.between(14, 16)].copy()
q["snap"] = pd.to_datetime(q["snapshot_ts_utc"], utc=True, errors="coerce")

def winner(city, day):
    return pm_history_winner_label(city, day)

def run(group, globs):
    obs = {}
    for city, (icao, unit) in group.items():
        df, col = load_obs(icao, unit, globs)
        if df is not None:
            obs[city] = (df, col, unit)
    rows = []
    for r in q[q.city.isin(obs)].itertuples():
        if pd.isna(r.snap): continue
        df, col, unit = obs[r.city]
        lo_t = pd.Timestamp(r.target_date + " 00:00", tz="UTC") - pd.Timedelta(hours=14)
        c = df[(df["valid"] <= r.snap) & (df["valid"] >= lo_t)]
        if c.empty: continue
        rv = rhu(c[col].max())
        lo = r.bracket_low if pd.notna(r.bracket_low) else -1e9
        hi = r.bracket_high if pd.notna(r.bracket_high) else 1e9
        if not (lo <= rv <= hi): continue
        if not (0.05 <= r.best_ask <= 0.95): continue
        w = winner(r.city, r.target_date)
        if w is None: continue
        rows.append(dict(city=r.city, date=r.target_date, hour=int(r.decision_hour_local),
                         bracket=str(r.bracket).strip(), ask=r.best_ask,
                         win=int(str(r.bracket).strip() == str(w).strip())))
    t = pd.DataFrame(rows).sort_values("hour").drop_duplicates(["city", "date", "bracket"], keep="first")
    se = math.sqrt(0.25 / len(t)) * 100 if len(t) else float("nan")
    edge = (t.win.mean() - t.ask.mean()) * 100 if len(t) else float("nan")
    return t, edge, se

rep_globs = ["runtime/rule_source_research/obs_cache/iem_{icao}_*.csv"]
wl_globs = ["runtime/rule_source_research/wl_iem_cache/iem_{icao}.csv"]
tr, er, ser = run(REPAIRED, rep_globs)
tw, ew, sew = run(WHITELIST, wl_globs)
print("=== STRICT no-leak, IEM running max, apples-to-apples ===")
print(f"REPAIRED 6城:  n={len(tr)} avg_ask={tr.ask.mean():.3f} realized={tr.win.mean():.3f} edge={er:+.1f}pp (SE~{ser:.1f})")
print(f"WHITELIST 12城: n={len(tw)} avg_ask={tw.ask.mean():.3f} realized={tw.win.mean():.3f} edge={ew:+.1f}pp (SE~{sew:.1f})")
print(f"\ngap repaired-whitelist = {er-ew:+.1f}pp")
