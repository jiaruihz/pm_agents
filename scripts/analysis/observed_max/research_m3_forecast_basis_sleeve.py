#!/usr/bin/env python3
"""M3 forecast-basis sleeve research (v0).

Question: in the FORECAST window (pre-peak, ~10-13h local) of the 6 "basis"
cities (official resolution station != the station we / the crowd watch), can
the station basis already be captured from forecasts? If the forecast cannot
tell the two co-located airports apart, there is no forecast-window basis and
the early sleeve does not exist. A negative result is fully acceptable.

Three steps:
  1. station discrimination: open-meteo historical-forecast daily max at the
     official-station coords vs the wrong-station coords; distribution of
     forecast(official) - forecast(wrong).
  2. market alignment: at 10-13h local snapshots, which forecast (official vs
     wrong station) better explains the market-implied YES probability.
  3. early backtest (only if step 1 discriminates): 10-13h decision, buy the
     YES bracket implied by the OFFICIAL-station forecast max (and tail NO),
     settle against pm_history single winner. Control: same rule on whitelist
     cities (expected negative).

No look-ahead: we never use post-decision information. The early backtest uses
the morning forecast run that is available by 10-13h local on the event date.
open-meteo historical-forecast archives the forecast that was current; for a
past date this includes the same-day morning run, which is exactly what a
10-13h trader could see.

Settlement truth: pm_history single winner only.
open-meteo cache: runtime/rule_source_research/forecast_cache/
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.production_paths import historical_strategy_snapshots  # noqa: E402

# ---------------------------------------------------------------------------
# Station geometry. Official = Polymarket resolution station (from
# official_station_running_max_v0); wrong = station we/the crowd watch (the
# FULL_CITY_CONFIGS icao). Coordinates are IEM ASOS metadata (authoritative).
# ---------------------------------------------------------------------------
BASIS_STATIONS: dict[str, dict] = {
    "Paris": {
        "tz": "Europe/Paris",
        "unit": "C",
        "official": {"icao": "LFPB", "lat": 48.9672, "lon": 2.4272},
        "wrong": {"icao": "LFPG", "lat": 49.0153, "lon": 2.5344},
    },
    "London": {
        "tz": "Europe/London",
        "unit": "C",
        "official": {"icao": "EGLC", "lat": 51.5053, "lon": 0.0553},
        "wrong": {"icao": "EGLL", "lat": 51.4785, "lon": -0.4614},
    },
    "Milan": {
        "tz": "Europe/Rome",
        "unit": "C",
        "official": {"icao": "LIMC", "lat": 45.63, "lon": 8.7231},
        "wrong": {"icao": "LIML", "lat": 45.4614, "lon": 9.2626},
    },
    "Chicago": {
        "tz": "America/Chicago",
        "unit": "F",
        "official": {"icao": "KORD", "lat": 41.9602, "lon": -87.9316},
        "wrong": {"icao": "KMDW", "lat": 41.786, "lon": -87.7524},
    },
    "KualaLumpur": {
        "tz": "Asia/Kuala_Lumpur",
        "unit": "C",
        "official": {"icao": "WMKK", "lat": 2.7167, "lon": 101.7},
        "wrong": {"icao": "WMSA", "lat": 3.1323, "lon": 101.5817},
    },
    "PanamaCity": {
        "tz": "America/Panama",
        "unit": "C",
        "official": {"icao": "MPMG", "lat": 8.9833, "lon": -79.5167},
        "wrong": {"icao": "MPTO", "lat": 9.05, "lon": -79.3667},
    },
}

CACHE_DIR = Path("runtime/rule_source_research/forecast_cache")
HIST_FORECAST_API = "https://historical-forecast-api.open-meteo.com/v1/forecast"

EVAL_START = "2026-05-19"
EVAL_END = "2026-06-09"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def round_half_up(value: float) -> int:
    return int(math.floor(value + 0.5))


def c_to_f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


def fetch_daily_max(lat: float, lon: float, start: str, end: str) -> dict[str, float]:
    """open-meteo historical-forecast daily temperature_2m_max, cached on disk.

    Returns {date_iso: max_temp_c}. Cache key is rounded coords + range.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = f"hf_{lat:.4f}_{lon:.4f}_{start}_{end}.json"
    path = CACHE_DIR / key
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    url = (
        f"{HIST_FORECAST_API}?latitude={lat}&longitude={lon}"
        f"&start_date={start}&end_date={end}"
        f"&daily=temperature_2m_max&timezone=auto"
    )
    last_err = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                data = json.load(resp)
            break
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(2 * (attempt + 1))
    else:
        raise RuntimeError(f"open-meteo fetch failed for {lat},{lon}: {last_err}")
    daily = data.get("daily", {})
    times = daily.get("time", [])
    maxes = daily.get("temperature_2m_max", [])
    out = {t: m for t, m in zip(times, maxes) if m is not None}
    path.write_text(json.dumps(out), encoding="utf-8")
    time.sleep(0.4)
    return out


# ---------------------------------------------------------------------------
# settlement (pm_history single winner)
# ---------------------------------------------------------------------------
def load_pm_winner(pm_dir: Path, city: str, date: str) -> dict | None:
    path = pm_dir / f"{city}_{date}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    brackets = data.get("brackets")
    if not isinstance(brackets, list):
        return None
    winners = [str(b.get("label")) for b in brackets if b.get("final_price") == 1.0]
    if len(winners) != 1:
        return None
    return {
        "unit": str(data.get("unit") or "").upper(),
        "winner": winners[0],
        "labels": [str(b.get("label")) for b in brackets],
    }


def parse_bracket_bounds(label: str, unit: str) -> tuple[float | None, float | None]:
    """Return (low, high) in the market unit. Open brackets use None."""
    s = str(label).replace("°", "").replace("F", "").replace("C", "").strip()
    if s.endswith("+"):
        return float(s[:-1]), None
    if s.startswith("<="):
        return None, float(s[2:])
    if s.startswith("<"):
        return None, float(s[1:]) - 1e-9
    if "-" in s and not s.startswith("-"):
        left, right = s.split("-", 1)
        return float(left), float(right)
    x = float(s)
    return x, x


def forecast_to_label(forecast_value: float, labels: list[str], unit: str) -> str | None:
    """Map a forecast max (already in market unit, integer-rounded) to the bracket
    label that contains it."""
    for label in labels:
        low, high = parse_bracket_bounds(label, unit)
        if low is not None and forecast_value < low:
            continue
        if high is not None and forecast_value > high:
            continue
        return label
    return None


# ---------------------------------------------------------------------------
# STEP 1 — station discrimination
# ---------------------------------------------------------------------------
def step1_station_discrimination(dates: list[str]) -> pd.DataFrame:
    start, end = dates[0], dates[-1]
    rows: list[dict] = []
    for city, cfg in BASIS_STATIONS.items():
        off = fetch_daily_max(cfg["official"]["lat"], cfg["official"]["lon"], start, end)
        wrn = fetch_daily_max(cfg["wrong"]["lat"], cfg["wrong"]["lon"], start, end)
        for d in dates:
            if d not in off or d not in wrn:
                continue
            off_c = off[d]
            wrn_c = wrn[d]
            diff_c = off_c - wrn_c
            rows.append(
                {
                    "city": city,
                    "unit": cfg["unit"],
                    "target_date": d,
                    "official_icao": cfg["official"]["icao"],
                    "wrong_icao": cfg["wrong"]["icao"],
                    "forecast_official_c": round(off_c, 3),
                    "forecast_wrong_c": round(wrn_c, 3),
                    "diff_official_minus_wrong_c": round(diff_c, 3),
                    "diff_official_minus_wrong_f": round(diff_c * 9.0 / 5.0, 3),
                }
            )
    return pd.DataFrame(rows)


def summarize_step1(detail: pd.DataFrame) -> pd.DataFrame:
    if detail.empty:
        return pd.DataFrame()
    rows: list[dict] = []
    for city, g in detail.groupby("city"):
        unit = g["unit"].iloc[0]
        diff_c = g["diff_official_minus_wrong_c"]
        # bucket delta: how often the integer market bucket differs between stations
        if unit == "F":
            off_b = g["forecast_official_c"].apply(lambda c: round_half_up(c_to_f(c)))
            wrn_b = g["forecast_wrong_c"].apply(lambda c: round_half_up(c_to_f(c)))
        else:
            off_b = g["forecast_official_c"].apply(round_half_up)
            wrn_b = g["forecast_wrong_c"].apply(round_half_up)
        bucket_diff = (off_b != wrn_b)
        rows.append(
            {
                "city": city,
                "unit": unit,
                "n_days": int(len(g)),
                "mean_diff_c": round(float(diff_c.mean()), 3),
                "std_diff_c": round(float(diff_c.std(ddof=1)) if len(g) > 1 else 0.0, 3),
                "mean_abs_diff_c": round(float(diff_c.abs().mean()), 3),
                "mean_diff_f": round(float(diff_c.mean() * 9.0 / 5.0), 3),
                "p10_diff_c": round(float(diff_c.quantile(0.1)), 3),
                "p90_diff_c": round(float(diff_c.quantile(0.9)), 3),
                "frac_bucket_differs": round(float(bucket_diff.mean()), 3),
            }
        )
    out = pd.DataFrame(rows)
    # overall
    diff_c = detail["diff_official_minus_wrong_c"]
    overall = {
        "city": "ALL",
        "unit": "mixed",
        "n_days": int(len(detail)),
        "mean_diff_c": round(float(diff_c.mean()), 3),
        "std_diff_c": round(float(diff_c.std(ddof=1)), 3),
        "mean_abs_diff_c": round(float(diff_c.abs().mean()), 3),
        "mean_diff_f": round(float(diff_c.mean() * 9.0 / 5.0), 3),
        "p10_diff_c": round(float(diff_c.quantile(0.1)), 3),
        "p90_diff_c": round(float(diff_c.quantile(0.9)), 3),
        "frac_bucket_differs": round(float(out["frac_bucket_differs"].mean()), 3),
    }
    return pd.concat([out, pd.DataFrame([overall])], ignore_index=True)


# ---------------------------------------------------------------------------
# STEP 2 — market alignment at 10-13h local
# ---------------------------------------------------------------------------
def load_early_snapshot_quotes(
    snapshot_dir: Path,
    cities: set[str],
    hours: set[int],
) -> pd.DataFrame:
    """One row per (city, event_date, bracket): the LAST snapshot quote inside the
    10-13h local window on the event date. Keeps yes/no best ask and the live
    wrong-station forecast_max_f."""
    rows: list[dict] = []
    for fp in sorted(snapshot_dir.glob("snapshot_*.json")):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for r in data.get("records", []):
            city = r.get("city")
            if city not in cities:
                continue
            tl = r.get("ts_local")
            ed = r.get("event_date")
            if not tl or not ed:
                continue
            try:
                dt = datetime.fromisoformat(tl)
            except ValueError:
                continue
            if dt.date().isoformat() != ed or dt.hour not in hours:
                continue
            rows.append(
                {
                    "city": city,
                    "target_date": ed,
                    "bracket": str(r.get("bracket")),
                    "unit": str(r.get("unit") or "").upper(),
                    "ts_local": tl,
                    "ts_utc": r.get("ts_utc"),
                    "decision_hour_local": dt.hour,
                    "forecast_max_f": r.get("forecast_max_f"),
                    "forecast_source": r.get("forecast_source"),
                    "yes_best_ask": r.get("yes_best_ask"),
                    "no_best_ask": r.get("no_best_ask"),
                    "yes_best_bid": r.get("yes_best_bid"),
                    "no_best_bid": r.get("no_best_bid"),
                    "market_yes_price": r.get("market_yes_price"),
                    "question": r.get("question"),
                }
            )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["ts_sort"] = pd.to_datetime(df["ts_utc"], utc=True, errors="coerce")
    df = df.dropna(subset=["ts_sort"]).sort_values("ts_sort")
    df = df.groupby(["city", "target_date", "bracket"], as_index=False).tail(1)
    return df.drop(columns=["ts_sort"])


def market_implied_yes(row: pd.Series) -> float | None:
    """Mid-implied YES probability from best asks: use yes_best_ask if present,
    else 1 - no_best_ask. Returns None if neither side quoted."""
    ya = row.get("yes_best_ask")
    na = row.get("no_best_ask")
    yb = row.get("yes_best_bid")
    nb = row.get("no_best_bid")

    def mid(bid, ask):
        if bid is not None and ask is not None:
            return (float(bid) + float(ask)) / 2.0
        if ask is not None:
            return float(ask)
        if bid is not None:
            return float(bid)
        return None

    yes_mid = mid(yb, ya)
    no_mid = mid(nb, na)
    if yes_mid is not None and no_mid is not None:
        # blend: yes prob from yes mid and from 1 - no mid
        return (yes_mid + (1.0 - no_mid)) / 2.0
    if yes_mid is not None:
        return yes_mid
    if no_mid is not None:
        return 1.0 - no_mid
    return None


def step2_market_alignment(
    quotes: pd.DataFrame,
    dates: list[str],
) -> pd.DataFrame:
    """For each (city, target_date) build the market-implied YES probability
    across brackets, find the market's argmax bracket, and compare it to the
    bracket implied by the official-station forecast and the wrong-station
    forecast. Which forecast's bracket matches the market peak more often?"""
    if quotes.empty:
        return pd.DataFrame()
    start, end = dates[0], dates[-1]
    # cache per-city forecasts
    fc: dict[str, dict] = {}
    for city, cfg in BASIS_STATIONS.items():
        fc[city] = {
            "official": fetch_daily_max(cfg["official"]["lat"], cfg["official"]["lon"], start, end),
            "wrong": fetch_daily_max(cfg["wrong"]["lat"], cfg["wrong"]["lon"], start, end),
        }
    quotes = quotes.copy()
    quotes["implied_yes"] = quotes.apply(market_implied_yes, axis=1)

    rows: list[dict] = []
    for (city, date), g in quotes.groupby(["city", "target_date"]):
        cfg = BASIS_STATIONS[city]
        unit = cfg["unit"]
        off = fc[city]["official"].get(date)
        wrn = fc[city]["wrong"].get(date)
        if off is None or wrn is None:
            continue
        g2 = g.dropna(subset=["implied_yes"])
        if g2.empty:
            continue
        labels = list(g2["bracket"])
        # market argmax bracket (highest implied YES)
        market_peak = g2.loc[g2["implied_yes"].idxmax(), "bracket"]
        # forecast values in market unit, integer-rounded
        if unit == "F":
            off_v = round_half_up(c_to_f(off))
            wrn_v = round_half_up(c_to_f(wrn))
        else:
            off_v = round_half_up(off)
            wrn_v = round_half_up(wrn)
        off_label = forecast_to_label(off_v, labels, unit)
        wrn_label = forecast_to_label(wrn_v, labels, unit)
        rows.append(
            {
                "city": city,
                "target_date": date,
                "unit": unit,
                "n_brackets_quoted": int(len(g2)),
                "market_peak_bracket": market_peak,
                "official_forecast_unit": off_v,
                "wrong_forecast_unit": wrn_v,
                "official_label": off_label,
                "wrong_label": wrn_label,
                "official_matches_peak": int(off_label == market_peak) if off_label else 0,
                "wrong_matches_peak": int(wrn_label == market_peak) if wrn_label else 0,
                "snapshot_forecast_max_f": g2["forecast_max_f"].dropna().iloc[-1]
                if g2["forecast_max_f"].notna().any()
                else None,
            }
        )
    return pd.DataFrame(rows)


def summarize_step2(detail: pd.DataFrame) -> pd.DataFrame:
    if detail.empty:
        return pd.DataFrame()
    rows: list[dict] = []
    for city, g in detail.groupby("city"):
        rows.append(
            {
                "city": city,
                "n_city_days": int(len(g)),
                "official_peak_match_rate": round(float(g["official_matches_peak"].mean()), 3),
                "wrong_peak_match_rate": round(float(g["wrong_matches_peak"].mean()), 3),
            }
        )
    out = pd.DataFrame(rows)
    overall = {
        "city": "ALL",
        "n_city_days": int(len(detail)),
        "official_peak_match_rate": round(float(detail["official_matches_peak"].mean()), 3),
        "wrong_peak_match_rate": round(float(detail["wrong_matches_peak"].mean()), 3),
    }
    return pd.concat([out, pd.DataFrame([overall])], ignore_index=True)


# ---------------------------------------------------------------------------
# STEP 3 — early-window backtest
# ---------------------------------------------------------------------------
def build_whitelist(settlement_csv: Path) -> list[str]:
    df = pd.read_csv(settlement_csv)
    df = df[df["pm_history_valid"].astype(str) == "True"]
    df = df[df["winner_count"].astype(str) == "1"]
    df = df[df["match_round"].notna()]
    df["match_round"] = pd.to_numeric(df["match_round"], errors="coerce")
    df = df.dropna(subset=["match_round"])
    wl: list[str] = []
    for city, g in df.groupby("city"):
        if len(g) >= 20 and float(g["match_round"].mean()) == 1.0:
            wl.append(city)
    return sorted(wl)


def early_backtest(
    quotes: pd.DataFrame,
    pm_dir: Path,
    dates: list[str],
    use_station: str,
    label_tag: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """For basis cities only. At the early decision, use the chosen station's
    forecast max to pick the YES bracket; buy YES at yes_best_ask; settle vs
    pm_history single winner. Also a tail-NO leg: buy NO on brackets strictly
    below the forecast bracket low (using no_best_ask).
    """
    if quotes.empty:
        return pd.DataFrame(), pd.DataFrame()
    start, end = dates[0], dates[-1]
    fc: dict[str, dict] = {}
    for city, cfg in BASIS_STATIONS.items():
        fc[city] = fetch_daily_max(
            cfg[use_station]["lat"], cfg[use_station]["lon"], start, end
        )

    trades: list[dict] = []
    for (city, date), g in quotes.groupby(["city", "target_date"]):
        cfg = BASIS_STATIONS.get(city)
        if cfg is None:
            continue
        win = load_pm_winner(pm_dir, city, date)
        if win is None:
            continue
        unit = win["unit"]
        labels = win["labels"]
        f_c = fc[city].get(date)
        if f_c is None:
            continue
        f_v = round_half_up(c_to_f(f_c)) if unit == "F" else round_half_up(f_c)
        yes_label = forecast_to_label(f_v, labels, unit)
        if yes_label is None:
            continue
        # YES leg on the forecast bracket
        yrow = g[g["bracket"] == yes_label]
        if not yrow.empty:
            ask = yrow["yes_best_ask"].dropna()
            if not ask.empty:
                entry = float(ask.iloc[-1])
                if 0.005 <= entry <= 0.995:
                    payout = 1.0 if yes_label == win["winner"] else 0.0
                    trades.append(
                        {
                            "leg": "early_yes",
                            "city": city,
                            "target_date": date,
                            "bracket": yes_label,
                            "winner": win["winner"],
                            "entry_cost": entry,
                            "payout": payout,
                            "pnl": payout - entry,
                        }
                    )
        # tail-NO leg: brackets strictly below forecast bracket low
        yl_low, _ = parse_bracket_bounds(yes_label, unit)
        for _, qr in g.iterrows():
            blabel = qr["bracket"]
            blow, bhigh = parse_bracket_bounds(blabel, unit)
            if bhigh is None or yl_low is None:
                continue
            if bhigh < yl_low:  # strictly below forecast bracket
                nask = qr.get("no_best_ask")
                if nask is None or not (0.005 <= float(nask) <= 0.995):
                    continue
                entry = float(nask)
                payout = 1.0 if blabel != win["winner"] else 0.0
                trades.append(
                    {
                        "leg": "early_tail_no",
                        "city": city,
                        "target_date": date,
                        "bracket": blabel,
                        "winner": win["winner"],
                        "entry_cost": entry,
                        "payout": payout,
                        "pnl": payout - entry,
                    }
                )
    td = pd.DataFrame(trades)
    if td.empty:
        return td, pd.DataFrame()
    td["roi"] = td["pnl"] / td["entry_cost"]
    td["group"] = label_tag
    # summary with t-stat on pnl per trade
    rows: list[dict] = []
    for (grp, leg), gg in td.groupby(["group", "leg"]):
        n = len(gg)
        pnl = gg["pnl"]
        cost = gg["entry_cost"].sum()
        mean = float(pnl.mean())
        std = float(pnl.std(ddof=1)) if n > 1 else 0.0
        tstat = mean / (std / math.sqrt(n)) if std > 0 and n > 1 else 0.0
        rows.append(
            {
                "group": grp,
                "leg": leg,
                "trades": n,
                "cities": int(gg["city"].nunique()),
                "city_days": int((gg["city"] + "|" + gg["target_date"]).nunique()),
                "cost": round(cost, 4),
                "pnl": round(float(pnl.sum()), 4),
                "roi": round(float(pnl.sum() / cost), 4) if cost else 0.0,
                "win_rate": round(float((gg["payout"] > 0).mean()), 4),
                "mean_pnl_per_trade": round(mean, 5),
                "t_stat": round(tstat, 3),
            }
        )
    # combined all-leg
    for grp, gg in td.groupby("group"):
        n = len(gg)
        pnl = gg["pnl"]
        cost = gg["entry_cost"].sum()
        std = float(pnl.std(ddof=1)) if n > 1 else 0.0
        tstat = float(pnl.mean()) / (std / math.sqrt(n)) if std > 0 and n > 1 else 0.0
        rows.append(
            {
                "group": grp,
                "leg": "ALL",
                "trades": n,
                "cities": int(gg["city"].nunique()),
                "city_days": int((gg["city"] + "|" + gg["target_date"]).nunique()),
                "cost": round(cost, 4),
                "pnl": round(float(pnl.sum()), 4),
                "roi": round(float(pnl.sum() / cost), 4) if cost else 0.0,
                "win_rate": round(float((gg["payout"] > 0).mean()), 4),
                "mean_pnl_per_trade": round(float(pnl.mean()), 5),
                "t_stat": round(tstat, 3),
            }
        )
    return td, pd.DataFrame(rows)


# ---------------------------------------------------------------------------
def date_range(start: str, end: str) -> list[str]:
    s = datetime.fromisoformat(start).date()
    e = datetime.fromisoformat(end).date()
    out = []
    d = s
    while d <= e:
        out.append(d.isoformat())
        d = d.fromordinal(d.toordinal() + 1)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--snapshot-dir",
        default=str(historical_strategy_snapshots()),
    )
    parser.add_argument(
        "--pm-history-dir",
        default="runtime/weather_edge_v1/market_data/cache/pm_history",
    )
    parser.add_argument(
        "--settlement-csv",
        default="docs/analysis/2026-06/generated/m3_settlement_alignment_v1/m3_settlement_alignment_city_days.csv",
    )
    parser.add_argument(
        "--output-dir",
        default="docs/analysis/2026-06/generated/m3_forecast_basis_sleeve_v0",
    )
    parser.add_argument("--early-hours", default="10,11,12,13")
    parser.add_argument("--eval-start", default=EVAL_START)
    parser.add_argument("--eval-end", default=EVAL_END)
    parser.add_argument(
        "--bucket-discrim-threshold",
        type=float,
        default=0.10,
        help="If overall frac_bucket_differs >= this, step1 is treated as discriminating.",
    )
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    hours = {int(x) for x in args.early_hours.split(",") if x.strip()}
    dates = date_range(args.eval_start, args.eval_end)

    # STEP 1
    print("[step1] fetching forecasts for both stations ...")
    s1_detail = step1_station_discrimination(dates)
    s1_detail.to_csv(out_dir / "step1_station_forecast_diff_detail.csv", index=False)
    s1_summary = summarize_step1(s1_detail)
    s1_summary.to_csv(out_dir / "step1_station_forecast_diff_summary.csv", index=False)
    print(s1_summary.to_string(index=False))

    overall_row = s1_summary[s1_summary["city"] == "ALL"].iloc[0]
    discriminates = bool(overall_row["frac_bucket_differs"] >= args.bucket_discrim_threshold)
    print(f"[step1] frac_bucket_differs(ALL)={overall_row['frac_bucket_differs']} "
          f"mean_diff_c={overall_row['mean_diff_c']} std={overall_row['std_diff_c']} "
          f"-> discriminates={discriminates}")

    # STEP 2 (market alignment) — always run; it is descriptive
    print("[step2] loading early-window snapshot quotes ...")
    quotes = load_early_snapshot_quotes(
        Path(args.snapshot_dir), set(BASIS_STATIONS), hours
    )
    quotes.to_csv(out_dir / "step2_early_quotes_detail.csv", index=False)
    s2_detail = step2_market_alignment(quotes, dates)
    s2_detail.to_csv(out_dir / "step2_market_alignment_detail.csv", index=False)
    s2_summary = summarize_step2(s2_detail)
    s2_summary.to_csv(out_dir / "step2_market_alignment_summary.csv", index=False)
    if not s2_summary.empty:
        print(s2_summary.to_string(index=False))

    # STEP 3 (early backtest) — only meaningful if step1 discriminates, but we
    # run and record either way; the verdict respects step1.
    print("[step3] early-window backtest (official-station forecast) ...")
    whitelist = build_whitelist(Path(args.settlement_csv))
    print(f"[step3] whitelist cities (>=20d, match_round==1.0): {whitelist}")

    bt_trades, bt_summary = early_backtest(
        quotes, Path(args.pm_history_dir), dates, use_station="official",
        label_tag="basis_official",
    )
    # control: same official-station rule on whitelist cities. Need whitelist
    # forecast coords; we only have basis-city coords, so the control reuses
    # the snapshot wrong-station (city-point) forecast_max_f for whitelist —
    # but whitelist cities are not in BASIS_STATIONS, so we run a separate
    # snapshot-forecast control below.
    ctrl_trades, ctrl_summary = whitelist_control(
        Path(args.snapshot_dir), Path(args.pm_history_dir), whitelist, hours, dates
    )

    summary_frames = [f for f in (bt_summary, ctrl_summary) if not f.empty]
    if summary_frames:
        bt_all = pd.concat(summary_frames, ignore_index=True)
    else:
        bt_all = pd.DataFrame()
    bt_all.to_csv(out_dir / "step3_early_backtest_summary.csv", index=False)
    trade_frames = [f for f in (bt_trades, ctrl_trades) if not f.empty]
    if trade_frames:
        pd.concat(trade_frames, ignore_index=True).to_csv(
            out_dir / "step3_early_backtest_trades.csv", index=False
        )
    if not bt_all.empty:
        print(bt_all.to_string(index=False))

    manifest = {
        "generated_at_utc": datetime.utcnow().isoformat() + "Z",
        "eval_window": [args.eval_start, args.eval_end],
        "early_hours_local": sorted(hours),
        "basis_cities": list(BASIS_STATIONS),
        "stations": {c: {"official": v["official"], "wrong": v["wrong"]} for c, v in BASIS_STATIONS.items()},
        "step1_discriminates": discriminates,
        "step1_overall": {
            "mean_diff_c": float(overall_row["mean_diff_c"]),
            "std_diff_c": float(overall_row["std_diff_c"]),
            "mean_abs_diff_c": float(overall_row["mean_abs_diff_c"]),
            "frac_bucket_differs": float(overall_row["frac_bucket_differs"]),
        },
        "whitelist_cities": whitelist,
        "settlement_truth": "pm_history single winner",
        "forecast_source": "open-meteo historical-forecast daily temperature_2m_max",
        "no_lookahead_note": (
            "Early backtest uses the morning forecast run available by 10-13h "
            "local; open-meteo historical-forecast archives the run current for "
            "that date. Settlement uses pm_history single winner only."
        ),
        "outputs": [
            "step1_station_forecast_diff_detail.csv",
            "step1_station_forecast_diff_summary.csv",
            "step2_early_quotes_detail.csv",
            "step2_market_alignment_detail.csv",
            "step2_market_alignment_summary.csv",
            "step3_early_backtest_summary.csv",
            "step3_early_backtest_trades.csv",
        ],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"[done] wrote outputs to {out_dir}")
    return 0


def whitelist_control(
    snapshot_dir: Path,
    pm_dir: Path,
    whitelist: list[str],
    hours: set[int],
    dates: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Control group: same early-window rule on whitelist cities, using the
    snapshot's own city-point forecast (forecast_max_f) to pick the YES bracket.
    Expected negative (market is efficient where station matches)."""
    quotes = load_early_snapshot_quotes(snapshot_dir, set(whitelist), hours)
    if quotes.empty:
        return pd.DataFrame(), pd.DataFrame()
    trades: list[dict] = []
    for (city, date), g in quotes.groupby(["city", "target_date"]):
        win = load_pm_winner(pm_dir, city, date)
        if win is None:
            continue
        unit = win["unit"]
        labels = win["labels"]
        fcast = g["forecast_max_f"].dropna()
        if fcast.empty:
            continue
        f_f = float(fcast.iloc[-1])  # forecast_max_f is in F per snapshot schema
        f_v = round_half_up(f_f) if unit == "F" else round_half_up((f_f - 32.0) * 5.0 / 9.0)
        yes_label = forecast_to_label(f_v, labels, unit)
        if yes_label is None:
            continue
        yrow = g[g["bracket"] == yes_label]
        if yrow.empty:
            continue
        ask = yrow["yes_best_ask"].dropna()
        if ask.empty:
            continue
        entry = float(ask.iloc[-1])
        if not (0.005 <= entry <= 0.995):
            continue
        payout = 1.0 if yes_label == win["winner"] else 0.0
        trades.append(
            {
                "leg": "early_yes",
                "city": city,
                "target_date": date,
                "bracket": yes_label,
                "winner": win["winner"],
                "entry_cost": entry,
                "payout": payout,
                "pnl": payout - entry,
            }
        )
    td = pd.DataFrame(trades)
    if td.empty:
        return td, pd.DataFrame()
    td["roi"] = td["pnl"] / td["entry_cost"]
    td["group"] = "control_whitelist"
    n = len(td)
    pnl = td["pnl"]
    cost = td["entry_cost"].sum()
    std = float(pnl.std(ddof=1)) if n > 1 else 0.0
    tstat = float(pnl.mean()) / (std / math.sqrt(n)) if std > 0 and n > 1 else 0.0
    summary = pd.DataFrame(
        [
            {
                "group": "control_whitelist",
                "leg": "early_yes",
                "trades": n,
                "cities": int(td["city"].nunique()),
                "city_days": int((td["city"] + "|" + td["target_date"]).nunique()),
                "cost": round(cost, 4),
                "pnl": round(float(pnl.sum()), 4),
                "roi": round(float(pnl.sum() / cost), 4) if cost else 0.0,
                "win_rate": round(float((td["payout"] > 0).mean()), 4),
                "mean_pnl_per_trade": round(float(pnl.mean()), 5),
                "t_stat": round(tstat, 3),
            }
        ]
    )
    return td, summary


if __name__ == "__main__":
    raise SystemExit(main())
