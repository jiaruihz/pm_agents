#!/usr/bin/env python3
"""Diagnose: is the strict repaired +10.3pp a broad pattern or a few lucky
cities/days? Per-city breakdown + drop-top-day concentration + first/second
half split. STRICT no-leak (obs <= snapshot_ts), IEM, pm_history settlement.
"""
from __future__ import annotations
import glob, json, math
from pathlib import Path
import pandas as pd

def rhu(x): return math.floor(float(x) + 0.5)
CFG = {"Paris": ("LFPB", "C"), "London": ("EGLC", "C"), "Milan": ("LIMC", "C"),
       "Chicago": ("KORD", "F"), "KualaLumpur": ("WMKK", "C"), "PanamaCity": ("MPMG", "C")}
obs = {}
for city, (icao, unit) in CFG.items():
    fs = sorted(glob.glob(f"runtime/rule_source_research/obs_cache/iem_{icao}_*.csv"))
    df = pd.read_csv(fs[-1]); col = "tmpf" if unit == "F" else "tmpc"
    df["valid"] = pd.to_datetime(df["valid"], utc=True, errors="coerce")
    df[col] = pd.to_numeric(df[col], errors="coerce")
    obs[city] = (df.dropna(subset=["valid", col]).sort_values("valid"), col, unit)

q = pd.concat([pd.read_csv(f) for f in glob.glob(
    "docs/analysis/2026-06/generated/m3_orderbook_best_ask_v2_h14_17/m3_orderbook_best_ask_quotes.csv")],
    ignore_index=True)
q = q[(q.outcome == "yes") & q.city.isin(CFG) & q.decision_hour_local.between(14, 16)].copy()
q["snap"] = pd.to_datetime(q["snapshot_ts_utc"], utc=True, errors="coerce")
pmdir = Path("runtime/weather_edge_v1/market_data/cache/pm_history")
def winner(city, day):
    f = pmdir / f"{city}_{day}.json"
    if not f.exists(): return None
    d = json.loads(f.read_text())
    if not isinstance(d, dict): return None
    ws = [str(b.get("label", "")).strip() for b in (d.get("brackets") or [])
          if (b.get("final_price") or 0) >= 0.99]
    return ws[0] if len(ws) == 1 else None

rows = []
for r in q.itertuples():
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
    rows.append(dict(city=r.city, date=r.target_date, ask=r.best_ask, hour=int(r.decision_hour_local),
                     bracket=str(r.bracket).strip(), pnl=(1 - r.best_ask) if str(r.bracket).strip() == str(w).strip() else -r.best_ask,
                     win=int(str(r.bracket).strip() == str(w).strip())))
t = pd.DataFrame(rows).sort_values("hour").drop_duplicates(["city", "date", "bracket"], keep="first")

def edge(d): return (d.win.mean() - d.ask.mean()) * 100 if len(d) else float("nan")
print(f"OVERALL strict: n={len(t)} edge={edge(t):+.1f}pp  total_pnl={t.pnl.sum():+.2f}")
print("\n--- per city (is it broad or 1-2 cities?) ---")
for city, g in t.groupby("city"):
    se = math.sqrt(0.25/len(g))*100
    print(f"  {city:12} n={len(g):3} edge={edge(g):+6.1f}pp (SE~{se:.0f})  pnl={g.pnl.sum():+.2f}")
print(f"  positive cities: {sum(edge(g)>0 for _,g in t.groupby('city'))}/6")

print("\n--- day concentration (drop best days) ---")
byday = t.groupby("date").pnl.sum().sort_values()
print(f"  total pnl={t.pnl.sum():+.2f} over {t.date.nunique()} days")
print(f"  drop top-1 day pnl: {t.pnl.sum()-byday.iloc[-1]:+.2f}")
print(f"  drop top-3 days pnl: {t.pnl.sum()-byday.iloc[-3:].sum():+.2f}")
print(f"  positive days: {(byday>0).sum()}/{len(byday)}")

print("\n--- first half vs second half of window ---")
mid = sorted(t.date.unique())[len(t.date.unique())//2]
for lbl, d in [("first half", t[t.date < mid]), ("second half", t[t.date >= mid])]:
    print(f"  {lbl}: n={len(d)} edge={edge(d):+.1f}pp")
