#!/usr/bin/env python3
"""Readiness probe for station-basis v1.

This is a liveability check, not a trading script. It reuses the v1 shadow
configuration and verifies the current data chain for each v1 city:

- city-local signal window
- official-station METAR freshness
- Gamma event + rules station match
- CLOB book availability for any currently triggered candidate

Output is written to station_basis_shadow_v1/readiness.json for handoff and
reporting. No orders are placed.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

OPS = Path(__file__).resolve().parent
if str(OPS) not in sys.path:
    sys.path.insert(0, str(OPS))

import weather_station_basis_shadow_v1 as v1  # noqa: E402


base = v1.base
OUT = base.DATA_ROOT / "runtime/weather_edge_v1/station_basis_shadow_v1/readiness.json"


def next_window(now_local: datetime, hours: range) -> str:
    for day_offset in range(2):
        day = now_local.date() + timedelta(days=day_offset)
        for hour in hours:
            candidate = datetime.combine(day, datetime.min.time(), tzinfo=now_local.tzinfo).replace(hour=hour)
            if candidate >= now_local:
                return candidate.isoformat()
    return ""


def candidate_rows(markets: list[dict], met: dict, unit: str, running_value: int, in_yes: bool, in_no: bool) -> list[tuple[str, dict, dict]]:
    rows = []
    for mkt in markets:
        label = mkt.get("groupItemTitle") or ""
        parsed = base.parse_label(label, mkt.get("question") or "")
        if parsed is None or parsed["bottom"]:
            continue
        low = parsed["low"]
        high = parsed["high"]
        step = 2 if unit == "F" else 1
        if in_yes and high is not None and low is not None and low <= running_value <= high:
            rows.append(("yes_bucket", mkt, parsed))
        if in_no and met.get("decline_c", 0.0) >= base.EXHAUSTION_MIN_DECLINE_C and low is not None and low > running_value:
            dist = math.ceil((low - running_value) / step)
            if dist == 1:
                rows.append(("no_d1_exh", mkt, parsed))
            elif dist == 2:
                rows.append(("no_d2_exh", mkt, parsed))
    return rows


def probe_city(city: str, now_utc: datetime) -> dict:
    slug, icao, unit, tz_name = base.CITIES[city]
    tz = ZoneInfo(tz_name)
    now_local = now_utc.astimezone(tz)
    local_date = now_local.date()
    hour = now_local.hour
    in_yes = hour in base.YES_HOURS
    in_no = hour in base.NO_HOURS
    row = {
        "city": city,
        "ts_utc": now_utc.isoformat(),
        "local_time": now_local.isoformat(),
        "hour_local": hour,
        "in_yes_window": in_yes,
        "in_no_window": in_no,
        "next_yes_window": next_window(now_local, base.YES_HOURS),
        "next_no_window": next_window(now_local, base.NO_HOURS),
        "official_icao": icao,
        "status": "outside_window",
        "candidate_books": [],
    }
    if not (in_yes or in_no):
        return row

    try:
        met = base.fetch_metar_day(icao, tz, local_date)
    except RuntimeError as exc:
        row["status"] = "metar_fetch_failed"
        row["error"] = str(exc)
        return row
    row["metar"] = met
    if met.get("status") != "ok":
        row["status"] = met.get("status", "metar_not_ok")
        return row

    if unit == "F":
        running_value = base.round_half_up(met["running_max_c"] * 9 / 5 + 32)
    else:
        running_value = base.round_half_up(met["running_max_c"])
    row["running_value"] = running_value

    es = base.event_slug(slug, local_date)
    row["event_slug"] = es
    try:
        evs = base.fetch_json(f"{base.GAMMA}/events", {"slug": es})
    except RuntimeError as exc:
        row["status"] = "gamma_fetch_failed"
        row["error"] = str(exc)
        return row
    if not evs or not (evs[0].get("markets") or []):
        row["status"] = "no_event"
        return row

    markets = evs[0]["markets"]
    desc = str(markets[0].get("description") or "")
    match = base.WU_URL_RE.search(desc)
    rules_icao = match.group(1) if match else None
    row["rules_icao"] = rules_icao
    if rules_icao != icao:
        row["status"] = "RULES_STATION_MISMATCH"
        return row

    candidates = candidate_rows(markets, met, unit, running_value, in_yes, in_no)
    row["candidate_count"] = len(candidates)
    row["status"] = "ok_no_candidates" if not candidates else "ok_candidates"
    for rule, mkt, _parsed in candidates:
        side = "BUY_YES" if rule == "yes_bucket" else "BUY_NO"
        label = str(mkt.get("groupItemTitle") or "").strip()
        try:
            token_ids = json.loads(mkt["clobTokenIds"])
            token_id = token_ids[0] if side == "BUY_YES" else token_ids[1]
            book = base.fetch_json(f"{base.CLOB}/book", {"token_id": token_id})
            best = base.best_ask_from_book(book)
        except Exception as exc:  # noqa: BLE001
            row["candidate_books"].append(
                {"rule": rule, "side": side, "bracket": label, "book_status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            )
            continue
        if best is None:
            row["candidate_books"].append({"rule": rule, "side": side, "bracket": label, "book_status": "no_asks"})
            continue
        ask, size = best
        lo, hi = (base.YES_ASK_MIN, base.YES_ASK_MAX) if side == "BUY_YES" else (base.NO_ASK_MIN, base.NO_ASK_MAX)
        row["candidate_books"].append(
            {
                "rule": rule,
                "side": side,
                "bracket": label,
                "book_status": "ok",
                "ask": ask,
                "ask_size": size,
                "ask_in_band": lo <= ask <= hi,
                "token_id": token_id,
            }
        )
    return row


def main() -> int:
    now_utc = datetime.now(timezone.utc)
    rows = [probe_city(city, now_utc) for city in sorted(base.CITIES)]
    status_counts: dict[str, int] = {}
    for row in rows:
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
    out = {
        "generated_at_utc": now_utc.isoformat(),
        "strategy_id": "station_basis_taker_live5_h16_yes_no_d1_v1",
        "status_counts": status_counts,
        "rows": rows,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(OUT), "status_counts": status_counts}, ensure_ascii=False))
    for row in rows:
        print(
            f"{row['city']:12} hour={row['hour_local']:02d} "
            f"yes={int(row['in_yes_window'])} no={int(row['in_no_window'])} "
            f"status={row['status']} candidates={row.get('candidate_count', 0)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
