#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
if sys.prefix == sys.base_prefix and VENV_PYTHON.exists():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), __file__, *sys.argv[1:]])
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.platform.clients.polymarket_gamma import PolymarketGammaClient
from src.platform.notification.telegram import send_telegram_message_sync
from src.strategies.weather_edge_v1.tools.airport_weather_tool import AirportWeatherTool
from src.strategies.weather_edge_v1.tools.case_record import CaseRecordWriter
from src.strategies.weather_edge_v1.tools.market_query_tool import build_market_snapshot
from src.strategies.weather_edge_v1.tools.profile_resolver import load_profiles


DEFAULT_CITIES = [
    "shanghai",
    "seoul",
    "tokyo",
    "hong_kong",
    "taipei",
    "osaka",
    "singapore",
    "dubai",
    "doha",
]

DISPLAY_NAME_MAP = {
    "hong_kong": "Hong Kong",
    "new_york": "New York",
}

AIRPORT_CONTEXT_MAP = {
    "shanghai": "浦东机场远离市区热岛，海风和低云会让机场口径明显低于市区页。",
    "seoul": "仁川是沿海机场，海风和低云会让 1 度边界盘比表面更脆。",
    "tokyo": "羽田在东京湾边，不能把市区热岛直接套到机场高温路径上。",
    "hong_kong": "香港机场是离岸机场，海洋调节强，暖尾比城市体感更容易被压平。",
    "taipei": "桃园是沿海湿润机场，锋面、海风和低云都会改变峰值兑现。",
    "osaka": "关西机场在海湾上，市区比机场更暖很常见，错锚风险高。",
    "singapore": "樟宜是热带海洋机场，午后对流会让看似线性的暖尾突然失效。",
    "dubai": "迪拜是沙漠海岸机场，整体偏线性，但海风和沙尘仍会压高温一档。",
    "doha": "多哈是海湾机场，不该把它当成纯内陆沙漠盘来理解。",
}

RIGHT_TAIL_RISK_CITIES = {"shanghai", "seoul", "tokyo", "hong_kong", "taipei", "osaka", "singapore"}
HIGH_VARIABILITY_CITIES = {"singapore", "hong_kong", "taipei"}


@dataclass
class Candidate:
    question: str
    bucket_type: str
    lower: Optional[float]
    upper: Optional[float]
    yes_price: float
    no_price: float
    no_best_bid: Optional[float]
    no_best_ask: Optional[float]
    distance_to_bucket: float
    side_bias: str
    score: int
    win_probability_pct: int
    recommendation: str
    rationale: List[str]
    entry_cap: Optional[float]


def _display_name(city_key: str) -> str:
    if city_key in DISPLAY_NAME_MAP:
        return DISPLAY_NAME_MAP[city_key]
    return city_key.replace("_", " ").title()


def _slug_city(city_key: str) -> str:
    return city_key.replace("_", "-")


def _event_slug(city_key: str, local_date: str) -> str:
    dt = datetime.fromisoformat(local_date)
    return f"highest-temperature-in-{_slug_city(city_key)}-on-{dt.strftime('%B').lower()}-{dt.day}-{dt.year}"


def _safe_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _parse_question_bucket(question: str) -> Tuple[str, Optional[float], Optional[float], str]:
    text = str(question or "").strip()
    m = re.search(r"be\s+(-?\d+(?:\.\d+)?)°([CF])\s+on", text, flags=re.IGNORECASE)
    if m:
        value = float(m.group(1))
        unit = m.group(2).upper()
        return "exact", value, value, unit
    m = re.search(r"be\s+(-?\d+(?:\.\d+)?)°([CF])\s+or\s+higher", text, flags=re.IGNORECASE)
    if m:
        value = float(m.group(1))
        unit = m.group(2).upper()
        return "or_higher", value, None, unit
    m = re.search(r"be\s+(-?\d+(?:\.\d+)?)°([CF])\s+or\s+below", text, flags=re.IGNORECASE)
    if m:
        value = float(m.group(1))
        unit = m.group(2).upper()
        return "or_below", None, value, unit
    return "unknown", None, None, ""


def _distance_to_bucket(value: float, lower: Optional[float], upper: Optional[float]) -> float:
    lo = lower
    hi = upper
    if lo is not None and hi is None:
        if value >= lo:
            return 0.0
        return lo - value
    if hi is not None and lo is None:
        if value <= hi:
            return 0.0
        return value - hi
    if lo is not None and hi is not None:
        if lo <= value <= hi:
            return 0.0
        if value < lo:
            return lo - value
        return value - hi
    return 0.0


def _infer_side_bias(city_key: str, lower: Optional[float], upper: Optional[float], forecast_max: float) -> str:
    if lower is not None and forecast_max < lower:
        return "right_tail"
    if upper is not None and forecast_max > upper:
        return "left_tail"
    if city_key in RIGHT_TAIL_RISK_CITIES:
        return "right_tail"
    return "neutral"


def _orderbook_for_outcome(market: Dict[str, Any], outcome: str) -> Tuple[Optional[float], Optional[float]]:
    for token in market.get("tokens", []) or []:
        if str(token.get("outcome") or "").lower() != outcome.lower():
            continue
        book = dict(token.get("orderbook") or {})
        return _safe_float(book.get("best_bid")), _safe_float(book.get("best_ask"))
    return None, None


def _entry_cap(no_price: float, no_best_ask: Optional[float]) -> Optional[float]:
    if no_price <= 0:
        return None
    cap = min(0.96, no_price + 0.02)
    if no_best_ask is not None:
        cap = min(cap, no_best_ask - 0.01)
    cap = round(cap, 3)
    if cap <= 0:
        return None
    return cap


def _format_local_observation_time(raw_utc: str, timezone_name: str) -> str:
    text = str(raw_utc or "").strip()
    if not text:
        return "-"
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.astimezone(ZoneInfo(timezone_name)).strftime("%Y-%m-%d %H:%M %Z")
    except Exception:
        return text


def _build_weather_path_notes(weather_snapshot: Dict[str, Any]) -> List[str]:
    forecast = dict(weather_snapshot.get("latest_forecast") or {})
    observation = dict(weather_snapshot.get("latest_observation") or {})
    station = dict(weather_snapshot.get("station") or {})
    taf_summary = dict(weather_snapshot.get("taf_summary") or {})
    multi_model = dict(weather_snapshot.get("multi_model_forecast") or {})
    validation = dict(weather_snapshot.get("secondary_validation") or {})
    notes: List[str] = []
    forecast_max = _safe_float(forecast.get("max_temp_market_unit"))
    obs_temp = _safe_float(observation.get("temp_c"))
    precip = _safe_float(forecast.get("precipitation_probability_max"))
    wind_kt = _safe_float(observation.get("wind_speed_kt"))
    dewp = _safe_float(observation.get("dewp_c"))
    peak_hour = str(forecast.get("peak_hour_local") or "").strip() or "-"
    forecast_status = str(forecast.get("forecast_status") or "").strip()
    timezone_name = str(station.get("timezone") or "UTC")
    obs_local = _format_local_observation_time(str(observation.get("observed_at_utc") or ""), timezone_name)
    if forecast_max is not None and obs_temp is not None:
        delta = forecast_max - obs_temp
        if delta > 0.7:
            notes.append(f"当前观测 {obs_temp:.1f}C，离 forecast max 还差 {delta:.1f}C，升温路径仍然存在。")
        elif delta >= -0.7:
            notes.append(f"当前观测 {obs_temp:.1f}C，已经接近 forecast max {forecast_max:.1f}C，后续上冲空间有限。")
        else:
            notes.append(f"当前观测 {obs_temp:.1f}C，已经高于 forecast max {forecast_max:.1f}C，forecast 可能偏冷或滞后。")
    notes.append(f"峰值时段预估在 {peak_hour}，最新机场观测时间约 {obs_local}。")
    if forecast_status and forecast_status != "ok":
        notes.append(f"主 forecast 当前状态是 {forecast_status}，这次判断带有降级或 fallback。")
    if wind_kt is not None:
        notes.append(f"机场风速约 {wind_kt:.0f}kt。沿海机场里，风场会直接影响升温兑现。")
    if precip is not None:
        notes.append(f"目标日最大降水概率约 {precip:.0f}%。降水/云量越高，右尾高温越难兑现。")
    if dewp is not None:
        notes.append(f"当前露点约 {dewp:.1f}C，可用来判断湿层和云量是否可能压温。")
    available_models = multi_model.get("available_model_count")
    spread_c = _safe_float(multi_model.get("max_temp_spread_c"))
    if available_models is not None:
        notes.append(
            f"多模型层可用 {int(available_models)}/{int(multi_model.get('requested_model_count') or 0)} 个，"
            f"peak window 约 {multi_model.get('peak_window_start_local') or '-'}..{multi_model.get('peak_window_end_local') or '-'}。"
        )
    if spread_c is not None:
        notes.append(f"多模型最高温分歧约 {spread_c:.1f}C，分歧越大，exact-bin 越不该重仓。")
    taf_max = _safe_float(taf_summary.get("forecast_max_temp_c"))
    if taf_max is not None:
        notes.append(f"TAF 的 TX 指示大致到 {taf_max:.0f}C，可作为官方航空预报的第二意见。")
    else:
        notes.append("TAF 没有稳定给出 TX 标记，官方航空预报对最高温的直接帮助有限。")
    wu_hourly = dict(validation.get("wunderground_hourly") or {})
    if wu_hourly:
        if wu_hourly.get("status") == "ok" and wu_hourly.get("station_match"):
            notes.append("WU Hourly 同站点页面可用，说明交易锚至少对齐到了正确机场对象。")
        else:
            notes.append("WU Hourly 当前没有完成稳定同站点验证，结论置信度要下调。")
    checkwx_metar = dict(validation.get("checkwx_metar") or {})
    checkwx_taf = dict(validation.get("checkwx_taf") or {})
    if checkwx_metar or checkwx_taf:
        if checkwx_metar.get("status") == "ok" or checkwx_taf.get("status") == "ok":
            notes.append("CheckWX 备份机场页在线，可作为 AviationWeather 的冗余校验。")
        else:
            notes.append("CheckWX 备份机场页当前不可用，观测冗余不足。")
    local_rows = validation.get("local_official_rows") or []
    if isinstance(local_rows, list) and local_rows:
        ok_count = sum(1 for row in local_rows if isinstance(row, dict) and row.get("status") == "ok" and row.get("station_match"))
        notes.append(f"本地官方机场页验证 {ok_count}/{len(local_rows)} 条可用，用来防止城市页错锚。")
    return notes


def _build_candidate(
    *,
    city_key: str,
    market: Dict[str, Any],
    forecast_max: float,
    main_range_low: Optional[int],
    main_range_high: Optional[int],
) -> Optional[Candidate]:
    question = str(market.get("question") or "")
    bucket_type, lower, upper, unit = _parse_question_bucket(question)
    if bucket_type == "unknown" or unit != "C":
        return None

    yes_price = _safe_float((market.get("outcome_prices") or [None, None])[0]) or 0.0
    no_price = _safe_float((market.get("outcome_prices") or [None, None])[1]) or 0.0
    no_best_bid, no_best_ask = _orderbook_for_outcome(market, "No")
    distance = _distance_to_bucket(forecast_max, lower, upper)
    side_bias = _infer_side_bias(city_key, lower, upper, forecast_max)

    if distance <= 0:
        return None
    if city_key in RIGHT_TAIL_RISK_CITIES and side_bias != "right_tail":
        return None
    if side_bias == "right_tail" and lower is None:
        return None
    if side_bias == "left_tail" and upper is None:
        return None
    if no_price >= 0.995:
        return None
    if no_best_ask is None and no_price >= 0.985:
        return None
    if main_range_low is not None and main_range_high is not None:
        if lower is not None and lower <= main_range_high:
            if side_bias == "right_tail":
                return None
        if upper is not None and upper >= main_range_low:
            if side_bias == "left_tail":
                return None

    score = 55
    score += min(25, int(round(distance * 7)))
    if no_price >= 0.99:
        score -= 10
    elif no_price >= 0.97:
        score -= 4
    elif no_price >= 0.9:
        score += 1
    elif no_price <= 0.55:
        score -= 6

    if city_key in HIGH_VARIABILITY_CITIES:
        score -= 8
    if city_key == "singapore":
        score -= 4

    if no_best_ask is None:
        score -= 10
    elif no_best_ask >= 0.995:
        score -= 12
    elif no_best_ask >= 0.985:
        score -= 8
    elif no_best_ask >= 0.975:
        score -= 4

    if no_best_bid is not None and no_best_ask is not None:
        spread = max(0.0, no_best_ask - no_best_bid)
        if spread >= 0.08:
            score -= 8
        elif spread >= 0.04:
            score -= 5

    score = max(0, min(score, 100))

    rationale = [
        f"Open-Meteo 目标日最高温约 {forecast_max:.1f}C，离目标桶约 {distance:.1f}C。",
        f"主区间大致在 {main_range_low}..{main_range_high}C，候选桶在主区间外。",
        f"市场中间价约 No {no_price:.3f}。",
    ]
    if no_best_ask is None:
        rationale.append("No 盘口缺少可成交 ask，执行质量偏差。")
    else:
        rationale.append(f"No 最优盘口约 {no_best_bid if no_best_bid is not None else '-'} / {no_best_ask:.3f}。")
    if city_key in HIGH_VARIABILITY_CITIES:
        rationale.append("该机场属于更高不稳定性气候，不给高分。")

    entry_cap = _entry_cap(no_price, no_best_ask)

    recommendation = _recommendation_for_score(score, no_best_ask)

    return Candidate(
        question=question,
        bucket_type=bucket_type,
        lower=lower,
        upper=upper,
        yes_price=yes_price,
        no_price=no_price,
        no_best_bid=no_best_bid,
        no_best_ask=no_best_ask,
        distance_to_bucket=distance,
        side_bias=side_bias,
        score=score,
        win_probability_pct=0,
        recommendation=recommendation,
        rationale=rationale,
        entry_cap=entry_cap,
    )


def _recommendation_for_score(score: int, no_best_ask: Optional[float]) -> str:
    if score >= 74 and no_best_ask is not None and no_best_ask <= 0.985:
        return "轻仓被动试 No"
    if score >= 60:
        return "只挂被动单，不追价"
    return "观察，不下单"


def _estimate_win_probability_pct(
    *,
    city_key: str,
    bucket_type: str,
    side_bias: str,
    distance: float,
    forecast_max: float,
    lower: Optional[float],
    model_spread_c: Optional[float],
    peak_spread_minutes: Optional[float],
    taf_max_c: Optional[float],
    forecast_status: str,
    validation_flags: List[str],
) -> int:
    probability = 52.0
    probability += min(28.0, distance * 8.0)

    if side_bias == "right_tail":
        probability += 3.0
    elif side_bias == "left_tail":
        probability += 1.0

    if bucket_type == "exact":
        probability -= 4.0

    if city_key in HIGH_VARIABILITY_CITIES:
        probability -= 8.0
    if city_key == "singapore":
        probability -= 4.0

    if model_spread_c is not None:
        if model_spread_c >= 2.5:
            probability -= 16.0
        elif model_spread_c >= 1.5:
            probability -= 10.0
        elif model_spread_c >= 0.8:
            probability -= 5.0

    if peak_spread_minutes is not None:
        if peak_spread_minutes >= 180:
            probability -= 8.0
        elif peak_spread_minutes >= 120:
            probability -= 5.0
        elif peak_spread_minutes >= 60:
            probability -= 2.0

    if side_bias == "right_tail" and taf_max_c is not None and lower is not None:
        if taf_max_c >= lower - 0.2:
            probability -= 14.0
        elif taf_max_c >= forecast_max + 1.0:
            probability -= 8.0

    if forecast_status != "ok":
        probability -= 8.0

    if "wu_hourly_validation_gap" in validation_flags:
        probability -= 5.0
    if "checkwx_validation_gap" in validation_flags:
        probability -= 3.0
    if "local_official_validation_gap" in validation_flags:
        probability -= 2.0

    probability = max(5.0, min(probability, 95.0))
    return int(round(probability))


def _pick_candidates(city_key: str, market_snapshot: Dict[str, Any], weather_snapshot: Dict[str, Any]) -> List[Candidate]:
    forecast = dict(weather_snapshot.get("latest_forecast") or {})
    taf_summary = dict(weather_snapshot.get("taf_summary") or {})
    multi_model = dict(weather_snapshot.get("multi_model_forecast") or {})
    validation = dict(weather_snapshot.get("secondary_validation") or {})
    forecast_max = _safe_float(forecast.get("max_temp_market_unit"))
    if forecast_max is None:
        return []
    forecast_status = str(forecast.get("forecast_status") or "").strip() or "unknown"
    main_range_low = forecast.get("main_range_low")
    main_range_high = forecast.get("main_range_high")
    model_spread_c = _safe_float(multi_model.get("max_temp_spread_c"))
    peak_spread_minutes = _safe_float(multi_model.get("peak_hour_spread_minutes"))
    taf_max_c = _safe_float(taf_summary.get("forecast_max_temp_c"))
    validation_flags = [str(item).strip() for item in (validation.get("validation_flags") or []) if str(item).strip()]

    candidates: List[Candidate] = []
    for market in market_snapshot.get("markets", []) or []:
        if not isinstance(market, dict):
            continue
        candidate = _build_candidate(
            city_key=city_key,
            market=market,
            forecast_max=forecast_max,
            main_range_low=int(main_range_low) if main_range_low is not None else None,
            main_range_high=int(main_range_high) if main_range_high is not None else None,
        )
        if candidate is not None:
            if model_spread_c is not None and model_spread_c >= 1.5:
                candidate.score = max(0, candidate.score - 8)
                candidate.rationale.append(f"多模型最高温分歧达到 {model_spread_c:.1f}C，说明这不是干净路径。")
            elif model_spread_c is not None and model_spread_c >= 0.8:
                candidate.score = max(0, candidate.score - 3)
                candidate.rationale.append(f"多模型最高温分歧约 {model_spread_c:.1f}C，置信度要打折。")
            if peak_spread_minutes is not None and peak_spread_minutes >= 120:
                candidate.score = max(0, candidate.score - 4)
                candidate.rationale.append(
                    f"多模型 peak window 分歧约 {int(peak_spread_minutes)} 分钟，尾盘路径不够干净。"
                )
            if candidate.side_bias == "right_tail" and taf_max_c is not None and candidate.lower is not None:
                if taf_max_c >= candidate.lower - 0.2:
                    candidate.score = max(0, candidate.score - 8)
                    candidate.rationale.append(f"TAF 的 TX 已经贴近目标桶 {candidate.lower:.0f}C，右尾 No 要降级。")
                elif taf_max_c >= forecast_max + 1.0:
                    candidate.score = max(0, candidate.score - 4)
                    candidate.rationale.append(
                        f"TAF 的 TX {taf_max_c:.0f}C 明显高于主 forecast {forecast_max:.1f}C，右尾风险偏高。"
                    )
            if "wu_hourly_validation_gap" in validation_flags:
                candidate.score = max(0, candidate.score - 4)
                candidate.rationale.append("WU Hourly 没有完成稳定同站点验证，交易锚可信度不足。")
            if "checkwx_validation_gap" in validation_flags:
                candidate.score = max(0, candidate.score - 2)
                candidate.rationale.append("CheckWX 备份观测和 TAF 同时缺失，冗余校验层偏弱。")
            if "local_official_validation_gap" in validation_flags:
                candidate.score = max(0, candidate.score - 2)
                candidate.rationale.append("本地官方机场页没有完成有效验证，错锚保护偏弱。")
            candidate.win_probability_pct = _estimate_win_probability_pct(
                city_key=city_key,
                bucket_type=candidate.bucket_type,
                side_bias=candidate.side_bias,
                distance=candidate.distance_to_bucket,
                forecast_max=forecast_max,
                lower=candidate.lower,
                model_spread_c=model_spread_c,
                peak_spread_minutes=peak_spread_minutes,
                taf_max_c=taf_max_c,
                forecast_status=forecast_status,
                validation_flags=validation_flags,
            )
            candidate.rationale.append(
                f"模型认为这张 No 的赢面约 {candidate.win_probability_pct}% 。这个数是启发式概率，不是回测校准后的真实胜率。"
            )
            candidate.recommendation = _recommendation_for_score(candidate.score, candidate.no_best_ask)
            candidates.append(candidate)

    candidates.sort(key=lambda item: (item.score, item.distance_to_bucket, item.no_price), reverse=True)
    return candidates


def _build_human_summary(
    *,
    city_key: str,
    forecast_max: Optional[float],
    main_range_low: Optional[int],
    main_range_high: Optional[int],
    candidate: Optional[Candidate],
    market_exists: bool,
) -> str:
    city_name = _display_name(city_key)
    if not market_exists:
        return f"{city_name} 今天没有找到活跃温度盘口。这次只完成配置和记录，不给下单建议。"
    if forecast_max is None or candidate is None:
        return f"{city_name} 今天虽然有盘口，但当前自动化数据不足以给出可靠候选，只建议人工复核。"
    return (
        f"{city_name} 当前自动化判断更偏向保守尾部 No。"
        f"Open-Meteo 目标日高温约 {forecast_max:.1f}C，主区间约 {main_range_low}..{main_range_high}C，"
        f"相对更顺手的候选是 `{candidate.question}`，模型赢面估计约 {candidate.win_probability_pct}%，"
        f"但执行上仍要防止追到过贵的 ask。"
    )


def _build_telegram_text(
    *,
    city_key: str,
    local_date: str,
    market_exists: bool,
    forecast_max: Optional[float],
    main_range_low: Optional[int],
    main_range_high: Optional[int],
    candidate: Optional[Candidate],
    station_code: str,
    weather_snapshot: Optional[Dict[str, Any]] = None,
    top_candidates: Optional[List[Candidate]] = None,
) -> str:
    city_name = _display_name(city_key)
    header = f"【天气盘扫描】{city_name} {local_date}"
    if not market_exists:
        return (
            f"{header}\n"
            f"结论：今天未发现活跃盘口，跳过下单。\n"
            f"站点：{station_code}\n"
            f"建议：只保留案例记录。"
        )
    observation = dict((weather_snapshot or {}).get("latest_observation") or {})
    forecast = dict((weather_snapshot or {}).get("latest_forecast") or {})
    station = dict((weather_snapshot or {}).get("station") or {})
    validation = dict((weather_snapshot or {}).get("secondary_validation") or {})
    obs_temp = _safe_float(observation.get("temp_c"))
    obs_time = str(observation.get("observed_at_utc") or "")
    peak_hour = str(forecast.get("peak_hour_local") or "")
    precip = _safe_float(forecast.get("precipitation_probability_max"))
    wind_kt = _safe_float(observation.get("wind_speed_kt"))
    dewp = _safe_float(observation.get("dewp_c"))
    forecast_status = str(forecast.get("forecast_status") or "").strip() or "unknown"
    obs_local = _format_local_observation_time(obs_time, str(station.get("timezone") or "UTC"))
    conflict_note = ""
    if obs_temp is not None and forecast_max is not None and obs_temp > forecast_max + 0.7:
        conflict_note = "当前观测已经明显高于 forecast max，说明 forecast 偏冷或刷新滞后，右尾风险要上调。"
    validation_flags = [str(item).strip() for item in (validation.get("validation_flags") or []) if str(item).strip()]
    wu_hourly = dict(validation.get("wunderground_hourly") or {})
    checkwx_metar = dict(validation.get("checkwx_metar") or {})
    checkwx_taf = dict(validation.get("checkwx_taf") or {})
    local_rows = validation.get("local_official_rows") or []

    if forecast_max is None or candidate is None:
        lines = [
            header,
            f"站点：{station_code}",
            f"天气：forecast max {forecast_max if forecast_max is not None else '-'}C，主区间 {main_range_low if main_range_low is not None else '-'}..{main_range_high if main_range_high is not None else '-'}C，peak {peak_hour or '-'}",
            f"观测：METAR {obs_temp if obs_temp is not None else '-'}C @ {obs_local}",
            f"风雨：wind {wind_kt if wind_kt is not None else '-'}kt，precip {precip if precip is not None else '-'}%，dewpoint {dewp if dewp is not None else '-'}C",
            "",
            "主结论：人工复核，不自动下单",
            "主候选：当前没有找到足够干净的桶，不能为了给建议而硬挑。",
            f"forecast 状态：{forecast_status}",
        ]
        lines.extend(["", "理由："])
        for item in _build_weather_path_notes(weather_snapshot or {})[:8]:
            lines.append(f"- {item}")
        if validation_flags:
            lines.append(f"- 验证层风险：{', '.join(validation_flags)}")
        if conflict_note:
            lines.append(f"- {conflict_note}")
        return "\n".join(lines)

    lines = [
        header,
        f"站点：{station_code}",
        f"天气：forecast max {forecast_max:.1f}C，主区间 {main_range_low}..{main_range_high}C，peak {peak_hour or '-'}",
        f"观测：METAR {obs_temp if obs_temp is not None else '-'}C @ {obs_local}",
        f"风雨：wind {wind_kt if wind_kt is not None else '-'}kt，precip {precip if precip is not None else '-'}%，dewpoint {dewp if dewp is not None else '-'}C",
        "",
        f"主结论：{candidate.recommendation}（可做性 {candidate.score}/100，模型赢面 {candidate.win_probability_pct}%）",
        f"主候选：{candidate.question}",
        f"盘口：No 中间价 {candidate.no_price:.3f}，bid/ask {candidate.no_best_bid if candidate.no_best_bid is not None else '-'} / {candidate.no_best_ask if candidate.no_best_ask is not None else '-'}",
    ]
    if candidate.entry_cap is not None and candidate.recommendation != "观察，不下单":
        lines.append(f"执行：只考虑被动挂单，No 入场上限约 {candidate.entry_cap:.3f}，不要追现在的 ask。")
    elif candidate.entry_cap is not None:
        lines.append(f"执行：如果一定要试，只能远离 ask 被动挂单，No 上限约 {candidate.entry_cap:.3f}。")

    lines.extend(["", "理由："])
    for item in candidate.rationale[:4]:
        lines.append(f"- {item}")
    for item in _build_weather_path_notes(weather_snapshot or {})[:4]:
        lines.append(f"- {item}")
    if conflict_note:
        lines.append(f"- {conflict_note}")

    extra = [row for row in (top_candidates or []) if row.question != candidate.question][:2]
    if extra:
        lines.extend(["", "次选参考："])
        for row in extra:
            lines.append(
                f"- {row.question} | {row.recommendation} | score {row.score} | win {row.win_probability_pct}% | No {row.no_price:.3f} | cap {row.entry_cap if row.entry_cap is not None else '-'}"
            )
    return "\n".join(lines)


def _send_telegram(text: str, enabled: bool) -> None:
    if not enabled:
        return
    send_telegram_message_sync(text=text)


def _build_case_payload_no_market(city_key: str, local_date: str) -> Dict[str, Any]:
    city_name = _display_name(city_key)
    run_time = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S %Z")
    return {
        "city_key": city_key,
        "local_date": local_date,
        "run_time_local": run_time,
        "question": f"Highest temperature in {city_name} on {local_date}",
        "market_title": "No active market found",
        "outcome": "N/A",
        "station": load_profiles(city_key, local_date).station.get("station_code", ""),
        "analysis_mode": "no_active_market",
        "human_summary": f"{city_name} 今天没有活跃天气盘口，所以本条只做记录，不给交易结论。",
        "current_judgment": "no_active_market",
        "conclusion": "skip",
        "airport_context": AIRPORT_CONTEXT_MAP.get(city_key, ""),
        "delta_vs_previous": "今天新增的是盘口存在性检查；没有活跃事件，无法和具体桶位做对比。",
        "action_suggestion": "跳过，不下单",
        "data_fetch_times": {
            "market_fetch_time": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        },
        "source_summary": [
            {
                "name": "internal_gamma_client",
                "role": "market_lookup",
                "status": "not_found",
                "detail": "按 city+date 生成 event slug 后未找到活跃事件。",
            }
        ],
        "risk_flags": ["today_has_no_active_market"],
        "reasoning_summary": [
            "没有盘口时不继续做天气数值推断，更不应硬给下单建议。",
            "今天的动作是保留配置和案例记录，等待市场上线。",
        ],
        "staleness_notes": ["这条记录只包含市场存在性检查，不包含天气源抓取。"],
    }


def _build_case_payload_market(
    *,
    city_key: str,
    local_date: str,
    market_snapshot: Dict[str, Any],
    weather_snapshot: Dict[str, Any],
    candidate: Optional[Candidate],
) -> Dict[str, Any]:
    city_name = _display_name(city_key)
    forecast = dict(weather_snapshot.get("latest_forecast") or {})
    observation = dict(weather_snapshot.get("latest_observation") or {})
    station = dict(weather_snapshot.get("station") or {})
    forecast_status = str(forecast.get("forecast_status") or "").strip() or "unknown"
    forecast_max = _safe_float(forecast.get("max_temp_market_unit"))
    main_range_low = forecast.get("main_range_low")
    main_range_high = forecast.get("main_range_high")
    run_time = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S %Z")
    weather_path_notes = _build_weather_path_notes(weather_snapshot)

    if candidate is None:
        judgment = "manual_check"
        action = "人工复核，不自动下单"
        conclusion = "data_gap_or_no_candidate"
        reasoning_summary = [
            "市场存在，但没有找到明显在主区间外、又足够干净的候选桶。",
            "当前自动化流程不应该为了给建议而硬挑一个桶。",
        ]
        reasoning_summary.extend(weather_path_notes)
        risk_flags = ["no_clean_candidate_today"]
        if forecast_status != "ok":
            risk_flags.append(f"forecast_status_{forecast_status}")
    else:
        judgment = candidate.recommendation
        action = candidate.recommendation
        conclusion = f"best_candidate_score={candidate.score}"
        reasoning_summary = candidate.rationale
        reasoning_summary.extend(weather_path_notes)
        risk_flags = []
        if candidate.no_best_ask is None or (candidate.no_best_ask is not None and candidate.no_best_ask >= 0.985):
            risk_flags.append("execution_too_expensive_or_thin")
        if city_key in HIGH_VARIABILITY_CITIES:
            risk_flags.append("high_intraday_variability_city")
        if candidate.distance_to_bucket < 2.0:
            risk_flags.append("tail_distance_not_large")
        if _safe_float(observation.get("temp_c")) is not None and forecast_max is not None:
            obs_temp = _safe_float(observation.get("temp_c"))
            if obs_temp is not None and obs_temp > forecast_max + 0.7:
                risk_flags.append("observation_above_forecast_max")

    return {
        "city_key": city_key,
        "local_date": local_date,
        "run_time_local": run_time,
        "question": market_snapshot.get("event", {}).get("title", f"Highest temperature in {city_name}"),
        "market_title": candidate.question if candidate else market_snapshot.get("event", {}).get("title", ""),
        "outcome": "No",
        "station": station.get("station_code") or station.get("icao_id") or "",
        "analysis_mode": "market_plus_weather_scan",
        "human_summary": _build_human_summary(
            city_key=city_key,
            forecast_max=forecast_max,
            main_range_low=main_range_low,
            main_range_high=main_range_high,
            candidate=candidate,
            market_exists=True,
        ),
        "current_judgment": judgment,
        "conclusion": conclusion,
        "airport_context": AIRPORT_CONTEXT_MAP.get(city_key, ""),
        "delta_vs_previous": "这次是新的日扫描记录，后续复跑时再和本条比较 forecast 与盘口漂移。",
        "action_suggestion": action,
        "data_fetch_times": {
            "market_fetch_time": str((market_snapshot.get("meta") or {}).get("fetched_at_utc") or ""),
            "weather_fetch_time": str(weather_snapshot.get("generated_at_utc") or ""),
            "observation_time": str(observation.get("observed_at_utc") or ""),
            "latest_update_seen": str(observation.get("observed_at_utc") or ""),
        },
        "market_snapshot": {
            "center": f"{forecast_max:.1f}C / {main_range_low}..{main_range_high}C" if forecast_max is not None else "",
            "best_bid": f"{candidate.no_best_bid:.3f}" if candidate and candidate.no_best_bid is not None else "",
            "best_ask": f"{candidate.no_best_ask:.3f}" if candidate and candidate.no_best_ask is not None else "",
            "last_trade_price": f"{candidate.no_price:.3f}" if candidate else "",
            "model_win_probability_pct": str(candidate.win_probability_pct) if candidate else "",
            "volume": str((market_snapshot.get("event") or {}).get("volume") or ""),
            "liquidity": str((market_snapshot.get("event") or {}).get("liquidity") or ""),
        },
        "source_summary": [
            {
                "name": "internal_gamma_clob_clients",
                "role": "market_snapshot",
                "status": "ok",
                "detail": "事件、市场和 token 级 orderbook 通过内部 market query 工具抓取。",
            },
            {
                "name": "aviationweather",
                "role": "airport_observation",
                "status": "ok",
                "detail": "用机场 METAR 看当前温度和观测时间，确认对象是同一机场。",
            },
            {
                "name": "aviationweather_taf",
                "role": "official_airport_forecast",
                "status": "ok" if weather_snapshot.get("taf_summary") else "missing",
                "detail": "用 TAF 看官方机场预报窗口和 TX/TN 标记是否支持主判断。",
            },
            {
                "name": "open-meteo",
                "role": "target_day_forecast",
                "status": forecast_status,
                "detail": "用目标日最高温和小时路径做自动化尾部筛选；拿不到时应降级而不是脑补。",
            },
            {
                "name": "open_meteo_multi_model",
                "role": "model_spread",
                "status": "ok" if (weather_snapshot.get("multi_model_forecast") or {}).get("available_model_count") else "data_gap",
                "detail": "比较 ECMWF / ICON / GFS / JMA 等模型分歧，判断 peak window 和尾部不确定性。",
            },
            {
                "name": "wunderground_hourly",
                "role": "secondary_forecast_anchor",
                "status": str(((weather_snapshot.get("secondary_validation") or {}).get("wunderground_hourly") or {}).get("status") or "missing"),
                "detail": "验证同站点 WU Hourly 页面是否可用，防止交易锚对象错位。",
            },
            {
                "name": "checkwx",
                "role": "secondary_observation",
                "status": "ok"
                if (
                    str(((weather_snapshot.get("secondary_validation") or {}).get("checkwx_metar") or {}).get("status") or "") == "ok"
                    or str(((weather_snapshot.get("secondary_validation") or {}).get("checkwx_taf") or {}).get("status") or "") == "ok"
                )
                else "data_gap",
                "detail": "作为 AviationWeather 的冗余机场观测和 TAF 校验层。",
            },
            {
                "name": "local_official_airport_page",
                "role": "local_official_validation",
                "status": "ok"
                if any(
                    isinstance(row, dict) and row.get("status") == "ok" and row.get("station_match")
                    for row in (((weather_snapshot.get("secondary_validation") or {}).get("local_official_rows")) or [])
                )
                else "missing",
                "detail": "使用 Korea AMO 或 JMA 机场页做对象和本地官方存在性校验。",
            },
        ],
        "risk_flags": risk_flags,
        "reasoning_summary": reasoning_summary,
        "staleness_notes": [
            "市场和天气并非同一刷新节奏，盘口价格可能先动，天气页可能滞后数分钟到数十分钟。",
            "只要主 forecast 缺失或多模型拿不齐，就应该直接降级为 manual_check，而不是脑补路径。",
        ],
    }


def _lookup_active_market(city_key: str, local_date: str) -> Optional[str]:
    slug = _event_slug(city_key, local_date)
    event = PolymarketGammaClient().fetch_event_by_id_or_slug(slug=slug)
    if not event or not bool(event.get("active", True)) or bool(event.get("closed", False)):
        return None
    return slug


def run_review(
    *,
    local_date: str,
    cities: List[str],
    send_telegram: bool,
    skip_no_market_telegram: bool = True,
) -> List[Dict[str, Any]]:
    writer = CaseRecordWriter()
    weather_tool = AirportWeatherTool()
    results: List[Dict[str, Any]] = []

    for city_key in cities:
        station_code = str(load_profiles(city_key, local_date).station.get("station_code") or "")
        slug = _lookup_active_market(city_key, local_date)
        if not slug:
            payload = _build_case_payload_no_market(city_key, local_date)
            path = writer.append_entry(payload)
            msg = _build_telegram_text(
                city_key=city_key,
                local_date=local_date,
                market_exists=False,
                forecast_max=None,
                main_range_low=None,
                main_range_high=None,
                candidate=None,
                station_code=station_code,
            )
            _send_telegram(msg, send_telegram and not skip_no_market_telegram)
            results.append(
                {
                    "city_key": city_key,
                    "market_exists": False,
                    "case_path": str(path),
                    "telegram_text": msg,
                }
            )
            continue

        market_snapshot = build_market_snapshot(target_market=slug, include_orderbook=True, orderbook_top_n=3)
        weather_snapshot = weather_tool.build_snapshot(city_key=city_key, local_date=local_date)
        candidates = _pick_candidates(city_key, market_snapshot, weather_snapshot)
        candidate = candidates[0] if candidates else None
        payload = _build_case_payload_market(
            city_key=city_key,
            local_date=local_date,
            market_snapshot=market_snapshot,
            weather_snapshot=weather_snapshot,
            candidate=candidate,
        )
        path = writer.append_entry(payload)

        forecast = dict(weather_snapshot.get("latest_forecast") or {})
        msg = _build_telegram_text(
            city_key=city_key,
            local_date=local_date,
            market_exists=True,
            forecast_max=_safe_float(forecast.get("max_temp_market_unit")),
            main_range_low=forecast.get("main_range_low"),
            main_range_high=forecast.get("main_range_high"),
            candidate=candidate,
            station_code=station_code,
            weather_snapshot=weather_snapshot,
            top_candidates=candidates,
        )
        _send_telegram(msg, send_telegram)
        results.append(
            {
                "city_key": city_key,
                "market_exists": True,
                "candidate": candidate,
                "top_candidates": candidates,
                "case_path": str(path),
                "telegram_text": msg,
            }
        )

    summary_lines = [f"【天气盘总览】{local_date} 亚洲城市扫描"]
    tradable = [
        item for item in results if item.get("market_exists") and isinstance(item.get("candidate"), Candidate)
    ]
    tradable.sort(key=lambda item: item["candidate"].score, reverse=True)
    if tradable:
        for item in tradable[:5]:
            candidate = item["candidate"]
            summary_lines.append(
                f"- {_display_name(item['city_key'])}: score {candidate.score}/100, win {candidate.win_probability_pct}%, {candidate.recommendation}, {candidate.question}"
            )
    else:
        summary_lines.append("- 今天没有找到可给出自动候选的城市。")

    skipped = [_display_name(item["city_key"]) for item in results if not item.get("market_exists")]
    if skipped:
        summary_lines.append(f"- 无活跃盘口: {', '.join(skipped)}")

    summary_text = "\n".join(summary_lines)
    _send_telegram(summary_text, send_telegram)
    print(summary_text)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Asia weather-city daily review, append case files, and send Telegram.")
    parser.add_argument("--local-date", default=datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat())
    parser.add_argument("--cities", nargs="*", default=DEFAULT_CITIES)
    parser.add_argument("--telegram", default="true", help="Send Telegram updates: true/false")
    parser.add_argument("--skip-no-market-telegram", default="true", help="Do not Telegram no-market cities")
    parser.add_argument("--loop", default="false", help="Keep running and resend every interval")
    parser.add_argument("--interval-seconds", type=int, default=3600)
    args = parser.parse_args()

    send_tg = str(args.telegram).strip().lower() in {"1", "true", "yes", "y", "on"}
    skip_no_market = str(args.skip_no_market_telegram).strip().lower() in {"1", "true", "yes", "y", "on"}
    loop = str(args.loop).strip().lower() in {"1", "true", "yes", "y", "on"}
    cities = [str(x).strip().lower() for x in args.cities if str(x).strip()]
    while True:
        current_date = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat() if loop else args.local_date
        run_review(
            local_date=current_date,
            cities=cities,
            send_telegram=send_tg,
            skip_no_market_telegram=skip_no_market,
        )
        if not loop:
            break
        time.sleep(max(300, int(args.interval_seconds)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
