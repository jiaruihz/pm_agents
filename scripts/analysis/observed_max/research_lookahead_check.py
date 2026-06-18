#!/usr/bin/env python3
"""Decisive look-ahead check for the station-basis YES-bucket edge.

Hypothesis: the +14pp "edge" is inflated because running max was computed
through the END of the decision hour while the market quote is near the START.
Test: recompute running max with obs strictly <= the quote's snapshot timestamp
(no leak) vs <= snapshot+1h (mimics end-of-hour leak). If the edge collapses
under the strict cutoff, it was look-ahead, not a market edge.
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
    df = pd.read_csv(fs[-1])
    col = "tmpf" if unit == "F" else "tmpc"
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
    if not f.exists():
        return None
    d = json.loads(f.read_text())
    if not isinstance(d, dict):
        return None
    ws = [str(b.get("label", "")).strip() for b in (d.get("brackets") or [])
          if (b.get("final_price") or 0) >= 0.99]
    return ws[0] if len(ws) == 1 else None

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
