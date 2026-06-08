#!/usr/bin/env python3
"""Overlay side-band gates on blocked v1_25_75 city/ECMWF live fills."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
DB_PATH = ROOT / "runtime" / "weather.db"
OUT_DIR = ROOT / "docs" / "analysis" / "2026-06"
OUT_MD = OUT_DIR / "2026-06-07-v1-ecmwf-blocked-side-band-overlay.md"
OUT_JSON = OUT_DIR / "2026-06-07-v1-ecmwf-blocked-side-band-overlay.json"

STRATEGY_ID = "live_weather_edge_v1_4ef9b3ec3e2e"
RECENT_START = "2026-06-01"
BLOCKED_CITIES = ["BuenosAires", "Munich", "Jeddah", "Karachi", "Moscow", "Ankara"]


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def fetchall(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def side_prob(side: str, model_p_yes: float) -> float:
    return model_p_yes if side == "BUY_YES" else 1.0 - model_p_yes


def period_of(date: str) -> str:
    return "post_2026_06_01" if date >= RECENT_START else "pre_2026_06_01"


def money(value: Any) -> str:
    if value is None:
        return ""
    return f"{float(value):+.2f}"


def pct(value: Any) -> str:
    if value is None:
        return ""
    return f"{float(value) * 100:.1f}%"


def num(value: Any, digits: int = 3) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass
    return f"{float(value):.{digits}f}"


def table(rows: list[dict[str, Any]], columns: list[str] | None = None) -> str:
    if not rows:
        return "_No rows._"
    cols = columns or list(rows[0].keys())
    out = ["| " + " | ".join(cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    for row in rows:
        out.append("| " + " | ".join(str(row.get(col, "")) for col in cols) + " |")
    return "\n".join(out)


def load_trades(conn: sqlite3.Connection) -> pd.DataFrame:
    placeholders = ",".join("?" for _ in BLOCKED_CITIES)
    df = pd.read_sql_query(
        f"""
        SELECT
          fill_id,
          city,
          side,
          model_version,
          target_date,
          bracket,
          condition_id,
          market_price,
          fill_price,
          model_p_yes,
          edge,
          abs_edge,
          cost_usd,
          pnl_usd_at_fill,
          win_by_count
        FROM fact_trades
        WHERE trade_class='live_real'
          AND settlement_status='settled'
          AND strategy_id=?
          AND execution_policy='mid_price_core_v1'
          AND entry_price_window='0.25-0.75'
          AND model_version='ecmwf'
          AND city IN ({placeholders})
        """,
        conn,
        params=(STRATEGY_ID, *BLOCKED_CITIES),
    )
    if df.empty:
        return df
    df["period"] = df["target_date"].map(period_of)
    df["side_p_raw"] = df.apply(lambda r: side_prob(str(r["side"]), float(r["model_p_yes"])), axis=1)
    df["raw_edge_at_entry"] = df["side_p_raw"] - df["market_price"]
    df["raw_edge_at_fill"] = df["side_p_raw"] - df["fill_price"]
    df["side_band_pass"] = df.apply(_side_band_pass, axis=1)
    df["side_band_reason"] = df.apply(_side_band_reason, axis=1)
    return df


def _side_band_pass(row: pd.Series) -> bool:
    side = str(row["side"])
    price = float(row["market_price"])
    edge = float(row["raw_edge_at_entry"])
    if side == "BUY_YES":
        return 0.20 <= price < 0.45 and edge >= 0.20
    if side == "BUY_NO":
        return 0.35 <= price < 0.65 and edge >= 0.10
    return False


def _side_band_reason(row: pd.Series) -> str:
    side = str(row["side"])
    price = float(row["market_price"])
    edge = float(row["raw_edge_at_entry"])
    if side == "BUY_YES":
        if price < 0.20:
            return "yes_price_below_0.20"
        if price >= 0.45:
            return "yes_price_at_or_above_0.45"
        if edge < 0.20:
            return "yes_edge_below_0.20"
        return "pass"
    if side == "BUY_NO":
        if price < 0.35:
            return "no_price_below_0.35"
        if price >= 0.65:
            return "no_price_at_or_above_0.65"
        if edge < 0.10:
            return "no_edge_below_0.10"
        return "pass"
    return "bad_side"


def perf(df: pd.DataFrame) -> dict[str, Any]:
    fills = int(len(df))
    cost = float(df["cost_usd"].sum()) if fills else 0.0
    pnl = float(df["pnl_usd_at_fill"].sum()) if fills else 0.0
    return {
        "fills": fills,
        "days": int(df["target_date"].nunique()) if fills else 0,
        "cost_usd": round(cost, 4),
        "pnl_usd": round(pnl, 4),
        "roi": None if not cost else round(pnl / cost, 6),
        "win_rate": None if not fills else round(float((df["pnl_usd_at_fill"] > 0).mean()), 6),
        "avg_entry_price": None if not fills else round(float(df["market_price"].mean()), 6),
        "avg_raw_edge_entry": None if not fills else round(float(df["raw_edge_at_entry"].mean()), 6),
    }


def group(df: pd.DataFrame, keys: list[str], min_n: int = 1) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key, g in df.groupby(keys, dropna=False):
        if len(g) < min_n:
            continue
        if not isinstance(key, tuple):
            key = (key,)
        row = {k: "" if pd.isna(v) else str(v) for k, v in zip(keys, key)}
        row.update(perf(g))
        out.append(row)
    return out


def overlay_summary(df: pd.DataFrame, keys: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, g in df.groupby(keys, dropna=False):
        if not isinstance(key, tuple):
            key = (key,)
        base = perf(g)
        kept = perf(g[g["side_band_pass"]])
        filtered = perf(g[~g["side_band_pass"]])
        row = {k: "" if pd.isna(v) else str(v) for k, v in zip(keys, key)}
        row.update(
            {
                "baseline_fills": base["fills"],
                "baseline_pnl": base["pnl_usd"],
                "baseline_roi": base["roi"],
                "side_band_kept_fills": kept["fills"],
                "side_band_kept_pnl": kept["pnl_usd"],
                "side_band_kept_roi": kept["roi"],
                "filtered_fills": filtered["fills"],
                "filtered_pnl": filtered["pnl_usd"],
                "delta_if_side_band_filter": round(-float(filtered["pnl_usd"]), 4),
            }
        )
        rows.append(row)
    return rows


def fmt(rows: list[dict[str, Any]], columns: list[str]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        r = dict(row)
        for k, v in list(r.items()):
            if k.endswith("_pnl") or k == "delta_if_side_band_filter":
                r[k] = money(v)
            elif k.endswith("_roi") or k == "roi" or k == "win_rate":
                r[k] = pct(v)
            elif k in {"cost_usd", "pnl_usd", "avg_entry_price", "avg_raw_edge_entry"}:
                r[k] = num(v, 3 if k.startswith("avg") else 2)
        out.append({c: r.get(c, "") for c in columns})
    return out


def self_checks(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "db_path": str(DB_PATH),
        "db_mtime_utc": dt.datetime.fromtimestamp(DB_PATH.stat().st_mtime, tz=dt.timezone.utc).isoformat(),
        "fact_built_at_utc": fetchall(conn, "SELECT MAX(fact_built_at_utc) AS v FROM fact_trades")[0]["v"],
    }


def render(data: dict[str, Any]) -> str:
    summary_cols = [
        "period",
        "baseline_fills",
        "baseline_pnl",
        "baseline_roi",
        "side_band_kept_fills",
        "side_band_kept_pnl",
        "side_band_kept_roi",
        "filtered_fills",
        "filtered_pnl",
        "delta_if_side_band_filter",
    ]
    city_cols = ["period", "city", *summary_cols[1:]]
    side_cols = ["period", "side", *summary_cols[1:]]
    reason_cols = ["period", "side_band_reason", "fills", "cost_usd", "pnl_usd", "roi", "win_rate", "avg_entry_price", "avg_raw_edge_entry"]
    kept_cols = ["period", "city", "side", "fills", "cost_usd", "pnl_usd", "roi", "win_rate", "avg_entry_price", "avg_raw_edge_entry"]

    return "\n".join(
        [
            "# v1_25_75 blocked ECMWF cities side-band overlay",
            "",
            "## 数据快照",
            "",
            f"- 数据源：`{data['checks']['db_path']}`。",
            f"- DB mtime UTC：`{data['checks']['db_mtime_utc']}`；`MAX(fact_built_at_utc)`：`{data['checks']['fact_built_at_utc']}`。",
            f"- 样本：`{', '.join(BLOCKED_CITIES)}` 的 `ECMWF + v1_25_75 + live_real + settled` 历史 fills。",
            "- Overlay 规则：BUY_YES `0.20<=market_price<0.45 and raw_edge>=0.20`；BUY_NO `0.35<=market_price<0.65 and raw_edge>=0.10`。",
            "- 这是同一批已成交 fill 的 side-band filter overlay，不是重新撮合/重新下单模拟。",
            "",
            "## 结论",
            "",
            data["conclusion"],
            "",
            "## 1. period 总览",
            "",
            table(fmt(data["by_period"], summary_cols), summary_cols),
            "",
            "## 2. city overlay",
            "",
            table(fmt(data["by_city"], city_cols), city_cols),
            "",
            "## 3. side overlay",
            "",
            table(fmt(data["by_side"], side_cols), side_cols),
            "",
            "## 4. 被过滤原因",
            "",
            table(fmt(data["by_reason"], reason_cols), reason_cols),
            "",
            "## 5. side-band 会保留的组合",
            "",
            table(fmt(data["kept_by_city_side"], kept_cols), kept_cols),
            "",
            "## 交易含义",
            "",
            data["actions"],
            "",
            "## 口径限制",
            "",
            "- side-band overlay 只能说明这些已成交 v1_25_75 fills 是否会被 side-band gate 拦住；它不包含 side-band 可能新增的其他机会。",
            "- 使用 `market_price` 作为原始信号入场价，PnL 仍使用 `pnl_usd_at_fill`。",
            "- post realized 样本当前到 target_date 2026-06-05；未结算目标日不纳入 realized PnL。",
            "",
        ]
    )


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect()
    trades = load_trades(conn)
    if trades.empty:
        raise SystemExit("empty blocked ECMWF sample")
    by_period = overlay_summary(trades, ["period"])
    by_city = overlay_summary(trades, ["period", "city"])
    by_side = overlay_summary(trades, ["period", "side"])
    by_reason = group(trades[~trades["side_band_pass"]], ["period", "side_band_reason"])
    kept_by_city_side = group(trades[trades["side_band_pass"]], ["period", "city", "side"])

    post = next(row for row in by_period if row["period"] == "post_2026_06_01")
    pre = next(row for row in by_period if row["period"] == "pre_2026_06_01")
    conclusion = (
        f"- 对这 6 个 blocked ECMWF 城市，6 月后 baseline PnL `{post['baseline_pnl']:+.2f}`；"
        f"如果只保留会通过 side-band 的历史 fills，PnL `{post['side_band_kept_pnl']:+.2f}`，过滤掉的 fills PnL `{post['filtered_pnl']:+.2f}`，净改善 `{post['delta_if_side_band_filter']:+.2f}`。\n"
        f"- 6 月前 baseline PnL `{pre['baseline_pnl']:+.2f}`；side-band kept PnL `{pre['side_band_kept_pnl']:+.2f}`，过滤掉的 fills PnL `{pre['filtered_pnl']:+.2f}`，净变化 `{pre['delta_if_side_band_filter']:+.2f}`。\n"
        "- 因此 side-band 对这些 ECMWF 城市是有改善的，尤其是 post 期；但它不是完全解决方案，因为 side-band 仍会保留一部分亏损组合。"
    )
    actions = (
        "1. 这 6 个 city×ECMWF 从 v1_25_75 live 剔除是合理的；历史 post 期 side-band overlay 比原 v1_25_75 明显改善。\n"
        "2. 不建议把这些城市的 ECMWF 直接转成 side-band live；应先 shadow，因为 overlay 不是重新下单模拟，且 side-band 保留样本仍可能亏。\n"
        "3. 如果要探索恢复，优先 shadow `side-band + ECMWF + raw_edge/ timing 二级过滤`，而不是恢复 flat 25-75。"
    )
    data = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "checks": self_checks(conn),
        "blocked_cities": BLOCKED_CITIES,
        "by_period": by_period,
        "by_city": by_city,
        "by_side": by_side,
        "by_reason": by_reason,
        "kept_by_city_side": kept_by_city_side,
        "conclusion": conclusion,
        "actions": actions,
    }
    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    OUT_MD.write_text(render(data), encoding="utf-8")
    print(f"wrote {OUT_MD}")
    print(f"wrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
