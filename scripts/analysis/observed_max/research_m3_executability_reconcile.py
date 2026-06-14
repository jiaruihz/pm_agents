#!/usr/bin/env python3
"""M3 executability reconcile v0.

Question this answers
---------------------
The station-basis taker backtest (YES official running bucket, NO d1/d2 after
exhaustion) reports +17% / +5.5% ROI. But every prior backtest *pre-filtered*
quotes to ``best_ask in [0.005, 0.97]`` before counting a trade. That makes the
reported ROI conditional on a cheap/in-band ask existing. This script rebuilds
the FULL rule-triggered candidate universe WITHOUT any ask pre-filter, then for
each candidate pulls the TRUE best ask + size from the raw orderbook snapshot
closest to the decision hour, and classifies it:

  executable : ask in [0.05, 0.97]  AND  ask_size >= 5 shares
  no_edge    : ask > 0.97 (market already converged / no premium left)
  no_asks    : token had no asks at the chosen snapshot (or not_found)
  thin       : ask in [0.05, 0.97] but ask_size < 5 (dust, not really fillable)
  below_floor: ask < 0.05 (degenerate; only meaningful for YES, never for tail NO)

It then recomputes ROI on the executable subset (official pm_history single
winner) and reports the executable share by rule x city.

Hard rules
----------
- Read-only analysis + file outputs. No orders, no src/ edits, no live dirs.
- Settlement truth = pm_history single winner only.
- Full set first: do NOT pre-filter by ask. Missing/converged asks are kept and
  classified, not dropped.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

REPO = Path(__file__).resolve().parents[3]
OBS = REPO / "scripts/analysis/observed_max"
if str(OBS) not in sys.path:
    sys.path.insert(0, str(OBS))

from research_m3_observed_max_residual import CITY_TIMEZONE  # noqa: E402
from research_m3_paper_snapshot_proxy_backtest import parse_bracket  # noqa: E402

BASIS_CITIES = ["Paris", "London", "Milan", "Chicago", "KualaLumpur", "PanamaCity"]
DECISION_HOURS = (14, 15, 16, 17)

# executability thresholds
ASK_BAND_LO = 0.05
ASK_BAND_HI = 0.97
MIN_SIZE = 5.0

# NO exhaustion gate
DECLINE_MIN_C = 1.0


def round_half_up(x: float) -> int:
    return int(math.floor(float(x) + 0.5))


@dataclass(frozen=True)
class Book:
    best_ask: float | None
    ask_size: float | None
    n_ask_levels: int
    status: str  # 'ok' | 'no_asks' | 'not_found'


def book_from_raw(record: dict) -> Book:
    status = str(record.get("status") or "").lower()
    raw = record.get("raw")
    if not isinstance(raw, dict):
        return Book(None, None, 0, "not_found" if status != "ok" else "no_asks")
    asks = raw.get("asks")
    if not isinstance(asks, list) or not asks:
        return Book(None, None, 0, "no_asks")
    best: tuple[float, float | None] | None = None
    n = 0
    for a in asks:
        if not isinstance(a, dict):
            continue
        try:
            price = float(a.get("price"))
        except (TypeError, ValueError):
            continue
        n += 1
        sz = a.get("size")
        try:
            size = float(sz) if sz is not None else None
        except (TypeError, ValueError):
            size = None
        if best is None or price < best[0]:
            best = (price, size)
    if best is None:
        return Book(None, None, 0, "no_asks")
    return Book(best[0], best[1], n, "ok")


def load_running_max(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df = df[df["city"].isin(BASIS_CITIES)].copy()
    df = df[df["decision_hour_local"].isin(DECISION_HOURS)].copy()
    # running market value: F city uses round(running_max_raw) [F]; C city uses round(running_max_c)
    df["running_value"] = df.apply(
        lambda r: round_half_up(r["running_max_raw"]) if str(r["unit"]).upper() == "F"
        else round_half_up(r["running_max_c"]),
        axis=1,
    )
    df["decline_c"] = df["running_max_c"] - df["current_temp_c"]
    return df[[
        "city", "target_date", "decision_hour_local", "unit",
        "running_max_c", "current_temp_c", "running_value", "decline_c",
    ]].copy()


def load_valid_city_days(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    valid = df[df["pm_history_valid"].fillna(False) & df["winner_count"].eq(1)].copy()
    valid["winner_label"] = valid["winner_labels"].astype(str).str.strip()
    return valid[["city", "target_date", "unit", "winner_label"]].copy()


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
                bottoms.add((str(city), str(day), str(b.get("label") or "").strip()))
    return bottoms


def tail_distance(bracket_low: float | None, running_value: float, unit: str) -> int | None:
    if bracket_low is None:
        return None
    if float(bracket_low) <= float(running_value):
        return None
    if str(unit).upper() == "F":
        return int(math.ceil((float(bracket_low) - float(running_value)) / 2.0))
    return int(round(float(bracket_low) - float(running_value)))


def iter_books(
    orderbook_dir: Path,
    target_keys: set[tuple[str, str]],
    decision_hours: set[int],
) -> pd.DataFrame:
    """Return all book rows for (city, event_date) pairs we care about, in 14-17h
    local, one row per (city, date, hour, bracket, outcome, snapshot)."""
    rows: list[dict] = []
    for path in sorted(orderbook_dir.glob("*/*.jsonl.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                city = str(record.get("city") or "")
                event_date = str(record.get("event_date") or "")
                if (city, event_date) not in target_keys:
                    continue
                tz = CITY_TIMEZONE.get(city)
                if not tz:
                    continue
                ts = pd.to_datetime(record.get("snapshot_ts_utc"), utc=True, errors="coerce")
                if pd.isna(ts):
                    continue
                local = ts.tz_convert(ZoneInfo(tz))
                if local.date().isoformat() != event_date:
                    continue
                hour = int(local.hour)
                if hour not in decision_hours:
                    continue
                outcome = str(record.get("outcome") or "").lower()
                if outcome not in {"yes", "no"}:
                    continue
                book = book_from_raw(record)
                rows.append({
                    "city": city,
                    "target_date": event_date,
                    "decision_hour_local": hour,
                    "bracket": str(record.get("bracket") or "").strip(),
                    "outcome": outcome,
                    "snapshot_ts_utc": ts,
                    "best_ask": book.best_ask,
                    "ask_size": book.ask_size,
                    "n_ask_levels": book.n_ask_levels,
                    "book_status": book.status,
                    "token_id": record.get("token_id"),
                })
    return pd.DataFrame(rows)


def pick_closest_per_hour(books: pd.DataFrame) -> pd.DataFrame:
    """For each (city, date, hour, bracket, outcome) keep the snapshot closest to
    the top of the decision hour (i.e. latest within the hour as the decision
    proxy — mirrors backtest last_quote_per_hour)."""
    if books.empty:
        return books
    books = books.sort_values("snapshot_ts_utc")
    key = ["city", "target_date", "decision_hour_local", "bracket", "outcome"]
    return books.groupby(key, as_index=False).tail(1)


def classify(ask: float | None, size: float | None, status: str) -> str:
    if status != "ok" or ask is None:
        return "no_asks"
    if ask > ASK_BAND_HI:
        return "no_edge"
    if ask < ASK_BAND_LO:
        return "below_floor"
    if size is None or size < MIN_SIZE:
        return "thin"
    return "executable"


def build_candidates(
    rmax: pd.DataFrame,
    valid: pd.DataFrame,
    bottoms: set[tuple[str, str, str]],
    books: pd.DataFrame,
) -> pd.DataFrame:
    """Full rule-triggered candidate set, joined to true book, classified.

    Drives off rmax (rule decision rows) x pm_history brackets. The bracket
    universe is taken from the books frame (every bracket that exists in the
    market on that city-day appears there as both yes and no tokens)."""
    # restrict rmax to valid (single-winner) city-days
    rmax = rmax.merge(valid, on=["city", "target_date"], how="inner", suffixes=("", "_v"))
    # bracket universe per city-day from books (distinct bracket labels with parsed bounds)
    uni = books[["city", "target_date", "bracket"]].drop_duplicates().copy()
    parsed = uni["bracket"].apply(parse_bracket)
    uni["bracket_low"] = parsed.apply(lambda b: b.low_f if b else None)
    uni["bracket_high"] = parsed.apply(lambda b: b.high_f if b else None)

    cand_rows: list[dict] = []
    for _, dr in rmax.iterrows():
        city, day, hour = dr["city"], dr["target_date"], int(dr["decision_hour_local"])
        unit = str(dr["unit"]).upper()
        rv = float(dr["running_value"])
        decline = float(dr["decline_c"])
        winner = str(dr["winner_label"]).strip()
        bu = uni[(uni["city"] == city) & (uni["target_date"] == day)]
        if bu.empty:
            continue
        for _, br in bu.iterrows():
            label = str(br["bracket"]).strip()
            low = br["bracket_low"]
            high = br["bracket_high"]
            is_bottom = (city, day, label) in bottoms

            # YES rule: bracket that contains the running market value
            contains = (
                (low is None or rv >= float(low) - 1e-9)
                and (high is None or rv <= float(high) + 1e-9)
            )
            if contains:
                cand_rows.append(_cand(dr, city, day, hour, unit, rv, decline, winner,
                                       label, low, high, "yes_bucket", "yes", None, is_bottom))

            # NO rule: distance d1/d2 above running max, decline>=1C, not bottom
            if decline >= DECLINE_MIN_C and not is_bottom:
                dist = tail_distance(None if low is None or (isinstance(low, float) and pd.isna(low)) else float(low), rv, unit)
                if dist is not None and dist in (1, 2):
                    rule = f"no_d{dist}_exh"
                    cand_rows.append(_cand(dr, city, day, hour, unit, rv, decline, winner,
                                           label, low, high, rule, "no", dist, is_bottom))

    cand = pd.DataFrame(cand_rows)
    if cand.empty:
        return cand

    # join the closest book per (city,date,hour,bracket,outcome)
    bk = books[[
        "city", "target_date", "decision_hour_local", "bracket", "outcome",
        "best_ask", "ask_size", "n_ask_levels", "book_status", "snapshot_ts_utc", "token_id",
    ]].copy()
    cand = cand.merge(
        bk,
        on=["city", "target_date", "decision_hour_local", "bracket", "outcome"],
        how="left",
    )
    # rows with no matching snapshot at all -> no book record
    cand["book_status"] = cand["book_status"].fillna("missing_snapshot")
    cand["status"] = cand.apply(
        lambda r: "no_snapshot" if r["book_status"] == "missing_snapshot"
        else classify(r["best_ask"], r["ask_size"], r["book_status"]),
        axis=1,
    )
    return cand


def _cand(dr, city, day, hour, unit, rv, decline, winner, label, low, high,
          rule, outcome, dist, is_bottom) -> dict:
    return {
        "city": city,
        "target_date": day,
        "decision_hour_local": hour,
        "unit": unit,
        "running_value": rv,
        "running_max_c": float(dr["running_max_c"]),
        "current_temp_c": float(dr["current_temp_c"]),
        "decline_c": decline,
        "winner_label": winner,
        "bracket": label,
        "bracket_low": low,
        "bracket_high": high,
        "rule": rule,
        "outcome": outcome,
        "distance": dist,
        "is_bottom": is_bottom,
    }


def dedup_one_per_cityday_bracket(cand: pd.DataFrame, keep: str = "first") -> pd.DataFrame:
    """One entry per (city, date, rule, bracket). keep='first' -> earliest hour
    (matches the NO backtest); keep='last' -> latest hour. Used only for the
    secondary 'one realistic entry per city-day' view, not the primary per-quote
    full set."""
    if cand.empty:
        return cand
    cand = cand.sort_values(["city", "target_date", "rule", "bracket", "decision_hour_local"])
    return cand.groupby(["city", "target_date", "rule", "bracket"], as_index=False).tail(1) \
        if keep == "last" else \
        cand.groupby(["city", "target_date", "rule", "bracket"], as_index=False).head(1)


def _settle(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["bracket_is_winner"] = df["bracket"].astype(str).str.strip().eq(df["winner_label"].astype(str).str.strip())
    yes = df["outcome"].eq("yes")
    df["payout"] = 0.0
    df.loc[yes, "payout"] = df.loc[yes, "bracket_is_winner"].astype(float)
    df.loc[~yes, "payout"] = (~df.loc[~yes, "bracket_is_winner"]).astype(float)
    df["cost"] = df["best_ask"].astype(float)
    df["pnl"] = df["payout"] - df["cost"]
    return df


def roi_by_rule(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    ex = _settle(df)
    grp = ex.groupby("rule").agg(
        trades=("pnl", "size"),
        city_days=("target_date", "size"),
        cities=("city", "nunique"),
        avg_ask=("best_ask", "mean"),
        median_ask=("best_ask", "median"),
        median_size=("ask_size", "median"),
        win_rate=("payout", lambda s: float((s > 0).mean())),
        cost=("cost", "sum"),
        pnl=("pnl", "sum"),
    ).reset_index()
    grp["roi"] = grp["pnl"] / grp["cost"]
    return grp


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--running-max", default=str(
        REPO / "docs/analysis/2026-06/generated/official_station_running_max_v0/official_station_running_max_detail.csv"))
    ap.add_argument("--alignment", default=str(
        REPO / "docs/analysis/2026-06/generated/m3_settlement_alignment_v1/m3_settlement_alignment_city_days.csv"))
    ap.add_argument("--pm-history-dir", default=str(
        REPO / "runtime/weather_edge_v1/market_data/cache/pm_history"))
    ap.add_argument("--orderbook-dir", default=str(
        REPO / "runtime/weather_edge_v1/market_data/orderbook_snapshots"))
    ap.add_argument("--output-dir", default=str(
        REPO / "docs/analysis/2026-06/generated/m3_executability_reconcile_v0"))
    args = ap.parse_args()

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    rmax = load_running_max(Path(args.running_max))
    valid = load_valid_city_days(Path(args.alignment))
    valid = valid[valid["city"].isin(BASIS_CITIES)].copy()
    bottoms = load_bottom_brackets(Path(args.pm_history_dir), valid)

    # target (city, date) keys = valid basis city-days that also have running max
    rmax_days = rmax[["city", "target_date"]].drop_duplicates()
    target_days = rmax_days.merge(valid[["city", "target_date"]].drop_duplicates(),
                                  on=["city", "target_date"], how="inner")
    target_keys = set(map(tuple, target_days.itertuples(index=False)))

    books = iter_books(Path(args.orderbook_dir), target_keys, set(DECISION_HOURS))
    books = pick_closest_per_hour(books)

    cand = build_candidates(rmax, valid, bottoms, books)
    if cand.empty:
        print("NO CANDIDATES — check inputs")
        return 1

    # PRIMARY full set = every rule-triggered (city,date,hour,bracket,outcome)
    # quote in h14-17, no ask pre-filter. This is the same granularity the
    # backtest counts trades at (per quote), so executable-share and ROI here
    # are directly comparable to the backtest.

    # ---- summary by rule x city
    def share(df: pd.DataFrame) -> pd.DataFrame:
        g = df.groupby(["rule", "city", "status"]).size().unstack(fill_value=0)
        for s in ["executable", "no_edge", "no_asks", "thin", "below_floor", "no_snapshot"]:
            if s not in g.columns:
                g[s] = 0
        g["total"] = g.sum(axis=1)
        g["executable_pct"] = (g["executable"] / g["total"] * 100).round(1)
        return g.reset_index()

    by_rule_city = share(cand)

    # earliest-hour-per-(city,date,rule,bracket): "is there ANY executable entry
    # for this city-day-bracket?" view (matches realistic single-entry trading)
    cand_first = dedup_one_per_cityday_bracket(cand, keep="first")
    by_rule_city_first = share(cand_first)
    by_rule_first = (
        cand_first.groupby(["rule", "status"]).size().unstack(fill_value=0)
    )
    for s in ["executable", "no_edge", "no_asks", "thin", "below_floor", "no_snapshot"]:
        if s not in by_rule_first.columns:
            by_rule_first[s] = 0
    by_rule_first["total"] = by_rule_first.sum(axis=1)
    by_rule_first["executable_pct"] = (by_rule_first["executable"] / by_rule_first["total"] * 100).round(1)
    by_rule_first = by_rule_first.reset_index()

    # executable-share by hour (mechanism: convergence with clock)
    by_hour = (
        cand.groupby(["rule", "decision_hour_local", "status"]).size().unstack(fill_value=0)
    )
    for s in ["executable", "no_edge", "no_asks", "thin", "below_floor", "no_snapshot"]:
        if s not in by_hour.columns:
            by_hour[s] = 0
    by_hour["total"] = by_hour.sum(axis=1)
    by_hour["executable_pct"] = (by_hour["executable"] / by_hour["total"] * 100).round(1)
    by_hour = by_hour.reset_index()
    by_rule = (
        cand.groupby(["rule", "status"]).size().unstack(fill_value=0)
    )
    for s in ["executable", "no_edge", "no_asks", "thin", "below_floor", "no_snapshot"]:
        if s not in by_rule.columns:
            by_rule[s] = 0
    by_rule["total"] = by_rule.sum(axis=1)
    by_rule["executable_pct"] = (by_rule["executable"] / by_rule["total"] * 100).round(1)
    by_rule = by_rule.reset_index()

    # executable ask/size distribution
    ex = cand[cand["status"] == "executable"]
    dist_rows = []
    for rule, g in cand.groupby("rule"):
        exg = g[g["status"] == "executable"]
        dist_rows.append({
            "rule": rule,
            "n_executable": int(len(exg)),
            "ask_min": float(exg["best_ask"].min()) if len(exg) else None,
            "ask_p25": float(exg["best_ask"].quantile(0.25)) if len(exg) else None,
            "ask_median": float(exg["best_ask"].median()) if len(exg) else None,
            "ask_p75": float(exg["best_ask"].quantile(0.75)) if len(exg) else None,
            "ask_max": float(exg["best_ask"].max()) if len(exg) else None,
            "size_min": float(exg["ask_size"].min()) if len(exg) else None,
            "size_median": float(exg["ask_size"].median()) if len(exg) else None,
            "size_max": float(exg["ask_size"].max()) if len(exg) else None,
        })
    dist = pd.DataFrame(dist_rows)

    # ---- ROI on three nested subsets (per-quote granularity, official winner)
    ex = cand[cand["status"] == "executable"].copy()
    inband_any_size = cand[
        cand["best_ask"].notna()
        & (cand["best_ask"] >= ASK_BAND_LO)
        & (cand["best_ask"] <= ASK_BAND_HI)
    ].copy()
    # backtest-band: the filter the original backtest actually used
    #   YES: ask in [0.005, 0.995] ; NO: ask in [0.005, 0.97]
    bt_band = cand[cand["best_ask"].notna()].copy()
    bt_band = bt_band[
        ((bt_band["outcome"] == "yes") & (bt_band["best_ask"] >= 0.005) & (bt_band["best_ask"] <= 0.995))
        | ((bt_band["outcome"] == "no") & (bt_band["best_ask"] >= 0.005) & (bt_band["best_ask"] <= 0.97))
    ].copy()

    roi_exec = roi_by_rule(ex)
    roi_inband = roi_by_rule(inband_any_size)
    roi_btband = roi_by_rule(bt_band)
    for d, tag in [(roi_exec, "executable_strict"), (roi_inband, "inband_any_size"), (roi_btband, "backtest_band")]:
        if not d.empty:
            d.insert(0, "subset", tag)
    roi_all = pd.concat([d for d in [roi_exec, roi_inband, roi_btband] if not d.empty], ignore_index=True)

    # secondary: one realistic entry per city-day-bracket (NO=earliest hour, YES=earliest)
    cand_one = dedup_one_per_cityday_bracket(cand, keep="first")
    roi_one_exec = roi_by_rule(cand_one[cand_one["status"] == "executable"])
    if not roi_one_exec.empty:
        roi_one_exec.insert(0, "subset", "executable_strict_one_per_cityday")

    # write outputs
    cand_out = cand.copy()
    cand_out["snapshot_ts_utc"] = cand_out["snapshot_ts_utc"].astype(str)
    cand_path = out / "executability_candidates.csv"
    cand_out.to_csv(cand_path, index=False)
    by_rule_city.to_csv(out / "executability_share_by_rule_city.csv", index=False)
    by_rule.to_csv(out / "executability_share_by_rule.csv", index=False)
    by_rule_first.to_csv(out / "executability_share_by_rule_one_per_cityday.csv", index=False)
    by_rule_city_first.to_csv(out / "executability_share_by_rule_city_one_per_cityday.csv", index=False)
    by_hour.to_csv(out / "executability_share_by_hour.csv", index=False)
    dist.to_csv(out / "executable_ask_size_distribution.csv", index=False)
    roi_all.to_csv(out / "subset_roi_by_rule.csv", index=False)
    if not roi_one_exec.empty:
        roi_one_exec.to_csv(out / "executable_one_per_cityday_roi.csv", index=False)

    manifest = {
        "experiment": "m3_executability_reconcile_v0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "basis_cities": BASIS_CITIES,
        "decision_hours": list(DECISION_HOURS),
        "executable_def": {
            "ask_band": [ASK_BAND_LO, ASK_BAND_HI],
            "min_ask_size_shares": MIN_SIZE,
            "decline_min_c_for_no": DECLINE_MIN_C,
        },
        "inputs": {
            "running_max": args.running_max,
            "alignment": args.alignment,
            "pm_history_dir": args.pm_history_dir,
            "orderbook_dir": args.orderbook_dir,
        },
        "counts": {
            "target_city_days": int(len(target_days)),
            "book_rows": int(len(books)),
            "candidates": int(len(cand)),
            "candidates_yes": int((cand["outcome"] == "yes").sum()),
            "candidates_no": int((cand["outcome"] == "no").sum()),
            "executable_strict": int(len(ex)),
            "inband_any_size": int(len(inband_any_size)),
            "backtest_band": int(len(bt_band)),
        },
        "status_counts_overall": {k: int(v) for k, v in cand["status"].value_counts().items()},
        "outputs": {
            "candidates": str(cand_path),
            "share_by_rule_city": str(out / "executability_share_by_rule_city.csv"),
            "share_by_rule": str(out / "executability_share_by_rule.csv"),
            "share_by_rule_one_per_cityday": str(out / "executability_share_by_rule_one_per_cityday.csv"),
            "share_by_rule_city_one_per_cityday": str(out / "executability_share_by_rule_city_one_per_cityday.csv"),
            "share_by_hour": str(out / "executability_share_by_hour.csv"),
            "ask_size_distribution": str(out / "executable_ask_size_distribution.csv"),
            "subset_roi_by_rule": str(out / "subset_roi_by_rule.csv"),
            "executable_one_per_cityday_roi": str(out / "executable_one_per_cityday_roi.csv"),
        },
        "notes": [
            "PRIMARY full set = every rule-triggered (city,date,hour,bracket,outcome) quote in h14-17, NO ask pre-filter (same per-quote granularity as the backtest).",
            "Book = raw orderbook snapshot closest to decision hour (latest within hour).",
            "status: executable=ask in [0.05,0.97] and ask_size>=5; no_edge=ask>0.97; no_asks=token had no asks; thin=in band but size<5; below_floor=ask<0.05; no_snapshot=no book at that hour.",
            "subset_roi_by_rule reports ROI on 3 nested subsets: executable_strict (my taker bar), inband_any_size (ask 0.05-0.97 any size), backtest_band (the original filter: YES ask<=0.995, NO ask<=0.97).",
            "Settlement = pm_history single winner (alignment_city_days pm_history_valid & winner_count==1).",
        ],
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 30)
    print("=== executability share by rule (FULL per-quote set) ===")
    print(by_rule.to_string(index=False))
    print("\n=== executability share by rule (one entry per city-day-bracket, earliest hour) ===")
    print(by_rule_first.to_string(index=False))
    print("\n=== executability share by hour (convergence mechanism) ===")
    print(by_hour.to_string(index=False))
    print("\n=== executability share by rule x city (per-quote) ===")
    print(by_rule_city.to_string(index=False))
    print("\n=== executable ask/size distribution ===")
    print(dist.to_string(index=False))
    print("\n=== subset ROI by rule (executable / inband / backtest_band) ===")
    print(roi_all.to_string(index=False) if not roi_all.empty else "(no trades)")
    print("\n=== executable ROI, one entry per city-day-bracket ===")
    print(roi_one_exec.to_string(index=False) if not roi_one_exec.empty else "(no executable trades)")
    print("\n=== overall status counts ===")
    print(cand["status"].value_counts().to_string())
    print(json.dumps(manifest["counts"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
