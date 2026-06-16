#!/usr/bin/env python3
"""Expanded replay for theta-NO carry vs current-max YES.

This is a narrow materializer for the pure carry question.  It uses synced raw
orderbook snapshots, WU observed running max for source-aligned whitelist
cities, and pm_history winners.  It does not read paper/live fills and does not
change production behavior.
"""

from __future__ import annotations

import gzip
import json
import math
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

OBSERVED_MAX_DIR = Path(__file__).resolve().parents[1] / "observed_max"
if str(OBSERVED_MAX_DIR) not in sys.path:
    sys.path.insert(0, str(OBSERVED_MAX_DIR))

from research_m3_observed_max_residual import CITY_TIMEZONE
from research_m3_paper_snapshot_proxy_backtest import Bracket, parse_bracket


ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "runtime/weather.db"
GATE = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"
OBSERVED_DETAIL = (
    ROOT
    / "docs/analysis/2026-06/generated/m3_observed_max_v5_h10_21_theta_patch_20260614/"
    / "m3_observed_max_residual_detail.csv"
)
ALIGNMENT = ROOT / "docs/analysis/2026-06/generated/m3_settlement_alignment_v1/m3_settlement_alignment_city_days.csv"
ORDERBOOK_DIR = ROOT / "runtime/weather_edge_v1/market_data/orderbook_snapshots"
PM_HISTORY_DIR = ROOT / "runtime/weather_edge_v1/market_data/cache/pm_history"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/theta_no_carry_expanded_replay_v4"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-16-theta-no-carry-expanded-replay-v4.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-16-theta-no-carry-expanded-replay-v4.md"

START_DATE = "2026-05-19"
END_DATE = "2026-06-14"
SPLIT_DATE = "2026-06-01"
NEW_DATES_START = "2026-06-10"
DECISION_HOURS = set(range(13, 18))


@dataclass(frozen=True)
class PmHistory:
    unit: str
    winner_label: str
    labels: tuple[str, ...]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def pct(x: float | None, signed: bool = True) -> str:
    if x is None or not math.isfinite(float(x)):
        return "NA"
    sign = "+" if signed else ""
    return f"{float(x) * 100:{sign}.1f}%"


def fnum(x: float | None, signed: bool = False) -> str:
    if x is None or not math.isfinite(float(x)):
        return "NA"
    sign = "+" if signed else ""
    return f"{float(x):{sign}.2f}"


def round_half_up(value: float) -> int:
    return int(math.floor(float(value) + 0.5))


def best_ask(raw: object) -> tuple[float | None, float | None]:
    if not isinstance(raw, dict):
        return None, None
    asks = raw.get("asks")
    if not isinstance(asks, list) or not asks:
        return None, None
    out: list[tuple[float, float | None]] = []
    for ask in asks:
        if not isinstance(ask, dict):
            continue
        try:
            price = float(ask.get("price"))
        except (TypeError, ValueError):
            continue
        try:
            size = float(ask.get("size")) if ask.get("size") is not None else None
        except (TypeError, ValueError):
            size = None
        out.append((price, size))
    if not out:
        return None, None
    return min(out, key=lambda x: x[0])


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
    data = json.loads(GATE.read_text())
    return {
        "gate_pass": data.get("gate_pass"),
        "fail_reasons": data.get("fail_reasons", []),
        "missing_order_rows": data.get("db_fills", {}).get("missing_order_rows"),
        "over_order_keys": data.get("db_fills", {}).get("over_order_keys"),
        "db_fill_cost_minus_fact_cost": data.get("db_fill_cost_minus_fact_cost"),
    }


def load_source_aligned_whitelist() -> set[str]:
    df = pd.read_csv(ALIGNMENT)
    valid = df[df["pm_history_valid"].fillna(False) & df["winner_count"].eq(1)].copy()
    per_city = (
        valid.dropna(subset=["match_round"])
        .groupby("city")
        .agg(days=("match_round", "size"), match_rate=("match_round", "mean"))
    )
    return set(per_city[(per_city["days"].ge(20)) & (per_city["match_rate"].eq(1.0))].index)


def parse_pm_history(path: Path) -> PmHistory | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("brackets"), list):
        return None
    unit = str(data.get("unit") or "").upper()
    if unit not in {"C", "F"}:
        return None
    winners: list[str] = []
    labels: list[str] = []
    for bracket in data["brackets"]:
        if not isinstance(bracket, dict):
            continue
        label = str(bracket.get("label") or "").replace("°", "").strip()
        if label:
            labels.append(label)
        try:
            final_price = float(bracket.get("final_price"))
        except (TypeError, ValueError):
            continue
        if final_price >= 0.99 and label:
            winners.append(label)
    if len(winners) != 1:
        return None
    return PmHistory(unit=unit, winner_label=winners[0], labels=tuple(labels))


def load_pm_history_map() -> dict[tuple[str, str], PmHistory]:
    out: dict[tuple[str, str], PmHistory] = {}
    for path in PM_HISTORY_DIR.glob("*_????-??-??.json"):
        stem = path.stem
        if "_" not in stem:
            continue
        city, target_date = stem.rsplit("_", 1)
        if not (START_DATE <= target_date <= END_DATE):
            continue
        parsed = parse_pm_history(path)
        if parsed is not None:
            out[(city, target_date)] = parsed
    return out


def load_observed(whitelist: set[str]) -> pd.DataFrame:
    df = pd.read_csv(OBSERVED_DETAIL)
    df["target_date"] = df["target_date"].astype(str)
    df = df[
        df["city"].isin(whitelist)
        & df["target_date"].between(START_DATE, END_DATE)
        & df["decision_hour_local"].isin(DECISION_HOURS)
    ].copy()
    keep = [
        "city",
        "target_date",
        "decision_hour_local",
        "running_max_c",
        "running_max_f",
        "current_temp_c",
        "final_max_c",
        "final_max_f",
    ]
    return df[keep].drop_duplicates(["city", "target_date", "decision_hour_local"]).copy()


def iter_orderbook(pm_history: dict[tuple[str, str], PmHistory]) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    local_time_cache: dict[tuple[str, str], tuple[str, int, str]] = {}
    files_seen = 0
    records_seen = 0
    kept = 0
    missing_tz = 0
    missing_history = 0
    wrong_day_hour = 0
    no_best_ask = 0
    bad_bracket = 0
    for date_dir in sorted(ORDERBOOK_DIR.iterdir()):
        if not date_dir.is_dir() or not (START_DATE <= date_dir.name <= END_DATE):
            continue
        for path in sorted(date_dir.glob("*.jsonl.gz")):
            files_seen += 1
            with gzip.open(path, "rt", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    records_seen += 1
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if record.get("status") != "ok":
                        continue
                    city = str(record.get("city"))
                    target_date = str(record.get("event_date"))
                    hist = pm_history.get((city, target_date))
                    if hist is None:
                        missing_history += 1
                        continue
                    tz_name = CITY_TIMEZONE.get(city)
                    if not tz_name:
                        missing_tz += 1
                        continue
                    ts_raw = str(record.get("snapshot_ts_utc") or "")
                    if not ts_raw:
                        continue
                    cache_key = (ts_raw, city)
                    cached = local_time_cache.get(cache_key)
                    if cached is None:
                        try:
                            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                        except ValueError:
                            continue
                        local_ts = ts.astimezone(ZoneInfo(tz_name))
                        cached = (ts.astimezone(timezone.utc).isoformat(), int(local_ts.hour), local_ts.date().isoformat())
                        local_time_cache[cache_key] = cached
                    ts_iso, hour, local_date = cached
                    if local_date != target_date or hour not in DECISION_HOURS:
                        wrong_day_hour += 1
                        continue
                    bracket = parse_bracket(record.get("bracket"))
                    if bracket is None:
                        bad_bracket += 1
                        continue
                    ask, size = best_ask(record.get("raw"))
                    if ask is None or not (0.005 <= ask <= 0.995):
                        no_best_ask += 1
                        continue
                    outcome = str(record.get("outcome") or "").lower()
                    if outcome not in {"yes", "no"}:
                        continue
                    rows.append(
                        {
                            "orderbook_file": str(path.relative_to(ROOT)),
                            "snapshot_ts_utc": ts_iso,
                            "snapshot_ts_local": f"{local_date}T{hour:02d}:00:00[{tz_name}]",
                            "decision_hour_local": hour,
                            "city": city,
                            "target_date": target_date,
                            "unit": hist.unit,
                            "winner_label": hist.winner_label,
                            "bracket": bracket.raw,
                            "bracket_low": bracket.low_f,
                            "bracket_high": bracket.high_f,
                            "outcome": outcome,
                            "best_ask": ask,
                            "best_ask_size": size,
                            "condition_id": record.get("condition_id"),
                            "market_id": record.get("market_id"),
                            "token_id": record.get("token_id"),
                        }
                    )
                    kept += 1
    raw = pd.DataFrame(rows)
    if raw.empty:
        return raw, {
            "orderbook_files_seen": files_seen,
            "orderbook_records_seen": records_seen,
            "orderbook_records_kept": kept,
        }
    raw["snapshot_sort"] = pd.to_datetime(raw["snapshot_ts_utc"], utc=True, errors="coerce")
    raw = raw.sort_values("snapshot_sort")
    raw = raw.groupby(
        ["city", "target_date", "decision_hour_local", "bracket", "outcome"], as_index=False
    ).tail(1)
    raw = raw.drop(columns=["snapshot_sort"]).reset_index(drop=True)
    return raw, {
        "orderbook_files_seen": files_seen,
        "orderbook_records_seen": records_seen,
        "orderbook_records_kept_before_hourly_dedupe": kept,
        "quote_rows_after_hourly_dedupe": int(len(raw)),
        "records_missing_history": missing_history,
        "records_missing_tz": missing_tz,
        "records_wrong_day_hour": wrong_day_hour,
        "records_no_best_ask": no_best_ask,
        "records_bad_bracket": bad_bracket,
    }


def running_value(row: pd.Series) -> int:
    if str(row["unit"]).upper() == "F":
        return round_half_up(float(row["running_max_f"]))
    return round_half_up(float(row["running_max_c"]))


def bracket_contains_value(bracket: Bracket, value: float) -> bool:
    if bracket.low_f is not None and value < bracket.low_f:
        return False
    if bracket.high_f is not None and value > bracket.high_f:
        return False
    return True


def tail_distance(row: pd.Series) -> int | None:
    low = row["bracket_low"]
    if pd.isna(low):
        return None
    rv = float(row["running_value"])
    if float(low) <= rv:
        return None
    if str(row["unit"]).upper() == "F":
        return int(math.ceil((float(low) - rv) / 2.0))
    return int(round(float(low) - rv))


def build_quote_rows() -> tuple[pd.DataFrame, dict[str, Any]]:
    whitelist = load_source_aligned_whitelist()
    pm_history = load_pm_history_map()
    observed = load_observed(whitelist)
    orderbook, orderbook_meta = iter_orderbook(pm_history)
    joined = orderbook.merge(
        observed,
        on=["city", "target_date", "decision_hour_local"],
        how="inner",
    )
    joined["running_value"] = joined.apply(running_value, axis=1)
    joined["decline"] = joined["running_max_c"] - joined["current_temp_c"]
    joined["period"] = np.where(joined["target_date"] < SPLIT_DATE, "train", "holdout")
    joined["materialized_period"] = np.where(joined["target_date"] >= NEW_DATES_START, "new_2026_06_10_14", "old_2026_05_19_06_09")

    yes = joined[joined["outcome"].eq("yes")].copy()
    yes["bracket_obj"] = yes["bracket"].apply(parse_bracket)
    yes["contains_running"] = yes.apply(
        lambda r: bracket_contains_value(r["bracket_obj"], float(r["running_value"])) if r["bracket_obj"] else False,
        axis=1,
    )
    current_yes = yes[yes["contains_running"]].copy()
    current_yes["specificity"] = current_yes["bracket_high"].notna().astype(int)
    current_yes = (
        current_yes.sort_values(["specificity", "best_ask"], ascending=[False, True])
        .drop_duplicates(["orderbook_file", "city", "target_date", "decision_hour_local"], keep="first")
        [[
            "orderbook_file",
            "city",
            "target_date",
            "decision_hour_local",
            "bracket",
            "best_ask",
            "best_ask_size",
        ]]
        .rename(
            columns={
                "bracket": "current_bracket",
                "best_ask": "yes_current_ask",
                "best_ask_size": "yes_current_size",
            }
        )
    )

    no = joined[joined["outcome"].eq("no")].copy()
    no["distance"] = no.apply(tail_distance, axis=1)
    no = no[no["distance"].isin([1, 2]) & no["best_ask"].between(0.005, 0.97)].copy()
    no = no.merge(current_yes, on=["orderbook_file", "city", "target_date", "decision_hour_local"], how="inner")
    no["no_loses"] = no["winner_label"].astype(str).eq(no["bracket"].astype(str))
    no["no_pnl"] = np.where(no["no_loses"], -no["best_ask"], 1.0 - no["best_ask"])
    no["current_yes_wins"] = no["winner_label"].astype(str).eq(no["current_bracket"].astype(str))
    no["yes_current_pnl"] = no["current_yes_wins"].astype(float) - no["yes_current_ask"]
    no["skip_over_wins_no_only"] = (~no["current_yes_wins"]) & (~no["no_loses"])
    no["row_key"] = (
        no["city"].astype(str)
        + "|"
        + no["target_date"].astype(str)
        + "|"
        + no["decision_hour_local"].astype(str)
        + "|"
        + no["bracket"].astype(str)
    )
    coverage = {
        "source_aligned_whitelist_cities": len(whitelist),
        "pm_history_city_dates_loaded": len(pm_history),
        "observed_rows": int(len(observed)),
        "joined_quote_rows": int(len(joined)),
        "paired_tail_no_rows": int(len(no)),
        "date_min": str(no["target_date"].min()) if not no.empty else None,
        "date_max": str(no["target_date"].max()) if not no.empty else None,
        "active_dates": int(no["target_date"].nunique()) if not no.empty else 0,
        **orderbook_meta,
    }
    return no, coverage


def dedupe_strategy(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.sort_values(["decision_hour_local", "snapshot_ts_utc", "city", "target_date", "bracket"])
        .drop_duplicates(["city", "target_date", "bracket"], keep="first")
        .copy()
    )


def summarize(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {"rows": 0}
    no_cost = float(df["best_ask"].sum())
    no_pnl = float(df["no_pnl"].sum())
    yes_cost = float(df["yes_current_ask"].sum())
    yes_pnl = float(df["yes_current_pnl"].sum())
    daily = df.groupby("target_date").agg(no_pnl=("no_pnl", "sum"), yes_pnl=("yes_current_pnl", "sum"))
    return {
        "rows": int(len(df)),
        "active_dates": int(df["target_date"].nunique()),
        "cities": int(df["city"].nunique()),
        "avg_no_ask": float(df["best_ask"].mean()),
        "avg_yes_current_ask": float(df["yes_current_ask"].mean()),
        "no_cost": no_cost,
        "no_pnl": no_pnl,
        "no_roi": no_pnl / no_cost if no_cost else None,
        "yes_cost": yes_cost,
        "yes_pnl": yes_pnl,
        "yes_roi": yes_pnl / yes_cost if yes_cost else None,
        "no_minus_yes_pnl": no_pnl - yes_pnl,
        "no_minus_yes_roi": (no_pnl / no_cost - yes_pnl / yes_cost) if no_cost and yes_cost else None,
        "no_win_rate": float((~df["no_loses"]).mean()),
        "current_win_rate": float(df["current_yes_wins"].mean()),
        "no_loses_rate": float(df["no_loses"].mean()),
        "skip_over_rate": float(df["skip_over_wins_no_only"].mean()),
        "no_positive_date_rate": float((daily["no_pnl"] > 0).mean()),
        "yes_positive_date_rate": float((daily["yes_pnl"] > 0).mean()),
    }


def bootstrap_delta(df: pd.DataFrame, reps: int = 3000) -> dict[str, Any]:
    if df.empty or df["target_date"].nunique() < 3:
        return {"roi_delta_no_minus_yes": None, "ci95": [None, None], "reps": 0}
    daily = df.groupby("target_date").agg(
        no_cost=("best_ask", "sum"),
        no_pnl=("no_pnl", "sum"),
        yes_cost=("yes_current_ask", "sum"),
        yes_pnl=("yes_current_pnl", "sum"),
    )

    def delta(frame: pd.DataFrame, idx: np.ndarray | None = None) -> float:
        work = frame if idx is None else frame.iloc[idx]
        no_cost = float(work["no_cost"].sum())
        yes_cost = float(work["yes_cost"].sum())
        if no_cost <= 0 or yes_cost <= 0:
            return float("nan")
        return float(work["no_pnl"].sum() / no_cost - work["yes_pnl"].sum() / yes_cost)

    point = delta(daily)
    rng = np.random.default_rng(20260616)
    vals = []
    for _ in range(reps):
        idx = rng.integers(0, len(daily), len(daily))
        v = delta(daily, idx)
        if math.isfinite(v):
            vals.append(v)
    lo, hi = np.quantile(vals, [0.025, 0.975]) if vals else (float("nan"), float("nan"))
    return {"roi_delta_no_minus_yes": float(point), "ci95": [float(lo), float(hi)], "reps": len(vals)}


def profile_masks(df: pd.DataFrame) -> dict[str, pd.Series]:
    d1 = df["distance"].eq(1)
    d2 = df["distance"].eq(2)
    return {
        "d1_all_h13_17": d1,
        "d1_ask75_decline05": d1 & df["best_ask"].ge(0.75) & df["decline"].ge(0.5),
        "d1_ask85_decline05": d1 & df["best_ask"].ge(0.85) & df["decline"].ge(0.5),
        "d1_ask75_decline10": d1 & df["best_ask"].ge(0.75) & df["decline"].ge(1.0),
        "d2_ask75_decline05": d2 & df["best_ask"].ge(0.75) & df["decline"].ge(0.5),
    }


def evaluate(q: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    frames: list[pd.DataFrame] = []
    for profile, mask in profile_masks(q).items():
        selected = dedupe_strategy(q[mask].copy())
        selected["profile"] = profile
        frames.append(selected)
        for period_name, period_mask in {
            "train": selected["target_date"] < SPLIT_DATE,
            "holdout": selected["target_date"] >= SPLIT_DATE,
            "new_2026_06_10_14": selected["target_date"] >= NEW_DATES_START,
            "all": pd.Series(True, index=selected.index),
        }.items():
            frame = selected[period_mask].copy()
            sm = summarize(frame)
            boot = bootstrap_delta(frame)
            rows.append({"profile": profile, "period": period_name, **sm, **boot})
    return (
        pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(),
        pd.DataFrame(rows),
    )


def write_markdown(payload: dict[str, Any], summary: pd.DataFrame) -> None:
    self_check = payload["data_self_check"]
    gate = payload["clob_gate"]

    def pick(profile: str, period: str) -> dict[str, Any]:
        row = summary[(summary["profile"].eq(profile)) & (summary["period"].eq(period))]
        return row.iloc[0].to_dict() if len(row) else {"rows": 0}

    core_hold = pick("d1_ask75_decline05", "holdout")
    core_new = pick("d1_ask75_decline05", "new_2026_06_10_14")
    strict_hold = pick("d1_ask85_decline05", "holdout")
    d2_hold = pick("d2_ask75_decline05", "holdout")

    lines = [
        "# Theta NO Carry Expanded Replay v4",
        "",
        "Status: snapshot",
        f"Generated: {payload['generated_at_utc']}",
        "Target metric: `source_aligned_theta_no_carry_vs_current_yes` = 在历史 source-aligned whitelist 城市里，用同一 orderbook 文件比较 d1/d2 高 ask NO carry 和当前 running-max 档 YES。",
        "",
        "## 数据快照",
        "",
        "- 数据更新: 已使用 2026-05-19..2026-06-14 已结算 pm_history + raw orderbook；2026-06-15/16 orderbook 虽已同步，但 pm_history 未结算，不进入 PnL。",
        "- 天气补全: 默认 WU/IEM cache 停在 2026-06-09 UTC；本次用 `theta_no_wu_obs_patch_v1` 研究补丁抓取 36 个 source-aligned 城市的 2026-05-19..2026-06-14 IEM/METAR，并重建 observed running-max detail。",
        f"- whitelist cities: {payload['coverage']['source_aligned_whitelist_cities']}; paired tail NO quote rows: {payload['coverage']['paired_tail_no_rows']}; active dates: {payload['coverage']['active_dates']}.",
        f"- orderbook files seen: {payload['coverage']['orderbook_files_seen']}; quote rows after hourly dedupe: {payload['coverage']['quote_rows_after_hourly_dedupe']}.",
        f"- fact_built_at_utc: `{self_check['fact_trades_max_built_at_utc']}`。",
        f"- fact_trades trade_class: `{self_check['fact_trades_by_class']}`。",
        f"- fact_trades settlement_status: `{self_check['fact_trades_by_settlement_status']}`。",
        f"- fact_signal_candidates coverage: `{self_check['fact_signal_candidate_coverage']}`。",
        f"- CLOB orders/fills join: `{self_check['clob_order_fill_join']}`。",
        f"- CLOB coverage gate: gate_pass={gate.get('gate_pass')}, missing_order_rows={gate.get('missing_order_rows')}, over_order_keys={gate.get('over_order_keys')}, db_fill_cost_minus_fact_cost={gate.get('db_fill_cost_minus_fact_cost')}.",
        "",
        "## 人话结论",
        "",
        "补数据后，NO carry 和当前最高温 YES 仍然不是“完全同一张票”：NO d1 输在最终刚好落到上方一档，赢在当前档守住或直接跳过 d1；当前 YES 只赢在最终等于当前档。也就是说，二者共享同一个 no-reheat 物理判断，但 payout 不同。",
        "",
        f"交易上更重要的是：扩展到 6/14 后，核心 `d1 ask>=0.75 + decline>=0.5` holdout NO ROI 是 {pct(core_hold.get('no_roi'))}，当前 YES ROI 是 {pct(core_hold.get('yes_roi'))}，NO-YES ROI 差 {pct(core_hold.get('no_minus_yes_roi'))}，CI95 [{pct(core_hold.get('ci95', [None, None])[0])}, {pct(core_hold.get('ci95', [None, None])[1])}]。新增 6/10-6/14 单独看，NO ROI {pct(core_new.get('no_roi'))}，当前 YES ROI {pct(core_new.get('yes_roi'))}。",
        "",
        f"这说明“补更多历史”是对的，而且已经把样本从原来 21 个 replay 日期推进到 {payload['coverage']['active_dates']} 个 active replay 日期；但这次补出来的新增日期没有把证据推到 live。真正卡点仍是：高 ask carry 的可交易样本有限，且 NO 相对当前 YES 的优势区间还压着 0。",
        "",
        "## 关键切片",
        "",
        "| profile | period | rows | dates | NO ask | NO ROI | YES ROI | NO-YES ROI | CI95 | NO lose | skip-over |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for profile in ["d1_ask75_decline05", "d1_ask85_decline05", "d1_ask75_decline10", "d2_ask75_decline05"]:
        for period in ["holdout", "new_2026_06_10_14", "all"]:
            r = pick(profile, period)
            if int(r.get("rows", 0) or 0) == 0:
                continue
            ci = r.get("ci95", [None, None])
            lines.append(
                f"| `{profile}` | {period} | {int(r['rows'])} | {int(r['active_dates'])} | {fnum(r['avg_no_ask'])} | "
                f"{pct(r['no_roi'])} | {pct(r['yes_roi'])} | {pct(r['no_minus_yes_roi'])} | "
                f"[{pct(ci[0])}, {pct(ci[1])}] | {pct(r['no_loses_rate'], signed=False)} | {pct(r['skip_over_rate'], signed=False)} |"
            )
    lines.extend(
        [
            "",
            "## 交易动作",
            "",
            "- 不上 live：这份 expanded replay 是 opportunity/orderbook replay，不是 shadow/paper/live fill；而且核心 NO-over-YES 优势 CI 仍跨 0。",
            "- 下一步如果继续，只应该做 shadow-only sibling selector：同一 city-day 同时记录 `current YES`、`d1 NO`、`d2 NO` 的可买价和事后结算，让 live 之前先验证表达选择，而不是直接发真钱。",
            "",
            "## 产物",
            "",
            f"- CSV: `{payload['outputs']['quote_rows']}`",
            f"- CSV: `{payload['outputs']['strategy_rows']}`",
            f"- CSV: `{payload['outputs']['summary']}`",
            f"- JSON: `{OUT_JSON.relative_to(ROOT)}`",
        ]
    )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    quote_rows, coverage = build_quote_rows()
    strategy_rows, summary = evaluate(quote_rows)

    quote_path = OUT_DIR / "expanded_quote_rows.csv"
    strategy_path = OUT_DIR / "paired_strategy_rows.csv"
    summary_path = OUT_DIR / "paired_summary.csv"
    quote_rows.to_csv(quote_path, index=False)
    strategy_rows.to_csv(strategy_path, index=False)
    summary.to_csv(summary_path, index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "status": "snapshot",
        "target_metric": "source_aligned_theta_no_carry_vs_current_yes",
        "date_range": {"start": START_DATE, "end": END_DATE, "split": SPLIT_DATE, "new_start": NEW_DATES_START},
        "decision_hours": sorted(DECISION_HOURS),
        "data_self_check": data_self_check(),
        "clob_gate": load_gate(),
        "coverage": coverage,
        "outputs": {
            "quote_rows": str(quote_path.relative_to(ROOT)),
            "strategy_rows": str(strategy_path.relative_to(ROOT)),
            "summary": str(summary_path.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
        "summary": summary.to_dict(orient="records"),
        "verdict": {
            "live_ready": False,
            "reason": "Expanded opportunity replay still has limited high-ask carry rows and NO-vs-current-YES confidence interval crosses zero.",
        },
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_markdown(payload, summary)
    print(json.dumps({"coverage": coverage, "outputs": payload["outputs"], "verdict": payload["verdict"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
