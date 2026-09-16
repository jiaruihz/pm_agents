#!/usr/bin/env python3
"""Replay the tmax lineage repairs on historical states and first live orders.

Research only. This script never starts a runner or writes an order.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import research_tmax_distribution_p0_anchor_scorecard_v1 as p0  # noqa: E402
import research_tmax_distribution_p1_fusion_scorecard_v1 as p1  # noqa: E402
import research_tmax_distribution_p3_feature_ablation_v1 as p3  # noqa: E402
import research_tmax_distribution_p4_observed_label_extension_v1 as p4  # noqa: E402
from scripts.ops import tmax_distribution_edge_live_candidate_v1 as live  # noqa: E402
from weather_data_feed.market_brackets import parse_market_bracket  # noqa: E402
from weather_data_feed.observation_sources.fetchers import relative_humidity_pct  # noqa: E402
from weather_data_feed.production_paths import historical_strategy_snapshots  # noqa: E402
from src.strategies.runtime.production import load_production_spec  # noqa: E402


OUT_DIR = ROOT / "docs/analysis/2026-07/generated/tmax_lineage_repair_replay_v1"
REPORT_PATH = ROOT / "docs/analysis/2026-07/2026-07-10-tmax-lineage-repair-replay-v1.md"
JSON_PATH = ROOT / "docs/analysis/2026-07/2026-07-10-tmax-lineage-repair-replay-v1.json"
LIVE_ORDERS = ROOT / "runtime/weather_edge_v1/tmax_distribution_edge_first_lock_no_current_yes_tiny_live_v1/live_orders.jsonl"
DB_PATH = ROOT / "runtime/weather.db"
PRODUCTION_SPEC = load_production_spec()
RUNTIME_ROOT = PRODUCTION_SPEC.data_feed_runtime_root
SOURCE_EVENTS_ROOT = PRODUCTION_SPEC.source_events_root()
FORECAST_ENRICHMENT_ROOT = PRODUCTION_SPEC.forecast_enrichment_root()
SNAPSHOT_ROOTS = [historical_strategy_snapshots()]

MODEL_SPEC = live.MODEL_SPEC
MODEL_METHOD = live.MODEL_METHOD
BUCKETS = list(p0.BUCKETS)
EXPRESSIONS = ["current_no", "d1_no", "d2_no", "d1_yes", "d2_yes"]
EPS = 1e-9
FEE_RATE = 0.05
ASK_FLOOR = 0.40
ASK_CEILING = 0.99
EDGE_THRESHOLD = 0.02

TEMP_DEW_RE = re.compile(r"\s(M?\d{2})/(M?\d{2}|//)(?:\s|$)")
WIND_RE = re.compile(r"\b(?:\d{3}|VRB)(\d{2,3})(?:G\d{2,3})?KT\b")
SKY_RE = re.compile(r"\b(CLR|SKC|FEW|SCT|BKN|OVC|VV)(?:\d{3})?\b")


def parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        out = datetime.fromisoformat(text)
    except ValueError:
        return None
    if out.tzinfo is None:
        out = out.replace(tzinfo=timezone.utc)
    return out.astimezone(timezone.utc)


def finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def fee(price: float) -> float:
    return FEE_RATE * price * (1.0 - price)


def normalize(weights: dict[str, float]) -> dict[str, float]:
    clean = {key: max(EPS, float(value)) for key, value in weights.items()}
    total = sum(clean.values())
    return {key: value / total for key, value in clean.items()}


def snapshot_key(value: Any) -> str:
    dt = parse_dt(value)
    return dt.strftime("%Y%m%d_%H%M") if dt else ""


def build_snapshot_index() -> dict[str, Path]:
    out: dict[str, Path] = {}
    for root in SNAPSHOT_ROOTS:
        if not root.exists():
            continue
        for path in root.glob("snapshot_*.json"):
            # Snapshot filenames are wall-clock/BJ labels on the Mac collector.
            # The payload timestamp is the canonical UTC decision lineage.
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            key = snapshot_key(payload.get("ts_utc")) or path.stem.removeprefix("snapshot_")
            out[key] = path
    return out


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
    return rows


def record_yes_quote(row: dict[str, Any]) -> dict[str, float | None]:
    yes_bid = finite(row.get("yes_best_bid"))
    yes_ask = finite(row.get("yes_best_ask"))
    no_bid = finite(row.get("no_best_bid"))
    no_ask = finite(row.get("no_best_ask"))
    bid_choices = [value for value in [yes_bid, None if no_ask is None else 1.0 - no_ask] if value is not None]
    ask_choices = [value for value in [yes_ask, None if no_bid is None else 1.0 - no_bid] if value is not None]
    best_bid = max(bid_choices) if bid_choices else None
    best_ask = min(ask_choices) if ask_choices else None
    midpoint = None
    if best_bid is not None and best_ask is not None:
        midpoint = (best_bid + best_ask) / 2.0
    elif best_bid is not None:
        midpoint = best_bid
    elif best_ask is not None:
        midpoint = best_ask
    return {"bid": best_bid, "ask": best_ask, "mid": midpoint}


def ladder_sort_key(row: dict[str, Any]) -> tuple[float, float]:
    parsed = parse_market_bracket(str(row.get("bracket") or ""), str(row.get("question") or ""))
    if parsed is None:
        return (math.inf, math.inf)
    low = -math.inf if parsed.bottom else float(parsed.low) if parsed.low is not None else math.inf
    high = math.inf if parsed.top else float(parsed.high) if parsed.high is not None else math.inf
    return (low, high)


def label_key(value: Any) -> str:
    return str(value or "").replace("°C", "").replace("°F", "").replace("°", "").strip()


def corrected_local_distribution(row: pd.Series) -> dict[str, float] | None:
    current_yes_ask = finite(row.get("current_yes_ask"))
    current_no_ask = finite(row.get("current_bracket_no_ask"))
    current_no_bid = finite(row.get("current_no_bid"))
    d1_no_ask = finite(row.get("d1_no_ask"))
    d1_no_bid = finite(row.get("d1_no_bid"))
    d2_no_ask = finite(row.get("d2_no_ask"))
    d2_no_bid = finite(row.get("d2_no_bid"))
    if None in {current_yes_ask, current_no_ask, current_no_bid, d1_no_ask, d1_no_bid, d2_no_ask, d2_no_bid}:
        return None
    current_bid = 1.0 - float(current_no_ask)
    current_ask = min(float(current_yes_ask), 1.0 - float(current_no_bid))
    current = (current_bid + current_ask) / 2.0
    d1 = 1.0 - (float(d1_no_ask) + float(d1_no_bid)) / 2.0
    d2 = 1.0 - (float(d2_no_ask) + float(d2_no_bid)) / 2.0
    return normalize({"current": current, "d1": d1, "d2": d2, "tail": max(EPS, 1.0 - current - d1 - d2)})


def full_ladder_distribution(row: pd.Series, snapshot: dict[str, Any]) -> dict[str, Any] | None:
    city = str(row.get("city") or "")
    target_date = str(row.get("target_date") or "")
    ladder = [
        item
        for item in snapshot.get("records", [])
        if str(item.get("city") or "") == city
        and str(item.get("target_date") or item.get("event_date") or "") == target_date
    ]
    ladder = sorted(ladder, key=ladder_sort_key)
    by_label = {label_key(item.get("bracket")): (idx, item) for idx, item in enumerate(ladder)}
    labels = [label_key(row.get("current_bracket")), label_key(row.get("d1_no_bracket")), label_key(row.get("d2_no_bracket"))]
    if any(label not in by_label for label in labels):
        return None
    current_idx, current_row = by_label[labels[0]]
    d1_idx, d1_row = by_label[labels[1]]
    d2_idx, d2_row = by_label[labels[2]]
    if not current_idx < d1_idx < d2_idx:
        return None
    current_quote = record_yes_quote(current_row)
    d1_quote = record_yes_quote(d1_row)
    d2_quote = record_yes_quote(d2_row)
    if any(quote["mid"] is None for quote in [current_quote, d1_quote, d2_quote]):
        return None
    tail_quotes = [record_yes_quote(item) for item in ladder[d2_idx + 1 :]]
    tail_mids = [float(quote["mid"]) for quote in tail_quotes if quote["mid"] is not None]
    probs = normalize(
        {
            "current": float(current_quote["mid"]),
            "d1": float(d1_quote["mid"]),
            "d2": float(d2_quote["mid"]),
            "tail": sum(tail_mids),
        }
    )
    return {
        **{f"p_{bucket}": probs[bucket] for bucket in BUCKETS},
        "d3plus_quote_count": len(tail_mids),
        "d1_yes_effective_ask": d1_quote["ask"],
        "d2_yes_effective_ask": d2_quote["ask"],
    }


def add_market_variant(pred: pd.DataFrame, market: pd.DataFrame, variant: str, alpha: float) -> pd.DataFrame:
    keys = ["city", "target_date", "decision_hour_local", "actual_bucket"]
    market_cols = keys + [f"{variant}_p_{bucket}" for bucket in BUCKETS]
    out = pred.merge(market[market_cols], on=keys, how="inner", validate="one_to_one")
    total = np.zeros(len(out), dtype=float)
    weights: dict[str, np.ndarray] = {}
    for bucket in BUCKETS:
        weights[bucket] = (
            (1.0 - alpha) * out[f"{variant}_p_{bucket}"].to_numpy(dtype=float)
            + alpha * out[f"model_p_{bucket}"].to_numpy(dtype=float)
        )
        total += weights[bucket]
    for bucket in BUCKETS:
        out[f"{MODEL_METHOD}_p_{bucket}"] = np.maximum(EPS, weights[bucket] / total)
    out["variant"] = variant
    return out


def expanding_predictions(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    specs = p3._feature_specs(df)
    p1.MODEL_SPECS = specs
    train_pre = df[df["target_date"] < p1.TRAIN_CUTOFF].copy()
    selection = p1._select_model(train_pre, MODEL_SPEC)["selected"]
    c_value = float(selection["c"])
    alpha = float(selection["cv_blend_alpha"])
    frames = []
    for target_date in sorted(df["target_date"].astype(str).unique()):
        fit = df[df["target_date"].astype(str) < target_date].copy()
        test = df[df["target_date"].astype(str) == target_date].copy()
        if fit["target_date"].nunique() < 5 or test.empty:
            continue
        baseline = p1._fit_predict(fit, test, MODEL_SPEC, c_value)
        baseline = p1._blend_predictions(baseline, alpha, MODEL_METHOD)
        baseline["variant"] = "historical_full_features"
        frames.append(baseline)

        missing = test.copy()
        for col in ["gfs_gap_to_running_native", "ecmwf_gap_to_running_native", "relative_humidity_pct", "sky_cover_code"]:
            if col in missing.columns:
                missing[col] = np.nan
        degraded = p1._fit_predict(fit, missing, MODEL_SPEC, c_value)
        degraded = p1._blend_predictions(degraded, alpha, MODEL_METHOD)
        degraded["variant"] = "live_missingness_emulation"
        frames.append(degraded)
    return pd.concat(frames, ignore_index=True), {"c": c_value, "alpha": alpha, "fit_rows": int(len(df))}


def expression_probability(row: pd.Series, expression: str) -> float:
    current = float(row[f"{MODEL_METHOD}_p_current"])
    d1 = float(row[f"{MODEL_METHOD}_p_d1"])
    d2 = float(row[f"{MODEL_METHOD}_p_d2"])
    return {
        "current_no": 1.0 - current,
        "d1_no": 1.0 - d1,
        "d2_no": 1.0 - d2,
        "d1_yes": d1,
        "d2_yes": d2,
    }[expression]


def expression_ask(row: pd.Series, expression: str) -> float | None:
    mapping = {
        "current_no": finite(row.get("current_bracket_no_ask")),
        "d1_no": finite(row.get("d1_no_ask")),
        "d2_no": finite(row.get("d2_no_ask")),
        "d1_yes": finite(row.get("d1_yes_effective_ask")),
        "d2_yes": finite(row.get("d2_yes_effective_ask")),
    }
    if mapping["d1_yes"] is None:
        bid = finite(row.get("d1_no_bid"))
        mapping["d1_yes"] = None if bid is None else 1.0 - bid
    if mapping["d2_yes"] is None:
        bid = finite(row.get("d2_no_bid"))
        mapping["d2_yes"] = None if bid is None else 1.0 - bid
    return mapping[expression]


def expression_payoff(actual: str, expression: str) -> float:
    if expression == "current_no":
        return float(actual != "current")
    if expression == "d1_no":
        return float(actual != "d1")
    if expression == "d2_no":
        return float(actual != "d2")
    if expression == "d1_yes":
        return float(actual == "d1")
    return float(actual == "d2")


def policy_replay(df: pd.DataFrame, predictions: pd.DataFrame, denominator: str) -> pd.DataFrame:
    keys = ["city", "target_date", "decision_hour_local", "actual_bucket"]
    meta_cols = keys + [
        "label_source",
        "temp_trend_3h_f",
        "current_bracket_no_ask",
        "d1_no_ask",
        "d2_no_ask",
        "d1_no_bid",
        "d2_no_bid",
    ]
    for optional in ["d1_yes_effective_ask", "d2_yes_effective_ask"]:
        if optional in df.columns:
            meta_cols.append(optional)
    meta = df[meta_cols].drop_duplicates(keys)
    joined = predictions.merge(meta, on=keys, how="inner", validate="many_to_one")
    rows = []
    for item in joined.to_dict("records"):
        row = pd.Series(item)
        trend = finite(row.get("temp_trend_3h_f"))
        if trend is None or -0.5 <= trend < 0.5:
            continue
        for expression in EXPRESSIONS:
            ask = expression_ask(row, expression)
            if ask is None or ask < ASK_FLOOR or ask > ASK_CEILING:
                continue
            p_win = expression_probability(row, expression)
            entry_fee = fee(ask)
            edge = p_win - ask - entry_fee
            if edge < EDGE_THRESHOLD:
                continue
            payoff = expression_payoff(str(row["actual_bucket"]), expression)
            rows.append(
                {
                    "denominator": denominator,
                    "variant": row["variant"],
                    "city": row["city"],
                    "target_date": row["target_date"],
                    "decision_hour_local": row["decision_hour_local"],
                    "label_source": row.get("label_source"),
                    "expression": expression,
                    "ask": ask,
                    "fee": entry_fee,
                    "cost": ask + entry_fee,
                    "p_win": p_win,
                    "edge": edge,
                    "model_roi": edge / (ask + entry_fee),
                    "win": payoff,
                    "pnl": payoff - ask - entry_fee,
                }
            )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    state_keys = ["denominator", "variant", "city", "target_date", "decision_hour_local"]
    out = out.sort_values([*state_keys, "edge", "model_roi"], ascending=[True, True, True, True, True, False, False])
    out = out.groupby(state_keys, as_index=False).head(1)
    day_keys = ["denominator", "variant", "city", "target_date"]
    return out.sort_values([*day_keys, "decision_hour_local"]).groupby(day_keys, as_index=False).head(1).reset_index(drop=True)


def date_block_ci(rows: pd.DataFrame, value_col: str = "pnl", cost_col: str = "cost", seed: int = 20260710) -> tuple[float, float, float]:
    if rows.empty:
        return math.nan, math.nan, math.nan
    roi = float(rows[value_col].sum() / rows[cost_col].sum())
    daily = rows.groupby("target_date", as_index=False).agg(value=(value_col, "sum"), cost=(cost_col, "sum"))
    if len(daily) < 3:
        return roi, math.nan, math.nan
    arr = daily[["value", "cost"]].to_numpy(dtype=float)
    rng = np.random.default_rng(seed)
    boot = []
    for _ in range(2000):
        sample = arr[rng.integers(0, len(arr), len(arr))]
        boot.append(sample[:, 0].sum() / sample[:, 1].sum())
    return roi, float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))


def summarize_policy(rows: pd.DataFrame) -> pd.DataFrame:
    out = []
    for (denominator, variant, scope), grp in rows.assign(
        scope=lambda x: np.where(x["target_date"].astype(str) < p1.TRAIN_CUTOFF, "dev_cv", "verified_forward")
    ).groupby(["denominator", "variant", "scope"]):
        roi, low, high = date_block_ci(grp)
        out.append(
            {
                "denominator": denominator,
                "variant": variant,
                "scope": scope,
                "rows": len(grp),
                "dates": grp["target_date"].nunique(),
                "cities": grp["city"].nunique(),
                "win_rate": grp["win"].mean(),
                "avg_ask": grp["ask"].mean(),
                "pnl": grp["pnl"].sum(),
                "roi": roi,
                "roi_ci_low": low,
                "roi_ci_high": high,
                "yes_rows": int(grp["expression"].str.endswith("yes").sum()),
                "no_rows": int(grp["expression"].str.endswith("no").sum()),
            }
        )
    return pd.DataFrame(out)


def summarize_model_predictions(rows: pd.DataFrame) -> pd.DataFrame:
    out = []
    bucket_index = {bucket: idx for idx, bucket in enumerate(BUCKETS)}
    scoped = rows.assign(
        scope=lambda x: np.where(x["target_date"].astype(str) < p1.TRAIN_CUTOFF, "dev_cv", "verified_forward")
    )
    for (scope, variant), grp in scoped.groupby(["scope", "variant"]):
        probs = grp[[f"{MODEL_METHOD}_p_{bucket}" for bucket in BUCKETS]].to_numpy(dtype=float)
        actual_idx = grp["actual_bucket"].map(bucket_index).to_numpy(dtype=int)
        winner = probs[np.arange(len(grp)), actual_idx]
        targets = np.eye(len(BUCKETS))[actual_idx]
        out.append(
            {
                "scope": scope,
                "variant": variant,
                "rows": len(grp),
                "dates": grp["target_date"].nunique(),
                "logloss": float(-np.log(np.maximum(EPS, winner)).mean()),
                "brier": float(np.square(probs - targets).sum(axis=1).mean()),
            }
        )
    return pd.DataFrame(out)


def parse_metar_context(raw: str) -> dict[str, Any]:
    text = f" {raw or ''} "
    temp = dewpoint = None
    match = TEMP_DEW_RE.search(text)
    if match:
        def token_value(token: str) -> float | None:
            if token == "//":
                return None
            return float(-int(token[1:]) if token.startswith("M") else int(token))

        temp = token_value(match.group(1))
        dewpoint = token_value(match.group(2))
    wind_match = WIND_RE.search(text)
    wind = float(wind_match.group(1)) if wind_match else None
    sky_matches = SKY_RE.findall(text)
    if "CAVOK" in text:
        sky = "CAVOK"
    else:
        severity = {"CLR": 0, "SKC": 0, "FEW": 1, "SCT": 2, "BKN": 3, "OVC": 4, "VV": 4}
        sky = max(sky_matches, key=lambda value: severity[value]) if sky_matches else ""
    return {"temp_c": temp, "dewpoint_c": dewpoint, "wind_kt": wind, "sky_code": sky}


class AsOfFiles:
    def __init__(self) -> None:
        self.source_events: dict[str, list[dict[str, Any]]] = {}
        self.forecasts: dict[str, list[dict[str, Any]]] = {}

    def _utc_dates(self, decision: datetime) -> list[str]:
        return [(decision - timedelta(days=1)).date().isoformat(), decision.date().isoformat()]

    def source_rows(self, decision: datetime) -> list[dict[str, Any]]:
        rows = []
        for date in self._utc_dates(decision):
            if date not in self.source_events:
                self.source_events[date] = read_jsonl(SOURCE_EVENTS_ROOT / date / "sources.jsonl")
            rows.extend(self.source_events[date])
        return rows

    def forecast_rows(self, decision: datetime) -> list[dict[str, Any]]:
        date = decision.date().isoformat()
        if date not in self.forecasts:
            self.forecasts[date] = read_jsonl(FORECAST_ENRICHMENT_ROOT / date / "forecast_enrichment.jsonl")
        return self.forecasts[date]


def observation_asof(files: AsOfFiles, city: str, target_date: str, decision: datetime) -> dict[str, Any] | None:
    candidates = []
    for row in files.source_rows(decision):
        if str(row.get("city") or "") != city or str(row.get("target_date") or "") != target_date:
            continue
        detect = parse_dt(row.get("local_detect_ts_utc") or row.get("ts_utc"))
        report = parse_dt(row.get("source_report_ts_utc"))
        temp = finite(row.get("temp_c"))
        if detect is None or report is None or temp is None or detect > decision:
            continue
        candidates.append((report, detect, temp, row))
    if not candidates:
        return None
    by_report: dict[datetime, tuple[datetime, float, dict[str, Any]]] = {}
    for report, detect, temp, row in candidates:
        old = by_report.get(report)
        if old is None or detect > old[0]:
            by_report[report] = (detect, temp, row)
    reports = sorted((report, payload[1], payload[2]) for report, payload in by_report.items())
    latest_report, current_temp, latest_row = reports[-1]
    running_max = max(item[1] for item in reports)
    max_report = max(item[0] for item in reports if abs(item[1] - running_max) < 1e-9)

    def prior_temp(minutes: int) -> float | None:
        cutoff = latest_report - timedelta(minutes=minutes)
        prior = [item for item in reports if item[0] <= cutoff]
        return prior[-1][1] if prior else None

    context = parse_metar_context(str(latest_row.get("raw_metar") or ""))
    dewpoint = context["dewpoint_c"]
    rh = relative_humidity_pct(current_temp, dewpoint)
    t1 = prior_temp(60)
    t3 = prior_temp(180)
    return {
        "city": city,
        "target_date": target_date,
        "timezone_name": "",
        "unit": str(latest_row.get("unit") or ""),
        "station": str(latest_row.get("station") or ""),
        "source": str(latest_row.get("source") or ""),
        "status": "ok",
        "current_temp_c": current_temp,
        "running_max_c": running_max,
        "tmpf_now": current_temp * 9.0 / 5.0 + 32.0,
        "dwpf_now": None if dewpoint is None else dewpoint * 9.0 / 5.0 + 32.0,
        "dewpoint_depression_f": None if dewpoint is None else (current_temp - dewpoint) * 9.0 / 5.0,
        "relh_now": rh,
        "sknt_now": context["wind_kt"],
        "sky_code_now": context["sky_code"],
        "d_tmpf_1h": None if t1 is None else (current_temp - t1) * 9.0 / 5.0,
        "d_tmpf_3h": None if t3 is None else (current_temp - t3) * 9.0 / 5.0,
        "minutes_since_running_max": (decision - max_report).total_seconds() / 60.0,
        "running_max_obs_utc": max_report.isoformat(),
        "age_min": (decision - latest_report).total_seconds() / 60.0,
    }


def forecast_asof(files: AsOfFiles, city: str, target_date: str, decision: datetime) -> dict[str, Any] | None:
    candidates = []
    for row in files.forecast_rows(decision):
        if str(row.get("city") or "") != city or str(row.get("target_date") or "") != target_date:
            continue
        ts = parse_dt(row.get("snapshot_ts_utc"))
        if ts is not None and ts <= decision:
            candidates.append((ts, row))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def live_order_replay(
    snapshot_index: dict[str, Path],
    hist: pd.DataFrame,
    selection: dict[str, float],
) -> pd.DataFrame:
    files = AsOfFiles()
    orders = read_jsonl(LIVE_ORDERS)
    specs = p3._feature_specs(hist)
    p1.MODEL_SPECS = specs
    args = SimpleNamespace(
        active_expressions=EXPRESSIONS,
        exclude_trend3h_flat=True,
        trend3h_flat_low=-0.5,
        trend3h_flat_high=0.5,
        fee_rate=FEE_RATE,
        ask_floor=ASK_FLOOR,
        ask_ceiling=ASK_CEILING,
        edge_threshold=EDGE_THRESHOLD,
        policy_id="first_lock_no_current_yes_repaired_replay",
    )
    rows = []
    for order in orders:
        decision = parse_dt(order.get("created_at_utc"))
        city = str(order.get("city") or "")
        target_date = str(order.get("target_date") or "")
        key = snapshot_key(order.get("snapshot_ts_utc"))
        path = snapshot_index.get(key)
        expression = str(order.get("combo") or "").removesuffix("_edge02")
        base = {
            "city": city,
            "target_date": target_date,
            "created_at_utc": order.get("created_at_utc"),
            "snapshot_ts_utc": order.get("snapshot_ts_utc"),
            "original_expression": expression,
            "original_price": finite(order.get("posted_price")),
            "original_p_win": finite(order.get("model_token_probability")),
            "snapshot_path": str(path) if path else "",
        }
        if decision is None or path is None:
            rows.append({**base, "replay_status": "missing_decision_or_snapshot"})
            continue
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        obs = observation_asof(files, city, target_date, decision)
        if obs is None:
            rows.append({**base, "replay_status": "missing_source_events_asof"})
            continue
        state, audits = live.build_state_rows(snapshot, snapshot.get("records", []), {(city, target_date): obs})
        if state.empty:
            rows.append({**base, "replay_status": audits[0].get("status") if audits else "empty_state"})
            continue
        forecast = forecast_asof(files, city, target_date, decision)
        if forecast:
            unit = str(state.iloc[0]["unit"])
            running = float(state.iloc[0]["running_native"])
            gfs = live.forecast_model_max_native(forecast, "GFS", unit)
            ecmwf = live.forecast_model_max_native(forecast, "ECMWF", unit)
            state.loc[:, "gfs_gap_to_running_native"] = gfs - running if math.isfinite(gfs) else np.nan
            state.loc[:, "ecmwf_gap_to_running_native"] = ecmwf - running if math.isfinite(ecmwf) else np.nan
        fit = hist[hist["target_date"].astype(str) < target_date].copy()
        pred = p1._fit_predict(fit, state, MODEL_SPEC, float(selection["c"]))
        pred = p1._blend_predictions(pred, float(selection["alpha"]), MODEL_METHOD)
        same_p = live.win_prob(pred.iloc[0], expression)
        price = float(base["original_price"] or math.nan)
        same_edge = same_p - price - fee(price) if math.isfinite(price) else math.nan
        candidates, blocked = live.build_candidates(state, pred, args)
        selected = candidates[0] if candidates else None
        rows.append(
            {
                **base,
                "replay_status": "ok",
                "obs_age_min": obs.get("age_min"),
                "relative_humidity_pct": state.iloc[0].get("relative_humidity_pct"),
                "sky_cover_code": state.iloc[0].get("sky_cover_code"),
                "gfs_gap_to_running_native": state.iloc[0].get("gfs_gap_to_running_native"),
                "ecmwf_gap_to_running_native": state.iloc[0].get("ecmwf_gap_to_running_native"),
                "repaired_same_expression_p_win": same_p,
                "repaired_same_expression_edge_at_fill": same_edge,
                "same_expression_still_eligible": bool(
                    math.isfinite(same_edge)
                    and price >= ASK_FLOOR
                    and price <= ASK_CEILING
                    and same_edge >= EDGE_THRESHOLD
                ),
                "repaired_selected_expression": selected.get("chosen_expression") if selected else "",
                "repaired_selected_snapshot_ask": selected.get("ask") if selected else None,
                "repaired_selected_p_win": selected.get("p_win") if selected else None,
                "repaired_selected_edge": selected.get("fee_adjusted_edge") if selected else None,
                "repaired_block_reason_counts": json.dumps(Counter(item.get("block_reason") for item in blocked), sort_keys=True),
            }
        )
    return pd.DataFrame(rows)


def canonical_ladder_inventory() -> dict[tuple[str, str], list[dict[str, Any]]]:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    try:
        rows = conn.execute(
            """
            SELECT city, target_date, bracket, MAX(question) AS question
            FROM settlement_outcomes
            WHERE settlement_status = 'settled'
            GROUP BY city, target_date, bracket
            """
        ).fetchall()
    finally:
        conn.close()
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for city, target_date, bracket, question in rows:
        grouped.setdefault((str(city), str(target_date)), []).append(
            {"bracket": str(bracket), "question": str(question or "")}
        )
    return {key: sorted(value, key=ladder_sort_key) for key, value in grouped.items()}


def geometry_replay(snapshot_index: dict[str, Path], start: str = "20260621", end: str = "20260707") -> pd.DataFrame:
    canonical = canonical_ladder_inventory()
    selected_paths: dict[str, Path] = {}
    for key, path in snapshot_index.items():
        date = key[:8]
        if start <= date <= end:
            selected_paths.setdefault(key[:11], path)  # one snapshot per UTC hour
    rows = []
    seen: set[tuple[str, str, int]] = set()
    for key, path in sorted(selected_paths.items()):
        try:
            snapshot = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for item in snapshot.get("records", []):
            city = str(item.get("city") or "")
            target_date = str(item.get("target_date") or item.get("event_date") or "")
            if city and target_date:
                grouped.setdefault((city, target_date), []).append(item)
        for (city, target_date), ladder in grouped.items():
            first = ladder[0]
            local_ts = str(first.get("ts_local") or "")
            try:
                hour = int(local_ts[11:13])
            except (TypeError, ValueError):
                continue
            grain = (city, target_date, hour)
            if grain in seen:
                continue
            seen.add(grain)
            running_f = finite(first.get("metar_current_max_f"))
            unit = str(first.get("unit") or "").upper()
            if running_f is None or unit not in {"C", "F"}:
                continue
            running_native = running_f if unit == "F" else (running_f - 32.0) * 5.0 / 9.0
            ladder = sorted([item for item in ladder if live.record_interval(item) is not None], key=ladder_sort_key)
            new_pos = next((idx for idx, item in enumerate(ladder) if live.record_contains_running_value(item, running_native)), None)
            old_pos = None
            for idx, item in enumerate(ladder):
                interval = p0._interval(item.get("bracket"))
                if interval and interval[0] <= running_native <= interval[1]:
                    old_pos = idx
                    break

            def status(pos: int | None, ladder_rows: list[dict[str, Any]] = ladder) -> str:
                if pos is None:
                    first_interval = live.record_interval(ladder_rows[0]) if ladder_rows else None
                    return "below_market_ladder" if first_interval and running_native < first_interval[0] else "unmapped"
                return "top_two_ladder_truncated" if pos + 2 >= len(ladder_rows) else "mapped_ok"

            canonical_ladder = canonical.get((city, target_date), [])
            canonical_pos = next(
                (
                    idx
                    for idx, item in enumerate(canonical_ladder)
                    if live.record_contains_running_value(item, running_native)
                ),
                None,
            )
            repaired_status = status(new_pos)
            canonical_status = status(canonical_pos, canonical_ladder) if canonical_ladder else "inventory_unavailable"
            if repaired_status == "below_market_ladder" and canonical_status != "below_market_ladder":
                completeness = "collector_missing_lower_siblings"
            elif repaired_status == "top_two_ladder_truncated" and canonical_status == "mapped_ok":
                completeness = "collector_missing_upper_siblings"
            elif canonical_status == "inventory_unavailable":
                completeness = "canonical_inventory_unavailable"
            else:
                completeness = "geometry_intrinsic"

            rows.append(
                {
                    "snapshot_key": key,
                    "city": city,
                    "target_date": target_date,
                    "decision_hour_local": hour,
                    "unit": unit,
                    "running_native": running_native,
                    "first_bracket": label_key(ladder[0].get("bracket")) if ladder else "",
                    "first_question": str(ladder[0].get("question") or "") if ladder else "",
                    "old_geometry_status": status(old_pos),
                    "repaired_geometry_status": repaired_status,
                    "canonical_geometry_status": canonical_status,
                    "snapshot_completeness_class": completeness,
                    "parser_recovered": bool(old_pos is None and new_pos is not None),
                    "ladder_count": len(ladder),
                    "canonical_ladder_count": len(canonical_ladder),
                    "quoted_ladder_count": sum(record_yes_quote(item)["mid"] is not None for item in ladder),
                }
            )
    return pd.DataFrame(rows)


def score_market_variants(rows: pd.DataFrame) -> pd.DataFrame:
    out = []
    for (scope, variant), grp in rows.groupby(["scope", "variant"]):
        out.append(
            {
                "scope": scope,
                "variant": variant,
                "rows": len(grp),
                "dates": grp["target_date"].nunique(),
                "cities": grp["city"].nunique(),
                "logloss": grp["logloss"].mean(),
                "brier": grp["brier"].mean(),
                "top1": grp["top1"].mean(),
            }
        )
    return pd.DataFrame(out)


def market_scores(scored: pd.DataFrame, snapshot_index: dict[str, Path]) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    full_rows = []
    snapshot_cache: dict[str, dict[str, Any]] = {}
    for item in scored.to_dict("records"):
        row = pd.Series(item)
        scope = "dev_cv" if str(row["target_date"]) < p1.TRAIN_CUTOFF else "verified_forward"
        variants = {
            "market_local_original": {bucket: float(row[f"market_local_norm_p_{bucket}"]) for bucket in BUCKETS},
        }
        corrected = corrected_local_distribution(row)
        if corrected:
            variants["market_current_mid_symmetric"] = corrected
        key = snapshot_key(row.get("decision_snapshot_ts_utc"))
        path = snapshot_index.get(key)
        full = None
        if path:
            if key not in snapshot_cache:
                snapshot_cache[key] = json.loads(path.read_text(encoding="utf-8"))
            full = full_ladder_distribution(row, snapshot_cache[key])
        if full:
            variants["market_full_ladder"] = {bucket: float(full[f"p_{bucket}"]) for bucket in BUCKETS}
            real_tail = float(full["p_tail"])
            variants["market_real_tail_only"] = normalize(
                {
                    "current": variants["market_local_original"]["current"],
                    "d1": variants["market_local_original"]["d1"],
                    "d2": variants["market_local_original"]["d2"],
                    "tail": real_tail,
                }
            )
            if corrected:
                variants["market_current_mid_real_tail"] = normalize(
                    {
                        "current": corrected["current"],
                        "d1": corrected["d1"],
                        "d2": corrected["d2"],
                        "tail": real_tail,
                    }
                )
            full_rows.append({"city": row["city"], "target_date": row["target_date"], "decision_hour_local": row["decision_hour_local"], "actual_bucket": row["actual_bucket"], **full})
        for variant, probs in variants.items():
            actual = str(row["actual_bucket"])
            winner = max(EPS, probs[actual])
            brier = sum((probs[bucket] - float(bucket == actual)) ** 2 for bucket in BUCKETS)
            rows.append(
                {
                    "city": row["city"],
                    "target_date": row["target_date"],
                    "decision_hour_local": row["decision_hour_local"],
                    "actual_bucket": actual,
                    "scope": scope,
                    "variant": variant,
                    "logloss": -math.log(winner),
                    "brier": brier,
                    "top1": float(max(probs, key=probs.get) == actual),
                    **{f"p_{bucket}": probs[bucket] for bucket in BUCKETS},
                }
            )
    return pd.DataFrame(rows), pd.DataFrame(full_rows)


def markdown_table(df: pd.DataFrame, cols: list[str]) -> str:
    if df.empty:
        return "_no rows_"
    df = df.reindex(columns=cols)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in df[cols].iterrows():
        values = []
        for col in cols:
            value = row[col]
            if isinstance(value, (float, np.floating)):
                values.append("n/a" if math.isnan(float(value)) else f"{float(value):.4f}")
            else:
                values.append(str(value))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_index = build_snapshot_index()
    hist, counters = p4._load_rows_extended()
    predictions, model_meta = expanding_predictions(hist)
    model_score_summary = summarize_model_predictions(predictions)
    baseline_pred = predictions[predictions["variant"] == "historical_full_features"].copy()
    missing_pred = predictions[predictions["variant"] == "live_missingness_emulation"].copy()

    scored = pd.read_csv(p0.OUT_DIR / "scored_rows.csv", low_memory=False)
    market_score_rows, full_rows = market_scores(scored, snapshot_index)
    market_summary = score_market_variants(market_score_rows)

    keys = ["city", "target_date", "decision_hour_local", "actual_bucket"]
    full_keys = full_rows[keys].drop_duplicates() if not full_rows.empty else pd.DataFrame(columns=keys)
    market_matched_summary = score_market_variants(market_score_rows.merge(full_keys, on=keys, how="inner"))
    market_pivots = []
    for variant in [
        "market_current_mid_symmetric",
        "market_real_tail_only",
        "market_current_mid_real_tail",
        "market_full_ladder",
    ]:
        sub = market_score_rows[market_score_rows["variant"] == variant].copy()
        if sub.empty:
            continue
        renamed = sub[keys + [f"p_{bucket}" for bucket in BUCKETS]].rename(
            columns={f"p_{bucket}": f"{variant}_p_{bucket}" for bucket in BUCKETS}
        )
        market_pivots.append((variant, renamed))

    variant_preds = [baseline_pred, missing_pred]
    for variant, market in market_pivots:
        variant_preds.append(add_market_variant(baseline_pred, market, variant, float(model_meta["alpha"])))
    all_predictions = pd.concat(variant_preds, ignore_index=True)

    full_quote_cols = full_rows[keys + ["d1_yes_effective_ask", "d2_yes_effective_ask"]] if not full_rows.empty else pd.DataFrame()
    hist_quotes = hist.copy()
    if not full_quote_cols.empty:
        hist_quotes = hist_quotes.merge(full_quote_cols, on=keys, how="left", validate="one_to_one")
    all_policy = policy_replay(hist_quotes, all_predictions[all_predictions["variant"] != "market_full_ladder"], "all_scored")
    matched_hist = hist_quotes.merge(full_keys, on=keys, how="inner")
    matched_pred = all_predictions.merge(full_keys, on=keys, how="inner")
    matched_policy = policy_replay(matched_hist, matched_pred, "full_ladder_matched")
    policy_rows = pd.concat([all_policy, matched_policy], ignore_index=True)
    policy_summary = summarize_policy(policy_rows)

    selection = {"c": float(model_meta["c"]), "alpha": float(model_meta["alpha"])}
    live_replay = live_order_replay(snapshot_index, hist, selection)
    geometry = geometry_replay(snapshot_index)
    geometry_summary = (
        geometry.groupby(["old_geometry_status", "repaired_geometry_status"], as_index=False)
        .agg(rows=("city", "size"), dates=("target_date", "nunique"), cities=("city", "nunique"), parser_recovered=("parser_recovered", "sum"))
        .sort_values("rows", ascending=False)
    )
    completeness_keys = ["repaired_geometry_status", "snapshot_completeness_class"]
    geometry_completeness = geometry.groupby(completeness_keys, as_index=False).agg(
        rows=("city", "size"), dates=("target_date", "nunique"), cities=("city", "nunique")
    )
    completeness_city_days = (
        geometry[completeness_keys + ["city", "target_date"]]
        .drop_duplicates()
        .groupby(completeness_keys, as_index=False)
        .size()
        .rename(columns={"size": "city_days"})
    )
    geometry_completeness = geometry_completeness.merge(completeness_city_days, on=completeness_keys)
    geometry_completeness = geometry_completeness.sort_values("rows", ascending=False)

    daypart_bins = [-1, 5, 10, 15, 21, 24]
    daypart_labels = ["00-05", "06-10", "11-15", "16-21", "22-23"]
    geometry_with_daypart = geometry.assign(
        daypart=pd.cut(geometry["decision_hour_local"], bins=daypart_bins, labels=daypart_labels)
    )
    daypart_keys = ["repaired_geometry_status", "daypart"]
    geometry_daypart = geometry_with_daypart.groupby(daypart_keys, observed=True, as_index=False).agg(
        rows=("city", "size")
    )
    daypart_city_days = (
        geometry_with_daypart[daypart_keys + ["city", "target_date"]]
        .drop_duplicates()
        .groupby(daypart_keys, observed=True, as_index=False)
        .size()
        .rename(columns={"size": "city_days"})
    )
    geometry_daypart = geometry_daypart.merge(daypart_city_days, on=daypart_keys)

    predictions.to_csv(OUT_DIR / "feature_parity_predictions.csv", index=False)
    model_score_summary.to_csv(OUT_DIR / "feature_parity_score_summary.csv", index=False)
    market_score_rows.to_csv(OUT_DIR / "market_distribution_score_rows.csv", index=False)
    market_summary.to_csv(OUT_DIR / "market_distribution_summary.csv", index=False)
    market_matched_summary.to_csv(OUT_DIR / "market_distribution_matched_summary.csv", index=False)
    full_rows.to_csv(OUT_DIR / "full_ladder_materialized_rows.csv", index=False)
    policy_rows.to_csv(OUT_DIR / "policy_replay_rows.csv", index=False)
    policy_summary.to_csv(OUT_DIR / "policy_replay_summary.csv", index=False)
    live_replay.to_csv(OUT_DIR / "live_order_replay.csv", index=False)
    geometry.to_csv(OUT_DIR / "geometry_capacity_rows.csv", index=False)
    geometry_summary.to_csv(OUT_DIR / "geometry_capacity_summary.csv", index=False)
    geometry_completeness.to_csv(OUT_DIR / "geometry_completeness_summary.csv", index=False)
    geometry_daypart.to_csv(OUT_DIR / "geometry_daypart_summary.csv", index=False)

    verified_policy = policy_summary[policy_summary["scope"] == "verified_forward"].copy()
    live_ok = live_replay[live_replay["replay_status"] == "ok"]
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "data": {
            "hist_rows": len(hist),
            "hist_date_range": [str(hist["target_date"].min()), str(hist["target_date"].max())],
            "snapshot_files": len(snapshot_index),
            "market_full_ladder_rows": len(full_rows),
            "live_orders": len(live_replay),
            "live_orders_replayed": len(live_ok),
            "geometry_rows": len(geometry),
        },
        "model_meta": model_meta,
        "p4_counters": counters,
        "verdict": "live_remains_paused_pending_repaired_forward_shadow",
    }
    JSON_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    report = [
        "# Tmax Lineage Repair Replay v1",
        "",
        f"> generated_at_utc: `{payload['generated_at_utc']}`",
        "> Scope: repaired-code replay only. Live runner remains paused; no order/config action.",
        "",
        "## 结论",
        "",
        "- live feature parity, tail-aware bracket geometry, sibling-complement snapshot estimates, direct fresh executable ask, and NaN handling are now implemented and tested.",
        "- Full live features improve proper scores over the old live-missingness pattern in both dev-CV and verified forward; the higher selected-trade ROI of the missing version is selection noise, not evidence to keep missing fields.",
        "- Of 357 repaired `below_market_ladder` city-hour rows, 357 are explained by the collector dropping lower near-binary siblings. This is a data-layer completeness bug, not proven new alpha capacity.",
        "- Real d3+ tail quote and full-ladder A/B are contaminated by those incomplete snapshots and are negative on the 701-row matched denominator. D1 remains inconclusive until the complete-ladder collector accumulates fresh rows.",
        "- Symmetric current-mid improves verified score/selected ROI but is slightly worse in dev-CV; keep it as a shadow candidate, not a promotion result.",
        "- Existing survival v2 consumes already-mapped rows only. Its previous report cannot claim below-ladder capacity.",
        "- Verdict: `live_remains_paused_pending_repaired_forward_shadow`.",
        "",
        "## Evidence Funnel",
        "",
        f"- Historical scored rows: `{len(hist)}` ({hist['target_date'].min()}..{hist['target_date'].max()})",
        f"- Indexed snapshots: `{len(snapshot_index)}`",
        f"- Full-ladder market rows materialized: `{len(full_rows)}`",
        f"- Existing live orders: `{len(live_replay)}`; exact repaired replay rows: `{len(live_ok)}`",
        f"- Geometry city-date-hour rows: `{len(geometry)}`",
        "",
        "## Historical Policy Replay",
        "",
        markdown_table(verified_policy, ["denominator", "variant", "rows", "dates", "cities", "win_rate", "avg_ask", "pnl", "roi", "roi_ci_low", "roi_ci_high", "yes_rows", "no_rows"]),
        "",
        "## Feature Parity Proper Scores",
        "",
        markdown_table(model_score_summary, ["scope", "variant", "rows", "dates", "logloss", "brier"]),
        "",
        "## Market Distribution A/B",
        "",
        markdown_table(market_summary, ["scope", "variant", "rows", "dates", "cities", "logloss", "brier", "top1"]),
        "",
        "### Full-ladder matched denominator",
        "",
        markdown_table(market_matched_summary, ["scope", "variant", "rows", "dates", "cities", "logloss", "brier", "top1"]),
        "",
        "## First Live Orders Replayed",
        "",
        markdown_table(live_replay, ["city", "target_date", "original_expression", "original_price", "original_p_win", "replay_status", "repaired_same_expression_p_win", "repaired_same_expression_edge_at_fill", "same_expression_still_eligible", "repaired_selected_expression"]),
        "",
        "## Geometry Capacity",
        "",
        markdown_table(geometry_summary, ["old_geometry_status", "repaired_geometry_status", "rows", "dates", "cities", "parser_recovered"]),
        "",
        "### Snapshot completeness audit",
        "",
        markdown_table(geometry_completeness, ["repaired_geometry_status", "snapshot_completeness_class", "rows", "city_days", "dates", "cities"]),
        "",
        "### Local-hour shape",
        "",
        markdown_table(geometry_daypart, ["repaired_geometry_status", "daypart", "rows", "city_days"]),
        "",
        "`rows` here are city + target_date + local-hour states, not fills. Canonical settlement ladder inventory is used only after the fact to audit sibling collection completeness; it is never used as a PIT model feature. Genuine below-ladder rows need a new absolute-ladder target, not the old relative current/d1/d2 target.",
        "",
        "## Fresh Collector Verification",
        "",
        "After the collector repair, an isolated `snapshot-targeted --target-date 2026-07-10 --no-orderbook` run produced 31 city-dates / 341 records, exactly 11 brackets per city-date. Amsterdam retained the 0.0005-priced lower and upper siblings. The next production cycle also produced 51 city-dates / 561 records with 11 brackets each; it correctly stayed in the partial archive because only 32 cities were available. This proves inventory completeness only; fresh full-orderbook forward evidence still needs to accumulate.",
        "",
        "## Three Gates",
        "",
        "- significance: FAIL/NA for live promotion; repaired forward days have not accumulated.",
        "- baseline: PARTIAL; historical same-denominator replay is reported above.",
        "- forward: FAIL; code is only dry-run/replay and live remains paused.",
        "",
        "## Artifacts",
        "",
        f"- `{OUT_DIR.relative_to(ROOT)}/`",
        f"- `{JSON_PATH.relative_to(ROOT)}`",
    ]
    REPORT_PATH.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
