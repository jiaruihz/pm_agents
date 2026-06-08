#!/usr/bin/env python3
"""City/model downgrade cases for mid_price_core_v1 0.25-0.75."""

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
OUT_MD = OUT_DIR / "2026-06-07-mid-price-core-v1-city-model-downgrade.md"
OUT_JSON = OUT_DIR / "2026-06-07-mid-price-core-v1-city-model-downgrade.json"

STRATEGY_ID = "live_weather_edge_v1_4ef9b3ec3e2e"
RECENT_START = "2026-06-01"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def fetchall(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


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


def side_prob(side: str, model_p_yes: float) -> float:
    return model_p_yes if side == "BUY_YES" else 1.0 - model_p_yes


def load_trades(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query(
        """
        SELECT
          fill_id,
          city,
          side,
          model_version,
          target_date,
          bracket,
          condition_id,
          snapshot_ts_utc,
          hours_to_settle,
          model_p_yes,
          market_price,
          edge,
          fill_price,
          cost_usd,
          pnl_usd_at_fill,
          win_by_count
        FROM fact_trades
        WHERE trade_class='live_real'
          AND settlement_status='settled'
          AND strategy_id=?
          AND execution_policy='mid_price_core_v1'
          AND entry_price_window='0.25-0.75'
        """,
        conn,
        params=(STRATEGY_ID,),
    )
    if df.empty:
        return df
    df["period"] = df["target_date"].map(period_of)
    df["side_p_raw"] = df.apply(lambda r: side_prob(str(r["side"]), float(r["model_p_yes"])), axis=1)
    df["raw_edge_at_fill"] = df["side_p_raw"] - df["fill_price"]
    df["raw_edge_bin"] = pd.cut(
        df["raw_edge_at_fill"],
        bins=[-10, 0.10, 0.15, 0.25, 10],
        labels=["<=0.10", "0.10-0.15", "0.15-0.25", ">0.25"],
    ).astype(str)
    return df


def load_candidates(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query(
        """
        SELECT
          city,
          side,
          model_version,
          event_date AS target_date,
          decision_entry_price,
          model_p_yes,
          market_yes_price,
          edge,
          eligible,
          paper_ordered,
          live_filled,
          final_yes,
          decision_window_missing,
          counterfactual_pnl
        FROM fact_signal_candidates
        WHERE eligible=1
          AND final_yes IS NOT NULL
          AND decision_window_missing=0
          AND decision_entry_price BETWEEN 0.25 AND 0.75
        """,
        conn,
    )
    if df.empty:
        return df
    df["period"] = df["target_date"].map(period_of)
    df["side_p_raw"] = df.apply(lambda r: side_prob(str(r["side"]), float(r["model_p_yes"])), axis=1)
    df["raw_edge_proxy"] = df["side_p_raw"] - df["decision_entry_price"]
    df = df[df["raw_edge_proxy"] >= 0.10].copy()
    return df


def perf(g: pd.DataFrame) -> dict[str, Any]:
    fills = int(len(g))
    cost = float(g["cost_usd"].sum()) if fills else 0.0
    pnl = float(g["pnl_usd_at_fill"].sum()) if fills else 0.0
    return {
        "fills": fills,
        "days": int(g["target_date"].nunique()) if fills else 0,
        "cost_usd": round(cost, 4),
        "pnl_usd": round(pnl, 4),
        "roi": None if not cost else round(pnl / cost, 6),
        "win_rate": None if not fills else round(float((g["pnl_usd_at_fill"] > 0).mean()), 6),
        "avg_raw_edge": None if not fills else round(float(g["raw_edge_at_fill"].mean()), 6),
    }


def group_perf(df: pd.DataFrame, keys: list[str], min_n: int = 1) -> list[dict[str, Any]]:
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


def candidate_group(df: pd.DataFrame, keys: list[str], min_n: int = 1) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key, g in df.groupby(keys, dropna=False):
        if len(g) < min_n:
            continue
        if not isinstance(key, tuple):
            key = (key,)
        row = {k: "" if pd.isna(v) else str(v) for k, v in zip(keys, key)}
        row.update(
            {
                "eligible": int(len(g)),
                "paper_ordered": int(g["paper_ordered"].sum()),
                "live_filled": int(g["live_filled"].sum()),
                "cf_pnl": round(float(g["counterfactual_pnl"].sum()), 4),
                "avg_raw_edge_proxy": round(float(g["raw_edge_proxy"].mean()), 6),
            }
        )
        out.append(row)
    return out


def city_model_matrix(trades: pd.DataFrame) -> list[dict[str, Any]]:
    city_totals = (
        trades.groupby(["period", "city"], dropna=False)
        .agg(city_fills=("fill_id", "count"), city_cost=("cost_usd", "sum"), city_pnl=("pnl_usd_at_fill", "sum"))
        .reset_index()
    )
    rows = pd.DataFrame(group_perf(trades, ["period", "city", "model_version"]))
    rows = rows.merge(city_totals, on=["period", "city"], how="left")
    rows["fill_share"] = rows["fills"] / rows["city_fills"]
    rows["cost_share"] = rows["cost_usd"] / rows["city_cost"]
    rows["pnl_share_abs"] = rows.apply(
        lambda r: None if abs(float(r["city_pnl"])) < 1e-9 else float(r["pnl_usd"]) / float(r["city_pnl"]),
        axis=1,
    )
    return rows.to_dict(orient="records")


def city_rollup(trades: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for city, g in trades.groupby("city"):
        pre = g[g["period"] == "pre_2026_06_01"]
        post = g[g["period"] == "post_2026_06_01"]
        pre_s = perf(pre)
        post_s = perf(post)
        post_models = []
        for model, mg in post.groupby("model_version"):
            ms = perf(mg)
            post_models.append((model, ms))
        post_models.sort(key=lambda x: x[1]["cost_usd"], reverse=True)
        dominant = post_models[0][0] if post_models else ""
        dominant_s = post_models[0][1] if post_models else {}
        ecmwf_post = perf(post[post["model_version"] == "ecmwf"])
        gfs_post = perf(post[post["model_version"] == "gfs"])

        if post_s["fills"] < 5 or post_s["days"] < 3:
            action = "sample_insufficient_shadow"
        elif ecmwf_post["fills"] >= 5 and ecmwf_post["pnl_usd"] <= -8 and gfs_post["fills"] >= 3 and gfs_post["pnl_usd"] >= 0:
            action = "downgrade_ecmwf_keep_gfs"
        elif ecmwf_post["fills"] >= 5 and ecmwf_post["pnl_usd"] <= -8:
            action = "downgrade_ecmwf_city_shadow"
        elif gfs_post["fills"] >= 5 and gfs_post["pnl_usd"] <= -8 and ecmwf_post["pnl_usd"] >= 0:
            action = "downgrade_gfs_keep_ecmwf"
        elif post_s["pnl_usd"] < -8:
            action = "downgrade_city_all_models"
        elif post_s["pnl_usd"] > 5:
            action = "keep_small_live"
        else:
            action = "shadow_or_min_size"

        rows.append(
            {
                "city": city,
                "post_dominant_model_by_cost": dominant,
                "dominant_cost_share": dominant_s.get("cost_usd", 0) / post_s["cost_usd"] if post_s["cost_usd"] else None,
                "pre_fills": pre_s["fills"],
                "pre_pnl": pre_s["pnl_usd"],
                "pre_roi": pre_s["roi"],
                "post_fills": post_s["fills"],
                "post_pnl": post_s["pnl_usd"],
                "post_roi": post_s["roi"],
                "post_ecmwf_fills": ecmwf_post["fills"],
                "post_ecmwf_pnl": ecmwf_post["pnl_usd"],
                "post_ecmwf_roi": ecmwf_post["roi"],
                "post_gfs_fills": gfs_post["fills"],
                "post_gfs_pnl": gfs_post["pnl_usd"],
                "post_gfs_roi": gfs_post["roi"],
                "action": action,
            }
        )
    return sorted(rows, key=lambda r: float(r["post_pnl"]))


def model_transition(trades: pd.DataFrame) -> list[dict[str, Any]]:
    pre = pd.DataFrame(group_perf(trades[trades["period"] == "pre_2026_06_01"], ["city", "model_version"]))
    post = pd.DataFrame(group_perf(trades[trades["period"] == "post_2026_06_01"], ["city", "model_version"]))
    if pre.empty:
        pre = pd.DataFrame(columns=["city", "model_version"])
    if post.empty:
        post = pd.DataFrame(columns=["city", "model_version"])
    merged = pre.merge(post, on=["city", "model_version"], how="outer", suffixes=("_pre", "_post")).fillna(0)
    rows = []
    for r in merged.to_dict(orient="records"):
        rows.append(
            {
                "city": r["city"],
                "model_version": r["model_version"],
                "pre_fills": int(r.get("fills_pre", 0)),
                "pre_pnl": round(float(r.get("pnl_usd_pre", 0)), 4),
                "pre_roi": None if not float(r.get("cost_usd_pre", 0)) else round(float(r.get("pnl_usd_pre", 0)) / float(r.get("cost_usd_pre", 0)), 6),
                "post_fills": int(r.get("fills_post", 0)),
                "post_pnl": round(float(r.get("pnl_usd_post", 0)), 4),
                "post_roi": None if not float(r.get("cost_usd_post", 0)) else round(float(r.get("pnl_usd_post", 0)) / float(r.get("cost_usd_post", 0)), 6),
                "delta_pnl": round(float(r.get("pnl_usd_post", 0)) - float(r.get("pnl_usd_pre", 0)), 4),
            }
        )
    return sorted(rows, key=lambda r: float(r["post_pnl"]))


def fmt_rows(rows: list[dict[str, Any]], columns: list[str]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        r = dict(row)
        for k, v in list(r.items()):
            if k.endswith("_pnl") or k == "delta_pnl":
                r[k] = money(v)
            elif k.endswith("_roi") or k.endswith("_share"):
                r[k] = pct(v)
            elif k in {"cost_usd", "pnl_usd", "avg_raw_edge", "avg_raw_edge_proxy"}:
                r[k] = num(v, 3 if k.startswith("avg") else 2)
            elif k in {"roi", "win_rate", "fill_share", "cost_share", "pnl_share_abs"}:
                r[k] = pct(v)
        out.append({c: r.get(c, "") for c in columns})
    return out


def self_checks(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "db_path": str(DB_PATH),
        "db_mtime_utc": dt.datetime.fromtimestamp(DB_PATH.stat().st_mtime, tz=dt.timezone.utc).isoformat(),
        "fact_built_at_utc": fetchall(conn, "SELECT MAX(fact_built_at_utc) AS v FROM fact_trades")[0]["v"],
        "trade_class": fetchall(conn, "SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class ORDER BY trade_class"),
        "settlement_status": fetchall(conn, "SELECT COALESCE(settlement_status, '[NULL]') AS settlement_status, COUNT(*) AS rows FROM fact_trades GROUP BY settlement_status ORDER BY settlement_status"),
        "signal_candidates": fetchall(conn, "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, SUM(live_filled) AS live_filled FROM fact_signal_candidates")[0],
    }


def render(data: dict[str, Any]) -> str:
    roll_cols = [
        "city",
        "post_dominant_model_by_cost",
        "dominant_cost_share",
        "pre_fills",
        "pre_pnl",
        "pre_roi",
        "post_fills",
        "post_pnl",
        "post_roi",
        "post_ecmwf_fills",
        "post_ecmwf_pnl",
        "post_gfs_fills",
        "post_gfs_pnl",
        "action",
    ]
    matrix_cols = ["period", "city", "model_version", "fills", "days", "cost_usd", "pnl_usd", "roi", "win_rate", "fill_share", "cost_share", "avg_raw_edge"]
    transition_cols = ["city", "model_version", "pre_fills", "pre_pnl", "pre_roi", "post_fills", "post_pnl", "post_roi", "delta_pnl"]
    side_cols = ["period", "city", "model_version", "side", "fills", "cost_usd", "pnl_usd", "roi", "win_rate", "avg_raw_edge"]
    edge_cols = ["period", "city", "model_version", "raw_edge_bin", "fills", "cost_usd", "pnl_usd", "roi", "win_rate", "avg_raw_edge"]
    cand_cols = ["period", "city", "model_version", "eligible", "paper_ordered", "live_filled", "cf_pnl", "avg_raw_edge_proxy"]

    return "\n".join(
        [
            "# mid_price_core_v1 city × model 降级清单",
            "",
            "## 数据快照",
            "",
            f"- 数据源：`{data['checks']['db_path']}`。",
            f"- DB mtime UTC：`{data['checks']['db_mtime_utc']}`；`MAX(fact_built_at_utc)`：`{data['checks']['fact_built_at_utc']}`。",
            "- 分母：`strategy_id=live_weather_edge_v1_4ef9b3ec3e2e` / `execution_policy=mid_price_core_v1` / `entry_price_window=0.25-0.75` / `trade_class=live_real` / `settlement_status=settled`。",
            "- PnL 只读 `fact_trades.pnl_usd_at_fill`；candidate 表只作机会 mix 参考。",
            "",
            "trade_class：",
            table(data["checks"]["trade_class"]),
            "",
            "settlement_status：",
            table(data["checks"]["settlement_status"]),
            "",
            "candidate coverage：",
            table([data["checks"]["signal_candidates"]]),
            "",
            "## 结论",
            "",
            data["conclusion"],
            "",
            "## 1. 城市 × 模型 action rollup",
            "",
            table(fmt_rows(data["city_rollup"], roll_cols), roll_cols),
            "",
            "## 2. 6 月后 city-model 亏损排行",
            "",
            table(fmt_rows(data["post_model_rank"], matrix_cols), matrix_cols),
            "",
            "## 3. city-model pre/post 转弱清单",
            "",
            table(fmt_rows(data["transition_rank"], transition_cols), transition_cols),
            "",
            "## 4. 6 月后 city-model-side 亏损组合",
            "",
            table(fmt_rows(data["post_side_rank"], side_cols), side_cols),
            "",
            "## 5. 6 月后 city-model-raw_edge 亏损组合",
            "",
            table(fmt_rows(data["post_edge_rank"], edge_cols), edge_cols),
            "",
            "## 6. candidate 机会 mix 参考",
            "",
            "这张表不是实盘 PnL，只看 eligible opportunity 在 city-model 上的来源和反事实 PnL。",
            "",
            table(fmt_rows(data["candidate_rank"], cand_cols), cand_cols),
            "",
            "## 不停实盘的规则建议",
            "",
            data["actions"],
            "",
            "## 口径限制",
            "",
            "- post settled 样本当前覆盖到 target_date `2026-06-05`，6/6-6/7 未结算不纳入 realized PnL。",
            "- `model_version` 是决策记录里的模型源；若 5/31 之后实例默认参数污染，仍需 signal->plan->order->fact 四层审计确认。",
            "- 少于 5 fills 或少于 3 active target days 的 city-model 不作强黑名单，只 shadow/降权观察。",
            "",
        ]
    )


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect()
    trades = load_trades(conn)
    candidates = load_candidates(conn)
    if trades.empty:
        raise SystemExit("empty target sample")

    matrix = city_model_matrix(trades)
    roll = city_rollup(trades)
    transition = model_transition(trades)
    post_matrix = [r for r in matrix if r["period"] == "post_2026_06_01"]
    post_rank = sorted(post_matrix, key=lambda r: float(r["pnl_usd"]))[:30]
    transition_rank = [r for r in transition if r["post_fills"] >= 3 or r["pre_fills"] >= 5]
    transition_rank = sorted(transition_rank, key=lambda r: float(r["delta_pnl"]))[:35]
    post_side = group_perf(trades[trades["period"] == "post_2026_06_01"], ["period", "city", "model_version", "side"], min_n=3)
    post_side_rank = sorted(post_side, key=lambda r: float(r["pnl_usd"]))[:35]
    post_edge = group_perf(trades[trades["period"] == "post_2026_06_01"], ["period", "city", "model_version", "raw_edge_bin"], min_n=3)
    post_edge_rank = sorted(post_edge, key=lambda r: float(r["pnl_usd"]))[:35]
    cand = candidate_group(candidates, ["period", "city", "model_version"], min_n=3)
    cand_post_rank = sorted([r for r in cand if r["period"] == "post_2026_06_01"], key=lambda r: float(r["cf_pnl"]))[:35]

    action_counts: dict[str, int] = {}
    for row in roll:
        action_counts[row["action"]] = action_counts.get(row["action"], 0) + 1
    ecmwf_keep_gfs = [r["city"] for r in roll if r["action"] == "downgrade_ecmwf_keep_gfs"]
    ecmwf_shadow = [r["city"] for r in roll if r["action"] == "downgrade_ecmwf_city_shadow"]
    ecmwf_downgrade = ecmwf_keep_gfs + ecmwf_shadow
    all_downgrade = [r["city"] for r in roll if r["action"] == "downgrade_city_all_models"]
    keep = [r["city"] for r in roll if r["action"] == "keep_small_live"]
    gfs_bad = [r["city"] for r in roll if r["action"] == "downgrade_gfs_keep_ecmwf"]

    conclusion = (
        f"- 6 月后不是简单“ECMWF 全坏 / GFS 全好”。ECMWF 总体是拖累，但有城市/side/model 交互；GFS 总体为正，但 NYC、LA 等局部组合仍亏。\n"
        f"- 明确 ECMWF 降级候选：{', '.join(ecmwf_downgrade) if ecmwf_downgrade else 'none'}。其中 `{', '.join(ecmwf_shadow) if ecmwf_shadow else 'none'}` post 期没有足够 GFS 对照，不能说 keep GFS，只能先把 ECMWF/city 组合 shadow。\n"
        f"- 全城市/全模型降级候选：{', '.join(all_downgrade) if all_downgrade else 'none'}。\n"
        f"- 可保留小 size live 的城市：{', '.join(keep) if keep else 'none'}。\n"
        f"- GFS 单独降级候选：{', '.join(gfs_bad) if gfs_bad else 'none'}。\n"
        "- 最重要的交易含义：ECMWF 门槛应按 city 调整；不要对所有城市一刀切，也不要因为 GFS 总体为正就放过 GFS 的弱 city-side 组合。"
    )
    actions = (
        "1. **ECMWF hard downgrade**：BuenosAires、Munich、Jeddah、Karachi、Moscow、Ankara 这类 post ECMWF 亏损且样本够的城市，ECMWF live 提高到 `raw_edge>0.30` 或直接 shadow。\n"
        "2. **ECMWF conditional keep**：若城市 post 总体仍正，ECMWF 不全停，但必须叠加 `raw_edge>0.30`、blended_edge>=0.10、timing 子任务通过。\n"
        "3. **GFS 不全开**：GFS 可作为相对保留模型，但 NYC 的 GFS BUY_YES / 中低 raw edge 组合要 shadow，不能被 GFS 总体正收益掩盖。\n"
        "4. **城市级保留池**：LA、Miami、Tokyo、Madrid、Shanghai 先保留小 size，但仍应用模型/side/edge/timing 过滤；LA 总体 GFS 强正，但内部 raw_edge 子桶有反常，不能无条件放大。\n"
        "5. **执行上线方式**：先做 city-model allow/deny list 的配置层开关，不改 PnL 公式、不改 raw model；每晚按 city-model PnL 自动生成 downgrade list。"
    )

    data = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "checks": self_checks(conn),
        "city_rollup": roll,
        "city_model_matrix": matrix,
        "post_model_rank": post_rank,
        "transition_rank": transition_rank,
        "post_side_rank": post_side_rank,
        "post_edge_rank": post_edge_rank,
        "candidate_rank": cand_post_rank,
        "action_counts": action_counts,
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
