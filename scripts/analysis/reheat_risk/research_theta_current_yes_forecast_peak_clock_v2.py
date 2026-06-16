#!/usr/bin/env python3
"""Forecast-peak-clock strategy design for current-bucket YES.

This is research-only.  It turns the fixed-hour peak-forming idea into a
forecast-relative rule and audits whether the current archive can honestly
backtest that rule.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "runtime/weather.db"
FEATURE_ROWS = ROOT / "docs/analysis/2026-06/generated/theta_yes_current_full_replay_v8/feature_rows.csv"
MODEL_ARTIFACT = ROOT / "docs/analysis/2026-06/generated/theta_yes_current_live_gate_v9/live_model.json"
CACHE_ROOT = ROOT / "runtime/weather_edge_v1/market_data/cache"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-16-theta-current-yes-forecast-peak-clock-v2.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-16-theta-current-yes-forecast-peak-clock-v2.md"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pct(value: float | None) -> str:
    if value is None or not math.isfinite(float(value)):
        return "NA"
    return f"{100 * float(value):+.1f}%"


def dollars(value: float | None) -> str:
    if value is None or not math.isfinite(float(value)):
        return "NA"
    return f"${float(value):.2f}"


def data_self_check() -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    conn.row_factory = sqlite3.Row
    try:
        return {
            "fact_built_at_utc": conn.execute("SELECT MAX(fact_built_at_utc) v FROM fact_trades").fetchone()["v"],
            "trade_class": [dict(r) for r in conn.execute("SELECT trade_class, COUNT(*) rows FROM fact_trades GROUP BY trade_class")],
            "settlement_status": [dict(r) for r in conn.execute("SELECT settlement_status, COUNT(*) rows FROM fact_trades GROUP BY settlement_status")],
            "signal_coverage": dict(
                conn.execute(
                    "SELECT COUNT(*) rows, SUM(eligible) eligible, SUM(paper_ordered) paper_ordered, SUM(live_filled) live_filled "
                    "FROM fact_signal_candidates"
                ).fetchone()
            ),
            "clob_orders_fills": [
                dict(r)
                for r in conn.execute(
                    "SELECT o.status, COUNT(*) orders, SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) with_fill "
                    "FROM orders o LEFT JOIN fills f USING(execution_id) WHERE o.venue='polymarket_clob' GROUP BY o.status"
                )
            ],
            "fact_signal_candidate_forecast_columns": [
                r["name"]
                for r in conn.execute("PRAGMA table_info(fact_signal_candidates)")
                if "forecast" in str(r["name"]).lower() or "peak" in str(r["name"]).lower()
            ],
        }
    finally:
        conn.close()


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_ready(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def local_hour_from_utc_like(ts: str, offset_hours: int) -> int | None:
    text = str(ts).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    if "+" not in text and text.count(":") == 1:
        text = f"{text}:00+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int((dt.astimezone(timezone.utc).hour + offset_hours) % 24)


def peak_rows_for_cache(path: Path, source: str) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    hourly = data.get("hourly") if isinstance(data, dict) else None
    if not isinstance(hourly, dict):
        return []
    times = hourly.get("time") or []
    temps = hourly.get("temperature_2m") or []
    if not times or not temps or len(times) != len(temps):
        return []
    stem = path.stem
    city = stem
    prefix = f"{source}_"
    if city.startswith(prefix):
        city = city[len(prefix) :]
    city = city.rsplit("_", 2)[0]
    by_date: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for ts, temp in zip(times, temps, strict=False):
        try:
            value = float(temp)
        except Exception:
            continue
        by_date[str(ts)[:10]].append((str(ts), value))
    out = []
    for date, rows in by_date.items():
        max_temp = max(v for _, v in rows)
        peak_ts = min(ts for ts, v in rows if abs(v - max_temp) < 1e-9)
        out.append(
            {
                "source": source,
                "city": city,
                "target_date": date,
                "forecast_max_f": max_temp,
                "forecast_peak_hour_utc": int(str(peak_ts)[11:13]),
                "source_file": str(path.relative_to(ROOT)),
            }
        )
    return out


def audit_forecast_cache() -> dict[str, Any]:
    sources = {
        "gfs_v4": CACHE_ROOT / "gfs_v4",
        "ecmwf_v4": CACHE_ROOT / "ecmwf_v4",
        "hrrr_v5": CACHE_ROOT / "hrrr_v5",
    }
    rows: list[dict[str, Any]] = []
    source_summary: dict[str, Any] = {}
    for source, folder in sources.items():
        files = sorted(folder.glob("*.json")) if folder.exists() else []
        n_hourly = 0
        dates: list[str] = []
        peak_counter: Counter[int] = Counter()
        for path in files:
            try:
                derived = peak_rows_for_cache(path, source)
            except Exception:
                derived = []
            if derived:
                n_hourly += 1
                rows.extend(derived)
                dates.extend(r["target_date"] for r in derived)
                peak_counter.update(int(r["forecast_peak_hour_utc"]) for r in derived)
        source_summary[source] = {
            "files": len(files),
            "hourly_files": n_hourly,
            "min_date": min(dates) if dates else None,
            "max_date": max(dates) if dates else None,
            "derived_city_dates": len(dates),
            "top_peak_hours_utc": [{"hour": h, "rows": c} for h, c in peak_counter.most_common(8)],
        }
    peak_df = pd.DataFrame(rows)
    replay_dates = set(pd.read_csv(FEATURE_ROWS, usecols=["target_date"])["target_date"].astype(str).unique())
    cache_dates = set(peak_df["target_date"].astype(str).unique()) if not peak_df.empty else set()
    overlap = sorted(replay_dates & cache_dates)
    return {
        "source_summary": source_summary,
        "derived_peak_rows": int(len(peak_df)),
        "replay_min_date": min(replay_dates) if replay_dates else None,
        "replay_max_date": max(replay_dates) if replay_dates else None,
        "cache_min_date": min(cache_dates) if cache_dates else None,
        "cache_max_date": max(cache_dates) if cache_dates else None,
        "replay_cache_overlap_dates": overlap[:20],
        "replay_cache_overlap_date_count": len(overlap),
        "can_backtest_current_yes_v8_with_forecast_clock": bool(overlap),
    }


def score_rows(rows: pd.DataFrame, artifact: dict[str, Any]) -> np.ndarray:
    numeric_features = artifact["numeric_features"]
    categorical_features = artifact["categorical_features"]
    numeric = rows[numeric_features].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    medians = np.asarray(artifact["numeric_medians"], dtype=float)
    means = np.asarray(artifact["numeric_means"], dtype=float)
    scales = np.asarray(artifact["numeric_scales"], dtype=float)
    numeric = np.where(np.isfinite(numeric), numeric, medians)
    numeric = (numeric - means) / scales
    cat_parts = []
    for idx, feature in enumerate(categorical_features):
        values = rows[feature].astype(str).to_numpy()
        cats = [str(x) for x in artifact["categories"][idx]]
        lookup = {cat: col for col, cat in enumerate(cats)}
        mat = np.zeros((len(rows), len(cats)), dtype=float)
        for row_idx, value in enumerate(values):
            col = lookup.get(str(value))
            if col is not None:
                mat[row_idx, col] = 1.0
        cat_parts.append(mat)
    transformed = np.concatenate([numeric, *cat_parts], axis=1)
    logits = transformed @ np.asarray(artifact["coef"], dtype=float) + float(artifact["intercept"])
    return 1.0 / (1.0 + np.exp(-logits))


def load_scored() -> pd.DataFrame:
    df = pd.read_csv(FEATURE_ROWS)
    artifact = json.loads(MODEL_ARTIFACT.read_text(encoding="utf-8"))
    df["p_yes_win"] = score_rows(df, artifact)
    df["edge_snapshot"] = df["p_yes_win"] - df["yes_current_ask"].astype(float)
    df["available_notional_at_ask"] = df["yes_current_ask"].astype(float) * df["yes_current_size"].astype(float)
    df["snapshot_dt"] = pd.to_datetime(df["snapshot_ts_utc"], utc=True, errors="coerce")
    return df


def dedupe_live(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    return (
        frame.sort_values("snapshot_dt")
        .drop_duplicates(["target_date", "city", "current_bracket"], keep="first")
        .sort_values(["target_date", "city", "snapshot_dt"])
        .groupby(["target_date", "city"])
        .head(2)
        .copy()
    )


def summarize(frame: pd.DataFrame, *, taker_cushion: float = 0.02) -> dict[str, Any]:
    d = dedupe_live(frame)
    if d.empty:
        return {"orders": 0, "active_dates": 0, "cities": 0}
    notional = 5.0
    price = np.minimum(d["yes_current_ask"].astype(float).to_numpy() + taker_cushion, 0.999)
    label = d["label_yes_wins"].astype(int).to_numpy()
    p = d["p_yes_win"].to_numpy()
    pnl = np.where(label == 1, notional / price - notional, -notional)
    ev = notional * (p / price - 1.0)
    return {
        "orders": int(len(d)),
        "active_dates": int(d["target_date"].nunique()),
        "cities": int(d["city"].nunique()),
        "notional": float(notional * len(d)),
        "orders_per_active_day": float(len(d) / d["target_date"].nunique()),
        "win_rate": float(label.mean()),
        "roi_plus_2c": float(pnl.sum() / (notional * len(d))),
        "pnl_plus_2c": float(pnl.sum()),
        "model_ev_plus_2c": float(ev.sum()),
        "model_ev_per_order": float(ev.mean()),
        "avg_ask": float(d["yes_current_ask"].mean()),
        "avg_p_yes_win": float(d["p_yes_win"].mean()),
        "avg_snapshot_edge": float(d["edge_snapshot"].mean()),
        "date_min": str(d["target_date"].min()),
        "date_max": str(d["target_date"].max()),
    }


def proxy_peak_forming_summaries() -> dict[str, Any]:
    scored = load_scored()
    holdout = scored[scored["period"].eq("holdout")].copy()
    base = holdout[
        holdout["has_d1_no"].astype(bool)
        & holdout["decision_hour_local"].between(12, 16)
        & holdout["yes_current_ask"].ge(0.55)
        & holdout["available_notional_at_ask"].ge(5.0)
        & holdout["p_yes_win"].ge(0.5)
    ].copy()
    slices = {
        "current_live_post_decline_proxy": base[
            base["decline_c"].ge(0.5) & base["decision_hour_local"].between(13, 15) & base["edge_snapshot"].ge(0.05)
        ],
        "old_fixed_h13_peak_forming_proxy": base[
            base["decline_c"].lt(0.01) & base["decision_hour_local"].eq(13) & base["edge_snapshot"].ge(0.05)
        ],
        "too_broad_peak_forming_proxy": base[
            base["decline_c"].lt(0.01) & base["edge_snapshot"].ge(0.05)
        ],
    }
    return {name: summarize(frame) for name, frame in slices.items()}


def md_summary_table(rows: dict[str, Any]) -> list[str]:
    out = [
        "| slice | orders | active days | orders/day | win | ROI +2c | model EV | avg ask | avg p | avg edge |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, row in rows.items():
        out.append(
            "| {name} | {orders} | {days} | {opd} | {win} | {roi} | {ev} | {ask} | {p} | {edge} |".format(
                name=name,
                orders=row.get("orders", 0),
                days=row.get("active_dates", 0),
                opd="NA" if row.get("orders_per_active_day") is None else f"{row['orders_per_active_day']:.1f}",
                win=pct(row.get("win_rate")),
                roi=pct(row.get("roi_plus_2c")),
                ev=dollars(row.get("model_ev_plus_2c")),
                ask="NA" if row.get("avg_ask") is None else f"{row['avg_ask']:.3f}",
                p="NA" if row.get("avg_p_yes_win") is None else f"{row['avg_p_yes_win']:.3f}",
                edge=pct(row.get("avg_snapshot_edge")),
            )
        )
    return out


def write_report(payload: dict[str, Any]) -> None:
    data = payload["data_self_check"]
    cache = payload["forecast_cache_audit"]
    proxy = payload["fixed_hour_proxy_results"]
    lines = [
        "# Theta Current YES Forecast Peak Clock v2",
        "",
        "Status: strategy_design / shadow_candidate_pending_data",
        f"Generated: {payload['generated_at_utc']}",
        "",
        "Target metric: `forecast_peak_clock_current_yes` = buy the current running-max bracket YES while the temperature has not visibly faded, but only when the forecast peak clock says the day is already at or near its expected high.",
        "",
        "## Data Self-Check",
        "",
        f"- fact_built_at_utc: `{data['fact_built_at_utc']}`",
        f"- fact_trades trade_class: `{data['trade_class']}`",
        f"- settlement_status: `{data['settlement_status']}`",
        f"- fact_signal_candidates coverage: `{data['signal_coverage']}`",
        f"- CLOB orders/fills join: `{data['clob_orders_fills']}`",
        f"- forecast-related fact columns: `{data['fact_signal_candidate_forecast_columns']}`",
        "",
        "## Human Verdict",
        "",
        "你这个方向是对的：`h13` 不应该是策略本体，它只是“接近预报峰值时段”的粗糙代理。真正的新策略应该看 forecast 给出的最高温小时和最高温 bracket。",
        "",
        "但现有 6 月 current-YES replay 不能诚实回测这个字段：当时 snapshot 只保存了 `forecast_max_f`，没有保存 `forecast_peak_hour_local` 或 hourly forecast vector。历史 forecast cache 有小时曲线，但只覆盖到 2026-05-06；current-YES 回放从 2026-05-19 开始，所以 overlap=0。",
        "",
        "因此这版结论不是 live-ready，而是一版清晰可执行的 shadow 策略定义：先把 `forecast_peak_hour_local` 落盘，再用同一套回放验证它是否真的优于固定 `h13`。",
        "",
        "## Existing Proxy Evidence",
        "",
        *md_summary_table(proxy),
        "",
        "读法：`old_fixed_h13_peak_forming_proxy` 是旧的固定小时代理，不是最终规则。它说明“峰值形成中”可能有机会；`too_broad_peak_forming_proxy` 说明不能只看 `decline_c == 0`，太宽会把 h14/h15 的坏样本也吃进去。",
        "",
        "## Forecast Cache Audit",
        "",
        f"- replay date range: `{cache['replay_min_date']}..{cache['replay_max_date']}`",
        f"- forecast cache date range: `{cache['cache_min_date']}..{cache['cache_max_date']}`",
        f"- replay/cache overlap dates: `{cache['replay_cache_overlap_date_count']}`",
        f"- can backtest v8 with forecast clock now: `{cache['can_backtest_current_yes_v8_with_forecast_clock']}`",
        "",
        "| source | files | hourly files | min date | max date | derived city-days | top UTC peak hours |",
        "|---|---:|---:|---|---|---:|---|",
    ]
    for source, row in cache["source_summary"].items():
        top_hours = ", ".join(f"{x['hour']}:{x['rows']}" for x in row["top_peak_hours_utc"][:5])
        lines.append(
            f"| {source} | {row['files']} | {row['hourly_files']} | {row['min_date']} | {row['max_date']} | {row['derived_city_dates']} | {top_hours} |"
        )
    lines.extend(
        [
            "",
            "## Strategy v2 Rule",
            "",
            "```text",
            "branch: theta_current_yes_forecast_peak_clock_v2",
            "side: BUY_YES current running-max bracket",
            "state: decline_c <= 0.1C, i.e. still at observed running max / plateau",
            "forecast clock: forecast_peak_hour_local is present",
            "relative timing: -1 <= decision_hour_local - forecast_peak_hour_local <= +1",
            "forecast bracket: forecast_max_native is inside current YES bracket, or forecast_max_native - running_native <= 0.5C equivalent",
            "market: yes_current_ask >= 0.55 and available_notional_at_ask >= $5",
            "model: p_yes_win >= 0.5 and p_yes_win - snapshot_ask >= 0.05",
            "execution: before submit, refresh CLOB book; require fresh_ask <= snapshot_ask + 0.02 and top ask notional >= $5",
            "dedupe: first eligible city-date-current bracket; max 2 brackets per city-date; max $5/order, $10/city-day in shadow only",
            "```",
            "",
            "Why these gates exist:",
            "",
            "- `decline_c <= 0.1C` keeps this branch separate from the already-live post-decline branch.",
            "- `decision_hour - forecast_peak_hour` replaces hardcoded local time. A Shanghai 13:00 and a Phoenix 15:00 can both be valid if they are near that city's forecast peak.",
            "- `forecast_max_native inside current bracket` is the core physical condition: the forecast is not calling for a later bracket jump.",
            "- The fresh-book guard handles the METAR update minute problem: if the quote reprices before we can hit it, the order is skipped.",
            "",
            "## Required Data Change",
            "",
            "每个 paper snapshot / live decision row 至少要新增这些字段：",
            "",
            "- `forecast_peak_hour_local`",
            "- `forecast_peak_hour_utc`",
            "- `forecast_max_native`",
            "- `forecast_peak_source` / `forecast_model_run_ts_utc`",
            "- `forecast_values_hash`",
            "- optional: `forecast_peak_hour_spread_minutes` for multi-model disagreement",
            "",
            "没有这些字段，固定 h13 看起来赚钱也只能叫 proxy，不能叫 forecast-clock 策略过门。",
            "",
            "## 8-Ring Coverage",
            "",
            "- descriptive performance: partial, via fixed-hour proxy only",
            "- statistical inference: not rerun here; use v1/v9 reports for fixed-hour proxy",
            "- signal discrimination: partial, existing current-YES model only",
            "- probability calibration: inherited from v10, not changed here",
            "- execution microstructure: design includes fresh-book guard; no filled evidence yet",
            "- capacity: shadow limit only, no live capacity claim",
            "- portfolio correlation: not evaluated",
            "- baseline/counterfactual: pending after forecast fields are logged",
            "",
            "Conclusion gates: significance=NA, baseline=NA, forward=NA, conclusion=`inconclusive` for live; `shadow_candidate_pending_data` for telemetry.",
            "",
            "## Output",
            "",
            f"- JSON: `{OUT_JSON.relative_to(ROOT)}`",
            f"- Script: `{Path(__file__).resolve().relative_to(ROOT)}`",
        ]
    )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    payload = {
        "generated_at_utc": now_utc(),
        "evidence_layer": "strategy design plus fixed-hour proxy; not a true forecast-clock backtest",
        "row_grain": "proxy summaries are live-style deduped current YES order candidates; cache rows are source-city-date forecast days",
        "data_self_check": data_self_check(),
        "forecast_cache_audit": audit_forecast_cache(),
        "fixed_hour_proxy_results": proxy_peak_forming_summaries(),
        "strategy_rule": {
            "branch": "theta_current_yes_forecast_peak_clock_v2",
            "side": "BUY_YES",
            "bracket": "current running-max bracket",
            "decline_c_max": 0.1,
            "relative_timing_hours": [-1, 1],
            "forecast_condition": "forecast_max_native inside current bracket or not above running_native by more than 0.5C equivalent",
            "min_yes_ask": 0.55,
            "min_available_notional_at_ask": 5.0,
            "min_snapshot_edge": 0.05,
            "max_taker_cushion": 0.02,
            "status": "shadow_candidate_pending_forecast_peak_fields",
        },
    }
    payload = json_ready(payload)
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_report(payload)
    print(json.dumps({"json": str(OUT_JSON), "md": str(OUT_MD)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
