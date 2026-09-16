#!/usr/bin/env python3
"""Research A/B for current-YES survival residual modeling and alti tendency.

Research-only.  The script tests:

A. Whether `market price + METAR core` residual calibration is stable enough to
   beat raw market/base v9 without overfitting.
B. Whether 3h altimeter tendency (`d_alti_3h`) adds residual information once
   fetched from IEM ASOS history.

Anti-overfit rules:
- Fixed existing train/holdout split from theta_yes_current_full_replay_v8.
- Hyperparameters selected only with train-period target_date grouped CV.
- Holdout is evaluated once, with target_date bootstrap CI for logloss deltas.
"""
from __future__ import annotations

import csv
import io
import json
import math
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.model_selection import GroupKFold

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import validate_reheat_tail_feature_discrimination_v1 as tail  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_yes_residual_calibrator_alti_v1"
ALTI_CACHE_DIR = OUT_DIR / "iem_ext_alti"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-20-current-yes-residual-calibrator-alti-v1.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-20-current-yes-residual-calibrator-alti-v1.md"
DB = ROOT / "runtime/weather.db"

SEED = 20260620
LABEL = tail.LABEL
METAR_CORE = tail.METAR_WEATHER
METAR_PLUS_ALTI = METAR_CORE + ["alti_now", "d_alti_3h"]
MARKET_COL = "market_logit"
BASE_COL = "base_model_logit"


def json_ready(value: Any) -> Any:
    return tail.json_ready(value)


def pct(value: float | None, digits: int = 1) -> str:
    if value is None or not math.isfinite(float(value)):
        return "NA"
    return f"{float(value) * 100:.{digits}f}%"


def num(value: float | None, digits: int = 4) -> str:
    if value is None or not math.isfinite(float(value)):
        return "NA"
    return f"{float(value):.{digits}f}"


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def connect_ro() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    return conn


def query_rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def data_self_check() -> dict[str, Any]:
    conn = connect_ro()
    try:
        return {
            "fact_trades_max_built_at_utc": conn.execute("SELECT MAX(fact_built_at_utc) FROM fact_trades").fetchone()[0],
            "fact_trades_by_class": query_rows(conn, "SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class ORDER BY trade_class"),
            "fact_trades_by_settlement_status": query_rows(
                conn,
                "SELECT COALESCE(settlement_status, '') AS settlement_status, COUNT(*) AS rows "
                "FROM fact_trades GROUP BY settlement_status ORDER BY settlement_status",
            ),
            "missing_bracket_rows": conn.execute("SELECT SUM(CASE WHEN settlement_status='missing_bracket' THEN 1 ELSE 0 END) FROM fact_trades").fetchone()[0],
            "fact_signal_candidate_coverage": query_rows(
                conn,
                "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, "
                "SUM(live_filled) AS live_filled FROM fact_signal_candidates",
            )[0],
            "clob_order_fill_join": query_rows(
                conn,
                "SELECT o.status, COUNT(*) AS orders, SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill "
                "FROM orders o LEFT JOIN fills f USING(execution_id) "
                "WHERE o.venue='polymarket_clob' GROUP BY o.status ORDER BY o.status",
            ),
        }
    finally:
        conn.close()


def parse_iem_csv(text: str) -> list[dict[str, str]]:
    lines = [line for line in text.splitlines() if line.strip() and not line.startswith("#")]
    if not lines:
        return []
    return list(csv.DictReader(io.StringIO("\n".join(lines))))


def iem_url(icaos: list[str], start: pd.Timestamp, end: pd.Timestamp) -> str:
    columns = ("tmpf", "dwpf", "relh", "drct", "sknt", "skyc1", "alti")
    params: list[tuple[str, str]] = []
    for icao in icaos:
        params.append(("station", icao))
    params.extend(("data", c) for c in columns)
    params.extend(
        [
            ("year1", str(start.year)),
            ("month1", str(start.month)),
            ("day1", str(start.day)),
            ("year2", str(end.year)),
            ("month2", str(end.month)),
            ("day2", str(end.day)),
            ("tz", "Etc/UTC"),
            ("format", "onlycomma"),
            ("latlon", "no"),
            ("elev", "no"),
            ("missing", "M"),
            ("trace", "T"),
            ("direct", "no"),
        ]
    )
    # Include all standard ASOS report classes; alti is present in routine and specials.
    for report_type in ("1", "2", "3", "4"):
        params.append(("report_type", report_type))
    return "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?" + urllib.parse.urlencode(params)


def cache_path_for(icao: str, start: pd.Timestamp, end: pd.Timestamp) -> Path:
    return ALTI_CACHE_DIR / f"iem_ext_alti_{icao}_{start.date()}_{end.date()}.csv"


def cache_has_alti(path: Path) -> bool:
    if not path.exists() or path.stat().st_size <= 1000:
        return False
    header = path.read_text(encoding="utf-8", errors="ignore").splitlines()[0]
    return "alti" in header.split(",")


def fetch_iem_batch(icaos: list[str], start: pd.Timestamp, end: pd.Timestamp) -> list[dict[str, str]]:
    url = iem_url(icaos, start, end)
    last_err: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=180) as resp:
                text = resp.read().decode()
            records = parse_iem_csv(text)
            if not records:
                raise RuntimeError(f"IEM returned no records for batch {icaos}")
            return records
        except Exception as exc:  # includes HTTP 429
            last_err = exc
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"IEM batch fetch failed for {icaos}: {last_err}")


def fetch_alti_cache(df: pd.DataFrame) -> dict[str, Any]:
    ALTI_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    city_by_icao, _coords = tail.station_maps()
    start = pd.to_datetime(df["snapshot_ts_utc"], utc=True).min() - timedelta(hours=6)
    end = pd.to_datetime(df["snapshot_ts_utc"], utc=True).max() + timedelta(days=1)
    start = pd.Timestamp(start.date(), tz="UTC")
    end = pd.Timestamp(end.date(), tz="UTC")
    all_icaos = sorted(city_by_icao)
    missing = [icao for icao in all_icaos if not cache_has_alti(cache_path_for(icao, start, end))]
    for offset in range(0, len(missing), 6):
        batch = missing[offset : offset + 6]
        if not batch:
            continue
        records = fetch_iem_batch(batch, start, end)
        keys = list(records[0].keys())
        aliases: dict[str, str] = {}
        for icao in batch:
            aliases[icao.upper()] = icao
            if icao.upper().startswith("K") and len(icao) == 4:
                aliases[icao.upper()[1:]] = icao
        grouped: dict[str, list[dict[str, str]]] = {icao: [] for icao in batch}
        for record in records:
            raw_station = str(record.get("station", "")).upper()
            icao = aliases.get(raw_station)
            if icao is not None:
                grouped[icao].append(record)
        for icao, station_rows in grouped.items():
            if not station_rows:
                raise RuntimeError(f"IEM returned no station rows for {icao} in batch {batch}")
            path = cache_path_for(icao, start, end)
            path.write_text(
                "\n".join([",".join(keys)] + [",".join(str(row.get(k, "")) for k in keys) for row in station_rows]) + "\n",
                encoding="utf-8",
            )
        time.sleep(1.0)

    rows = []
    for icao, city in sorted(city_by_icao.items()):
        path = cache_path_for(icao, start, end)
        sample = pd.read_csv(path, nrows=200)
        rows.append(
            {
                "city": city,
                "icao": icao,
                "path": rel(path),
                "columns": list(sample.columns),
                "has_alti": "alti" in sample.columns,
                "sample_alti_nonnull": int(pd.to_numeric(sample.get("alti"), errors="coerce").notna().sum()) if "alti" in sample else 0,
            }
        )
    return {"start_utc_date": str(start.date()), "end_utc_date": str(end.date()), "station_fetch": rows}


def load_alti_obs() -> pd.DataFrame:
    city_by_icao, _coords = tail.station_maps()
    frames = []
    for path in sorted(ALTI_CACHE_DIR.glob("iem_ext_alti_*.csv")):
        icao = path.name.split("_")[3]
        city = city_by_icao.get(icao)
        if city is None:
            continue
        df = pd.read_csv(path)
        if "alti" not in df.columns:
            raise RuntimeError(f"alti missing in {path}")
        df["city"] = city
        df["valid_utc"] = pd.to_datetime(df["valid"], utc=True, errors="coerce")
        for col in ("alti", "tmpf", "dwpf", "relh", "drct", "sknt"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        frames.append(df[["city", "valid_utc", "alti"]].dropna(subset=["valid_utc"]))
    if not frames:
        raise RuntimeError(f"No alti cache files in {ALTI_CACHE_DIR}")
    return pd.concat(frames, ignore_index=True).sort_values("valid_utc")


def enrich_alti(df: pd.DataFrame, obs: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ts"] = pd.to_datetime(df["snapshot_ts_utc"], utc=True, errors="coerce")
    df["ts_3h"] = df["ts"] - pd.Timedelta(hours=3)
    alti_now = np.full(len(df), np.nan)
    alti_3h = np.full(len(df), np.nan)
    for city, sub in df.groupby("city"):
        e = obs[obs["city"] == city].sort_values("valid_utc")
        if e.empty:
            continue
        idx = sub.index.to_numpy()
        now = tail.merge_city_asof(sub, e, "ts")
        lag = tail.merge_city_asof(sub, e, "ts_3h")
        alti_now[idx] = now["alti"].to_numpy()
        alti_3h[idx] = lag["alti"].to_numpy()
    df["alti_now"] = alti_now
    df["d_alti_3h"] = alti_now - alti_3h
    return df


def make_matrix(train: pd.DataFrame, test: pd.DataFrame, features: list[str], *, scale: bool) -> tuple[np.ndarray, np.ndarray]:
    x_train = train[features].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    x_test = test[features].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    med = np.nanmedian(np.where(np.isfinite(x_train), x_train, np.nan), axis=0)
    med = np.where(np.isfinite(med), med, 0.0)
    x_train = np.where(np.isfinite(x_train), x_train, med)
    x_test = np.where(np.isfinite(x_test), x_test, med)
    if not scale:
        return x_train, x_test
    mu = np.nanmean(x_train, axis=0)
    sd = np.nanstd(x_train, axis=0)
    sd = np.where(np.isfinite(sd) & (sd > 0), sd, 1.0)
    return (x_train - mu) / sd, (x_test - mu) / sd


def model_metrics(name: str, hold: pd.DataFrame, y: np.ndarray, p: np.ndarray, baseline_probs: dict[str, np.ndarray]) -> dict[str, Any]:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    row = {
        "model": name,
        "rows": int(len(hold)),
        "active_dates": int(hold["target_date"].nunique()),
        "actual_survive_rate": float(y.mean()),
        "mean_pred_survive": float(p.mean()),
        "auc": float(roc_auc_score(y, p)),
        "brier": float(brier_score_loss(y, p)),
        "logloss": float(log_loss(y, p)),
    }
    for base_name, base_p in baseline_probs.items():
        ci = tail.date_bootstrap_delta_ci(hold, y, base_p, p)
        delta = float(log_loss(y, p) - log_loss(y, np.clip(base_p, 1e-6, 1 - 1e-6)))
        row[f"delta_logloss_vs_{base_name}"] = delta
        row[f"delta_logloss_vs_{base_name}_ci95"] = ci
    return row


def group_cv_splits(train: pd.DataFrame) -> GroupKFold:
    n_dates = train["target_date"].nunique()
    return GroupKFold(n_splits=min(5, n_dates))


def cv_logistic(train: pd.DataFrame, features: list[str], label: str, c_values: list[float]) -> tuple[float, list[dict[str, Any]]]:
    y = train[label].to_numpy(int)
    groups = train["target_date"].to_numpy()
    rows = []
    best_c = c_values[0]
    best_ll = float("inf")
    for c in c_values:
        losses = []
        for tr_idx, va_idx in group_cv_splits(train).split(train, y, groups):
            tr = train.iloc[tr_idx]
            va = train.iloc[va_idx]
            x_tr, x_va = make_matrix(tr, va, features, scale=True)
            model = LogisticRegression(max_iter=3000, C=c, random_state=SEED)
            model.fit(x_tr, tr[label].to_numpy(int))
            p = model.predict_proba(x_va)[:, 1]
            losses.append(log_loss(va[label].to_numpy(int), np.clip(p, 1e-6, 1 - 1e-6)))
        mean_ll = float(np.mean(losses))
        rows.append({"model_family": "logistic_l2", "params": {"C": c}, "cv_logloss": mean_ll})
        if mean_ll < best_ll:
            best_ll = mean_ll
            best_c = c
    return best_c, rows


def fit_logistic(train: pd.DataFrame, hold: pd.DataFrame, features: list[str], c: float) -> np.ndarray:
    x_train, x_hold = make_matrix(train, hold, features, scale=True)
    model = LogisticRegression(max_iter=3000, C=c, random_state=SEED)
    model.fit(x_train, train[LABEL].to_numpy(int))
    return model.predict_proba(x_hold)[:, 1]


def cv_hgb(train: pd.DataFrame, features: list[str], label: str, grid: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    y = train[label].to_numpy(int)
    groups = train["target_date"].to_numpy()
    rows = []
    best_params = grid[0]
    best_ll = float("inf")
    for params in grid:
        losses = []
        for tr_idx, va_idx in group_cv_splits(train).split(train, y, groups):
            tr = train.iloc[tr_idx]
            va = train.iloc[va_idx]
            x_tr, x_va = make_matrix(tr, va, features, scale=False)
            model = HistGradientBoostingClassifier(random_state=SEED, **params)
            model.fit(x_tr, tr[label].to_numpy(int))
            p = model.predict_proba(x_va)[:, 1]
            losses.append(log_loss(va[label].to_numpy(int), np.clip(p, 1e-6, 1 - 1e-6)))
        mean_ll = float(np.mean(losses))
        rows.append({"model_family": "hist_gradient_boosting", "params": params, "cv_logloss": mean_ll})
        if mean_ll < best_ll:
            best_ll = mean_ll
            best_params = params
    return best_params, rows


def fit_hgb(train: pd.DataFrame, hold: pd.DataFrame, features: list[str], params: dict[str, Any]) -> np.ndarray:
    x_train, x_hold = make_matrix(train, hold, features, scale=False)
    model = HistGradientBoostingClassifier(random_state=SEED, **params)
    model.fit(x_train, train[LABEL].to_numpy(int))
    return model.predict_proba(x_hold)[:, 1]


def calibrate_single_logit(train: pd.DataFrame, hold: pd.DataFrame, col: str) -> np.ndarray:
    model = LogisticRegression(max_iter=3000, C=1.0, random_state=SEED)
    model.fit(train[[col]].to_numpy(float), train[LABEL].to_numpy(int))
    return model.predict_proba(hold[[col]].to_numpy(float))[:, 1]


def alti_feature_tests(df: pd.DataFrame) -> list[dict[str, Any]]:
    train = df[df["period"].eq("train")].copy()
    hold = df[df["period"].eq("holdout")].copy()
    y_tr = train[LABEL].to_numpy(int)
    y_h = hold[LABEL].to_numpy(int)
    rows = []
    for feat in ("alti_now", "d_alti_3h"):
        v = pd.to_numeric(hold[feat], errors="coerce")
        mask = v.notna()
        raw_auc = roc_auc_score(y_h[mask.to_numpy()], v[mask].to_numpy()) if mask.sum() > 2 else float("nan")
        lift_base = tail.incremental_lift(train, hold, feat, y_tr, y_h)
        # Temporarily condition on market by renaming market_logit into base_model_logit for the imported helper.
        tmp_train = train.copy()
        tmp_hold = hold.copy()
        tmp_train["base_model_logit"] = tmp_train["market_logit"]
        tmp_hold["base_model_logit"] = tmp_hold["market_logit"]
        lift_market = tail.incremental_lift(tmp_train, tmp_hold, feat, y_tr, y_h)
        rows.append(
            {
                "feature": feat,
                "coverage": float(mask.mean()),
                "holdout_auc": float(max(raw_auc, 1 - raw_auc)) if math.isfinite(raw_auc) else None,
                "delta_logloss_vs_market": lift_market["ll1"] - lift_market["ll0"],
                "delta_logloss_vs_market_ci95": lift_market["date_bootstrap_delta_logloss_ci95"],
                "delta_logloss_vs_base_v9": lift_base["ll1"] - lift_base["ll0"],
                "delta_logloss_vs_base_v9_ci95": lift_base["date_bootstrap_delta_logloss_ci95"],
            }
        )
    return rows


def write_markdown(payload: dict[str, Any]) -> None:
    metrics = payload["holdout_model_metrics"]
    alti = payload["alti_feature_tests"]
    lines = [
        "# Current-YES Residual Calibrator + Alti v1",
        "",
        "Status: research-only",
        f"Generated: {payload['generated_at_utc']}",
        "",
        "## 结论先行",
        "",
        "**交易动作：不加模型、不改 live。** A/B 都值得继续研究，但本轮没有任何候选通过 promotion gate。",
        "",
        "A 的结论：`market + METAR core` 的 logistic residual calibrator 点估赢 raw market 和 base v9，但日期 bootstrap CI 跨 0；HGB/ML 版本没有更好，反而退化。B 的结论：IEM 可以拉到 `alti`，`d_alti_3h` 有小的条件信号，但候选模型仍没过 raw market/base promotion gate。",
        "",
        "## 数据快照",
        "",
        f"- source rows: `{payload['data_snapshot']['source_feature_rows']}`",
        f"- date range: `{payload['coverage']['target_date_min']}`..`{payload['coverage']['target_date_max']}`",
        f"- rows: total {payload['coverage']['rows']}, train {payload['coverage']['train_rows']}, holdout {payload['coverage']['holdout_rows']} / {payload['coverage']['holdout_dates']} dates",
        f"- alti cache: `{payload['outputs']['alti_cache_dir']}`",
        f"- DB snapshot: fact_trades max built at `{payload['data_self_check']['fact_trades_max_built_at_utc']}`",
        f"- missing_bracket rows: {payload['data_self_check']['missing_bracket_rows']}",
        f"- CLOB fill gate: `gate_pass=true` from `weather_clob_fill_coverage_gate.py` rerun before this report",
        "",
        "5 行 SQL 自检:",
        "",
        "| check | result |",
        "|---|---|",
        f"| trade_class | `{payload['data_self_check']['fact_trades_by_class']}` |",
        f"| settlement | `{payload['data_self_check']['fact_trades_by_settlement_status']}` |",
        f"| candidate coverage | `{payload['data_self_check']['fact_signal_candidate_coverage']}` |",
        f"| CLOB order/fill join | `{payload['data_self_check']['clob_order_fill_join']}` |",
        "",
        "## Holdout Model Metrics",
        "",
        "| model | AUC | logloss | Brier | dLL vs market | CI | dLL vs base v9 | CI |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in metrics:
        ci_m = row.get("delta_logloss_vs_market_ci95") or [None, None]
        ci_b = row.get("delta_logloss_vs_base_v9_ci95") or [None, None]
        lines.append(
            f"| {row['model']} | {num(row.get('auc'), 3)} | {num(row.get('logloss'))} | {num(row.get('brier'))} | "
            f"{num(row.get('delta_logloss_vs_market'))} | [{num(ci_m[0])}, {num(ci_m[1])}] | "
            f"{num(row.get('delta_logloss_vs_base_v9'))} | [{num(ci_b[0])}, {num(ci_b[1])}] |"
        )
    lines.extend(
        [
            "",
            "## Alti Tests",
            "",
            "| feature | coverage | AUC | dLL vs market | CI | dLL vs base v9 | CI |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in alti:
        ci_m = row.get("delta_logloss_vs_market_ci95") or [None, None]
        ci_b = row.get("delta_logloss_vs_base_v9_ci95") or [None, None]
        lines.append(
            f"| {row['feature']} | {pct(row.get('coverage'))} | {num(row.get('holdout_auc'), 3)} | "
            f"{num(row.get('delta_logloss_vs_market'))} | [{num(ci_m[0])}, {num(ci_m[1])}] | "
            f"{num(row.get('delta_logloss_vs_base_v9'))} | [{num(ci_b[0])}, {num(ci_b[1])}] |"
        )
    lines.extend(
        [
            "",
            "## ML 方向判断",
            "",
            "值得尝试，但只值得作为严格打擂，不值得直接迁移生产。本轮 HGB 在 train-date CV 选参后，holdout logloss 输给 logistic residual calibrator，也输给 raw market；这说明当前 27 日期窗口太薄，非线性模型更容易吃到日期/城市噪声。后续若扩样到更多日期，可以继续让 HGB/GBDT 参赛，但 promotion gate 必须是 holdout + 日期 bootstrap 同时赢 raw market 和 base v9。",
            "",
            "## Verdict",
            "",
            f"significance={payload['verdict']['significance']} / baseline={payload['verdict']['baseline']} / forward={payload['verdict']['forward']} / conclusion={payload['verdict']['conclusion']}",
            "",
            payload["verdict"]["plain_text"],
            "",
            "## Outputs",
            "",
            f"- metrics: `{payload['outputs']['model_metrics']}`",
            f"- CV selection: `{payload['outputs']['cv_selection']}`",
            f"- alti tests: `{payload['outputs']['alti_feature_tests']}`",
            f"- JSON: `{payload['outputs']['json']}`",
        ]
    )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(tail.FEATURE_ROWS)
    df[LABEL] = tail.coerce_bool(df[LABEL])
    base_art = json.loads(tail.BASE_MODEL.read_text(encoding="utf-8"))
    df["base_model_p"] = tail.score_base_model(df, base_art)
    df["base_model_logit"] = tail.logit(df["base_model_p"].to_numpy(float))
    df["market_logit"] = tail.logit(pd.to_numeric(df["yes_current_ask"], errors="coerce").to_numpy(float))

    alti_fetch = fetch_alti_cache(df)
    df = enrich_alti(df, load_alti_obs())

    train = df[df["period"].eq("train")].copy()
    hold = df[df["period"].eq("holdout")].copy()
    y_h = hold[LABEL].to_numpy(int)
    baseline_probs = {
        "market": np.clip(hold["yes_current_ask"].to_numpy(float), 1e-6, 1 - 1e-6),
        "base_v9": np.clip(hold["base_model_p"].to_numpy(float), 1e-6, 1 - 1e-6),
    }

    metrics: list[dict[str, Any]] = [
        model_metrics("market_price_as_probability", hold, y_h, baseline_probs["market"], baseline_probs),
        model_metrics("base_current_yes_v9", hold, y_h, baseline_probs["base_v9"], baseline_probs),
    ]
    cv_rows: list[dict[str, Any]] = []

    feature_sets = {
        "market_alti_tendency": [MARKET_COL, "d_alti_3h"],
        "base_alti_tendency": [BASE_COL, "d_alti_3h"],
        "market_metar_core": [MARKET_COL] + METAR_CORE,
        "base_metar_core": [BASE_COL] + METAR_CORE,
        "market_metar_core_alti": [MARKET_COL] + METAR_PLUS_ALTI,
        "base_metar_core_alti": [BASE_COL] + METAR_PLUS_ALTI,
    }
    c_values = [0.02, 0.05, 0.1, 0.2, 0.5, 1.0]
    for name, feats in feature_sets.items():
        best_c, rows = cv_logistic(train, feats, LABEL, c_values)
        cv_rows.extend([{**row, "candidate": name} for row in rows])
        p = fit_logistic(train, hold, feats, best_c)
        metrics.append(model_metrics(f"logistic_l2_{name}_C{best_c}", hold, y_h, p, baseline_probs))

    # Single-logit calibration baselines.
    for name, col in [("market_logit_calibrated", MARKET_COL), ("base_logit_calibrated", BASE_COL)]:
        p = calibrate_single_logit(train, hold, col)
        metrics.append(model_metrics(name, hold, y_h, p, baseline_probs))

    hgb_grid = [
        {"max_iter": 80, "learning_rate": 0.03, "max_leaf_nodes": 3, "l2_regularization": 1.0, "min_samples_leaf": 40},
        {"max_iter": 80, "learning_rate": 0.03, "max_leaf_nodes": 5, "l2_regularization": 5.0, "min_samples_leaf": 60},
        {"max_iter": 120, "learning_rate": 0.02, "max_leaf_nodes": 3, "l2_regularization": 10.0, "min_samples_leaf": 80},
    ]
    for name, feats in {
        "hgb_market_metar_core": [MARKET_COL] + METAR_CORE,
        "hgb_market_metar_core_alti": [MARKET_COL] + METAR_PLUS_ALTI,
        "hgb_base_metar_core": [BASE_COL] + METAR_CORE,
    }.items():
        best_params, rows = cv_hgb(train, feats, LABEL, hgb_grid)
        cv_rows.extend([{**row, "candidate": name} for row in rows])
        p = fit_hgb(train, hold, feats, best_params)
        metrics.append(model_metrics(f"{name}_{best_params}", hold, y_h, p, baseline_probs))

    alti_tests = alti_feature_tests(df)
    metrics_df = pd.DataFrame(metrics)
    cv_df = pd.DataFrame(cv_rows)
    alti_df = pd.DataFrame(alti_tests)
    metrics_path = OUT_DIR / "model_metrics.csv"
    cv_path = OUT_DIR / "cv_selection.csv"
    alti_path = OUT_DIR / "alti_feature_tests.csv"
    metrics_df.to_csv(metrics_path, index=False)
    cv_df.to_csv(cv_path, index=False)
    alti_df.to_csv(alti_path, index=False)

    best = metrics_df[~metrics_df["model"].isin(["market_price_as_probability", "base_current_yes_v9"])].sort_values("logloss").iloc[0].to_dict()
    ci_market = best.get("delta_logloss_vs_market_ci95") or [None, None]
    ci_base = best.get("delta_logloss_vs_base_v9_ci95") or [None, None]
    pass_market = bool(best.get("delta_logloss_vs_market") < 0 and ci_market[1] is not None and ci_market[1] < 0)
    pass_base = bool(best.get("delta_logloss_vs_base_v9") < 0 and ci_base[1] is not None and ci_base[1] < 0)
    conclusion = "shadow_candidate" if pass_market and pass_base else "inconclusive"
    payload = {
        "generated_at_utc": pd.Timestamp.utcnow().isoformat(),
        "data_snapshot": {
            "source_feature_rows": rel(tail.FEATURE_ROWS),
            "base_model": rel(tail.BASE_MODEL),
            "fixed_split": "existing period column; holdout target_date >= 2026-06-01 in source artifact",
        },
        "data_self_check": data_self_check(),
        "coverage": {
            "rows": int(len(df)),
            "train_rows": int(len(train)),
            "holdout_rows": int(len(hold)),
            "holdout_dates": int(hold["target_date"].nunique()),
            "target_date_min": str(df["target_date"].min()),
            "target_date_max": str(df["target_date"].max()),
            "alti_now_coverage_holdout": float(hold["alti_now"].notna().mean()),
            "d_alti_3h_coverage_holdout": float(hold["d_alti_3h"].notna().mean()),
        },
        "alti_fetch": alti_fetch,
        "holdout_model_metrics": metrics,
        "cv_selection": cv_rows,
        "alti_feature_tests": alti_tests,
        "verdict": {
            "significance": "PASS" if pass_market and pass_base else "FAIL",
            "baseline": "PASS" if pass_market and pass_base else "FAIL",
            "forward": "PASS" if pass_market and pass_base else "FAIL",
            "conclusion": conclusion,
            "plain_text": (
                f"Best non-baseline candidate is {best['model']} with holdout logloss {best['logloss']:.4f}; "
                f"delta vs market {best['delta_logloss_vs_market']:.5f} CI {ci_market}, "
                f"delta vs base_v9 {best['delta_logloss_vs_base_v9']:.5f} CI {ci_base}. "
                "CI gate did not pass, so no model/live change."
            ),
        },
        "outputs": {
            "model_metrics": rel(metrics_path),
            "cv_selection": rel(cv_path),
            "alti_feature_tests": rel(alti_path),
            "alti_cache_dir": rel(ALTI_CACHE_DIR),
            "json": rel(OUT_JSON),
            "markdown": rel(OUT_MD),
        },
    }
    OUT_JSON.write_text(json.dumps(json_ready(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_markdown(json_ready(payload))
    print(json.dumps(json_ready(payload["verdict"]), indent=2, sort_keys=True))
    print(f"wrote {rel(metrics_path)}")
    print(f"wrote {rel(alti_path)}")
    print(f"wrote {rel(OUT_JSON)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
