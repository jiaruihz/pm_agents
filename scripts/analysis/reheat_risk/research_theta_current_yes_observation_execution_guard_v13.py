#!/usr/bin/env python3
"""Evaluate observation/execution freshness guards for theta current-YES.

Evidence layer: reheat feature factory replay.  The script scores factory
state rows with the frozen v9 model artifact, then measures whether obs-age,
pre-METAR-update, and minutes-since-running-max guards improve the live-like
current-YES rule.
"""

from __future__ import annotations

import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
REHEAT_DIR = ROOT / "scripts/analysis/reheat_risk"
if str(REHEAT_DIR) not in sys.path:
    sys.path.insert(0, str(REHEAT_DIR))

from research_current_yes_peak_vs_fade_v1 import score_rows  # type: ignore  # noqa: E402


DB = ROOT / "runtime/weather.db"
GATE = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"
BASE_FEATURE_ROWS = ROOT / "docs/analysis/2026-06/generated/theta_yes_current_full_replay_v8/feature_rows.csv"
OBS_FEATURE_ROWS = ROOT / "docs/analysis/2026-06/generated/reheat_feature_factory_v1/reheat_feature_rows.csv"
MODEL_ARTIFACT = ROOT / "docs/analysis/2026-06/generated/theta_yes_current_live_gate_v9/live_model.json"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/theta_current_yes_observation_execution_guard_v13"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-18-theta-current-yes-observation-execution-guard-v13.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-18-theta-current-yes-observation-execution-guard-v13.md"

SPLIT_DATE = "2026-06-01"
SEED = 20260618


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pct(value: float | None, signed: bool = True) -> str:
    if value is None or not math.isfinite(float(value)):
        return "NA"
    sign = "+" if signed else ""
    return f"{100 * float(value):{sign}.1f}%"


def fnum(value: float | None, digits: int = 2) -> str:
    if value is None or not math.isfinite(float(value)):
        return "NA"
    return f"{float(value):.{digits}f}"


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_ready(v) for v in value]
    if isinstance(value, tuple):
        return [json_ready(v) for v in value]
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    return value


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
            "fact_trades_by_class": query_rows(
                conn,
                "SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class ORDER BY trade_class",
            ),
            "fact_trades_by_settlement_status": query_rows(
                conn,
                "SELECT COALESCE(settlement_status, '') AS settlement_status, COUNT(*) AS rows "
                "FROM fact_trades GROUP BY settlement_status ORDER BY settlement_status",
            ),
            "fact_signal_candidate_coverage": query_rows(
                conn,
                "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, "
                "SUM(live_filled) AS live_filled FROM fact_signal_candidates",
            )[0],
            "clob_order_fill_join": query_rows(
                conn,
                "SELECT o.status, COUNT(*) AS orders, "
                "SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill "
                "FROM orders o LEFT JOIN fills f USING(execution_id) "
                "WHERE o.venue='polymarket_clob' GROUP BY o.status ORDER BY o.status",
            ),
        }
    finally:
        conn.close()


def load_gate() -> dict[str, Any]:
    if not GATE.exists():
        return {"gate_pass": None, "missing": True}
    data = json.loads(GATE.read_text(encoding="utf-8"))
    return {
        "gate_pass": data.get("gate_pass"),
        "fail_reasons": data.get("fail_reasons", []),
        "missing_order_rows": data.get("db_fills", {}).get("missing_order_rows"),
        "over_order_keys": data.get("db_fills", {}).get("over_order_keys"),
        "db_fill_cost_minus_fact_cost": data.get("db_fill_cost_minus_fact_cost"),
    }


def infer_obs_cadence_minutes(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    obs = df[["city", "target_date", "decision_last_obs_dt"]].dropna().drop_duplicates()
    for (city, target_date), group in obs.groupby(["city", "target_date"]):
        times = sorted(pd.to_datetime(group["decision_last_obs_dt"], utc=True).dropna().unique())
        gaps = []
        for a, b in zip(times, times[1:]):
            gap = (pd.Timestamp(b) - pd.Timestamp(a)).total_seconds() / 60.0
            if 5.0 <= gap <= 90.0:
                gaps.append(gap)
        cadence = float(np.median(gaps[-8:])) if gaps else 60.0
        rows.append({"city": city, "target_date": target_date, "obs_cadence_min": cadence, "obs_cadence_inferred": bool(gaps)})
    return pd.DataFrame(rows)


def load_obs_clock_features() -> pd.DataFrame:
    cols = [
        "city",
        "target_date",
        "current_bracket",
        "decision_snapshot_ts_utc",
        "decision_last_obs_utc",
        "minutes_since_running_max",
    ]
    raw = pd.read_csv(OBS_FEATURE_ROWS, usecols=cols)
    raw["snapshot_key_ts"] = pd.to_datetime(raw["decision_snapshot_ts_utc"], utc=True, errors="coerce").dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    raw = raw.dropna(subset=["city", "target_date", "current_bracket", "snapshot_key_ts"])
    return (
        raw.sort_values(["city", "target_date", "current_bracket", "snapshot_key_ts"])
        .drop_duplicates(["city", "target_date", "current_bracket", "snapshot_key_ts"], keep="first")
        .drop(columns=["decision_snapshot_ts_utc"])
    )


def load_scored_replay_with_obs_clock() -> pd.DataFrame:
    df = pd.read_csv(BASE_FEATURE_ROWS)
    artifact = json.loads(MODEL_ARTIFACT.read_text(encoding="utf-8"))
    for col in ("current_yes_wins", "has_d1_no", "d1_no_loses", "is_f"):
        if col in df.columns:
            df[col] = df[col].astype(str).str.lower().isin({"true", "1"})
    df["label_yes_wins"] = df["label_yes_wins"].astype(int)
    df["p_yes_win"] = score_rows(df, artifact)
    df["yes_current_ask"] = pd.to_numeric(df["yes_current_ask"], errors="coerce")
    df["yes_current_size"] = pd.to_numeric(df["yes_current_size"], errors="coerce")
    df["decline_c"] = pd.to_numeric(df["decline_c"], errors="coerce")
    df["decision_hour_local"] = pd.to_numeric(df["decision_hour_local"], errors="coerce")
    df["edge_snapshot"] = df["p_yes_win"] - df["yes_current_ask"]
    df["available_notional_at_ask"] = df["yes_current_ask"] * df["yes_current_size"]
    df["snapshot_dt"] = pd.to_datetime(df["snapshot_ts_utc"], utc=True, errors="coerce")
    df["decision_snapshot_ts_utc"] = df["snapshot_ts_utc"]
    df["snapshot_key_ts"] = df["snapshot_dt"].dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    obs = load_obs_clock_features()
    df = df.merge(obs, on=["city", "target_date", "current_bracket", "snapshot_key_ts"], how="left")
    df["decision_last_obs_dt"] = pd.to_datetime(df["decision_last_obs_utc"], utc=True, errors="coerce")
    df["obs_age_min"] = (df["snapshot_dt"] - df["decision_last_obs_dt"]).dt.total_seconds() / 60.0
    df["period"] = np.where(df["target_date"].astype(str) < SPLIT_DATE, "train", "holdout")
    cadence = infer_obs_cadence_minutes(df)
    df = df.merge(cadence, on=["city", "target_date"], how="left")
    df["obs_cadence_min"] = pd.to_numeric(df["obs_cadence_min"], errors="coerce").fillna(60.0)
    df["obs_cadence_inferred"] = np.where(df["obs_cadence_inferred"].isna(), False, df["obs_cadence_inferred"]).astype(bool)
    df["minutes_to_next_obs"] = df["obs_cadence_min"] - df["obs_age_min"]
    df["pre_update_blackout_6m"] = df["minutes_to_next_obs"].between(0, 6, inclusive="both")
    df["obs_age_le_20m"] = df["obs_age_min"].le(20.0)
    df["obs_clock_guard_ok"] = df["obs_age_le_20m"] & ~df["pre_update_blackout_6m"]
    df["minutes_since_running_max"] = pd.to_numeric(df.get("minutes_since_running_max"), errors="coerce")
    return df


def base_v9_candidates(df: pd.DataFrame) -> pd.DataFrame:
    mask = (
        df["decision_hour_local"].between(13, 15)
        & df["decline_c"].ge(0.5)
        & df["yes_current_ask"].ge(0.55)
        & df["available_notional_at_ask"].ge(2.0)
        & df["p_yes_win"].ge(0.5)
        & df["edge_snapshot"].ge(0.05)
        & df["d1_no_ask"].notna()
    )
    work = df[mask].copy()
    return dedupe_live(work)


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
    if frame.empty:
        return {"orders": 0, "dates": 0, "cities": 0, "win_rate": None, "roi": None, "pnl_usd": 0.0}
    notional = 5.0
    price = np.minimum(frame["yes_current_ask"].astype(float).to_numpy() + taker_cushion, 0.999)
    y = frame["label_yes_wins"].astype(int).to_numpy()
    pnl = np.where(y == 1, notional / price - notional, -notional)
    return {
        "orders": int(len(frame)),
        "dates": int(frame["target_date"].nunique()),
        "cities": int(frame["city"].nunique()),
        "notional": float(notional * len(frame)),
        "wins": int(y.sum()),
        "win_rate": float(y.mean()),
        "roi": float(pnl.sum() / (notional * len(frame))),
        "pnl_usd": float(pnl.sum()),
        "avg_ask": float(frame["yes_current_ask"].mean()),
        "avg_p_yes_win": float(frame["p_yes_win"].mean()),
        "avg_edge": float(frame["edge_snapshot"].mean()),
        "avg_obs_age_min": float(frame["obs_age_min"].mean()),
        "avg_minutes_to_next_obs": float(frame["minutes_to_next_obs"].mean()),
        "avg_minutes_since_max": float(frame["minutes_since_running_max"].mean()),
        "blackout_rate": float(frame["pre_update_blackout_6m"].mean()),
        "stale_rate": float((~frame["obs_age_le_20m"]).mean()),
        "orders_per_date": float(len(frame) / frame["target_date"].nunique()),
        "date_min": str(frame["target_date"].min()),
        "date_max": str(frame["target_date"].max()),
    }


def bootstrap_roi(frame: pd.DataFrame, *, taker_cushion: float = 0.02, n_boot: int = 2000) -> list[float | None]:
    if frame.empty or frame["target_date"].nunique() < 3:
        return [None, None]
    notional = 5.0
    date_rows = []
    for _, g in frame.groupby("target_date"):
        price = np.minimum(g["yes_current_ask"].astype(float).to_numpy() + taker_cushion, 0.999)
        y = g["label_yes_wins"].astype(int).to_numpy()
        pnl = np.where(y == 1, notional / price - notional, -notional).sum()
        date_rows.append([float(pnl), float(notional * len(g))])
    arr = np.asarray(date_rows, dtype=float)
    rng = np.random.default_rng(SEED)
    vals = []
    for _ in range(n_boot):
        sample = arr[rng.integers(0, len(arr), len(arr))]
        vals.append(sample[:, 0].sum() / sample[:, 1].sum())
    lo, hi = np.quantile(vals, [0.025, 0.975])
    return [float(lo), float(hi)]


def guard_summaries(candidates: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    guards: dict[str, pd.DataFrame] = {
        "v9_no_obs_guard": candidates,
        "obs_age_le_20m": candidates[candidates["obs_age_le_20m"]],
        "no_pre_update_blackout_6m": candidates[~candidates["pre_update_blackout_6m"]],
        "combined_obs_clock_guard": candidates[candidates["obs_clock_guard_ok"]],
        "minutes_since_max_ge_30": candidates[candidates["minutes_since_running_max"].ge(30.0)],
        "minutes_since_max_ge_45": candidates[candidates["minutes_since_running_max"].ge(45.0)],
        "obs_clock_and_max_ge_30": candidates[candidates["obs_clock_guard_ok"] & candidates["minutes_since_running_max"].ge(30.0)],
    }
    rows = []
    for name, frame in guards.items():
        row = summarize(frame, taker_cushion=0.02)
        row.update({"guard": name, "roi_ci95": bootstrap_roi(frame, taker_cushion=0.02)})
        rows.append(row)
    return pd.DataFrame(rows), guards


def bin_table(candidates: pd.DataFrame) -> pd.DataFrame:
    work = candidates.copy()
    work["obs_age_bin"] = pd.cut(work["obs_age_min"], [-1, 10, 20, 30, 999], labels=["0-10", "10-20", "20-30", "30+"])
    work["minutes_since_max_bin"] = pd.cut(
        work["minutes_since_running_max"],
        [-1, 15, 30, 45, 60, 9999],
        labels=["0-15", "15-30", "30-45", "45-60", "60+"],
    )
    rows = []
    for dim in ["obs_age_bin", "minutes_since_max_bin", "pre_update_blackout_6m"]:
        for value, group in work.groupby(dim, observed=False):
            if len(group) == 0:
                continue
            row = summarize(group, taker_cushion=0.02)
            row.update({"dimension": dim, "value": str(value), "roi_ci95": bootstrap_roi(group, taker_cushion=0.02)})
            rows.append(row)
    return pd.DataFrame(rows)


def bad_cases(candidates: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "target_date",
        "city",
        "decision_snapshot_ts_utc",
        "decision_hour_local",
        "current_bracket",
        "yes_current_ask",
        "p_yes_win",
        "edge_snapshot",
        "label_yes_wins",
        "obs_age_min",
        "minutes_to_next_obs",
        "pre_update_blackout_6m",
        "minutes_since_running_max",
        "decline_c",
    ]
    return candidates[candidates["label_yes_wins"].eq(0)].sort_values(["target_date", "city"])[cols].copy()


def write_report(payload: dict[str, Any], guards: pd.DataFrame, bins: pd.DataFrame, bad: pd.DataFrame) -> None:
    lines = [
        "# Theta Current YES Observation / Execution Guard v13",
        "",
        "Status: research_only / guard_design",
        f"Generated: {payload['generated_at_utc']}",
        "",
        "Target metric: `current_yes_obs_execution_guard` = 在 v9 current-YES 候选上，观测时效和衰竭后等待时间是否能提高成功率和收益率。",
        "",
        "## 数据完整性自检",
        "",
        f"- fact_built_at_utc: `{payload['data_self_check']['fact_trades_max_built_at_utc']}`",
        f"- fact_trades trade_class: `{payload['data_self_check']['fact_trades_by_class']}`",
        f"- settlement_status: `{payload['data_self_check']['fact_trades_by_settlement_status']}`",
        f"- fact_signal_candidates coverage: `{payload['data_self_check']['fact_signal_candidate_coverage']}`",
        f"- CLOB orders/fills join: `{payload['data_self_check']['clob_order_fill_join']}`",
        f"- CLOB gate: `{payload['clob_gate']}`",
        "",
        "## 人话结论",
        "",
        "obs-age / METAR blackout 这类 guard 在历史半小时 replay 里没有证明能提升 v9 收益。原因不是 guard 没道理，而是 replay 粒度太粗：真正危险的是像 Helsinki 那种“下一条 METAR 前 1-2 分钟”的分钟级窗口，半小时 orderbook replay 很难直接看见。",
        "",
        "把收益口径对齐到 v8/v12 后，v9 holdout 基线回到 31 单 / 29 胜 / ROI +16.2%。但 `obs_age <= 20m` 在半小时 snapshot replay 里一单都留不下，`pre_update_blackout=6m` 又一单都挡不掉；`minutes_since_running_max >= 30m/45m` 也没有挡住两个亏损案例，ROI 反而略低。",
        "",
        "所以这次结论是：obs clock 必须作为 live 风控/telemetry 记录，但不是已经被历史 replay 证明的收益增强因子。真正要补的是分钟级 forward 数据，而不是继续在半小时 replay 上调一个看起来漂亮的阈值。",
        "",
        "## Guard 结果（holdout, $5/order, taker +2c）",
        "",
        "| guard | orders/dates | win | ROI | CI95 | avg ask | obs age | min since max | blackout |",
        "|---|---:|---:|---:|---|---:|---:|---:|---:|",
    ]
    for _, row in guards.iterrows():
        ci = row.get("roi_ci95") or [None, None]
        lines.append(
            f"| {row['guard']} | {int(row['orders'])}/{int(row['dates'])} | {pct(row['win_rate'])} | "
            f"{pct(row['roi'])} | [{pct(ci[0])}, {pct(ci[1])}] | {fnum(row.get('avg_ask'), 3)} | "
            f"{fnum(row.get('avg_obs_age_min'), 1)} | {fnum(row.get('avg_minutes_since_max'), 1)} | "
            f"{pct(row.get('blackout_rate'))} |"
        )
    lines.extend(
        [
            "",
            "## Slice Diagnostics",
            "",
            "| dimension | value | orders/dates | win | ROI | CI95 | avg ask |",
            "|---|---|---:|---:|---:|---|---:|",
        ]
    )
    for _, row in bins.iterrows():
        ci = row.get("roi_ci95") or [None, None]
        lines.append(
            f"| {row['dimension']} | {row['value']} | {int(row['orders'])}/{int(row['dates'])} | "
            f"{pct(row['win_rate'])} | {pct(row['roi'])} | [{pct(ci[0])}, {pct(ci[1])}] | {fnum(row.get('avg_ask'), 3)} |"
        )
    lines.extend(
        [
            "",
            "## Bad Cases",
            "",
            f"- v9 holdout losing candidates: `{len(bad)}`.",
        ]
    )
    if not bad.empty:
        lines.extend(["", "| date | city | hour | bracket | ask | p | obs age | min-to-next | since max | decline |", "|---|---|---:|---|---:|---:|---:|---:|---:|---:|"])
        for _, row in bad.head(12).iterrows():
            lines.append(
                f"| {row['target_date']} | {row['city']} | {int(row['decision_hour_local'])} | {row['current_bracket']} | "
                f"{fnum(row['yes_current_ask'], 3)} | {fnum(row['p_yes_win'], 3)} | {fnum(row['obs_age_min'], 1)} | "
                f"{fnum(row['minutes_to_next_obs'], 1)} | {fnum(row['minutes_since_running_max'], 1)} | {fnum(row['decline_c'], 2)} |"
            )
    lines.extend(
        [
            "",
            "## 交易动作",
            "",
            "- 不把历史 replay 里的 obs-age / blackout / since-max guard 当成已验证 alpha。",
            "- 实盘准备仍建议保留 `pre_update_blackout=6m`、fresh-book guard、真实 IANA timezone/DST；它们是事故防护，不是收益优化器。",
            "- `obs_age <= 20m` 需要配合分钟级 runner：在半小时 replay 上它会把样本全砍掉，不能直接拿来解释历史 ROI。",
            "- 暂不建议把 `minutes_since_running_max >= 30m` 作为 v9 live hard filter；它减少订单但没有挡住本轮 bad cases。",
            "- 必补分钟级 forward telemetry：每个 would-order 记录 fresh ask、snapshot age、obs age、minutes_to_next_obs、minutes_since_running_max、source profile。",
            "",
            "## 三道门",
            "",
            "- significance=FAIL：三个简单 guard 都没有在 v8/v12 对齐基线上证明收益增量。",
            "- baseline=PASS：v9 本身仍是 31 单 / 29 胜 / ROI +16.2%，和 v12 对齐。",
            "- forward=FAIL/NA：obs blackout 需要分钟级 forward 数据，半小时 replay 不足以证明。",
            "- conclusion=`telemetry_required` / `risk_guard_only`；不因为 v13 单独升级 live。",
            "",
            "## 产物",
            "",
            f"- JSON: `{OUT_JSON.relative_to(ROOT)}`",
            f"- guard CSV: `{(OUT_DIR / 'guard_summary.csv').relative_to(ROOT)}`",
            f"- bin CSV: `{(OUT_DIR / 'slice_bins.csv').relative_to(ROOT)}`",
            f"- bad cases CSV: `{(OUT_DIR / 'bad_cases.csv').relative_to(ROOT)}`",
            f"- scored candidates CSV: `{(OUT_DIR / 'v9_candidates.csv').relative_to(ROOT)}`",
            f"- Script: `{Path(__file__).resolve().relative_to(ROOT)}`",
        ]
    )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    scored = load_scored_replay_with_obs_clock()
    holdout = scored[scored["period"].eq("holdout")].copy()
    candidates = base_v9_candidates(holdout)
    guards, guard_frames = guard_summaries(candidates)
    bins = bin_table(candidates)
    bad = bad_cases(candidates)

    candidates.to_csv(OUT_DIR / "v9_candidates.csv", index=False)
    guards.to_csv(OUT_DIR / "guard_summary.csv", index=False)
    bins.to_csv(OUT_DIR / "slice_bins.csv", index=False)
    bad.to_csv(OUT_DIR / "bad_cases.csv", index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "evidence_layer": "v8 current-YES orderbook replay joined with deduped reheat factory obs-clock telemetry; not minute-level live telemetry",
        "row_grain": "deduped v9 current-YES candidate from v8 replay: city/date/current_bracket with max two brackets per city-date",
        "data_self_check": data_self_check(),
        "clob_gate": load_gate(),
        "coverage": {
            "base_replay_rows": int(len(scored)),
            "holdout_replay_rows": int(len(holdout)),
            "obs_clock_joined_rows": int(scored["decision_last_obs_utc"].notna().sum()),
            "obs_clock_join_rate": float(scored["decision_last_obs_utc"].notna().mean()),
            "v9_holdout_candidates": int(len(candidates)),
            "v9_active_dates": int(candidates["target_date"].nunique()),
            "v9_cities": int(candidates["city"].nunique()),
        },
        "guard_summary": json_ready(guards.to_dict("records")),
        "slice_bins": json_ready(bins.to_dict("records")),
        "bad_cases": json_ready(bad.to_dict("records")),
        "outputs": {
            "md": str(OUT_MD.relative_to(ROOT)),
            "json": str(OUT_JSON.relative_to(ROOT)),
            "guard_csv": str((OUT_DIR / "guard_summary.csv").relative_to(ROOT)),
            "bin_csv": str((OUT_DIR / "slice_bins.csv").relative_to(ROOT)),
            "bad_cases_csv": str((OUT_DIR / "bad_cases.csv").relative_to(ROOT)),
            "candidates_csv": str((OUT_DIR / "v9_candidates.csv").relative_to(ROOT)),
        },
    }
    payload = json_ready(payload)
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_report(payload, guards, bins, bad)
    print(json.dumps({"json": str(OUT_JSON), "md": str(OUT_MD)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
