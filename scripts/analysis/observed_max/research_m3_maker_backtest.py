#!/usr/bin/env python3
"""M3 maker-side NO backtest (pre-registered, v0).

Question: after the exhaustion signal (current temp fell >=1.0C from the day's
running max, city-local 14-18h), is there profit in *posting* NO bids on the
d1/d2 brackets above the running max (earning the spread) instead of taking
the ask (which was already proven ~zero/negative for whitelist cities)?

Rules (pre-registered, no post-hoc tuning):
- M1 (main):    post NO buy at best_no_bid + 0.01 (improve one tick), rest
                until filled or city-local 21:00, hold to settlement.
- M2 (control): take best_no_ask at the same decision snapshot (taker
                baseline, previously shown ~zero/negative).
- M3 (variant): post NO buy at NO mid - 0.01 (more conservative price).

Fill simulation (30-min snapshots, deliberately conservative):
- conservative (main): filled iff some later snapshot (<= local 21:00) shows
  best_no_ask <= posted price p; fill price = p. This is a lower bound on
  fills (a seller crossed through our level); resting hits on our bid that do
  not move the ask are invisible and *not* counted.
- optimistic (sensitivity only): if never conservatively filled but our bid
  would have been best bid (best_no_bid < p) for >=2 consecutive snapshots
  with best-bid size changing inside that run, count the order as filled with
  weight 0.5. Reported separately; never enters the main verdict.

Settlement truth: pm_history single-winner labels only (alignment CSV).
Groups: whitelist (36 clean cities, wu_obs running max) and repaired 6 cities
(Paris/London/Milan/Chicago/KualaLumpur/PanamaCity, official-station IEM
running max / current temp).
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import re
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from research_m3_observed_max_residual import CITY_TIMEZONE
from research_m3_paper_snapshot_proxy_backtest import parse_bracket

REPO = Path(__file__).resolve().parents[3]

WHITELIST_MIN_DAYS = 20
REPAIRED_CITIES = {"Paris", "London", "Milan", "Chicago", "KualaLumpur", "PanamaCity"}

SIGNAL_HOURS = (14, 15, 16, 17, 18)  # city-local decision hours
CANCEL_HOUR = 21                     # cancel resting orders at local 21:00
DECLINE_MIN_C = 1.0                  # exhaustion: running_max_c - current_temp_c
DISTANCES = (1, 2)                   # d1/d2 brackets above running max
TICK = 0.01
MAX_POST_PRICE = 0.99                # sanity: never buy NO above 0.99
MIN_POST_PRICE = 0.005


def round_half_up(x: float) -> int:
    return int(math.floor(float(x) + 0.5))


def load_valid_city_days(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    valid = df[df["pm_history_valid"].fillna(False) & df["winner_count"].eq(1)].copy()
    valid["winner_label"] = valid["winner_labels"].astype(str).str.strip()
    return valid[["city", "target_date", "unit", "winner_label", "match_round"]]


def build_whitelist(valid: pd.DataFrame) -> set[str]:
    per_city = (
        valid.dropna(subset=["match_round"])
        .groupby("city")
        .agg(days=("match_round", "size"), match_rate=("match_round", "mean"))
    )
    wl = set(per_city[(per_city["match_rate"].eq(1.0)) & (per_city["days"].ge(WHITELIST_MIN_DAYS))].index)
    return wl - REPAIRED_CITIES


def load_pm_history_brackets(pm_history_dir: Path, city_days: set[tuple[str, str]]) -> dict[tuple[str, str], dict]:
    """Per (city, target_date): {'labels': set, 'bottoms': set}."""
    out: dict[tuple[str, str], dict] = {}
    for city, day in city_days:
        f = pm_history_dir / f"{city}_{day}.json"
        if not f.exists():
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        labels: set[str] = set()
        bottoms: set[str] = set()
        for b in data.get("brackets") or []:
            label = str(b.get("label") or "").strip()
            if not label:
                continue
            labels.add(label)
            q = str(b.get("question") or "").lower()
            if "or below" in q or "or lower" in q:
                bottoms.add(label)
        out[(city, day)] = {"labels": labels, "bottoms": bottoms}
    return out


def load_signals_whitelist(detail_path: Path, whitelist: set[str], dates: set[str]) -> pd.DataFrame:
    df = pd.read_csv(
        detail_path,
        usecols=["city", "target_date", "decision_hour_local", "running_max_c", "running_max_f", "current_temp_c"],
    )
    df = df[df["city"].isin(whitelist) & df["target_date"].isin(dates)].copy()
    df["decline_c"] = df["running_max_c"] - df["current_temp_c"]
    df["group"] = "whitelist"
    return df


def load_signals_repaired(detail_path: Path, dates: set[str]) -> pd.DataFrame:
    df = pd.read_csv(
        detail_path,
        usecols=["city", "target_date", "decision_hour_local", "running_max_c", "running_max_raw", "current_temp_c", "unit"],
    )
    df = df[df["city"].isin(REPAIRED_CITIES) & df["target_date"].isin(dates)].copy()
    df["decline_c"] = df["running_max_c"] - df["current_temp_c"]
    df["group"] = "repaired"
    return df


def running_value(row: pd.Series, market_unit: str) -> float | None:
    """Market-unit running value at this decision hour."""
    if row["group"] == "repaired":
        raw = row.get("running_max_raw")
        if pd.isna(raw):
            return None
        return float(round_half_up(raw))  # repaired detail raw is already in market unit
    if market_unit == "F":
        v = row.get("running_max_f")
        return None if pd.isna(v) else float(v)
    v = row.get("running_max_c")
    return None if pd.isna(v) else float(round_half_up(v))


def tail_distance(low: float | None, rv: float, unit: str) -> int | None:
    if low is None or pd.isna(low):
        return None
    if float(low) <= rv:
        return None
    if unit == "F":
        return int(math.ceil((float(low) - rv) / 2.0))
    return int(round(float(low) - rv))


def best_levels(raw: object) -> dict:
    """Manual min/max over raw book; ordering in files is not trusted."""
    out = {"best_bid": None, "best_bid_size": None, "best_ask": None, "best_ask_size": None}
    if not isinstance(raw, dict):
        return out
    for side, key_price, key_size, pick in (
        ("bids", "best_bid", "best_bid_size", max),
        ("asks", "best_ask", "best_ask_size", min),
    ):
        levels = raw.get(side)
        if not isinstance(levels, list) or not levels:
            continue
        parsed = []
        for lv in levels:
            if not isinstance(lv, dict):
                continue
            try:
                p = float(lv.get("price"))
            except (TypeError, ValueError):
                continue
            try:
                s = float(lv.get("size")) if lv.get("size") is not None else None
            except (TypeError, ValueError):
                s = None
            parsed.append((p, s))
        if not parsed:
            continue
        p, s = pick(parsed, key=lambda x: x[0])
        out[key_price] = p
        out[key_size] = s
    return out


def collect_no_quotes(orderbook_dir: Path, pairs: set[tuple[str, str]]) -> pd.DataFrame:
    """All NO-token top-of-book quotes for candidate (city, event_date) pairs.

    Scans the UTC-day dirs of event_date and event_date+1 (local 14-21h can
    spill into the next UTC day for western timezones).
    """
    need_dirs: set[str] = set()
    for _, day in pairs:
        need_dirs.add(day)
        d = date.fromisoformat(day)
        need_dirs.add((d + timedelta(days=1)).isoformat())
    rows: list[dict] = []
    files_seen = 0
    lines_parsed = 0
    city_re = re.compile(r'"city":\s*"([^"]+)"')
    date_re = re.compile(r'"event_date":\s*"([^"]+)"')
    for dname in sorted(need_dirs):
        ddir = orderbook_dir / dname
        if not ddir.is_dir():
            continue
        for path in sorted(ddir.glob("orderbook_snapshot_*.jsonl.gz")):
            files_seen += 1
            with gzip.open(path, "rt", encoding="utf-8") as f:
                for line in f:
                    if '"outcome": "no"' not in line:
                        continue
                    mc = city_re.search(line)
                    md = date_re.search(line)
                    if not mc or not md or (mc.group(1), md.group(1)) not in pairs:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    lines_parsed += 1
                    ts = pd.to_datetime(rec.get("snapshot_ts_utc"), utc=True, errors="coerce")
                    if pd.isna(ts):
                        continue
                    lv = best_levels(rec.get("raw"))
                    rows.append(
                        {
                            "city": rec["city"],
                            "target_date": rec["event_date"],
                            "bracket": str(rec.get("bracket") or "").strip(),
                            "snapshot_ts_utc": ts,
                            "no_bid": lv["best_bid"],
                            "no_bid_size": lv["best_bid_size"],
                            "no_ask": lv["best_ask"],
                            "no_ask_size": lv["best_ask_size"],
                            "token_id": rec.get("token_id"),
                        }
                    )
    quotes = pd.DataFrame(rows)
    meta = {"orderbook_files_seen": files_seen, "no_lines_parsed": lines_parsed, "no_quote_rows": int(len(quotes))}
    if not quotes.empty:
        tz_map = {c: ZoneInfo(CITY_TIMEZONE[c]) for c in quotes["city"].unique() if c in CITY_TIMEZONE}
        quotes = quotes[quotes["city"].isin(tz_map)].copy()
        local = [ts.tz_convert(tz_map[c]) for ts, c in zip(quotes["snapshot_ts_utc"], quotes["city"])]
        quotes["local_date"] = [t.date().isoformat() for t in local]
        quotes["local_hour"] = [t.hour for t in local]
        quotes["local_minutes"] = [t.hour * 60 + t.minute for t in local]
        quotes = quotes[quotes["local_date"].eq(quotes["target_date"])].copy()
        quotes = quotes.sort_values(["city", "target_date", "bracket", "snapshot_ts_utc"]).reset_index(drop=True)
    quotes.attrs["meta"] = meta
    return quotes, meta


def conservative_fill(after: pd.DataFrame, p: float) -> pd.Series | None:
    hit = after[after["no_ask"].notna() & (after["no_ask"] <= p + 1e-9)]
    if hit.empty:
        return None
    return hit.iloc[0]


def optimistic_fill(after: pd.DataFrame, p: float) -> bool:
    """>=2 consecutive snapshots where we'd be best bid, with best-bid size changing."""
    run: list[tuple[float | None, float | None]] = []
    for _, r in after.iterrows():
        bid = r["no_bid"]
        if pd.notna(bid) and float(bid) < p - 1e-9:
            run.append((float(bid), None if pd.isna(r["no_bid_size"]) else float(r["no_bid_size"])))
            if len(run) >= 2:
                sizes = {(b, s) for b, s in run}
                if len(sizes) >= 2:  # price or size at best bid changed during the run
                    return True
        else:
            run = []
    return False


def daily_tstat(pnls: pd.Series) -> tuple[float | None, int]:
    n = len(pnls)
    if n < 2:
        return None, n
    sd = float(pnls.std(ddof=1))
    if sd == 0:
        return None, n
    return float(pnls.mean() / (sd / math.sqrt(n))), n


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--orderbook-dir", default=str(REPO / "runtime/weather_edge_v1/market_data/orderbook_snapshots"))
    parser.add_argument("--alignment-city-days", default=str(REPO / "docs/analysis/2026-06/generated/m3_settlement_alignment_v1/m3_settlement_alignment_city_days.csv"))
    parser.add_argument("--wu-observed-detail", default=str(REPO / "docs/analysis/2026-06/generated/m3_observed_max_v3_h10_21/m3_observed_max_residual_detail.csv"))
    parser.add_argument("--official-running-detail", default=str(REPO / "docs/analysis/2026-06/generated/official_station_running_max_v0/official_station_running_max_detail.csv"))
    parser.add_argument("--pm-history-dir", default=str(REPO / "runtime/weather_edge_v1/market_data/cache/pm_history"))
    parser.add_argument("--output-dir", default=str(REPO / "docs/analysis/2026-06/generated/m3_maker_backtest_v0"))
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    orderbook_dir = Path(args.orderbook_dir)

    ob_dates = sorted(d.name for d in orderbook_dir.iterdir() if d.is_dir())

    valid = load_valid_city_days(Path(args.alignment_city_days))
    valid = valid[valid["target_date"].isin(set(ob_dates))].copy()
    whitelist = build_whitelist(load_valid_city_days(Path(args.alignment_city_days)))

    sig_wl = load_signals_whitelist(Path(args.wu_observed_detail), whitelist, set(ob_dates))
    sig_rep = load_signals_repaired(Path(args.official_running_detail), set(ob_dates))
    signals = pd.concat([sig_wl, sig_rep], ignore_index=True)

    # keep only settled single-winner city-days
    winners = valid.set_index(["city", "target_date"])["winner_label"].to_dict()
    units = valid.set_index(["city", "target_date"])["unit"].to_dict()
    signals = signals[[((c, d) in winners) for c, d in zip(signals["city"], signals["target_date"])]].copy()

    # exhaustion city-days: first hour in 14-18 with decline >= 1.0C
    sig_hours = signals[signals["decision_hour_local"].isin(SIGNAL_HOURS) & (signals["decline_c"] >= DECLINE_MIN_C)]
    candidate_pairs = set(zip(sig_hours["city"], sig_hours["target_date"]))

    pm_brackets = load_pm_history_brackets(Path(args.pm_history_dir), candidate_pairs)

    quotes, ob_meta = collect_no_quotes(orderbook_dir, candidate_pairs)
    quote_groups = {k: g for k, g in quotes.groupby(["city", "target_date"])} if not quotes.empty else {}

    # hourly running max (C) lookup for adverse-selection check
    runmax_c = signals.set_index(["city", "target_date", "decision_hour_local"])["running_max_c"].to_dict()

    trades: list[dict] = []
    for (city, day), sig in signals[signals["decision_hour_local"].isin(SIGNAL_HOURS)].groupby(["city", "target_date"]):
        if (city, day) not in candidate_pairs:
            continue
        unit = str(units[(city, day)]).upper()
        winner = winners[(city, day)]
        group = sig["group"].iloc[0]
        pm = pm_brackets.get((city, day))
        if not pm:
            continue
        g = quote_groups.get((city, day))
        if g is None or g.empty:
            continue
        sig = sig.sort_values("decision_hour_local")
        placed: set[str] = set()  # bracket labels already ordered (shared by M1/M2/M3)
        for _, srow in sig.iterrows():
            if srow["decline_c"] < DECLINE_MIN_C:
                continue
            hour = int(srow["decision_hour_local"])
            rv = running_value(srow, unit)
            if rv is None:
                continue
            # entry snapshot: first snapshot at/after hour:00 local within this hour
            hr_quotes = g[(g["local_minutes"] >= hour * 60) & (g["local_minutes"] < (hour + 1) * 60)]
            if hr_quotes.empty:
                continue
            t0_ts = hr_quotes["snapshot_ts_utc"].min()
            book = hr_quotes[hr_quotes["snapshot_ts_utc"].eq(t0_ts)]
            for _, q in book.iterrows():
                label = q["bracket"]
                if label in placed or label not in pm["labels"] or label in pm["bottoms"]:
                    continue
                br = parse_bracket(label)
                if br is None:
                    continue
                dist = tail_distance(br.low_f, rv, unit)
                if dist is None or dist not in DISTANCES:
                    continue
                placed.add(label)
                no_bid, no_ask = q["no_bid"], q["no_ask"]
                after = g[
                    g["bracket"].eq(label)
                    & (g["snapshot_ts_utc"] > t0_ts)
                    & (g["local_minutes"] <= CANCEL_HOUR * 60)
                ]
                base = {
                    "group": group,
                    "city": city,
                    "target_date": day,
                    "decision_hour_local": hour,
                    "bracket": label,
                    "distance": dist,
                    "unit": unit,
                    "running_value": rv,
                    "decline_c": round(float(srow["decline_c"]), 2),
                    "entry_ts_utc": t0_ts.isoformat(),
                    "no_bid_t0": no_bid,
                    "no_ask_t0": no_ask,
                    "no_bid_size_t0": q["no_bid_size"],
                    "no_ask_size_t0": q["no_ask_size"],
                    "winner_label": winner,
                    "is_winner_bracket": int(label == winner),
                }

                def settle(p: float) -> tuple[float, float]:
                    payout = 0.0 if label == winner else 1.0
                    return payout, payout - p

                # --- M2: taker control ---
                if pd.notna(no_ask) and MIN_POST_PRICE <= no_ask <= 0.995:
                    payout, pnl = settle(float(no_ask))
                    trades.append({**base, "rule": "M2_taker", "post_price": float(no_ask), "status": "filled",
                                   "fill_kind": "taker", "fill_ts_utc": t0_ts.isoformat(), "fill_hour_local": hour,
                                   "entry_cost": float(no_ask), "payout": payout, "pnl": pnl,
                                   "optimistic_weight": 1.0, "runmax_c_delta_at_fill": 0.0})
                else:
                    trades.append({**base, "rule": "M2_taker", "post_price": None, "status": "no_quote",
                                   "fill_kind": None, "fill_ts_utc": None, "fill_hour_local": None,
                                   "entry_cost": None, "payout": None, "pnl": None,
                                   "optimistic_weight": 0.0, "runmax_c_delta_at_fill": None})

                # --- maker rules ---
                for rule, price in (
                    ("M1_join_improve", None if pd.isna(no_bid) else round(float(no_bid) + TICK, 4)),
                    ("M3_mid_minus", None if (pd.isna(no_bid) or pd.isna(no_ask)) else round((float(no_bid) + float(no_ask)) / 2.0 - TICK, 4)),
                ):
                    if price is None:
                        trades.append({**base, "rule": rule, "post_price": None, "status": "no_quote",
                                       "fill_kind": None, "fill_ts_utc": None, "fill_hour_local": None,
                                       "entry_cost": None, "payout": None, "pnl": None,
                                       "optimistic_weight": 0.0, "runmax_c_delta_at_fill": None})
                        continue
                    if not (MIN_POST_PRICE <= price <= MAX_POST_PRICE):
                        trades.append({**base, "rule": rule, "post_price": price, "status": "skip_price_cap",
                                       "fill_kind": None, "fill_ts_utc": None, "fill_hour_local": None,
                                       "entry_cost": None, "payout": None, "pnl": None,
                                       "optimistic_weight": 0.0, "runmax_c_delta_at_fill": None})
                        continue
                    # immediate cross: improving one tick locks/crosses the book -> taker at the ask
                    if pd.notna(no_ask) and price >= float(no_ask) - 1e-9:
                        payout, pnl = settle(float(no_ask))
                        trades.append({**base, "rule": rule, "post_price": price, "status": "filled",
                                       "fill_kind": "immediate_cross", "fill_ts_utc": t0_ts.isoformat(),
                                       "fill_hour_local": hour, "entry_cost": float(no_ask), "payout": payout,
                                       "pnl": pnl, "optimistic_weight": 1.0, "runmax_c_delta_at_fill": 0.0})
                        continue
                    hit = conservative_fill(after, price)
                    if hit is not None:
                        payout, pnl = settle(price)
                        fill_hour = min(int(hit["local_hour"]), 21)
                        rm0 = runmax_c.get((city, day, hour))
                        rm1 = runmax_c.get((city, day, fill_hour))
                        delta = None if (rm0 is None or rm1 is None or pd.isna(rm0) or pd.isna(rm1)) else round(float(rm1) - float(rm0), 2)
                        trades.append({**base, "rule": rule, "post_price": price, "status": "filled",
                                       "fill_kind": "passive_ask_crossed", "fill_ts_utc": hit["snapshot_ts_utc"].isoformat(),
                                       "fill_hour_local": int(hit["local_hour"]), "entry_cost": price, "payout": payout,
                                       "pnl": pnl, "optimistic_weight": 1.0, "runmax_c_delta_at_fill": delta})
                        continue
                    opt = optimistic_fill(after, price)
                    payout, pnl = settle(price)
                    trades.append({**base, "rule": rule, "post_price": price, "status": "no_fill",
                                   "fill_kind": "optimistic_half" if opt else None, "fill_ts_utc": None,
                                   "fill_hour_local": None, "entry_cost": price if opt else None,
                                   "payout": payout if opt else None, "pnl": pnl if opt else None,
                                   "optimistic_weight": 0.5 if opt else 0.0, "runmax_c_delta_at_fill": None})

    trades_df = pd.DataFrame(trades)
    trades_path = out_dir / "m3_maker_backtest_trades.csv"
    trades_df.to_csv(trades_path, index=False)

    # ---- summary: rule x group x distance (and ALL) ----
    def summarize(sub: pd.DataFrame, rule: str, group: str, dist_label: str) -> dict:
        orders = sub[sub["status"].isin(["filled", "no_fill"])]
        filled = sub[sub["status"].eq("filled")]
        n_orders = len(orders)
        n_filled = len(filled)
        out = {
            "rule": rule,
            "group": group,
            "distance": dist_label,
            "orders": n_orders,
            "no_quote": int((sub["status"] == "no_quote").sum()),
            "skip_price_cap": int((sub["status"] == "skip_price_cap").sum()),
            "filled": n_filled,
            "fill_rate": round(n_filled / n_orders, 4) if n_orders else None,
            "immediate_cross_fills": int((filled["fill_kind"] == "immediate_cross").sum()),
            "avg_post_price": round(float(orders["post_price"].mean()), 4) if n_orders else None,
            "avg_entry_cost": round(float(filled["entry_cost"].mean()), 4) if n_filled else None,
            "win_rate_filled": round(float((filled["payout"] > 0).mean()), 4) if n_filled else None,
            "cost_filled": round(float(filled["entry_cost"].sum()), 2) if n_filled else 0.0,
            "pnl_filled": round(float(filled["pnl"].sum()), 4) if n_filled else 0.0,
            "roi_filled": round(float(filled["pnl"].sum() / filled["entry_cost"].sum()), 4) if n_filled else None,
            "adverse_fills_runmax_up": int((filled["runmax_c_delta_at_fill"].fillna(0) > 0.05).sum()),
        }
        if n_filled:
            daily = filled.groupby("target_date")["pnl"].sum()
            t, n_days = daily_tstat(daily)
            out["fill_days"] = n_days
            out["daily_t"] = round(t, 2) if t is not None else None
            out["pos_days"] = int((daily > 0).sum())
        else:
            out["fill_days"] = 0
            out["daily_t"] = None
            out["pos_days"] = 0
        # optimistic sensitivity (conservative fills w=1 + optimistic-half w=0.5)
        opt = sub[sub["optimistic_weight"] > 0]
        if len(opt):
            w = opt["optimistic_weight"]
            cost = float((opt["entry_cost"] * w).sum())
            pnl = float((opt["pnl"] * w).sum())
            out["opt_fill_rate"] = round(float(w.sum()) / n_orders, 4) if n_orders else None
            out["opt_roi"] = round(pnl / cost, 4) if cost else None
        else:
            out["opt_fill_rate"] = out["fill_rate"]
            out["opt_roi"] = None
        return out

    summary_rows = []
    if not trades_df.empty:
        for (rule, group), sub in trades_df.groupby(["rule", "group"]):
            for dist in DISTANCES:
                summary_rows.append(summarize(sub[sub["distance"].eq(dist)], rule, group, str(dist)))
            summary_rows.append(summarize(sub, rule, group, "ALL"))
    summary_df = pd.DataFrame(summary_rows)
    summary_path = out_dir / "m3_maker_backtest_summary.csv"
    summary_df.to_csv(summary_path, index=False)

    manifest = {
        "experiment": "m3_maker_backtest_v0",
        "pre_registered_rules": {
            "signal": f"city-local hours {list(SIGNAL_HOURS)}, decline = running_max_c - current_temp_c >= {DECLINE_MIN_C}",
            "M1_join_improve": "post NO buy at best_no_bid + 0.01, rest to local 21:00, hold to settlement",
            "M2_taker": "take best_no_ask at the same entry snapshot (negative-control baseline)",
            "M3_mid_minus": "post NO buy at NO mid - 0.01",
            "fill_conservative": "filled iff a later snapshot best_no_ask <= post price (fill at post price)",
            "fill_optimistic": ">=2 consecutive snapshots best_no_bid < p with best-bid change -> weight 0.5 (sensitivity only)",
            "price_caps": [MIN_POST_PRICE, MAX_POST_PRICE],
            "kill_conditions": "M1 whitelist fill_rate<5% OR filled ROI<=0 OR daily t<1 -> negative; promising needs fill>=5% & ROI>0 & t>=1.5",
        },
        "inputs": {
            "orderbook_dir": args.orderbook_dir,
            "alignment_city_days": args.alignment_city_days,
            "wu_observed_detail": args.wu_observed_detail,
            "official_running_detail": args.official_running_detail,
            "pm_history_dir": args.pm_history_dir,
        },
        "orderbook_dates": [ob_dates[0], ob_dates[-1]],
        "whitelist_size": len(whitelist),
        "whitelist": sorted(whitelist),
        "repaired": sorted(REPAIRED_CITIES),
        "exhaustion_city_days": len(candidate_pairs),
        "trade_rows": int(len(trades_df)),
        **ob_meta,
        "notes": [
            "Settlement truth: pm_history single-winner labels only; non-single-winner city-days dropped.",
            "Repaired 6 cities use official-station IEM running max / current temp; whitelist uses wu_obs.",
            "'or below' bottom brackets excluded via pm_history question text; only labels present in pm_history are tradable.",
            "Fill simulation is a conservative lower bound from 30-min snapshots; see report for limitations.",
            "M1 posts that would lock/cross the book at t0 execute as taker at the ask (fill_kind=immediate_cross).",
        ],
        "outputs": {"trades": str(trades_path), "summary": str(summary_path)},
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    pd.set_option("display.width", 260)
    cols = ["rule", "group", "distance", "orders", "filled", "fill_rate", "immediate_cross_fills",
            "win_rate_filled", "roi_filled", "daily_t", "fill_days", "pos_days", "adverse_fills_runmax_up",
            "opt_fill_rate", "opt_roi"]
    if not summary_df.empty:
        print(summary_df[cols].to_string(index=False))
    print(json.dumps({k: manifest[k] for k in ("exhaustion_city_days", "trade_rows", "orderbook_files_seen", "no_quote_rows")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
