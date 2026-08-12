#!/usr/bin/env python3
"""Market-implied station tracking: does Polymarket pricing track the official
resolution station or the (wrong) station that public bots / market makers use?

For 6 "DIFF" cities (official station != the station our old pipeline / public
bots used), we reconstruct, for each (city, target_date, decision_hour_local in
14..16), the *market-implied most-likely daily-max reading* from the orderbook
YES-token mids across all brackets, and compare it against:

  - official station running max (rounded to bracket)  [IEM]
  - wrong   station running max (rounded to bracket)    [IEM]

If the market-implied reading is closer to the WRONG station, the station-basis
edge is real (the market is mispricing because it tracks the wrong station).
If it is closer to the OFFICIAL station, the market already tracks the right
station and our shadow wins were luck.

Read-only research. No live actions. Strict no-lookahead: for a given
(city,date,hour) we only use orderbook snapshots and observations at or before
that local hour.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from scripts.analysis.versioned_artifact_output import (  # noqa: E402
    resolve_content_addressed_artifact,
)
from scripts.ops.weather_market_proxy import production_market_proxy_url  # noqa: E402

OFFICIAL_RUNNING_DETAIL_SHA256 = "5ba66fa0201d2385d53fe085f827020172ab95aab726d20232755fce2a7b4e1a"

PROXIES = [None, production_market_proxy_url()]

# city -> tz
CITY_TZ = {
    "Paris": "Europe/Paris",
    "London": "Europe/London",
    "Milan": "Europe/Rome",
    "Chicago": "America/Chicago",
    "KualaLumpur": "Asia/Kuala_Lumpur",
    "PanamaCity": "America/Panama",
}

# city -> (official_icao, wrong_icao, unit, iem_data_col, bracket_width)
CITY_CFG = {
    "Paris": ("LFPB", "LFPG", "C", "tmpc", 1.0),
    "London": ("EGLC", "EGLL", "C", "tmpc", 1.0),
    "Milan": ("LIMC", "LIML", "C", "tmpc", 1.0),
    "Chicago": ("KORD", "KMDW", "F", "tmpf", 2.0),
    "KualaLumpur": ("WMKK", "WMSA", "C", "tmpc", 1.0),
    "PanamaCity": ("MPMG", "MPTO", "C", "tmpc", 1.0),
}

DECISION_HOURS = (14, 15, 16)


# --------------------------------------------------------------------------- #
# Bracket parsing (native units: C for most, F for Chicago)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Bracket:
    label: str
    low: float | None
    high: float | None
    is_tail: bool  # "or below" / "+" tail bracket

    def contains(self, value: float) -> bool:
        if self.low is not None and value < self.low:
            return False
        if self.high is not None and value > self.high:
            return False
        return True

    def representative(self) -> float:
        """A single representative reading for distance comparison.

        Point bracket -> its integer label. Open-low tail -> its high edge.
        Open-high tail -> its low edge. Width-2 (Chicago) range -> midpoint.
        """
        if self.low is not None and self.high is not None:
            return (self.low + self.high) / 2.0
        if self.high is not None:
            return self.high
        if self.low is not None:
            return self.low
        return float("nan")


def parse_bracket(label_value: object, question_value: object) -> Bracket | None:
    label = str(label_value or "").replace("°", "").strip()
    question = str(question_value or "").lower()
    if not label:
        return None
    s = label.replace("F", "").replace("C", "").strip()
    is_below = "or below" in question or "or lower" in question
    is_above = "or higher" in question or "or above" in question or s.endswith("+")
    nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", s)]
    if not nums:
        nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", question)]
    if not nums:
        return None
    if is_below:
        return Bracket(label=label, low=None, high=nums[0], is_tail=True)
    if is_above:
        return Bracket(label=label, low=nums[0], high=None, is_tail=True)
    if len(nums) >= 2:
        return Bracket(label=label, low=nums[0], high=nums[1], is_tail=False)
    return Bracket(label=label, low=nums[0], high=nums[0], is_tail=False)


# --------------------------------------------------------------------------- #
# IEM wrong-station running max
# --------------------------------------------------------------------------- #
def fetch_text(url: str, params: list, max_rounds: int = 3) -> str:
    last_err = None
    for rnd in range(max_rounds):
        for proxy in PROXIES:
            try:
                r = httpx.get(url, params=params, proxy=proxy, timeout=90)
                r.raise_for_status()
                return r.text
            except Exception as e:  # noqa: BLE001
                last_err = f"{proxy}: {type(e).__name__}"
                time.sleep(1 + rnd)
    raise RuntimeError(f"fetch failed {url}: {last_err}")


def fetch_iem(icao: str, data_col: str, start: str, end: str, cache_dir: Path) -> pd.DataFrame:
    cache = cache_dir / f"iem_{icao}_{start}_{end}.csv"
    if cache.exists() and cache.stat().st_size > 1000:
        return pd.read_csv(cache)
    y1, m1, d1 = start.split("-")
    y2, m2, d2 = end.split("-")
    params = [
        ("station", icao),
        ("data", data_col),
        ("year1", y1), ("month1", m1), ("day1", d1),
        ("year2", y2), ("month2", m2), ("day2", d2),
        ("tz", "UTC"),
        ("format", "comma"),
        ("latlon", "no"),
        ("missing", "M"),
        ("trace", "T"),
        ("direct", "no"),
        ("report_type", "3"),
        ("report_type", "4"),
    ]
    text = fetch_text("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py", params)
    lines = [l for l in text.splitlines() if not l.startswith("#") and l.strip()]
    if len(lines) < 2:
        raise RuntimeError(f"IEM returned no data for {icao}")
    cache.write_text("\n".join(lines) + "\n")
    return pd.read_csv(cache)


def wrong_running_max(
    icao: str, data_col: str, tz: str, start: str, end: str, cache_dir: Path
) -> dict[tuple[str, int], float]:
    """Return {(local_date, decision_hour): running_max_native} for the wrong
    station, using only obs at or before that local hour (no lookahead)."""
    fetch_end = (date.fromisoformat(end) + timedelta(days=2)).isoformat()
    df = fetch_iem(icao, data_col, start, fetch_end, cache_dir)
    df = df.copy()
    df["valid"] = pd.to_datetime(df["valid"], utc=True, errors="coerce")
    df[data_col] = pd.to_numeric(df[data_col], errors="coerce")
    df = df.dropna(subset=["valid", data_col])
    local = df["valid"].dt.tz_convert(ZoneInfo(tz))
    df["local_date"] = local.dt.date.astype(str)
    df["local_hour"] = local.dt.hour
    out: dict[tuple[str, int], float] = {}
    for (d, h), grp in df.groupby(["local_date", "local_hour"]):
        pass  # placeholder, computed below
    # running max within each local day up to and including each decision hour
    for d, day_grp in df.groupby("local_date"):
        for h in DECISION_HOURS:
            sub = day_grp[day_grp["local_hour"] <= h]
            if sub.empty:
                continue
            out[(str(d), int(h))] = float(sub[data_col].max())
    return out


def round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


def round_to_bracket(value: float, width: float) -> float:
    """Round a native reading to the bracket center. C width=1 -> integer.
    F width=2 (Chicago) -> nearest odd-or-even bucket center. We keep the
    bracket centers as the pm labels imply; for Chicago brackets are 2-wide so
    we snap to the nearest bracket midpoint inferred from observed brackets.
    For comparison purposes we just round to nearest `width` multiple offset
    appropriately; but since we compare against bracket.representative(), we
    instead return the rounded-half-up reading and let bracket matching handle
    width. Here width is only used for C=1 integer rounding."""
    if width == 1.0:
        return float(round_half_up(value))
    # Chicago F: round to nearest integer reading; bracket matching maps it.
    return float(round_half_up(value))


# --------------------------------------------------------------------------- #
# Orderbook -> market-implied distribution
# --------------------------------------------------------------------------- #
def mid_from_raw(raw: object) -> float | None:
    if not isinstance(raw, dict):
        return None
    bids = raw.get("bids") or []
    asks = raw.get("asks") or []
    bid_prices = []
    ask_prices = []
    for b in bids:
        if isinstance(b, dict):
            try:
                bid_prices.append(float(b.get("price")))
            except (TypeError, ValueError):
                pass
    for a in asks:
        if isinstance(a, dict):
            try:
                ask_prices.append(float(a.get("price")))
            except (TypeError, ValueError):
                pass
    best_bid = max(bid_prices) if bid_prices else None
    best_ask = min(ask_prices) if ask_prices else None
    if best_bid is not None and best_ask is not None:
        return (best_bid + best_ask) / 2.0
    if best_bid is not None:
        return best_bid
    if best_ask is not None:
        return best_ask
    return None


def collect_yes_mids(
    orderbook_dir: Path, start: str, end: str
) -> dict[tuple[str, str, int], dict[str, dict]]:
    """Return {(city,date,hour): {bracket_label: {"mid":..,"bracket":Bracket}}}.

    Uses last YES-token snapshot at or before the decision hour for each
    bracket (no lookahead: only snapshots whose local hour <= decision hour AND
    on the target local date)."""
    # We need bracket questions to classify tails; orderbook lacks question text,
    # so we resolve Bracket from pm_history token map later. Here we just collect
    # raw YES mids keyed by token_id and bracket.
    result: dict[tuple[str, str, int], dict[str, dict]] = {}
    # track latest snapshot ts per (city,date,hour,bracket)
    latest_ts: dict[tuple, str] = {}

    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    for day_dir in sorted(orderbook_dir.glob("*")):
        if not day_dir.is_dir():
            continue
        try:
            dir_d = date.fromisoformat(day_dir.name)
        except ValueError:
            continue
        # snapshots up to end+1 may carry late-UTC rows for target dates in range
        if dir_d < start_d - timedelta(days=1) or dir_d > end_d + timedelta(days=1):
            continue
        for path in sorted(day_dir.glob("*.jsonl.gz")):
            with gzip.open(path, "rt", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    city = rec.get("city")
                    if city not in CITY_TZ:
                        continue
                    if str(rec.get("outcome") or "").lower() != "yes":
                        continue
                    if str(rec.get("status") or "ok") == "not_found":
                        continue
                    tz = CITY_TZ[city]
                    ts = pd.to_datetime(rec.get("snapshot_ts_utc"), utc=True, errors="coerce")
                    if pd.isna(ts):
                        continue
                    local = ts.tz_convert(ZoneInfo(tz))
                    local_date = local.date().isoformat()
                    if local_date < start or local_date > end:
                        continue
                    event_date = str(rec.get("event_date") or "")
                    if event_date and event_date != local_date:
                        continue
                    local_hour = int(local.hour)
                    mid = mid_from_raw(rec.get("raw"))
                    if mid is None:
                        continue
                    bracket_label = str(rec.get("bracket") or "").strip()
                    if not bracket_label:
                        continue
                    question = rec.get("question") or rec.get("raw_question") or ""
                    for h in DECISION_HOURS:
                        if local_hour > h:
                            continue
                        key = (city, local_date, h)
                        bkey = (city, local_date, h, bracket_label)
                        ts_iso = ts.isoformat()
                        if bkey in latest_ts and latest_ts[bkey] >= ts_iso:
                            continue
                        latest_ts[bkey] = ts_iso
                        result.setdefault(key, {})[bracket_label] = {
                            "mid": mid,
                            "question": question,
                            "label": bracket_label,
                        }
    return result


def load_bracket_questions(pm_history_dir: Path, city: str, day: str) -> dict[str, str]:
    f = pm_history_dir / f"{city}_{day}.json"
    if not f.exists():
        return {}
    try:
        data = json.loads(f.read_text())
    except json.JSONDecodeError:
        return {}
    out: dict[str, str] = {}
    for b in data.get("brackets", []):
        out[str(b.get("label") or "").strip()] = str(b.get("question") or "")
    return out


def modal_reading(
    bracket_mids: dict[str, dict],
    pm_questions: dict[str, str],
    width: float,
) -> tuple[float | None, float | None, str | None, float]:
    """Return (modal_representative, weighted_expected_reading, modal_label,
    total_mass). Normalizes YES mids across brackets into a distribution."""
    parsed: list[tuple[Bracket, float]] = []
    total = 0.0
    for label, info in bracket_mids.items():
        q = pm_questions.get(label, info.get("question") or "")
        br = parse_bracket(label, q)
        if br is None:
            continue
        m = float(info["mid"])
        if m <= 0:
            continue
        parsed.append((br, m))
        total += m
    if not parsed or total <= 0:
        return None, None, None, 0.0
    # modal = bracket with max normalized mass
    modal_br, modal_mass = max(parsed, key=lambda t: t[1])
    # weighted expected representative reading
    wexp = sum(br.representative() * (m / total) for br, m in parsed)
    return modal_br.representative(), wexp, modal_br.label, total


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-05-19")
    ap.add_argument("--end", default="2026-06-09")
    ap.add_argument(
        "--orderbook-dir",
        default=str(REPO / "runtime/weather_edge_v1/market_data/orderbook_snapshots"),
    )
    ap.add_argument(
        "--pm-history-dir",
        default=str(REPO / "runtime/weather_edge_v1/market_data/cache/pm_history"),
    )
    ap.add_argument(
        "--official-running-max",
        default=str(resolve_content_addressed_artifact(OFFICIAL_RUNNING_DETAIL_SHA256)),
    )
    ap.add_argument(
        "--wrong-cache-dir",
        default=str(REPO / "runtime/rule_source_research/wrong_station_cache"),
    )
    ap.add_argument(
        "--output-dir",
        default=str(REPO / "docs/analysis/2026-06/generated/m3_market_station_tracking_v0"),
    )
    args = ap.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    wrong_cache = Path(args.wrong_cache_dir)
    wrong_cache.mkdir(parents=True, exist_ok=True)
    pm_dir = Path(args.pm_history_dir)

    # 1) official running max -> {(city,date,hour): running_max_native}
    off = pd.read_csv(args.official_running_max)
    off_unit_col = {}
    official_map: dict[tuple[str, str, int], float] = {}
    for _, r in off.iterrows():
        city = str(r["city"])
        if city not in CITY_CFG:
            continue
        unit = CITY_CFG[city][2]
        h = int(r["decision_hour_local"])
        if h not in DECISION_HOURS:
            continue
        val = float(r["running_max_raw"]) if unit == "F" else float(r["running_max_c"])
        official_map[(city, str(r["target_date"]), h)] = val

    # 2) wrong running max per city
    wrong_map: dict[str, dict[tuple[str, int], float]] = {}
    for city, (off_icao, wrong_icao, unit, data_col, width) in CITY_CFG.items():
        try:
            wrong_map[city] = wrong_running_max(
                wrong_icao, data_col, CITY_TZ[city], args.start, args.end, wrong_cache
            )
            print(f"{city:13} wrong={wrong_icao} keys={len(wrong_map[city])}", file=sys.stderr)
        except RuntimeError as e:
            print(f"{city:13} wrong={wrong_icao} FETCH FAILED: {e}", file=sys.stderr)
            wrong_map[city] = {}

    # 3) market YES mids
    yes_mids = collect_yes_mids(Path(args.orderbook_dir), args.start, args.end)
    print(f"market keys (city,date,hour) with YES mids: {len(yes_mids)}", file=sys.stderr)

    # 4) per city-day-hour join + classification
    rows = []
    pm_q_cache: dict[tuple[str, str], dict[str, str]] = {}
    for (city, day, h), bracket_mids in sorted(yes_mids.items()):
        cfg = CITY_CFG[city]
        unit, width = cfg[2], cfg[4]
        if (city, day) not in pm_q_cache:
            pm_q_cache[(city, day)] = load_bracket_questions(pm_dir, city, day)
        pm_questions = pm_q_cache[(city, day)]
        modal_rep, wexp, modal_label, mass = modal_reading(bracket_mids, pm_questions, width)
        if modal_rep is None:
            continue
        off_val = official_map.get((city, day, h))
        wrong_val = wrong_map.get(city, {}).get((day, h))
        if off_val is None or wrong_val is None:
            continue
        off_round = round_to_bracket(off_val, width)
        wrong_round = round_to_bracket(wrong_val, width)
        d_off = abs(modal_rep - off_round)
        d_wrong = abs(modal_rep - wrong_round)
        if d_off < d_wrong:
            tracks = "official"
        elif d_wrong < d_off:
            tracks = "wrong"
        else:
            tracks = "tie"
        rows.append(
            {
                "city": city,
                "target_date": day,
                "decision_hour_local": h,
                "unit": unit,
                "n_brackets_priced": len(bracket_mids),
                "total_yes_mass": round(mass, 4),
                "market_modal_label": modal_label,
                "market_modal_reading": round(modal_rep, 3),
                "market_weighted_reading": round(wexp, 3) if wexp is not None else None,
                "official_running_max": round(off_val, 3),
                "official_round": off_round,
                "wrong_running_max": round(wrong_val, 3),
                "wrong_round": wrong_round,
                "basis_official_minus_wrong": off_round - wrong_round,
                "market_modal_minus_official": round(modal_rep - off_round, 3),
                "market_modal_minus_wrong": round(modal_rep - wrong_round, 3),
                "dist_to_official": round(d_off, 3),
                "dist_to_wrong": round(d_wrong, 3),
                "tracks": tracks,
            }
        )

    detail = pd.DataFrame(rows)
    detail_path = out_dir / "market_station_tracking_detail.csv"
    detail.to_csv(detail_path, index=False)

    if detail.empty:
        print("NO ROWS produced", file=sys.stderr)
        return 1

    # exclude ties from the tracks ratio (ambiguous); report separately
    decisive = detail[detail["tracks"] != "tie"]

    def ratio(df: pd.DataFrame) -> dict:
        n = len(df)
        dec = df[df["tracks"] != "tie"]
        nw = int((dec["tracks"] == "wrong").sum())
        no = int((dec["tracks"] == "official").sum())
        nt = int((df["tracks"] == "tie").sum())
        return {
            "n_total": n,
            "n_tie": nt,
            "n_decisive": len(dec),
            "n_tracks_wrong": nw,
            "n_tracks_official": no,
            "pct_wrong_of_decisive": round(nw / len(dec), 4) if len(dec) else None,
            "pct_official_of_decisive": round(no / len(dec), 4) if len(dec) else None,
            "median_basis_off_minus_wrong": float(df["basis_official_minus_wrong"].median()),
            "median_modal_minus_wrong": float(df["market_modal_minus_wrong"].median()),
            "median_modal_minus_official": float(df["market_modal_minus_official"].median()),
        }

    overall = ratio(detail)
    by_city_rows = []
    for city, g in detail.groupby("city"):
        d = ratio(g)
        d["city"] = city
        by_city_rows.append(d)
    by_city = pd.DataFrame(by_city_rows)
    by_city_path = out_dir / "market_station_tracking_by_city.csv"
    by_city.to_csv(by_city_path, index=False)

    # basis distribution
    basis_dist = (
        detail.groupby("basis_official_minus_wrong").size().rename("count").reset_index()
    )
    modal_wrong_dist = (
        detail.groupby("market_modal_minus_wrong").size().rename("count").reset_index()
    )
    basis_path = out_dir / "market_station_tracking_basis_dist.csv"
    with open(basis_path, "w") as f:
        f.write("basis_official_minus_wrong,count\n")
        for _, r in basis_dist.iterrows():
            f.write(f"{r['basis_official_minus_wrong']},{int(r['count'])}\n")
        f.write("\nmarket_modal_minus_wrong,count\n")
        for _, r in modal_wrong_dist.iterrows():
            f.write(f"{r['market_modal_minus_wrong']},{int(r['count'])}\n")

    manifest = {
        "experiment": "m3_market_station_tracking_v0",
        "window": [args.start, args.end],
        "decision_hours": list(DECISION_HOURS),
        "cities": list(CITY_CFG),
        "overall": overall,
        "outputs": {
            "detail": str(detail_path),
            "by_city": str(by_city_path),
            "basis_dist": str(basis_path),
        },
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    print(json.dumps(overall, indent=2))
    print("\nBY CITY:")
    print(by_city[
        ["city", "n_total", "n_decisive", "n_tracks_wrong", "n_tracks_official",
         "pct_wrong_of_decisive", "median_basis_off_minus_wrong", "median_modal_minus_wrong"]
    ].to_string(index=False))
    print("\nBASIS (official_round - wrong_round) DIST:")
    print(basis_dist.to_string(index=False))
    print("\nMARKET_MODAL - WRONG_ROUND DIST:")
    print(modal_wrong_dist.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
