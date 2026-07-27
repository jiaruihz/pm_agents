#!/usr/bin/env python3
"""Calibrate KNMI station 240 ta/tx to later EHAM METAR and WU daily max."""

from __future__ import annotations

import argparse
import bisect
import concurrent.futures
import csv
import json
import math
import os
import re
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.forecast_quality import (  # noqa: E402
    research_high_frequency_strategy_eligibility_v2 as eligibility,
)
from weather_data_feed.high_frequency_observation_sources import (  # noqa: E402
    KNMI_API_BASE,
    KNMI_DATASET,
    KNMI_VERSION,
)
from weather_data_feed.knmi_open_data import parse_knmi_netcdf  # noqa: E402
from weather_data_feed.observation_sources.fetchers import (  # noqa: E402
    WEATHER_COM_API_KEY,
    WEATHER_COM_HISTORICAL_OBS,
)


RUNTIME = Path("/Volumes/jrs/weather_data_feed_service_runtime")
OUT = ROOT / "docs/analysis/2026-07/generated/knmi_eham_wu_alignment_v1"
REPORT = ROOT / "docs/analysis/2026-07/2026-07-28-knmi-eham-wu-alignment-v1.md"
DB = ROOT / "runtime/weather.db"
FILENAME_RE = re.compile(r"_(\d{12})\.nc$")
LOCAL_ZONE = ZoneInfo("Europe/Amsterdam")


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def half_up(value: float) -> int:
    return math.floor(float(value) + 0.5)


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def date_range(start_date: str, end_date: str) -> Iterable[date]:
    cursor = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    while cursor <= end:
        yield cursor
        cursor += timedelta(days=1)


def list_knmi_files(client: httpx.Client, token: str) -> list[dict[str, Any]]:
    url = f"{KNMI_API_BASE}/datasets/{KNMI_DATASET}/versions/{KNMI_VERSION}/files"
    response = client.get(
        url,
        params={"maxKeys": 1000, "sorting": "desc"},
        headers={"Authorization": token},
    )
    response.raise_for_status()
    return list(response.json().get("files") or [])


def file_local_date(filename: str) -> str:
    match = FILENAME_RE.search(filename)
    if not match:
        return ""
    timestamp = datetime.strptime(match.group(1), "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
    return timestamp.astimezone(LOCAL_ZONE).date().isoformat()


def backfill_knmi(start_date: str, end_date: str, timeout: float, workers: int) -> list[dict[str, Any]]:
    token = os.environ.get("KNMI_OPEN_DATA_API_KEY", "").strip()
    if not token:
        raise RuntimeError("KNMI_OPEN_DATA_API_KEY is not configured")
    files_url = f"{KNMI_API_BASE}/datasets/{KNMI_DATASET}/versions/{KNMI_VERSION}/files"
    headers = {"Authorization": token}
    rows: list[dict[str, Any]] = []
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        selected = [
            item
            for item in list_knmi_files(client, token)
            if start_date <= file_local_date(str(item.get("filename") or "")) <= end_date
        ]
        if len(selected) > 850:
            raise RuntimeError(f"refusing {len(selected)} KNMI files; 850-file quota headroom limit")
        print(f"knmi_files={len(selected)} authenticated_request_budget={len(selected) + 1}", flush=True)

    def download(file_meta: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            filename = str(file_meta["filename"])
            url_response = client.get(f"{files_url}/{filename}/url", headers=headers)
            url_response.raise_for_status()
            download_url = str(url_response.json().get("temporaryDownloadUrl") or "")
            content_response = client.get(download_url)
            content_response.raise_for_status()
            return file_meta, content_response.content

    downloaded: list[tuple[dict[str, Any], bytes]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(download, item) for item in selected]
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            downloaded.append(future.result())
            if index % 50 == 0 or index == len(selected):
                print(f"knmi_downloaded={index}/{len(selected)}", flush=True)

    for file_meta, content in sorted(downloaded, key=lambda item: str(item[0]["filename"])):
        parsed = parse_knmi_netcdf(
            content,
            fetched_at=datetime.now(timezone.utc),
            file_metadata=file_meta,
        )
        for row in parsed:
            row["collection_mode"] = "historical_backfill_not_pit"
            row["historical_retrieved_at_utc"] = row.pop("knmi_first_seen_at_utc", "")
            rows.append(row)
    return rows


def load_metar(start_date: str, end_date: str) -> list[dict[str, Any]]:
    earliest: dict[str, dict[str, Any]] = {}
    for day in date_range(
        (date.fromisoformat(start_date) - timedelta(days=1)).isoformat(),
        (date.fromisoformat(end_date) + timedelta(days=1)).isoformat(),
    ):
        path = RUNTIME / "output/source_events" / day.isoformat() / "sources.jsonl"
        for raw in iter_jsonl(path):
            if raw.get("source") != "aviationweather_metar" or raw.get("city") != "Amsterdam" or raw.get("status") != "ok":
                continue
            report = parse_dt(raw.get("source_report_ts_utc"))
            detect = parse_dt(raw.get("local_detect_ts_utc"))
            temp = raw.get("temp_c")
            if report is None or detect is None or temp is None:
                continue
            key = report.isoformat()
            row = {
                "report_ts": report,
                "detect_ts": detect,
                "temp_c": float(temp),
                "temp_round_c": half_up(float(temp)),
                "target_date": report.astimezone(LOCAL_ZONE).date().isoformat(),
                "raw_metar": str(raw.get("raw_metar") or ""),
            }
            if key not in earliest or detect < earliest[key]["detect_ts"]:
                earliest[key] = row
    return sorted(earliest.values(), key=lambda row: row["report_ts"])


def align_next_metar(knmi: list[dict[str, Any]], metar: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reports = [row["report_ts"] for row in metar]
    output: list[dict[str, Any]] = []
    for row in knmi:
        obs = parse_dt(row.get("observation_time_utc"))
        if obs is None:
            continue
        index = bisect.bisect_right(reports, obs)
        if index >= len(metar):
            continue
        following = metar[index]
        if following["target_date"] != row["target_date"]:
            continue
        lead = (following["report_ts"] - obs).total_seconds() / 60
        if not (0 < lead <= 45):
            continue
        ta = float(row["temp_c"])
        tx_raw = row.get("max_temp_c_past_10m")
        tx = float(tx_raw) if tx_raw not in (None, "") else ta
        output.append(
            {
                "target_date": row["target_date"],
                "knmi_obs_ts_utc": obs.isoformat(),
                "knmi_ta_c": ta,
                "knmi_tx_c": tx,
                "knmi_ta_round_c": half_up(ta),
                "knmi_tx_round_c": half_up(tx),
                "next_metar_report_ts_utc": following["report_ts"].isoformat(),
                "next_metar_detect_ts_utc": following["detect_ts"].isoformat(),
                "next_metar_temp_c": following["temp_c"],
                "lead_to_next_metar_min": lead,
                "ta_minus_next_metar_c": half_up(ta) - following["temp_round_c"],
                "tx_minus_next_metar_c": half_up(tx) - following["temp_round_c"],
                "ta_exact_next_metar": int(half_up(ta) == following["temp_round_c"]),
                "tx_exact_next_metar": int(half_up(tx) == following["temp_round_c"]),
                "ta_within1_next_metar": int(abs(half_up(ta) - following["temp_round_c"]) <= 1),
                "tx_within1_next_metar": int(abs(half_up(tx) - following["temp_round_c"]) <= 1),
                "raw_metar": following["raw_metar"],
            }
        )
    return output


def fetch_wu_day(target_date: str, timeout: float) -> dict[str, Any]:
    response = httpx.get(
        WEATHER_COM_HISTORICAL_OBS.format(location="EHAM:9:NL"),
        params={
            "apiKey": WEATHER_COM_API_KEY,
            "units": "m",
            "startDate": target_date.replace("-", ""),
            "endDate": target_date.replace("-", ""),
        },
        headers={
            "Origin": "https://www.wunderground.com",
            "Referer": "https://www.wunderground.com/history/daily/EHAM",
            "User-Agent": "Mozilla/5.0 pm-agents-knmi-calibration/1.0",
        },
        timeout=timeout,
        trust_env=False,
    )
    response.raise_for_status()
    observations = []
    for raw in response.json().get("observations") or []:
        timestamp = raw.get("valid_time_gmt")
        temp = raw.get("temp")
        if timestamp is None or temp is None:
            continue
        dt = datetime.fromtimestamp(int(timestamp), timezone.utc)
        if dt.astimezone(LOCAL_ZONE).date().isoformat() == target_date:
            observations.append((dt, float(temp)))
    maximum = max((temp for _, temp in observations), default=None)
    return {
        "target_date": target_date,
        "wu_rows": len(observations),
        "wu_native_daily_max_c": half_up(maximum) if maximum is not None else "",
        "wu_status": "ok" if observations else "empty",
    }


def load_winners(start_date: str, end_date: str) -> dict[str, str]:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    rows = conn.execute(
        """SELECT target_date,bracket FROM settlement_outcomes
           WHERE city='Amsterdam' AND settlement_status='settled'
             AND final_price>=.999 AND target_date BETWEEN ? AND ?""",
        (start_date, end_date),
    ).fetchall()
    conn.close()
    return {str(target_date): str(bracket) for target_date, bracket in rows}


def build_daily(
    knmi: list[dict[str, Any]],
    metar: list[dict[str, Any]],
    wu: list[dict[str, Any]],
    winners: dict[str, str],
) -> list[dict[str, Any]]:
    knmi_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    metar_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in knmi:
        knmi_by_day[str(row["target_date"])].append(row)
    for row in metar:
        metar_by_day[row["target_date"]].append(row)
    wu_by_day = {row["target_date"]: row for row in wu}
    output = []
    for target_date, rows in sorted(knmi_by_day.items()):
        wu_row = wu_by_day.get(target_date, {})
        ta_max = max(float(row["temp_c"]) for row in rows)
        tx_max = max(float(row.get("max_temp_c_past_10m") or row["temp_c"]) for row in rows)
        metar_max = max((row["temp_round_c"] for row in metar_by_day.get(target_date, [])), default=None)
        wu_max_raw = wu_row.get("wu_native_daily_max_c")
        wu_max = int(wu_max_raw) if wu_max_raw not in (None, "") else None
        winner = winners.get(target_date, "")
        output.append(
            {
                "target_date": target_date,
                "knmi_rows": len(rows),
                "knmi_ta_daily_max_raw_c": ta_max,
                "knmi_ta_daily_max_round_c": half_up(ta_max),
                "knmi_tx_daily_max_raw_c": tx_max,
                "knmi_tx_daily_max_round_c": half_up(tx_max),
                "eham_metar_daily_max_c": metar_max if metar_max is not None else "",
                "wu_native_daily_max_c": wu_max if wu_max is not None else "",
                "winning_bracket": winner,
                "ta_minus_wu_c": "" if wu_max is None else half_up(ta_max) - wu_max,
                "tx_minus_wu_c": "" if wu_max is None else half_up(tx_max) - wu_max,
                "metar_minus_wu_c": "" if wu_max is None or metar_max is None else metar_max - wu_max,
                "ta_in_winning_bracket": "" if not winner else int(eligibility.parse_bracket_contains(winner, half_up(ta_max))),
                "tx_in_winning_bracket": "" if not winner else int(eligibility.parse_bracket_contains(winner, half_up(tx_max))),
                "wu_in_winning_bracket": "" if not winner or wu_max is None else int(eligibility.parse_bracket_contains(winner, wu_max)),
                "tx_terminal_false_cross": "" if wu_max is None else int(half_up(tx_max) > wu_max),
                "wu_status": wu_row.get("wu_status", "missing"),
            }
        )
    return output


def ratio(rows: list[dict[str, Any]], key: str) -> str:
    return "NA" if not rows else f"{sum(int(row[key]) for row in rows)}/{len(rows)} ({100*sum(int(row[key]) for row in rows)/len(rows):.1f}%)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default="2026-07-22")
    parser.add_argument("--end-date", default="2026-07-26")
    parser.add_argument("--timeout-sec", type=float, default=30.0)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--reuse-knmi-csv", action="store_true")
    args = parser.parse_args()

    raw_path = OUT / "knmi_observations_backfill.csv"
    if args.reuse_knmi_csv:
        knmi = read_csv(raw_path)
    else:
        knmi = backfill_knmi(args.start_date, args.end_date, args.timeout_sec, args.workers)
        write_csv(raw_path, knmi)
    metar = load_metar(args.start_date, args.end_date)
    next_rows = align_next_metar(knmi, metar)
    wu = [fetch_wu_day(day.isoformat(), args.timeout_sec) for day in date_range(args.start_date, args.end_date)]
    winners = load_winners(args.start_date, args.end_date)
    daily = build_daily(knmi, metar, wu, winners)

    write_csv(OUT / "next_eham_metar_alignment.csv", next_rows)
    write_csv(OUT / "daily_knmi_eham_wu.csv", daily)
    write_csv(OUT / "wu_fetch_status.csv", wu)

    ta_bias = [int(row["ta_minus_wu_c"]) for row in daily if row["ta_minus_wu_c"] != ""]
    tx_bias = [int(row["tx_minus_wu_c"]) for row in daily if row["tx_minus_wu_c"] != ""]
    ta_next_bias = [int(row["ta_minus_next_metar_c"]) for row in next_rows]
    tx_next_bias = [int(row["tx_minus_next_metar_c"]) for row in next_rows]
    canonical_days = [row for row in daily if row["winning_bracket"]]
    daily_table = "\n".join(
        f"| `{row['target_date']}` | {row['knmi_ta_daily_max_raw_c']}→{row['knmi_ta_daily_max_round_c']} | "
        f"{row['knmi_tx_daily_max_raw_c']}→{row['knmi_tx_daily_max_round_c']} | "
        f"{row['eham_metar_daily_max_c']} | {row['wu_native_daily_max_c']} | "
        f"`{row['winning_bracket'] or 'coverage_gap'}` | {row['tx_terminal_false_cross']} |"
        for row in daily
    )
    report = f"""# KNMI EHAM → METAR → WU alignment v1

## 数据快照

- 数据源：KNMI Open Data 10-minute station 240 historical files；Mac JRS `source_events` EHAM METAR；Weather.com/WU EHAM history；`runtime/weather.db settlement_outcomes`
- 窗口：`{args.start_date}..{args.end_date}`；KNMI `{len(knmi)}` rows；next-METAR `{len(next_rows)}` rows；WU `{sum(row['wu_status']=='ok' for row in wu)}/{len(wu)}` days；canonical settlement `{len(canonical_days)}/{len(daily)}` days
- grain：observation→next routine METAR；city-day→WU/settlement。历史 KNMI retrieval 不是 PIT first-seen，不能用于延迟/盘口研究。
- unsettled：`{len(daily)-len(canonical_days)}/{len(daily)}`；missing_bracket：`0`（无 winner 的日期按 coverage gap，不伪装为策略筛除）

## 结论

- `ta` arithmetic-round → 下一份 EHAM METAR exact：`{ratio(next_rows, 'ta_exact_next_metar')}`；±1°C：`{ratio(next_rows, 'ta_within1_next_metar')}`。
- `tx` arithmetic-round → 下一份 EHAM METAR exact：`{ratio(next_rows, 'tx_exact_next_metar')}`；±1°C：`{ratio(next_rows, 'tx_within1_next_metar')}`。
- 下一份 METAR bias/MAE：`ta {statistics.mean(ta_next_bias):+.3f}/{statistics.mean(abs(value) for value in ta_next_bias):.3f}°C`；`tx {statistics.mean(tx_next_bias):+.3f}/{statistics.mean(abs(value) for value in tx_next_bias):.3f}°C`。因此逐报文映射优先 `ta`。
- 日最高对 WU：`ta-WU` median `{statistics.median(ta_bias) if ta_bias else 'NA'}°C`；`tx-WU` median `{statistics.median(tx_bias) if tx_bias else 'NA'}°C`。
- 日最高 exact WU：`ta {sum(value == 0 for value in ta_bias)}/{len(ta_bias)}`；`tx {sum(value == 0 for value in tx_bias)}/{len(tx_bias)}`。因此日最高候选优先 `tx`，但仍不能当 settlement latch。
- `tx > WU` terminal-false-cross days：`{sum(int(row['tx_terminal_false_cross']) for row in daily if row['tx_terminal_false_cross'] != '')}/{sum(row['tx_terminal_false_cross'] != '' for row in daily)}`。
- 结论等级：`inconclusive`。该窗口只校准 source basis；forward collector 从 2026-07-28 起才具备真实 first-seen clock，不授权 live。

## 每日对照

| Date | KNMI ta max raw→round | KNMI tx max raw→round | EHAM METAR max | WU max | canonical winner | tx false cross |
|---|---:|---:|---:|---:|---|---:|
{daily_table}

## 双漏斗

- signal funnel（observation grain）：KNMI files `{len(knmi)}` → next EHAM METAR `{len(next_rows)}`。
- evidence funnel（city-day grain）：KNMI `{len(daily)}` → WU `{sum(row['wu_status']=='ok' for row in wu)}` → canonical winner `{len(canonical_days)}` → PIT book `0` → fill `0`。

## 8 环

覆盖 source/reference/settlement basis；缺 PIT book、执行、容量、PnL、显著性 forward。`significance=NA baseline=NA forward=FAIL conclusion=inconclusive`。
"""
    REPORT.write_text(report, encoding="utf-8")
    print(json.dumps({"knmi_rows": len(knmi), "next_metar_rows": len(next_rows), "daily_rows": len(daily), "report": str(REPORT)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
