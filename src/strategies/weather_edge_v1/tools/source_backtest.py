from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.strategies.weather_edge_v1.tools.airport_weather_tool import load_watch_baseline


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WATCH_PATH = ROOT / "plan" / "watch"


def _safe_float(value: Any, default: Optional[float] = 0.0) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return default


def _build_session(proxy_url: str = "") -> requests.Session:
    session = requests.Session()
    session.mount(
        "https://",
        HTTPAdapter(
            max_retries=Retry(
                total=3,
                connect=3,
                read=3,
                backoff_factor=0.4,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=frozenset(["GET"]),
            )
        ),
    )
    proxy = proxy_url.strip()
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
        session.trust_env = False
    session.headers.update({"User-Agent": "pm-agent-weather-backtest/1.0"})
    return session


def _to_celsius(value_f: Optional[float]) -> Optional[float]:
    if value_f is None:
        return None
    return (value_f - 32.0) * 5.0 / 9.0


def _to_market_unit(value_c: Optional[float], market_unit: str) -> Optional[float]:
    if value_c is None:
        return None
    if str(market_unit or "").upper() == "F":
        return (value_c * 9.0 / 5.0) + 32.0
    return value_c


def _load_archive_entries(path: Path) -> List[Dict[str, Any]]:
    archive_dir = path / "weather_watch_archive" if path.is_dir() else path.parent / "weather_watch_archive"
    rows: List[Dict[str, Any]] = []
    if not archive_dir.exists():
        return rows
    for child in sorted(archive_dir.rglob("*.jsonl")):
        for raw_line in child.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def load_watch_samples(
    path: Path,
    *,
    city_keys: Optional[Iterable[str]] = None,
    date_from: str = "",
    date_to: str = "",
) -> List[Dict[str, Any]]:
    wanted_cities = {str(item).strip().lower() for item in (city_keys or []) if str(item).strip()}
    entries = _load_archive_entries(path)
    if not entries:
        payload = load_watch_baseline(path)
        entries = [dict(item) for item in payload.get("entries", []) if isinstance(item, dict)]
    filtered: List[Dict[str, Any]] = []
    for item in entries:
        city_key = str(item.get("city_key") or "").strip().lower()
        local_date = str(item.get("local_date") or "").strip()
        if wanted_cities and city_key not in wanted_cities:
            continue
        if date_from and local_date and local_date < date_from:
            continue
        if date_to and local_date and local_date > date_to:
            continue
        filtered.append(item)
    filtered.sort(
        key=lambda item: (
            str(item.get("local_date") or ""),
            str(item.get("city_key") or ""),
            str((((item.get("latest_snapshot") or {}).get("generated_at_utc")) or "")),
        )
    )
    return filtered


def _extract_app_root_state(html: str) -> Dict[str, Any]:
    match = re.search(r'<script id="app-root-state" type="application/json">(.*?)</script>', html, flags=re.DOTALL)
    if not match:
        raise RuntimeError("missing app-root-state payload")
    payload = json.loads(match.group(1))
    if not isinstance(payload, dict):
        raise RuntimeError("app-root-state payload is not an object")
    return payload


def _find_wu_payload(app_state: Dict[str, Any], fragment: str) -> tuple[str, Dict[str, Any]]:
    for value in app_state.values():
        if not isinstance(value, dict):
            continue
        url = value.get("u") or value.get("url")
        blob = value.get("b") or value.get("value")
        if isinstance(url, str) and fragment in url and isinstance(blob, dict):
            return url, blob
    raise RuntimeError(f"missing Weather Underground payload: {fragment}")


def _fetch_wu_app_state(session: requests.Session, station_code: str, local_date: str) -> Dict[str, Any]:
    for page_url in (
        f"https://www.wunderground.com/hourly/{station_code}/date/{local_date}",
        f"https://www.wunderground.com/history/daily/{station_code}/date/{local_date}",
    ):
        resp = session.get(page_url, timeout=20)
        resp.raise_for_status()
        try:
            return _extract_app_root_state(resp.text)
        except Exception:
            continue
    raise RuntimeError(f"failed to load embedded Weather Underground state for {station_code} {local_date}")


def fetch_settlement_truth(
    session: requests.Session,
    *,
    station_code: str,
    local_date: str,
    market_unit: str,
) -> Dict[str, Any]:
    app_state = _fetch_wu_app_state(session, station_code, local_date)
    source_url, payload = _find_wu_payload(app_state, "historical/dailysummary/30day")
    valid_times = payload.get("validTimeLocal") or []
    idx = next((i for i, value in enumerate(valid_times) if str(value).startswith(local_date)), -1)
    if idx < 0:
        raise RuntimeError(f"missing settlement row for {station_code} {local_date}")
    max_temp_f = _safe_float((payload.get("temperatureMax") or [None])[idx], None)
    min_temp_f = _safe_float((payload.get("temperatureMin") or [None])[idx], None)
    max_temp_c = _to_celsius(max_temp_f)
    min_temp_c = _to_celsius(min_temp_f)
    return {
        "source": "wunderground_history_daily",
        "source_url": source_url,
        "valid_time_local": str(valid_times[idx] or ""),
        "max_temp_c": None if max_temp_c is None else round(max_temp_c, 3),
        "min_temp_c": None if min_temp_c is None else round(min_temp_c, 3),
        "max_temp_f": max_temp_f,
        "min_temp_f": min_temp_f,
        "max_temp_market_unit": _to_market_unit(max_temp_c, market_unit),
        "min_temp_market_unit": _to_market_unit(min_temp_c, market_unit),
        "market_unit": str(market_unit or "C").upper(),
    }


def fetch_metar_path(
    session: requests.Session,
    *,
    station_code: str,
    local_date: str,
    timezone_name: str,
    market_unit: str,
    hours: int = 48,
) -> Dict[str, Any]:
    zone = ZoneInfo(str(timezone_name or "UTC"))
    local_day_start = datetime.fromisoformat(f"{local_date}T00:00:00").replace(tzinfo=zone)
    local_day_end = local_day_start.replace(hour=23, minute=59)
    query_anchor_utc = local_day_end.astimezone(timezone.utc)
    query_hours = max(30, int(hours))
    resp = session.get(
        "https://aviationweather.gov/api/data/metar",
        params={
            "ids": station_code,
            "format": "json",
            "date": query_anchor_utc.strftime("%Y%m%d_%H%M"),
            "hours": query_hours,
        },
        timeout=20,
    )
    resp.raise_for_status()
    rows = resp.json()
    points: List[Dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        report_time_utc = str(row.get("reportTime") or row.get("receiptTime") or "").strip()
        if not report_time_utc:
            continue
        try:
            dt_utc = datetime.fromisoformat(report_time_utc.replace("Z", "+00:00"))
        except Exception:
            continue
        dt_local = dt_utc.astimezone(zone)
        if dt_local.date().isoformat() != local_date:
            continue
        temp_c = _safe_float(row.get("temp"), None)
        points.append(
            {
                "report_time_utc": dt_utc.isoformat(),
                "report_time_local": dt_local.isoformat(),
                "temp_c": temp_c,
                "temp_market_unit": _to_market_unit(temp_c, market_unit),
                "dewp_c": _safe_float(row.get("dewp"), None),
                "wind_dir_deg": _safe_float(row.get("wdir"), None),
                "wind_speed_kt": _safe_float(row.get("wspd"), None),
                "cover": str(row.get("cover") or ""),
                "raw_metar": str(row.get("rawOb") or ""),
            }
        )
    points.sort(key=lambda item: str(item.get("report_time_utc") or ""))
    temps_c = [float(item["temp_c"]) for item in points if item.get("temp_c") is not None]
    peak_temp_c = max(temps_c) if temps_c else None
    low_temp_c = min(temps_c) if temps_c else None
    peak_rows = [item for item in points if peak_temp_c is not None and item.get("temp_c") == peak_temp_c]
    peak_time_local = ""
    if peak_rows:
        peak_dt = str(peak_rows[0].get("report_time_local") or "")
        peak_time_local = peak_dt.split("T", 1)[1][:5] if "T" in peak_dt else ""
    return {
        "source": "aviationweather_metar",
        "station_code": station_code,
        "local_date": local_date,
        "timezone": timezone_name,
        "observation_count": len(points),
        "peak_temp_c": peak_temp_c,
        "low_temp_c": low_temp_c,
        "peak_temp_market_unit": _to_market_unit(peak_temp_c, market_unit),
        "low_temp_market_unit": _to_market_unit(low_temp_c, market_unit),
        "peak_time_local": peak_time_local,
        "path": points,
    }


def _market_bucket_bounds(market_meta: Dict[str, Any]) -> tuple[Optional[float], Optional[float]]:
    bucket_min = market_meta.get("bucket_min")
    bucket_max = market_meta.get("bucket_max")
    bucket_value = market_meta.get("bucket_value")
    if bucket_min is None and bucket_value is not None:
        bucket_min = bucket_value
    if bucket_max is None and bucket_value is not None:
        bucket_max = bucket_value
    lo = _safe_float(bucket_min, None)
    hi = _safe_float(bucket_max, None)
    if lo is not None and hi is not None and lo > hi:
        lo, hi = hi, lo
    return lo, hi


def _bucket_contains(value: Optional[float], lower: Optional[float], upper: Optional[float]) -> Optional[bool]:
    if value is None or lower is None or upper is None:
        return None
    return lower <= value <= upper


def _hhmm_to_minutes(value: str) -> Optional[int]:
    text = str(value or "").strip()
    if len(text) < 5 or ":" not in text:
        return None
    hour, minute = text.split(":")[:2]
    try:
        return int(hour) * 60 + int(minute)
    except Exception:
        return None


def _extract_source_predictions(entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    snapshot = dict(entry.get("latest_snapshot") or {})
    latest = dict(snapshot.get("latest_forecast") or {})
    baseline = dict(entry.get("baseline_forecast") or {})
    multi_model = dict(snapshot.get("multi_model_forecast") or {})

    rows: List[Dict[str, Any]] = []

    def add_row(source_name: str, predicted_max_c: Optional[float], peak_hour_local: str = "", status: str = "ok") -> None:
        if predicted_max_c is None:
            return
        rows.append(
            {
                "source_name": source_name,
                "predicted_max_c": predicted_max_c,
                "predicted_peak_hour_local": str(peak_hour_local or ""),
                "status": str(status or "ok"),
            }
        )

    add_row(
        "open_meteo_latest",
        _safe_float(latest.get("max_temp_c"), None),
        peak_hour_local=str(latest.get("peak_hour_local") or ""),
        status=str(latest.get("forecast_status") or "ok"),
    )
    add_row(
        "open_meteo_baseline",
        _safe_float(baseline.get("max_temp_c"), None),
        peak_hour_local=str(baseline.get("peak_hour_local") or ""),
        status="baseline",
    )
    add_row(
        "taf_tx",
        _safe_float(latest.get("taf_max_temp_c"), None),
        status="ok" if latest.get("taf_max_temp_c") is not None else "missing",
    )
    add_row(
        "open_meteo_multi_model_consensus",
        _safe_float(multi_model.get("consensus_max_temp_c"), None),
        peak_hour_local=str(multi_model.get("consensus_peak_hour_local") or ""),
        status=str(multi_model.get("status") or "ok"),
    )
    for model_name, row in sorted((multi_model.get("models") or {}).items()):
        if not isinstance(row, dict):
            continue
        add_row(
            f"model:{model_name}",
            _safe_float(row.get("max_temp_c"), None),
            peak_hour_local=str(row.get("peak_hour_local") or ""),
            status=str(row.get("status") or "unknown"),
        )
    return rows


def evaluate_watch_entry(
    session: requests.Session,
    entry: Dict[str, Any],
    *,
    metar_hours: int = 48,
) -> Dict[str, Any]:
    snapshot = dict(entry.get("latest_snapshot") or {})
    station = dict(snapshot.get("station") or entry.get("station") or {})
    market_meta = dict(entry.get("market_meta") or snapshot.get("market_meta") or {})
    city_key = str(entry.get("city_key") or "").strip().lower()
    local_date = str(entry.get("local_date") or "").strip()
    market_unit = str(market_meta.get("unit") or station.get("market_unit") or "C").upper()
    station_code = str(station.get("station_code") or station.get("icao_id") or "").strip()
    timezone_name = str(station.get("timezone") or "UTC")
    if not station_code:
        raise RuntimeError(f"missing station_code for {city_key} {local_date}")

    settlement_truth = fetch_settlement_truth(
        session,
        station_code=station_code,
        local_date=local_date,
        market_unit=market_unit,
    )
    metar_path = fetch_metar_path(
        session,
        station_code=station_code,
        local_date=local_date,
        timezone_name=timezone_name,
        market_unit=market_unit,
        hours=metar_hours,
    )
    bucket_low, bucket_high = _market_bucket_bounds(market_meta)
    realized_yes = _bucket_contains(settlement_truth.get("max_temp_market_unit"), bucket_low, bucket_high)
    actual_peak_minutes = _hhmm_to_minutes(str(metar_path.get("peak_time_local") or ""))

    source_results: List[Dict[str, Any]] = []
    for prediction in _extract_source_predictions(entry):
        predicted_max_c = _safe_float(prediction.get("predicted_max_c"), None)
        predicted_max_market = _to_market_unit(predicted_max_c, market_unit)
        predicted_yes = _bucket_contains(predicted_max_market, bucket_low, bucket_high)
        peak_minutes = _hhmm_to_minutes(str(prediction.get("predicted_peak_hour_local") or ""))
        peak_hour_error_minutes = (
            None if peak_minutes is None or actual_peak_minutes is None else abs(peak_minutes - actual_peak_minutes)
        )
        source_results.append(
            {
                **prediction,
                "predicted_max_market_unit": predicted_max_market,
                "prediction_bias_c": None
                if predicted_max_c is None or settlement_truth.get("max_temp_c") is None
                else predicted_max_c - float(settlement_truth["max_temp_c"]),
                "abs_error_c": None
                if predicted_max_c is None or settlement_truth.get("max_temp_c") is None
                else abs(predicted_max_c - float(settlement_truth["max_temp_c"])),
                "abs_error_market_unit": None
                if predicted_max_market is None or settlement_truth.get("max_temp_market_unit") is None
                else abs(float(predicted_max_market) - float(settlement_truth["max_temp_market_unit"])),
                "predicted_yes": predicted_yes,
                "realized_yes": realized_yes,
                "outcome_correct": None
                if predicted_yes is None or realized_yes is None
                else bool(predicted_yes == realized_yes),
                "peak_hour_error_minutes": peak_hour_error_minutes,
            }
        )

    return {
        "sample_id": "|".join(
            [
                city_key,
                local_date,
                str((((snapshot.get("generated_at_utc")) or ""))),
                station_code,
            ]
        ),
        "city_key": city_key,
        "local_date": local_date,
        "market_title": str(entry.get("market_title") or ""),
        "outcome": str(entry.get("outcome") or ""),
        "station_code": station_code,
        "timezone": timezone_name,
        "generated_at_utc": str(snapshot.get("generated_at_utc") or ""),
        "market_meta": market_meta,
        "settlement_truth": settlement_truth,
        "metar_path": metar_path,
        "source_results": source_results,
    }


def _average(values: List[float]) -> Optional[float]:
    return None if not values else sum(values) / len(values)


def summarize_backtest(samples: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_source: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {
            "samples": 0,
            "abs_errors_c": [],
            "bias_c": [],
            "outcomes": [],
            "peak_hour_errors": [],
        }
    )
    for sample in samples:
        for row in sample.get("source_results", []):
            source_name = str(row.get("source_name") or "")
            bucket = by_source[source_name]
            bucket["samples"] += 1
            if row.get("abs_error_c") is not None:
                bucket["abs_errors_c"].append(float(row["abs_error_c"]))
            if row.get("prediction_bias_c") is not None:
                bucket["bias_c"].append(float(row["prediction_bias_c"]))
            if row.get("outcome_correct") is not None:
                bucket["outcomes"].append(1.0 if row["outcome_correct"] else 0.0)
            if row.get("peak_hour_error_minutes") is not None:
                bucket["peak_hour_errors"].append(float(row["peak_hour_error_minutes"]))

    leaderboard: List[Dict[str, Any]] = []
    total_samples = max(1, len(samples))
    for source_name, bucket in by_source.items():
        abs_errors = list(bucket["abs_errors_c"])
        bias_values = list(bucket["bias_c"])
        outcome_values = list(bucket["outcomes"])
        peak_errors = list(bucket["peak_hour_errors"])
        rmse_c = None
        if abs_errors:
            rmse_c = math.sqrt(sum(item * item for item in abs_errors) / len(abs_errors))
        leaderboard.append(
            {
                "source_name": source_name,
                "samples": int(bucket["samples"]),
                "coverage": round(int(bucket["samples"]) / total_samples, 4),
                "mae_c": None if not abs_errors else round(_average(abs_errors) or 0.0, 4),
                "rmse_c": None if rmse_c is None else round(rmse_c, 4),
                "mean_bias_c": None if not bias_values else round(_average(bias_values) or 0.0, 4),
                "outcome_accuracy": None if not outcome_values else round(_average(outcome_values) or 0.0, 4),
                "peak_hour_mae_minutes": None if not peak_errors else round(_average(peak_errors) or 0.0, 2),
            }
        )
    leaderboard.sort(
        key=lambda item: (
            item["mae_c"] is None,
            item["mae_c"] if item["mae_c"] is not None else 1e9,
            -(item["samples"]),
        )
    )
    return leaderboard


def run_source_backtest(
    *,
    watch_path: Path = DEFAULT_WATCH_PATH,
    city_keys: Optional[Iterable[str]] = None,
    date_from: str = "",
    date_to: str = "",
    metar_hours: int = 48,
    proxy_url: str = "",
) -> Dict[str, Any]:
    samples = load_watch_samples(watch_path, city_keys=city_keys, date_from=date_from, date_to=date_to)
    session = _build_session(proxy_url)
    evaluated: List[Dict[str, Any]] = []
    failed_samples: List[Dict[str, Any]] = []
    try:
        for item in samples:
            try:
                evaluated.append(evaluate_watch_entry(session, item, metar_hours=metar_hours))
            except Exception as exc:
                snapshot = dict(item.get("latest_snapshot") or {})
                station = dict(snapshot.get("station") or item.get("station") or {})
                failed_samples.append(
                    {
                        "city_key": str(item.get("city_key") or "").strip().lower(),
                        "local_date": str(item.get("local_date") or "").strip(),
                        "generated_at_utc": str(snapshot.get("generated_at_utc") or ""),
                        "station_code": str(station.get("station_code") or station.get("icao_id") or "").strip(),
                        "error": str(exc),
                    }
                )
    finally:
        session.close()
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "watch_path": str(watch_path),
        "requested_sample_count": len(samples),
        "sample_count": len(evaluated),
        "failed_sample_count": len(failed_samples),
        "source_leaderboard": summarize_backtest(evaluated),
        "samples": evaluated,
        "failed_samples": failed_samples,
        "notes": [
            "当前 watch 基线历史较短，样本量取决于已有 weather_watch JSON 和追加归档。",
            "结算真值使用 Weather Underground 页面嵌入的 historical daily summary。",
            "日内升温路径使用 AviationWeather 历史 METAR 序列，反映的是机场观测路径，不等于结算页展示样式。",
        ],
    }


def render_backtest_report(result: Dict[str, Any]) -> str:
    lines: List[str] = [
        "# Weather Source Backtest",
        "",
        f"- generated_at_utc: `{result.get('generated_at_utc', '')}`",
        f"- watch_path: `{result.get('watch_path', '')}`",
        f"- requested_sample_count: `{result.get('requested_sample_count', 0)}`",
        f"- sample_count: `{result.get('sample_count', 0)}`",
        f"- failed_sample_count: `{result.get('failed_sample_count', 0)}`",
        "",
        "## Source Leaderboard",
        "",
        "| source | samples | coverage | mae_c | bias_c | outcome_acc | peak_hour_mae_min |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in result.get("source_leaderboard", []):
        lines.append(
            "| {source_name} | {samples} | {coverage:.2%} | {mae_c} | {mean_bias_c} | {outcome_accuracy} | {peak_hour_mae_minutes} |".format(
                source_name=row.get("source_name", ""),
                samples=int(row.get("samples") or 0),
                coverage=float(row.get("coverage") or 0.0),
                mae_c="-" if row.get("mae_c") is None else f"{float(row['mae_c']):.3f}",
                mean_bias_c="-" if row.get("mean_bias_c") is None else f"{float(row['mean_bias_c']):+.3f}",
                outcome_accuracy="-" if row.get("outcome_accuracy") is None else f"{float(row['outcome_accuracy']):.2%}",
                peak_hour_mae_minutes="-" if row.get("peak_hour_mae_minutes") is None else f"{float(row['peak_hour_mae_minutes']):.1f}",
            )
        )
    lines.extend(["", "## Samples", ""])
    for sample in result.get("samples", []):
        truth = dict(sample.get("settlement_truth") or {})
        metar_path = dict(sample.get("metar_path") or {})
        lines.append(
            "- {city} {date}: settlement `{truth}` {unit}, METAR peak `{peak}` {unit} at `{peak_time}`, obs `{count}`".format(
                city=str(sample.get("city_key") or "").upper(),
                date=str(sample.get("local_date") or ""),
                truth="-" if truth.get("max_temp_market_unit") is None else f"{float(truth['max_temp_market_unit']):.2f}",
                peak="-" if metar_path.get("peak_temp_market_unit") is None else f"{float(metar_path['peak_temp_market_unit']):.2f}",
                unit=str(truth.get("market_unit") or "C"),
                peak_time=str(metar_path.get("peak_time_local") or "-"),
                count=int(metar_path.get("observation_count") or 0),
            )
        )
        ranked = sorted(
            [row for row in sample.get("source_results", []) if row.get("abs_error_c") is not None],
            key=lambda row: float(row.get("abs_error_c") or 1e9),
        )
        for row in ranked[:3]:
            lines.append(
                "best_source `{source}` pred `{pred:.2f}`C err `{err:.2f}`C outcome `{outcome}`".format(
                    source=str(row.get("source_name") or ""),
                    pred=float(row.get("predicted_max_c") or 0.0),
                    err=float(row.get("abs_error_c") or 0.0),
                    outcome="-" if row.get("outcome_correct") is None else ("ok" if row.get("outcome_correct") else "miss"),
                )
            )
    if result.get("failed_samples"):
        lines.extend(["", "## Failed Samples", ""])
        for row in result.get("failed_samples", []):
            lines.append(
                "- {city} {date} {station}: `{error}`".format(
                    city=str(row.get("city_key") or "").upper(),
                    date=str(row.get("local_date") or ""),
                    station=str(row.get("station_code") or "-"),
                    error=str(row.get("error") or ""),
                )
            )
    lines.extend(["", "## Notes", ""])
    for note in result.get("notes", []):
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


def write_backtest_outputs(result: Dict[str, Any], out_dir: Path) -> Dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "summary.json"
    report_path = out_dir / "report.md"
    summary_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(render_backtest_report(result), encoding="utf-8")
    return {
        "summary_path": str(summary_path),
        "report_path": str(report_path),
    }
