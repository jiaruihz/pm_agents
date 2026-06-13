#!/usr/bin/env python3
"""Station-basis shadow tracker for the 6 repaired cities.

Strategy under shadow validation (see
docs/analysis/2026-06/2026-06-12-m3-exhaustion-no-strategy-v0.md and
2026-06-12-official-resolution-source-and-entry-timing-v0.md):

- Universe: cities where the Polymarket official resolution station differs
  from the commonly-watched station (Paris/LFPB, London/EGLC, Milan/LIMC,
  Chicago/KORD, KualaLumpur/WMKK, PanamaCity/MPMG).
- RULE no_d1_exh / no_d2_exh: at city-local hour 13-17, when current temp has
  fallen >= 1.0C below the official-station running max, buy NO on the
  bracket(s) 1 (and 2) above the running market value, ask in [0.005, 0.97].
- RULE yes_bucket: at city-local hour 14-16, buy YES on the bracket containing
  the official-station running market value, ask in [0.05, 0.95].
- One shadow entry per (city, target_date, rule, bracket).
- Before any entry, re-verify the market rules still point at the expected
  official station; mismatch -> alert + skip (stations have drifted before).

Shadow only: records what would have been bought at the live top-of-book ask.
No orders are placed anywhere.

Commands:
  cycle    one pass over all cities (intended to run every ~15 min via loop)
  settle   settle past entries against Gamma closed prices
  report   print performance summary

State and outputs live in runtime/weather_edge_v1/station_basis_shadow/.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(os.environ.get("STATION_BASIS_DATA_ROOT") or os.environ.get("DATA_PROJECT_DIR") or ROOT)
OUT_DIR = DATA_ROOT / "runtime/weather_edge_v1/station_basis_shadow"

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
METAR_API = "https://aviationweather.gov/api/data/metar"

MONTHS = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
]

# city -> (gamma city slug, official ICAO, market unit, timezone)
CITIES = {
    "Paris": ("paris", "LFPB", "C", "Europe/Paris"),
    "London": ("london", "EGLC", "C", "Europe/London"),
    "Milan": ("milan", "LIMC", "C", "Europe/Rome"),
    "Chicago": ("chicago", "KORD", "F", "America/Chicago"),
    "KualaLumpur": ("kuala-lumpur", "WMKK", "C", "Asia/Kuala_Lumpur"),
    "PanamaCity": ("panama-city", "MPMG", "C", "America/Panama"),
    # batch2 (2026-06-13): official station is Halim (WIHH), not WIII; 8/8 days
    # aligned. Small sample — shadow-only until more settlement days accumulate.
    "Jakarta": ("jakarta", "WIHH", "C", "Asia/Jakarta"),
}

NO_HOURS = range(13, 18)       # 13..17 local
YES_HOURS = range(14, 17)      # 14..16 local
YES_ONE_PER_CITY_DAY = False
EXHAUSTION_MIN_DECLINE_C = 1.0
NO_ASK_MIN, NO_ASK_MAX = 0.005, 0.97
YES_ASK_MIN, YES_ASK_MAX = 0.05, 0.95
MAX_SHARES = 10.0
MIN_OBS = 6
MAX_OBS_AGE_MIN = 75
WIN_THRESHOLD = 0.99

WU_URL_RE = re.compile(r"wunderground\.com/history/daily/\S*?/([A-Z0-9]{4})")


def _parse_env_value(raw_value: str) -> str:
    try:
        return shlex.split(raw_value, comments=False, posix=True)[0] if raw_value.strip() else ""
    except Exception:
        return raw_value.strip().strip("\"'")


def _weather_predict_proxy_candidates() -> list[str | None]:
    candidates: list[str | None] = []
    for key in ("WEATHER_PREDICT_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
        value = os.environ.get(key)
        if value:
            candidates.append(value)

    env_path = Path(os.environ.get("WEATHER_PREDICT_DIR", "/home/jiarui/projects/weather-predict")) / ".env"
    if env_path.exists():
        values: dict[str, str] = {}
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, raw_value = line.split("=", 1)
            key = key.strip()
            if key in {"WEATHER_PREDICT_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"}:
                values[key] = _parse_env_value(raw_value)
        for key in ("WEATHER_PREDICT_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
            if values.get(key):
                candidates.append(values[key])

    candidates.extend(["http://127.0.0.1:7897", "http://127.0.0.1:7890", None])
    deduped: list[str | None] = []
    for value in candidates:
        if value not in deduped:
            deduped.append(value)
    return deduped


PROXIES = _weather_predict_proxy_candidates()


def fetch_json(url: str, params: dict | None = None, max_rounds: int = 3):
    last = None
    for rnd in range(max_rounds):
        for proxy in PROXIES:
            try:
                r = httpx.get(url, params=params, proxy=proxy, timeout=25)
                r.raise_for_status()
                return r.json()
            except Exception as e:  # noqa: BLE001
                last = f"{proxy}: {type(e).__name__}"
                time.sleep(0.5 + rnd)
    raise RuntimeError(f"fetch failed {url}: {last}")


def round_half_up(x: float) -> int:
    return math.floor(float(x) + 0.5)


def append_jsonl(path: Path, row: dict) -> None:
    with path.open("a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def load_state() -> dict:
    p = OUT_DIR / "state.json"
    if p.exists():
        return json.loads(p.read_text())
    return {"entered": []}


def save_state(state: dict) -> None:
    (OUT_DIR / "state.json").write_text(json.dumps(state, indent=1))


def event_slug(city_slug: str, d) -> str:
    return f"highest-temperature-in-{city_slug}-on-{MONTHS[d.month - 1]}-{d.day}-{d.year}"


def fetch_metar_day(icao: str, tz: ZoneInfo, local_date) -> dict:
    """Return running max / current temp for the local day from METAR history."""
    data = fetch_json(METAR_API, {"ids": icao, "format": "json", "hours": "30"})
    obs = []
    for rec in data:
        t = rec.get("temp")
        ts = rec.get("reportTime")
        if t is None or not ts:
            continue
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt.astimezone(tz).date() != local_date:
            continue
        obs.append((dt, float(t)))
    obs.sort()
    if len(obs) < MIN_OBS:
        return {"status": "insufficient_obs", "n_obs": len(obs)}
    last_dt, last_temp = obs[-1]
    age_min = (datetime.now(timezone.utc) - last_dt).total_seconds() / 60
    if age_min > MAX_OBS_AGE_MIN:
        return {"status": "stale_metar", "n_obs": len(obs), "age_min": round(age_min, 1)}
    running_max_c = max(t for _, t in obs)
    return {
        "status": "ok",
        "n_obs": len(obs),
        "age_min": round(age_min, 1),
        "running_max_c": running_max_c,
        "current_temp_c": last_temp,
        "decline_c": running_max_c - last_temp,
        "last_obs_utc": last_dt.isoformat(),
    }


def parse_label(label: str, question: str) -> dict | None:
    """Parse bracket label into low/high; flags bottom/top tails."""
    lab = str(label).replace("°C", "").replace("°F", "").replace("°", "").strip()
    q = str(question).lower()
    nums = re.findall(r"-?\d+(?:\.\d+)?", lab)
    if not nums:
        return None
    is_bottom = "or below" in q or "or lower" in q
    is_top = lab.endswith("+") or "or higher" in q or "or above" in q
    if is_bottom:
        return {"low": None, "high": float(nums[0]), "bottom": True, "top": False}
    if is_top:
        return {"low": float(nums[0]), "high": None, "bottom": False, "top": True}
    if "-" in lab and len(nums) >= 2:
        return {"low": float(nums[0]), "high": float(nums[1]), "bottom": False, "top": False}
    return {"low": float(nums[0]), "high": float(nums[0]), "bottom": False, "top": False}


def best_ask_from_book(book: dict) -> tuple[float, float] | None:
    asks = book.get("asks") or []
    if not asks:
        return None
    best = min(asks, key=lambda a: float(a["price"]))
    return float(best["price"]), float(best["size"])


def best_bid_from_book(book: dict) -> tuple[float, float] | None:
    bids = book.get("bids") or []
    if not bids:
        return None
    best = max(bids, key=lambda b: float(b["price"]))
    return float(best["price"]), float(best["size"])


def book_summary(book: dict) -> dict:
    bid = best_bid_from_book(book)
    ask = best_ask_from_book(book)
    return {
        "bid_levels": len(book.get("bids") or []),
        "ask_levels": len(book.get("asks") or []),
        "best_bid": None if bid is None else bid[0],
        "best_bid_size": None if bid is None else bid[1],
        "best_ask": None if ask is None else ask[0],
        "best_ask_size": None if ask is None else ask[1],
    }


def cycle(dry_run: bool = False) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    state = load_state()
    entered = set(tuple(x) for x in state["entered"])
    now_utc = datetime.now(timezone.utc)

    for city, (slug, icao, unit, tz_name) in CITIES.items():
        tz = ZoneInfo(tz_name)
        now_local = now_utc.astimezone(tz)
        local_date = now_local.date()
        hour = now_local.hour
        cyc = {
            "ts_utc": now_utc.isoformat(),
            "city": city,
            "local_time": now_local.isoformat(),
            "hour_local": hour,
        }
        in_no = hour in NO_HOURS
        in_yes = hour in YES_HOURS
        if not (in_no or in_yes):
            cyc["status"] = "outside_window"
            append_jsonl(OUT_DIR / "cycles.jsonl", cyc)
            continue

        try:
            met = fetch_metar_day(icao, tz, local_date)
        except RuntimeError as e:
            cyc["status"] = "metar_fetch_failed"
            cyc["error"] = str(e)
            append_jsonl(OUT_DIR / "cycles.jsonl", cyc)
            continue
        cyc.update(met)
        if met["status"] != "ok":
            cyc["status"] = met["status"]
            append_jsonl(OUT_DIR / "cycles.jsonl", cyc)
            continue

        if unit == "F":
            running_value = round_half_up(met["running_max_c"] * 9 / 5 + 32)
        else:
            running_value = round_half_up(met["running_max_c"])
        cyc["running_value"] = running_value

        es = event_slug(slug, local_date)
        try:
            evs = fetch_json(f"{GAMMA}/events", {"slug": es})
        except RuntimeError as e:
            cyc["status"] = "gamma_fetch_failed"
            cyc["error"] = str(e)
            append_jsonl(OUT_DIR / "cycles.jsonl", cyc)
            continue
        if not evs or not (evs[0].get("markets") or []):
            cyc["status"] = "no_event"
            append_jsonl(OUT_DIR / "cycles.jsonl", cyc)
            continue
        markets = evs[0]["markets"]

        # rules-station verification (hard gate)
        desc = str(markets[0].get("description") or "")
        m = WU_URL_RE.search(desc)
        rules_icao = m.group(1) if m else None
        if rules_icao != icao:
            cyc["status"] = "RULES_STATION_MISMATCH"
            cyc["rules_icao"] = rules_icao
            cyc["expected_icao"] = icao
            append_jsonl(OUT_DIR / "cycles.jsonl", cyc)
            append_jsonl(OUT_DIR / "alerts.jsonl", cyc)
            continue

        candidates = []  # (rule, market, parsed)
        for mkt in markets:
            label = mkt.get("groupItemTitle") or ""
            parsed = parse_label(label, mkt.get("question") or "")
            if parsed is None or parsed["bottom"]:
                continue
            low = parsed["low"]
            high = parsed["high"]
            step = 2 if unit == "F" else 1
            if in_yes and high is not None and low is not None and low <= running_value <= high:
                candidates.append(("yes_bucket", mkt, parsed))
            if in_no and met["decline_c"] >= EXHAUSTION_MIN_DECLINE_C and low is not None:
                if low > running_value:
                    dist = math.ceil((low - running_value) / step)
                    if dist == 1:
                        candidates.append(("no_d1_exh", mkt, parsed))
                    elif dist == 2:
                        candidates.append(("no_d2_exh", mkt, parsed))

        n_entries = 0

        def audit_candidate(rule: str, label: str, side: str, status: str, **extra) -> None:
            if dry_run:
                return
            append_jsonl(
                OUT_DIR / "candidate_audit.jsonl",
                {
                    "ts_utc": now_utc.isoformat(),
                    "city": city,
                    "target_date": str(local_date),
                    "hour_local": hour,
                    "rule": rule,
                    "side": side,
                    "bracket": label,
                    "event_slug": es,
                    "official_icao": icao,
                    "unit": unit,
                    "running_max_c": met["running_max_c"],
                    "current_temp_c": met["current_temp_c"],
                    "decline_c": round(met["decline_c"], 2),
                    "running_value": running_value,
                    "status": status,
                    **extra,
                },
            )

        for rule, mkt, parsed in candidates:
            label = str(mkt.get("groupItemTitle") or "").strip()
            side = "BUY_YES" if rule == "yes_bucket" else "BUY_NO"
            key = (city, str(local_date), rule) if rule == "yes_bucket" and YES_ONE_PER_CITY_DAY else (
                city,
                str(local_date),
                rule,
                label,
            )
            if key in entered:
                audit_candidate(rule, label, side, "duplicate")
                continue
            try:
                token_ids = json.loads(mkt["clobTokenIds"])
            except (KeyError, json.JSONDecodeError):
                audit_candidate(rule, label, side, "missing_token_ids")
                continue
            if len(token_ids) < 2:
                audit_candidate(rule, label, side, "missing_token_ids")
                continue
            yes_token_id, no_token_id = token_ids[0], token_ids[1]
            token_id = yes_token_id if side == "BUY_YES" else no_token_id
            opposite_token_id = no_token_id if side == "BUY_YES" else yes_token_id
            try:
                book = fetch_json(f"{CLOB}/book", {"token_id": token_id})
            except RuntimeError as e:
                audit_candidate(rule, label, side, "book_fetch_failed", token_id=token_id, error=str(e))
                append_jsonl(
                    OUT_DIR / "cycles.jsonl",
                    {**cyc, "status": "book_fetch_failed", "rule": rule, "bracket": label, "error": str(e)},
                )
                continue
            direct_summary = book_summary(book)
            opposite_summary = {}
            try:
                opposite_summary = book_summary(fetch_json(f"{CLOB}/book", {"token_id": opposite_token_id}))
            except RuntimeError as e:
                opposite_summary = {"fetch_error": str(e)}
            synthetic_long_cost = None
            opposite_bid = opposite_summary.get("best_bid")
            if opposite_bid is not None:
                synthetic_long_cost = round(1.0 - float(opposite_bid), 6)
            liquidity_audit = {
                "token_id": token_id,
                "opposite_token_id": opposite_token_id,
                "direct_book": direct_summary,
                "opposite_book": opposite_summary,
                "synthetic_long_cost_if_mint_and_sell_opposite": synthetic_long_cost,
            }
            ba = best_ask_from_book(book)
            if ba is None:
                audit_candidate(rule, label, side, "no_asks", **liquidity_audit)
                continue
            ask, size = ba
            lo, hi = (YES_ASK_MIN, YES_ASK_MAX) if side == "BUY_YES" else (NO_ASK_MIN, NO_ASK_MAX)
            if not (lo <= ask <= hi):
                audit_candidate(
                    rule,
                    label,
                    side,
                    "ask_out_of_band",
                    ask=ask,
                    ask_size=size,
                    min_ask=lo,
                    max_ask=hi,
                    **liquidity_audit,
                )
                continue
            shares = min(size, MAX_SHARES)
            entry = {
                "ts_utc": now_utc.isoformat(),
                "city": city,
                "target_date": str(local_date),
                "hour_local": hour,
                "rule": rule,
                "side": side,
                "bracket": label,
                "ask": ask,
                "ask_size": size,
                "shares": shares,
                "notional": round(ask * shares, 4),
                "token_id": token_id,
                "market_id": mkt.get("id"),
                "event_slug": es,
                "official_icao": icao,
                "unit": unit,
                "running_max_c": met["running_max_c"],
                "current_temp_c": met["current_temp_c"],
                "decline_c": round(met["decline_c"], 2),
                "running_value": running_value,
                "settlement_status": "pending",
            }
            if dry_run:
                print("DRY ENTRY:", json.dumps(entry, ensure_ascii=False))
            else:
                append_jsonl(OUT_DIR / "entries.jsonl", entry)
                audit_candidate(
                    rule,
                    label,
                    side,
                    "entry_logged",
                    ask=ask,
                    ask_size=size,
                    shares=shares,
                    **liquidity_audit,
                )
                entered.add(key)
                n_entries += 1

        cyc["status"] = "ok"
        cyc["candidates"] = len(candidates)
        cyc["new_entries"] = n_entries
        append_jsonl(OUT_DIR / "cycles.jsonl", cyc)
        print(
            f"{city}: hour={hour} run_max_c={met['running_max_c']} decline={met['decline_c']:.1f} "
            f"rv={running_value} candidates={len(candidates)} new_entries={n_entries}"
        )

    if not dry_run:
        state["entered"] = sorted(entered)
        save_state(state)


def settle() -> None:
    entries = read_jsonl(OUT_DIR / "entries.jsonl")
    existing = read_jsonl(OUT_DIR / "settlements.jsonl")
    done = {(r["city"], r["target_date"], r["rule"], r["bracket"]) for r in existing}
    now_utc = datetime.now(timezone.utc)

    by_event: dict[str, list[dict]] = {}
    for e in entries:
        k = (e["city"], e["target_date"], e["rule"], e["bracket"])
        if k in done:
            continue
        tz = ZoneInfo(CITIES[e["city"]][3])
        if now_utc.astimezone(tz).date().isoformat() <= e["target_date"]:
            continue  # not past yet
        by_event.setdefault(e["event_slug"], []).append(e)

    for es, rows in by_event.items():
        try:
            evs = fetch_json(f"{GAMMA}/events", {"slug": es})
        except RuntimeError as err:
            print(f"!! settle fetch failed {es}: {err}")
            continue
        if not evs:
            continue
        markets = evs[0].get("markets") or []
        winners = []
        for mkt in markets:
            try:
                prices = [float(x) for x in json.loads(mkt.get("outcomePrices") or "[]")]
            except (ValueError, json.JSONDecodeError):
                continue
            if prices and prices[0] >= WIN_THRESHOLD:
                winners.append(str(mkt.get("groupItemTitle") or "").strip())
        if len(winners) != 1:
            print(f"{es}: winners={winners} -> skip (not single-winner yet)")
            continue
        winner = winners[0]
        for e in rows:
            is_win = (e["bracket"] == winner) if e["side"] == "BUY_YES" else (e["bracket"] != winner)
            pnl_share = (1.0 - e["ask"]) if is_win else -e["ask"]
            row = {
                **{k: e[k] for k in ("city", "target_date", "rule", "side", "bracket", "ask", "shares", "notional")},
                "winner": winner,
                "win": is_win,
                "pnl_per_share": round(pnl_share, 4),
                "pnl": round(pnl_share * e["shares"], 4),
                "settled_at_utc": now_utc.isoformat(),
            }
            append_jsonl(OUT_DIR / "settlements.jsonl", row)
            print(f"settled {e['city']} {e['target_date']} {e['rule']} {e['bracket']} -> win={is_win} pnl={row['pnl']}")


def report() -> None:
    entries = read_jsonl(OUT_DIR / "entries.jsonl")
    setts = read_jsonl(OUT_DIR / "settlements.jsonl")
    print(f"entries: {len(entries)}  settled: {len(setts)}  pending: {len(entries) - len(setts)}")
    if not setts:
        return
    import pandas as pd

    df = pd.DataFrame(setts)
    for keys in (["rule"], ["city"], ["rule", "city"]):
        g = df.groupby(keys).agg(
            n=("pnl", "size"),
            win_rate=("win", "mean"),
            cost=("notional", "sum"),
            pnl=("pnl", "sum"),
        )
        g["roi"] = g["pnl"] / g["cost"]
        print(g.round(3).to_string())
        print()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["cycle", "settle", "report"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if args.command == "cycle":
        cycle(dry_run=args.dry_run)
    elif args.command == "settle":
        settle()
    else:
        report()
    if OUT_DIR.name == "station_basis_shadow" and args.command in {"cycle", "settle"} and not args.dry_run:
        v1 = ROOT / "scripts/ops/weather_station_basis_shadow_v1.py"
        if v1.exists() and os.environ.get("STATION_BASIS_SKIP_V1_SIDELOAD") != "1":
            env = dict(os.environ)
            env["STATION_BASIS_SKIP_V1_SIDELOAD"] = "1"
            try:
                subprocess.run([sys.executable, str(v1), args.command], cwd=ROOT, env=env, check=False, timeout=180)
            except Exception as exc:  # noqa: BLE001
                append_jsonl(
                    OUT_DIR / "alerts.jsonl",
                    {
                        "ts_utc": datetime.now(timezone.utc).isoformat(),
                        "status": "v1_sideload_failed",
                        "command": args.command,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
    return 0


if __name__ == "__main__":
    sys.exit(main())
