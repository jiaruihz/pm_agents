#!/usr/bin/env python3
"""Research METAR Celsius-to-Fahrenheit boundary traps.

This offline tool looks for Fahrenheit weather markets where the live crossing
runner treated an integer-C METAR value as a crossed whole-F bracket, while the
METAR's RMK T-group tenth-C value would not have crossed the same settlement
bracket. It then checks whether the orderbook later repriced back toward YES.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_BASE = ROOT / "runtime/weather_edge_v1"
DEFAULT_OPPS = DEFAULT_BASE / "metar_cross_prev_no_shadow/opportunities.jsonl"
DEFAULT_ORDERS = DEFAULT_BASE / "metar_cross_prev_no_shadow/orders.jsonl"
DEFAULT_BOOKS = DEFAULT_BASE / "source_orderbook_timing/books.jsonl"
DEFAULT_SOURCES = DEFAULT_BASE / "source_orderbook_timing/sources.jsonl"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-26-metar-cf-boundary-trap-v0.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-26-metar-cf-boundary-trap-v0.md"

MAIN_TEMP_RE = re.compile(r"\s(M?\d{2})/(M?\d{2}|//)\s")
T_GROUP_RE = re.compile(r"\bT([01])(\d{3})([01])(\d{3})\b")


def parse_ts(value: Any) -> dt.datetime | None:
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def iso(value: dt.datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def round_half_up(value: float) -> int:
    return int(math.floor(value + 0.5))


def c_to_f(value: float) -> float:
    return value * 9.0 / 5.0 + 32.0


def parse_metar_main_temp_c(raw: str | None) -> float | None:
    if not raw:
        return None
    match = MAIN_TEMP_RE.search(f" {raw} ")
    if not match:
        return None
    token = match.group(1)
    sign = -1 if token.startswith("M") else 1
    digits = token[1:] if token.startswith("M") else token
    return sign * float(int(digits))


def parse_metar_rmk_temp_c(raw: str | None) -> float | None:
    if not raw:
        return None
    match = T_GROUP_RE.search(raw)
    if not match:
        return None
    sign = -1 if match.group(1) == "1" else 1
    return sign * (int(match.group(2)) / 10.0)


def json_rows(path: Path):
    if not path.exists():
        return
    with path.open() as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{line_no}: invalid JSON: {exc}") from exc


def price_mid(row: dict[str, Any] | None) -> float | None:
    if not row:
        return None
    bid = to_float(row.get("best_bid"))
    ask = to_float(row.get("best_ask"))
    if bid is not None and ask is not None:
        return (bid + ask) / 2.0
    return bid if bid is not None else ask


def fmt(value: float | None, digits: int = 3) -> str:
    if value is None:
        return "NA"
    return f"{value:.{digits}f}"


def pct(value: float | None) -> str:
    if value is None:
        return "NA"
    return f"{100.0 * value:.1f}%"


def load_source_metars(path: Path, since: dt.datetime | None) -> dict[tuple[str, str], dict[str, Any]]:
    """Keep the first raw METAR seen for each city/report timestamp."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    source_rank = {
        "noaa_tgftp_station_txt": 0,
        "aviationweather_metar": 1,
        "checkwx_html": 2,
        "aviationweather_cache_csv": 3,
    }
    for row in json_rows(path):
        city = row.get("city")
        report_ts = row.get("source_report_ts_utc")
        raw = row.get("raw_metar")
        if not city or not report_ts or not raw:
            continue
        ts = parse_ts(row.get("local_detect_ts_utc") or row.get("ts_utc"))
        if since and ts and ts < since:
            continue
        main_c = parse_metar_main_temp_c(raw)
        rmk_c = parse_metar_rmk_temp_c(raw)
        if main_c is None and rmk_c is None:
            continue
        key = (str(city), str(report_ts))
        candidate = {
            "city": city,
            "source_report_ts_utc": report_ts,
            "source": row.get("source"),
            "local_detect_ts_utc": iso(ts),
            "raw_metar": raw,
            "main_temp_c": main_c,
            "rmk_temp_c": rmk_c,
            "main_round_f": round_half_up(c_to_f(main_c)) if main_c is not None else None,
            "rmk_round_f": round_half_up(c_to_f(rmk_c)) if rmk_c is not None else None,
            "main_f": c_to_f(main_c) if main_c is not None else None,
            "rmk_f": c_to_f(rmk_c) if rmk_c is not None else None,
        }
        current = out.get(key)
        if current is None:
            out[key] = candidate
            continue
        cur_rank = source_rank.get(str(current.get("source")), 99)
        new_rank = source_rank.get(str(candidate.get("source")), 99)
        cur_ts = parse_ts(current.get("local_detect_ts_utc"))
        if (new_rank, ts or dt.datetime.max.replace(tzinfo=dt.timezone.utc)) < (
            cur_rank,
            cur_ts or dt.datetime.max.replace(tzinfo=dt.timezone.utc),
        ):
            out[key] = candidate
    return out


def load_events(paths: list[Path], since: dt.datetime | None) -> list[dict[str, Any]]:
    events: dict[tuple[str, str, float, str], dict[str, Any]] = {}
    for path in paths:
        for row in json_rows(path):
            if row.get("status") != "cross_detected":
                continue
            if row.get("unit") != "F":
                continue
            city = row.get("city")
            report_ts = row.get("obs_last_obs_utc")
            token = row.get("no_token_id") or row.get("token_id")
            target = to_float(row.get("target_no_bracket"))
            event_ts = parse_ts(row.get("ts_utc"))
            if not city or not report_ts or not token or target is None or not event_ts:
                continue
            if since and event_ts < since:
                continue
            key = (str(city), str(report_ts), target, str(token))
            current = events.get(key)
            # Prefer orders over opportunities because they include live/fill fields.
            if current is None or path.name == "orders.jsonl":
                row = dict(row)
                row["_source_file"] = str(path)
                events[key] = row
    return sorted(events.values(), key=lambda row: row.get("ts_utc", ""))


def load_books(path: Path, since: dt.datetime | None, tokens: set[str]) -> dict[str, list[dict[str, Any]]]:
    books: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in json_rows(path):
        token = row.get("token_id")
        if str(token) not in tokens:
            continue
        side = row.get("side")
        ts = parse_ts(row.get("ts_utc"))
        if not token or side not in {"YES", "NO"} or not ts:
            continue
        if since and ts < since:
            continue
        compact = {
            "ts": ts,
            "ts_utc": iso(ts),
            "side": side,
            "best_bid": to_float(row.get("best_bid")),
            "best_ask": to_float(row.get("best_ask")),
            "best_bid_size": to_float(row.get("best_bid_size")),
            "best_ask_size": to_float(row.get("best_ask_size")),
            "status": row.get("status"),
        }
        books[str(token)].append(compact)
    for rows in books.values():
        rows.sort(key=lambda row: row["ts"])
    return books


def pick(rows: list[dict[str, Any]], ts: dt.datetime, mode: str) -> dict[str, Any] | None:
    if mode == "before":
        candidates = [row for row in rows if row["ts"] <= ts]
        return candidates[-1] if candidates else None
    candidates = [row for row in rows if row["ts"] >= ts]
    return candidates[0] if candidates else None


def book_state(books: dict[str, list[dict[str, Any]]], token: str | None, ts: dt.datetime, mode: str) -> dict[str, Any]:
    if not token:
        return {}
    row = pick(books.get(str(token), []), ts, mode)
    if not row:
        return {}
    return {
        "ts_utc": row.get("ts_utc"),
        "bid": row.get("best_bid"),
        "ask": row.get("best_ask"),
        "bid_size": row.get("best_bid_size"),
        "ask_size": row.get("best_ask_size"),
        "mid": price_mid(row),
    }


def first_correction(
    books: dict[str, list[dict[str, Any]]],
    yes_token: str | None,
    no_token: str | None,
    after: dt.datetime,
    before: dt.datetime,
    *,
    yes_bid_min: float,
    no_ask_max: float,
) -> dict[str, Any]:
    yes_rows = [
        row
        for row in books.get(str(yes_token), [])
        if row["ts"] >= after and row["ts"] <= before and row.get("side") == "YES"
    ]
    no_rows = [
        row
        for row in books.get(str(no_token), [])
        if row["ts"] >= after and row["ts"] <= before and row.get("side") == "NO"
    ]
    by_ts: dict[dt.datetime, dict[str, Any]] = defaultdict(dict)
    for row in yes_rows:
        by_ts[row["ts"]]["yes"] = row
    for row in no_rows:
        by_ts[row["ts"]]["no"] = row
    for ts in sorted(by_ts):
        yes = by_ts[ts].get("yes")
        no = by_ts[ts].get("no")
        yes_bid = to_float(yes.get("best_bid")) if yes else None
        no_ask = to_float(no.get("best_ask")) if no else None
        if (yes_bid is not None and yes_bid >= yes_bid_min) or (no_ask is not None and no_ask <= no_ask_max):
            return {
                "correction_ts_utc": iso(ts),
                "correction_after_detect_sec": None,
                "correction_yes_bid": yes_bid,
                "correction_yes_ask": to_float(yes.get("best_ask")) if yes else None,
                "correction_no_bid": to_float(no.get("best_bid")) if no else None,
                "correction_no_ask": no_ask,
            }
    return {}


def analyze(args: argparse.Namespace) -> dict[str, Any]:
    since = parse_ts(args.since) if args.since else None
    sources = load_source_metars(args.sources, since)
    events = load_events([args.opportunities, args.orders], since)
    tokens = {
        str(token)
        for event in events
        for token in (event.get("yes_token_id"), event.get("no_token_id"), event.get("token_id"))
        if token
    }
    books = load_books(args.books, since, tokens)
    rows: list[dict[str, Any]] = []

    for event in events:
        city = str(event.get("city"))
        report_ts_raw = str(event.get("obs_last_obs_utc"))
        event_ts = parse_ts(event.get("ts_utc"))
        report_ts = parse_ts(report_ts_raw)
        if not event_ts or not report_ts:
            continue
        target_high = to_float(event.get("target_no_bracket"))
        no_token = event.get("no_token_id") or event.get("token_id")
        yes_token = event.get("yes_token_id")
        metar = sources.get((city, report_ts_raw), {})
        main_round_f = metar.get("main_round_f")
        rmk_round_f = metar.get("rmk_round_f")
        target_int = int(target_high) if target_high is not None else None
        boundary_mismatch = (
            target_int is not None
            and isinstance(main_round_f, int)
            and isinstance(rmk_round_f, int)
            and main_round_f > rmk_round_f
            and target_int < main_round_f
            and target_int >= rmk_round_f
        )
        detect_yes = book_state(books, yes_token, event_ts, "after")
        detect_no = book_state(books, no_token, event_ts, "after")
        h15_yes = book_state(books, yes_token, event_ts + dt.timedelta(minutes=15), "after")
        h60_yes = book_state(books, yes_token, event_ts + dt.timedelta(minutes=60), "after")
        h120_yes = book_state(books, yes_token, event_ts + dt.timedelta(minutes=120), "after")
        h15_no = book_state(books, no_token, event_ts + dt.timedelta(minutes=15), "after")
        h60_no = book_state(books, no_token, event_ts + dt.timedelta(minutes=60), "after")
        correction = first_correction(
            books,
            str(yes_token) if yes_token else None,
            str(no_token) if no_token else None,
            event_ts,
            event_ts + dt.timedelta(minutes=args.correction_window_min),
            yes_bid_min=args.yes_bid_correction,
            no_ask_max=args.no_ask_correction,
        )
        if correction.get("correction_ts_utc"):
            corr_ts = parse_ts(correction["correction_ts_utc"])
            correction["correction_after_detect_sec"] = (
                round((corr_ts - event_ts).total_seconds(), 3) if corr_ts else None
            )
        detect_yes_mid = detect_yes.get("mid")
        h60_yes_mid = h60_yes.get("mid")
        yes_mid_delta_60m = (
            h60_yes_mid - detect_yes_mid
            if isinstance(h60_yes_mid, (int, float)) and isinstance(detect_yes_mid, (int, float))
            else None
        )
        market_reverted_to_yes = bool(
            correction.get("correction_ts_utc")
            or (
                isinstance(yes_mid_delta_60m, (int, float))
                and yes_mid_delta_60m >= args.min_yes_mid_rebound
            )
        )
        row = {
            "city": city,
            "target_date": event.get("target_date"),
            "event_slug": event.get("event_slug"),
            "market_label": event.get("market_label"),
            "target_no_bracket": target_int,
            "report_ts_utc": report_ts_raw,
            "detect_ts_utc": event.get("ts_utc"),
            "detected_after_report_sec": event.get("detected_after_report_sec"),
            "current_temp_c_runner": event.get("current_temp_c"),
            "runner_running_value_f": event.get("running_value") or event.get("recent_running_value"),
            "main_temp_c": metar.get("main_temp_c"),
            "main_f": metar.get("main_f"),
            "main_round_f": main_round_f,
            "rmk_temp_c": metar.get("rmk_temp_c"),
            "rmk_f": metar.get("rmk_f"),
            "rmk_round_f": rmk_round_f,
            "metar_source": metar.get("source"),
            "metar_local_detect_ts_utc": metar.get("local_detect_ts_utc"),
            "raw_metar": metar.get("raw_metar"),
            "boundary_mismatch": boundary_mismatch,
            "source_basis_risk": bool(main_round_f is not None and rmk_round_f is not None and main_round_f != rmk_round_f),
            "detect_yes_bid": detect_yes.get("bid"),
            "detect_yes_ask": detect_yes.get("ask"),
            "detect_yes_mid": detect_yes_mid,
            "detect_no_bid": detect_no.get("bid"),
            "detect_no_ask": detect_no.get("ask"),
            "detect_no_mid": detect_no.get("mid"),
            "yes_mid_15m": h15_yes.get("mid"),
            "yes_mid_60m": h60_yes_mid,
            "yes_mid_120m": h120_yes.get("mid"),
            "no_ask_15m": h15_no.get("ask"),
            "no_ask_60m": h60_no.get("ask"),
            "yes_mid_delta_60m": yes_mid_delta_60m,
            "market_reverted_to_yes": market_reverted_to_yes,
            "live_submit_status": event.get("live_submit_status"),
            "order_id": event.get("order_id"),
            "_event_source_file": event.get("_source_file"),
        }
        row.update(correction)
        rows.append(row)

    boundary_rows = [row for row in rows if row["boundary_mismatch"]]
    reverted_rows = [row for row in boundary_rows if row["market_reverted_to_yes"]]
    live_rows = [row for row in boundary_rows if row.get("live_submit_status")]
    by_city: dict[str, dict[str, Any]] = {}
    for city in sorted({row["city"] for row in boundary_rows}):
        city_rows = [row for row in boundary_rows if row["city"] == city]
        city_rev = [row for row in city_rows if row["market_reverted_to_yes"]]
        by_city[city] = {
            "boundary_mismatch_events": len(city_rows),
            "market_reverted_to_yes": len(city_rev),
            "live_submitted": sum(1 for row in city_rows if row.get("live_submit_status")),
            "median_detected_after_report_sec": median(
                [to_float(row.get("detected_after_report_sec")) for row in city_rows]
            ),
            "median_correction_after_detect_sec": median(
                [to_float(row.get("correction_after_detect_sec")) for row in city_rev]
            ),
        }
    return {
        "inputs": {
            "opportunities": str(args.opportunities),
            "orders": str(args.orders),
            "books": str(args.books),
            "sources": str(args.sources),
            "since": args.since,
        },
        "funnel": {
            "f_cross_events": len(rows),
            "events_with_raw_metar": sum(1 for row in rows if row.get("raw_metar")),
            "source_basis_risk": sum(1 for row in rows if row["source_basis_risk"]),
            "boundary_mismatch_events": len(boundary_rows),
            "boundary_mismatch_market_reverted_to_yes": len(reverted_rows),
            "boundary_mismatch_live_submitted": len(live_rows),
        },
        "by_city": by_city,
        "rows": rows,
    }


def median(values: list[float | None]) -> float | None:
    cleaned = sorted(value for value in values if value is not None and math.isfinite(value))
    if not cleaned:
        return None
    n = len(cleaned)
    mid = n // 2
    if n % 2:
        return cleaned[mid]
    return (cleaned[mid - 1] + cleaned[mid]) / 2.0


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# METAR C->F Boundary Trap V0",
        "",
        "Status: snapshot",
        "Updated: 2026-06-26",
        "",
        "## Conclusion",
        "",
    ]
    funnel = payload["funnel"]
    boundary = funnel["boundary_mismatch_events"]
    reverted = funnel["boundary_mismatch_market_reverted_to_yes"]
    if boundary:
        lines.append(
            f"在当前 raw timing logs 里，发现 `{boundary}` 个 METAR 整数 C 与 RMK 十分之一 C "
            f"跨不同 whole-F bracket 的 F 市场 crossing 事件；其中 `{reverted}` 个随后盘口向 YES 回摆。"
        )
    else:
        lines.append("当前 raw timing logs 没有抓到可确认的 METAR C->F boundary trap 样本。")
    lines.extend(
        [
            "",
            "这个研究只证明 source-basis 风险/窗口存在；没有通过显著性、基准、前瞻三道门，结论是 `research_only`。",
            "",
            "## Data Snapshot",
            "",
            f"- F crossing events: `{funnel['f_cross_events']}`",
            f"- events with raw METAR: `{funnel['events_with_raw_metar']}`",
            f"- source-basis risk events: `{funnel['source_basis_risk']}`",
            f"- boundary mismatch events: `{funnel['boundary_mismatch_events']}`",
            f"- boundary mismatch with YES reversion: `{funnel['boundary_mismatch_market_reverted_to_yes']}`",
            f"- boundary mismatch live submitted: `{funnel['boundary_mismatch_live_submitted']}`",
            "",
            "## City Summary",
            "",
            "| city | boundary events | reverted | live submitted | median source lag sec | median correction sec |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for city, row in sorted(payload["by_city"].items()):
        lines.append(
            "| {city} | {n} | {rev} | {live} | {lag} | {corr} |".format(
                city=city,
                n=row["boundary_mismatch_events"],
                rev=row["market_reverted_to_yes"],
                live=row["live_submitted"],
                lag=fmt(row["median_detected_after_report_sec"]),
                corr=fmt(row["median_correction_after_detect_sec"]),
            )
        )
    lines.extend(
        [
            "",
            "## Boundary Events",
            "",
            "| city | date | market | report | detect | main C/F | RMK C/F | detect YES/NO | 60m YES mid | correction | live |",
            "|---|---|---|---|---|---:|---:|---:|---:|---|---|",
        ]
    )
    for row in payload["rows"]:
        if not row["boundary_mismatch"]:
            continue
        main = f"{fmt(row.get('main_temp_c'), 1)}C/{row.get('main_round_f')}F"
        rmk = f"{fmt(row.get('rmk_temp_c'), 1)}C/{row.get('rmk_round_f')}F"
        detect = f"Y {fmt(row.get('detect_yes_mid'))} / N ask {fmt(row.get('detect_no_ask'))}"
        correction = row.get("correction_ts_utc") or ("rebound" if row.get("market_reverted_to_yes") else "NA")
        lines.append(
            "| {city} | {date} | {market} | {report} | {detect_ts} | {main} | {rmk} | {detect} | {y60} | {corr} | {live} |".format(
                city=row["city"],
                date=row.get("target_date"),
                market=row.get("market_label"),
                report=row.get("report_ts_utc"),
                detect_ts=row.get("detect_ts_utc"),
                main=main,
                rmk=rmk,
                detect=detect,
                y60=fmt(row.get("yes_mid_60m")),
                corr=correction,
                live=row.get("live_submit_status") or "",
            )
        )
    lines.extend(
        [
            "",
            "## When This Happens",
            "",
            "This setup appears when all of these are true:",
            "",
            "1. The market settles in whole Fahrenheit, usually WU/native-F style.",
            "2. The public trigger source is METAR with integer Celsius in the main temperature field.",
            "3. The main integer C value rounds to the next Fahrenheit bracket, but the RMK `Txxxx` tenth-C value does not.",
            "4. The target bracket high is below the main rounded-F value but not below the RMK rounded-F value.",
            "5. The market initially prices some chance of a crossing but later accepts the lower/native-F outcome.",
            "",
            "For the SFO example, `21C -> 69.8F -> 70F`, but `T0206 -> 20.6C -> 69.1F -> 69F`; `68-69F` was not dead under the RMK/native-F view.",
            "",
            "## Verdict",
            "",
            "significance=NA; baseline=NA; forward=NA; conclusion=research_only.",
            "",
            "Do not run the old crossing live logic on F/WU cities. The research direction is to monitor boundary ambiguity and study whether the market overreacts to the integer-C reading.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opportunities", type=Path, default=DEFAULT_OPPS)
    parser.add_argument("--orders", type=Path, default=DEFAULT_ORDERS)
    parser.add_argument("--books", type=Path, default=DEFAULT_BOOKS)
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    parser.add_argument("--since", default=None)
    parser.add_argument("--correction-window-min", type=float, default=180.0)
    parser.add_argument("--yes-bid-correction", type=float, default=0.80)
    parser.add_argument("--no-ask-correction", type=float, default=0.20)
    parser.add_argument("--min-yes-mid-rebound", type=float, default=0.25)
    parser.add_argument("--output-json", type=Path, default=OUT_JSON)
    parser.add_argument("--output-md", type=Path, default=OUT_MD)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()

    payload = analyze(args)
    markdown = render_markdown(payload)
    if args.no_write:
        print(markdown)
        return
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.write_text(markdown)
    print(json.dumps({"output_json": str(args.output_json), "output_md": str(args.output_md), "funnel": payload["funnel"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
