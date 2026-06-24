#!/usr/bin/env python3
"""Review current-bracket NO target alignment and source-model overlays.

This follow-up keeps the existing previous-day PIT replay fixed, then compares
the old afternoon-peak target with payoff-aligned labels.  It is research-only:
no live config is changed and no orders are placed.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import research_current_bracket_no_pass_through_v1 as pass_through  # noqa: E402
import research_current_bracket_no_prevday_pit_shadow_v1 as pit_shadow  # noqa: E402


OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_bracket_no_payoff_source_review_v1"
OUT_JSON = OUT_DIR / "summary.json"
OUT_VARIANTS = OUT_DIR / "variant_summary.csv"
OUT_DAILY = OUT_DIR / "daily_variant_summary.csv"
OUT_ALL_LOSS = OUT_DIR / "all_loss_day_trade_details.csv"
OUT_SOURCE = OUT_DIR / "source_model_overlay_summary.csv"
OUT_SELECTED = OUT_DIR / "selected_trade_rows.csv"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-23-current-bracket-no-payoff-source-review-v1.md"

CALIBRATION_RESULTS = Path("/Users/deepsleep/projects/weather-predict/calibration_results_v5.json")
SOURCE_PROFILES = ROOT / "weather_data_feed/source_profiles.json"

SEED = 20260623
STAKE_USD = pass_through.STAKE_USD

BASE_NUM_FEATURES = list(pit_shadow.NUM_FEATURES)
EXTRA_NUM_FEATURES = [
    "calibration_best_rmse",
    "calibration_best_bias",
    "calibration_gfs_rmse",
    "calibration_ecmwf_rmse",
    "calibration_gfs_minus_ecmwf_rmse",
    "calibration_gfs_minus_ecmwf_bias",
]
NUM_FEATURES = BASE_NUM_FEATURES + EXTRA_NUM_FEATURES
CAT_FEATURES = list(pit_shadow.CAT_FEATURES) + [
    "calibration_best_model",
    "source_bucket",
    "settlement_source_class",
]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def finite_or_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: finite_or_none(v) for k, v in value.items()}
    if isinstance(value, list):
        return [finite_or_none(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def pct(value: Any) -> str:
    try:
        fval = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(fval):
        return "NA"
    return f"{100.0 * fval:+.1f}%"


def money(value: Any) -> str:
    try:
        fval = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(fval):
        return "NA"
    return f"${fval:+,.2f}"


def source_bucket(cls: Any) -> str:
    value = str(cls or "unknown")
    if value == "default_wu_station_by_rules":
        return "default_wu"
    if value == "default_source_watchlist":
        return "default_watchlist"
    if value in {"official_station_diff_confirmed", "special_source_confirmed"}:
        return "source_sensitive_confirmed"
    if value == "blocked_unresolved_settlement_basis":
        return "blocked_unresolved"
    return "other_or_unknown"


def load_calibration() -> pd.DataFrame:
    if not CALIBRATION_RESULTS.exists():
        return pd.DataFrame(columns=["city"])
    data = json.loads(CALIBRATION_RESULTS.read_text(encoding="utf-8"))
    rows = []
    for city, row in data.items():
        gfs = row.get("gfs") or {}
        ecmwf = row.get("ecmwf") or {}
        rows.append(
            {
                "city": city,
                "calibration_best_model": row.get("_best_model"),
                "calibration_best_rmse": row.get("_best_rmse"),
                "calibration_best_bias": row.get("_best_bias"),
                "calibration_gfs_rmse": gfs.get("rmse"),
                "calibration_ecmwf_rmse": ecmwf.get("rmse"),
                "calibration_gfs_bias": gfs.get("bias"),
                "calibration_ecmwf_bias": ecmwf.get("bias"),
                "calibration_gfs_n": gfs.get("n"),
                "calibration_ecmwf_n": ecmwf.get("n"),
            }
        )
    out = pd.DataFrame(rows)
    out["calibration_gfs_minus_ecmwf_rmse"] = pd.to_numeric(out["calibration_gfs_rmse"], errors="coerce") - pd.to_numeric(
        out["calibration_ecmwf_rmse"], errors="coerce"
    )
    out["calibration_gfs_minus_ecmwf_bias"] = pd.to_numeric(out["calibration_gfs_bias"], errors="coerce") - pd.to_numeric(
        out["calibration_ecmwf_bias"], errors="coerce"
    )
    return out


def load_source_profiles() -> pd.DataFrame:
    if not SOURCE_PROFILES.exists():
        return pd.DataFrame(columns=["city"])
    rows = json.loads(SOURCE_PROFILES.read_text(encoding="utf-8")).get("source_profiles", [])
    cols = [
        "city",
        "settlement_source_class",
        "official_station_or_feed",
        "mapping_rule",
        "downstream_action",
        "live_eligible",
    ]
    out = pd.DataFrame(rows)
    for col in cols:
        if col not in out.columns:
            out[col] = np.nan
    out = out[cols].copy()
    out["source_bucket"] = out["settlement_source_class"].map(source_bucket)
    return out


def build_model(c: float = 0.2) -> Pipeline:
    pre = ColumnTransformer(
        [
            (
                "num",
                Pipeline([("imputer", SimpleImputer(strategy="median")), ("scaler", StandardScaler())]),
                NUM_FEATURES,
            ),
            (
                "cat",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="most_frequent")),
                        ("onehot", OneHotEncoder(handle_unknown="ignore", min_frequency=5)),
                    ]
                ),
                CAT_FEATURES,
            ),
        ]
    )
    return Pipeline(
        [
            ("pre", pre),
            ("clf", LogisticRegression(C=c, max_iter=1000, class_weight="balanced", random_state=SEED)),
        ]
    )


def load_scored_dataset() -> tuple[pd.DataFrame, dict[str, Any]]:
    train_df = pit_shadow.load_training_dataset()
    if pit_shadow.OUT_PIT_ROWS.exists():
        pit_rows = pd.read_csv(pit_shadow.OUT_PIT_ROWS)
        pit_stats = {"source": str(pit_shadow.OUT_PIT_ROWS.relative_to(ROOT)), "reused": True}
    else:
        future_universe = pit_shadow.discover_snapshot_peak_universe(str(train_df["target_date"].max()))
        forecast_universe = (
            pd.concat([train_df[["city", "target_date", "unit"]].drop_duplicates(), future_universe], ignore_index=True)
            .drop_duplicates(["city", "target_date"])
            .reset_index(drop=True)
        )
        pit_rows, pit_stats = pit_shadow.load_prevday_pit_forecasts(forecast_universe)
    pit_wide = pit_shadow.pivot_pit(pit_rows)
    scored = pit_shadow.apply_pit_features(train_df.copy(), pit_wide)
    scored = scored[scored["pit_prevday_available"]].copy()
    scored = scored.merge(load_calibration(), on="city", how="left")
    scored = scored.merge(load_source_profiles(), on="city", how="left")
    for col in NUM_FEATURES:
        if col not in scored.columns:
            scored[col] = np.nan
    for col in CAT_FEATURES:
        if col not in scored.columns:
            scored[col] = ""
    scored["noise_margin_native"] = np.where(scored["unit"].astype(str).str.upper().eq("F"), 0.5, 0.25)
    scored["label_up_margin"] = (
        pd.to_numeric(scored["final_max_native"], errors="coerce")
        > pd.to_numeric(scored["bracket_upper"], errors="coerce") + pd.to_numeric(scored["noise_margin_native"], errors="coerce")
    ).astype(int)
    scored["label_no_wins"] = pd.to_numeric(scored["label_no_wins"], errors="coerce").astype(int)
    scored["label_afternoon_peak"] = pd.to_numeric(scored["label_afternoon_peak"], errors="coerce").astype(int)
    scored["actual_margin_to_upper_native"] = pd.to_numeric(scored["final_max_native"], errors="coerce") - pd.to_numeric(
        scored["bracket_upper"], errors="coerce"
    )
    return scored.reset_index(drop=True), pit_stats


def split_dates(df: pd.DataFrame) -> tuple[str, pd.DataFrame, pd.DataFrame]:
    dates = sorted(df["target_date"].dropna().astype(str).unique())
    split_idx = max(1, int(len(dates) * 0.70))
    split_date = dates[split_idx - 1]
    train = df[df["target_date"].astype(str) <= split_date].copy()
    holdout = df[df["target_date"].astype(str) > split_date].copy()
    return split_date, train, holdout


def model_quality(pipe: Pipeline, frame: pd.DataFrame, label_col: str) -> dict[str, Any]:
    label = frame[label_col].astype(int)
    prob = pipe.predict_proba(frame[NUM_FEATURES + CAT_FEATURES])[:, 1]
    return {
        "rows": int(len(frame)),
        "active_dates": int(frame["target_date"].nunique()),
        "label_rate": float(label.mean()),
        "auc": float(roc_auc_score(label, prob)) if label.nunique() > 1 else None,
        "brier": float(brier_score_loss(label, prob)),
        "prob_mean": float(prob.mean()),
    }


def select_top_per_date(frame: pd.DataFrame, per_date: int) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    sort_cols = ["target_date", "edge_no_win", "p_no_win", "no_ask"]
    asc = [True, False, False, True]
    ranked = frame.sort_values(sort_cols, ascending=asc).copy()
    ranked["_date_rank"] = ranked.groupby("target_date").cumcount() + 1
    return ranked[ranked["_date_rank"].le(per_date)].drop(columns=["_date_rank"]).reset_index(drop=True)


def summarize_variant(name: str, raw: pd.DataFrame, baseline: pd.DataFrame | None = None) -> dict[str, Any]:
    selected = pass_through.select_first_per_city_day(raw.copy())
    out = pass_through.summarize(name, raw, selected, baseline)
    if selected.empty:
        return out
    all_loss_days = (
        selected.groupby("target_date")["label_no_wins"].sum().reset_index().query("label_no_wins == 0")["target_date"].astype(str).tolist()
    )
    out.update(
        {
            "selected_all_loss_days": int(len(all_loss_days)),
            "selected_all_loss_trades": int(selected[selected["target_date"].astype(str).isin(all_loss_days)].shape[0]),
            "selected_all_loss_dates": ",".join(all_loss_days),
            "up_margin_win_rate": float(selected["label_up_margin"].mean()),
            "avg_actual_margin_to_upper_native": float(selected["actual_margin_to_upper_native"].mean()),
            "source_default_wu_share": float(selected["source_bucket"].astype(str).eq("default_wu").mean()),
            "calibration_gfs_best_share": float(selected["calibration_best_model"].astype(str).str.lower().eq("gfs").mean()),
        }
    )
    out["calibration_non_gfs_best_share"] = float(
        (selected["calibration_best_model"].notna() & ~selected["calibration_best_model"].astype(str).str.lower().eq("gfs")).mean()
    )
    return out


def daily_summary(frame: pd.DataFrame, variant: str) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    selected = pass_through.select_first_per_city_day(frame.copy())
    grouped = (
        selected.groupby("target_date")
        .agg(
            trades=("city", "size"),
            cities=("city", "nunique"),
            wins=("label_no_wins", "sum"),
            up_margin_wins=("label_up_margin", "sum"),
            cost_usd=("stake_cost_usd", "sum"),
            profit_usd=("stake_profit_usd", "sum"),
            avg_no_ask=("no_ask", "mean"),
            avg_p_no_win=("p_no_win", "mean"),
            avg_p_up_margin=("p_up_margin", "mean"),
            avg_margin=("actual_margin_to_upper_native", "mean"),
        )
        .reset_index()
    )
    grouped["variant"] = variant
    grouped["win_rate"] = grouped["wins"] / grouped["trades"]
    grouped["roi"] = grouped["profit_usd"] / grouped["cost_usd"]
    grouped["loss_cities"] = grouped["target_date"].map(
        selected[selected["label_no_wins"].eq(0)].groupby("target_date")["city"].apply(lambda x: ",".join(x))
    )
    grouped["win_cities"] = grouped["target_date"].map(
        selected[selected["label_no_wins"].eq(1)].groupby("target_date")["city"].apply(lambda x: ",".join(x))
    )
    return grouped


def source_overlay(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for group_col in ["source_bucket", "calibration_best_model"]:
        for value, group in frame.groupby(group_col, dropna=False):
            selected = pass_through.select_first_per_city_day(group.copy())
            if selected.empty:
                continue
            cost = float(selected["stake_cost_usd"].sum())
            profit = float(selected["stake_profit_usd"].sum())
            rows.append(
                {
                    "group_col": group_col,
                    "group_value": str(value),
                    "trades": int(len(selected)),
                    "dates": int(selected["target_date"].nunique()),
                    "cities": int(selected["city"].nunique()),
                    "wins": int(selected["label_no_wins"].sum()),
                    "win_rate": float(selected["label_no_wins"].mean()),
                    "profit_usd": profit,
                    "roi": profit / cost if cost else None,
                    "all_loss_day_trade_share": float(
                        selected["target_date"].astype(str).isin(
                            selected.groupby("target_date")["label_no_wins"].sum().loc[lambda s: s == 0].index.astype(str)
                        ).mean()
                    ),
                    "up_margin_win_rate": float(selected["label_up_margin"].mean()),
                    "avg_actual_margin_to_upper_native": float(selected["actual_margin_to_upper_native"].mean()),
                }
            )
    return pd.DataFrame(rows)


def render_md(payload: dict[str, Any], variants: pd.DataFrame, daily: pd.DataFrame, source: pd.DataFrame) -> str:
    def table(df: pd.DataFrame, cols: list[str], limit: int | None = None) -> str:
        shown = df if limit is None else df.head(limit)
        lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
        for _, row in shown.iterrows():
            vals = []
            for col in cols:
                val = row.get(col)
                if col.endswith("roi") or col.endswith("rate") or col.endswith("share"):
                    vals.append(pct(val))
                elif col.endswith("usd") or col == "profit_usd":
                    vals.append(money(val))
                elif isinstance(val, float):
                    vals.append(f"{val:.3f}")
                else:
                    vals.append("" if pd.isna(val) else str(val))
            lines.append("| " + " | ".join(vals) + " |")
        return "\n".join(lines)

    old_row = variants[variants["variant"].eq("old_afternoon_p50")]
    payoff_row = variants[variants["variant"].eq("payoff_ev05_p35_default_wu")]
    old_row = old_row.iloc[0].to_dict() if not old_row.empty else {}
    payoff_row = payoff_row.iloc[0].to_dict() if not payoff_row.empty else {}
    focus_all_loss = daily[
        daily["wins"].eq(0)
        & daily["variant"].isin(["baseline_trade_base", "old_afternoon_p50", "payoff_ev05_p35_default_wu"])
    ].sort_values(["variant", "target_date"])
    lines = [
        "# Current-Bracket NO Payoff + Source Review V1",
        "",
        "## 结论",
        "",
        "这个方向值得继续，但不是因为这次已经找到可上线规则。真正有价值的发现是：旧表达式把目标学偏了，"
        "而且历史 PIT 预报层对 source/model 的处理太粗。",
        "",
        "旧模型学的是“最高温是不是出现在午后”；这笔交易真正需要的是“current bracket 的 NO 是否会赢”，"
        "更具体地说，是最终最高温能不能明确穿过 current bracket upper 并留出 source/noise margin。",
        "",
        "状态：`research_promising_shadow_only`。不改 live。",
        "",
        "## Data Snapshot",
        "",
        f"- Generated at UTC: `{payload['generated_at_utc']}`",
        f"- PIT scored rows: `{payload['dataset']['pit_scored_rows']}`",
        f"- PIT date range: `{payload['dataset']['date_min']}`..`{payload['dataset']['date_max']}`",
        f"- Historical selected universe date range: `{payload['dataset']['selected_date_min']}`..`{payload['dataset']['selected_date_max']}`",
        f"- Previous-day forecast layer: `{payload['dataset']['pit_forecast_layer']}`",
        f"- Calibration overlay: `{payload['dataset']['calibration_results']}`",
        "",
        "重要限制：历史 previous-day PIT 层主要是 `gfs_daily`。这里的 ECMWF/ICON/JMA 只是城市级 WU calibration overlay，"
        "还不是完整 point-in-time 的 ECMWF/ICON/JMA 预报 replay。",
        "",
        "## 为什么旧 Label 不对",
        "",
        "`avg_gap` 的定义是 `previous-day forecast peak max - current bracket upper`。它只能说明前一日预报是否看起来会穿档，"
        "不是交易结算 label。",
        "",
        "payoff label 应该是 `label_no_wins`：current bracket 的 NO 是否结算为 1。对“继续升温穿档”这个 setup，"
        "更干净的机制 label 是 `final_max > current bracket upper + margin`，这里暂用 F 城 `0.5F`、C 城 `0.25C`。",
        "",
        "## 这次多跑一把后的结果",
        "",
        table(
            variants,
            [
                "variant",
                "selected_trades",
                "active_dates",
                "cities",
                "no_win_rate",
                "up_margin_win_rate",
                "roi",
                "holdout_roi",
                "selected_all_loss_days",
                "selected_all_loss_trades",
            ],
        ),
        "",
        "这张表要这么读：payoff/up-margin 模型的 holdout AUC 比 old afternoon label 更好，说明它确实更贴近结算机制；"
        "但直接拿它做 EV/threshold 选单，并没有解决 all-loss day 问题。",
        "",
        f"- old afternoon 版本：`{old_row.get('selected_trades', 'NA')}` 笔，ROI `{pct(old_row.get('roi'))}`，"
        f"holdout ROI `{pct(old_row.get('holdout_roi'))}`，全亏 active days `{old_row.get('selected_all_loss_days', 'NA')}`。",
        f"- payoff+default_wu 版本：`{payoff_row.get('selected_trades', 'NA')}` 笔，ROI `{pct(payoff_row.get('roi'))}`，"
        f"holdout ROI `{pct(payoff_row.get('holdout_roi'))}`，全亏 active days `{payoff_row.get('selected_all_loss_days', 'NA')}`。",
        "",
        "所以本轮不是“新规则已胜出”，而是“target 修正方向成立，但还缺 day-regime/source-model replay”。",
        "",
        "## Source / Model Overlay",
        "",
        table(
            source,
            [
                "group_col",
                "group_value",
                "trades",
                "dates",
                "cities",
                "win_rate",
                "roi",
                "up_margin_win_rate",
                "avg_actual_margin_to_upper_native",
            ],
        ),
        "",
        "source overlay 的意义不是现在就删城市，而是说明 routing 层必须接回来。当前 PIT 用 GFS-first，"
        "但历史 calibration 明确存在 GFS/ECMWF/ICON/JMA 的城市差异；下一版应该消费 city preferred model 的 PIT forecast，"
        "并把多模型分歧当成风险特征。",
        "",
        "## All-Loss Days Focus",
        "",
        table(
            focus_all_loss,
            [
                "variant",
                "target_date",
                "trades",
                "wins",
                "roi",
                "avg_p_no_win",
                "avg_p_up_margin",
                "avg_margin",
                "loss_cities",
            ],
            limit=40,
        ),
        "",
        "全亏日仍然是核心 blocker。特别是 payoff 模型虽然更贴结算，但 naive EV 选单会把很多“概率看起来高、实际只差一点没穿档”的行挑出来，"
        "这说明还需要 day-level regime 和 source/model PIT replay，而不是只换 label。",
        "",
        "## 下一步实验",
        "",
        "1. 补 preferred-model PIT forecast：每城按 calibration 选择 GFS/ECMWF/ICON/JMA，不再 GFS-first。",
        "2. 主 label 改成 `label_no_wins` / `upper + margin`；`afternoon_peak` 降级成辅助特征。",
        "3. 做 day-regime gate：同一天最多 1-2 笔只是临时风控，不是根因修复；根因是识别“看似升温但不穿档”的日型。",
        "4. source-sensitive / non-default source 单独桶，不混入 default-WU 泛化结论。",
        "5. 继续 forward shadow；至少等 post-6/23 多个 settled day 通过后，才重新讨论 tiny live。",
        "",
        "## Files",
        "",
        f"- JSON: `{OUT_JSON.relative_to(ROOT)}`",
        f"- Variants: `{OUT_VARIANTS.relative_to(ROOT)}`",
        f"- Daily summary: `{OUT_DAILY.relative_to(ROOT)}`",
        f"- All-loss details: `{OUT_ALL_LOSS.relative_to(ROOT)}`",
        f"- Source/model overlay: `{OUT_SOURCE.relative_to(ROOT)}`",
        f"- Selected rows: `{OUT_SELECTED.relative_to(ROOT)}`",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    scored, pit_stats = load_scored_dataset()
    split_date, train, holdout = split_dates(scored)

    models = {}
    model_metrics: dict[str, Any] = {}
    for name, label_col in [
        ("afternoon_peak", "label_afternoon_peak"),
        ("no_win", "label_no_wins"),
        ("up_margin", "label_up_margin"),
    ]:
        pipe = build_model(0.2)
        pipe.fit(train[NUM_FEATURES + CAT_FEATURES], train[label_col].astype(int))
        models[name] = pipe
        model_metrics[name] = {
            "train": model_quality(pipe, train, label_col),
            "holdout": model_quality(pipe, holdout, label_col),
        }
        scored[f"p_{name}"] = pipe.predict_proba(scored[NUM_FEATURES + CAT_FEATURES])[:, 1]

    scored["p_no_win"] = scored["p_no_win"].astype(float)
    scored["p_up_margin"] = scored["p_up_margin"].astype(float)
    scored["edge_no_win"] = scored["p_no_win"] - pd.to_numeric(scored["no_ask"], errors="coerce")
    scored["edge_up_margin"] = scored["p_up_margin"] - pd.to_numeric(scored["no_ask"], errors="coerce")

    base_raw = scored[scored["trade_base"]].copy()
    base_selected = pass_through.select_first_per_city_day(base_raw)
    variants_raw = {
        "baseline_trade_base": base_raw,
        "old_afternoon_p50": base_raw[base_raw["p_afternoon_peak"].ge(0.50)].copy(),
        "payoff_p50": base_raw[base_raw["p_no_win"].ge(0.50)].copy(),
        "payoff_ev05_p35": base_raw[base_raw["p_no_win"].ge(0.35) & base_raw["edge_no_win"].ge(0.05)].copy(),
        "payoff_ev05_p35_default_wu": base_raw[
            base_raw["p_no_win"].ge(0.35) & base_raw["edge_no_win"].ge(0.05) & base_raw["source_bucket"].astype(str).eq("default_wu")
        ].copy(),
        "payoff_ev05_p35_default_wu_max2_day": select_top_per_date(
            base_raw[
                base_raw["p_no_win"].ge(0.35)
                & base_raw["edge_no_win"].ge(0.05)
                & base_raw["source_bucket"].astype(str).eq("default_wu")
            ].copy(),
            2,
        ),
        "up_margin_ev05_p25_default_wu_max2_day": select_top_per_date(
            base_raw[
                base_raw["p_up_margin"].ge(0.25)
                & base_raw["edge_up_margin"].ge(0.05)
                & base_raw["source_bucket"].astype(str).eq("default_wu")
            ].copy(),
            2,
        ),
    }

    variant_rows = [summarize_variant(name, raw, base_selected if name != "baseline_trade_base" else None) for name, raw in variants_raw.items()]
    variants = pd.DataFrame(variant_rows)
    variants.to_csv(OUT_VARIANTS, index=False)

    daily_rows = []
    selected_rows = []
    for name, raw in variants_raw.items():
        ds = daily_summary(raw, name)
        if not ds.empty:
            daily_rows.append(ds)
        selected = pass_through.select_first_per_city_day(raw.copy())
        if not selected.empty:
            selected["variant"] = name
            selected_rows.append(selected)
    daily = pd.concat(daily_rows, ignore_index=True) if daily_rows else pd.DataFrame()
    daily.to_csv(OUT_DAILY, index=False)
    selected_all = pd.concat(selected_rows, ignore_index=True) if selected_rows else pd.DataFrame()
    selected_all.to_csv(OUT_SELECTED, index=False)

    all_loss_dates = daily[daily["wins"].eq(0)][["variant", "target_date"]].drop_duplicates()
    all_loss_details = selected_all.merge(all_loss_dates, on=["variant", "target_date"], how="inner") if not selected_all.empty else pd.DataFrame()
    detail_cols = [
        "variant",
        "target_date",
        "city",
        "decision_hour_local",
        "bracket",
        "no_ask",
        "p_no_win",
        "p_up_margin",
        "edge_no_win",
        "label_no_wins",
        "label_up_margin",
        "label_afternoon_peak",
        "bracket_upper",
        "final_max_native",
        "actual_margin_to_upper_native",
        "forecast_gap_to_bracket_upper_native",
        "calibration_best_model",
        "source_bucket",
        "profit_usd",
    ]
    all_loss_details[[c for c in detail_cols if c in all_loss_details.columns]].to_csv(OUT_ALL_LOSS, index=False)

    source = source_overlay(base_raw)
    source.to_csv(OUT_SOURCE, index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "current_bracket_no_payoff_source_review_v1",
        "dataset": {
            "pit_scored_rows": int(len(scored)),
            "trade_base_rows": int(len(base_raw)),
            "date_min": str(scored["target_date"].min()),
            "date_max": str(scored["target_date"].max()),
            "selected_date_min": str(base_selected["target_date"].min()) if not base_selected.empty else None,
            "selected_date_max": str(base_selected["target_date"].max()) if not base_selected.empty else None,
            "split_date": split_date,
            "pit_forecast_layer": pit_stats,
            "calibration_results": str(CALIBRATION_RESULTS),
            "source_profiles": str(SOURCE_PROFILES.relative_to(ROOT)),
        },
        "model_metrics": model_metrics,
        "variant_summary": finite_or_none(variants.to_dict(orient="records")),
        "source_overlay": finite_or_none(source.to_dict(orient="records")),
        "outputs": {
            "summary_json": str(OUT_JSON.relative_to(ROOT)),
            "variant_summary_csv": str(OUT_VARIANTS.relative_to(ROOT)),
            "daily_variant_summary_csv": str(OUT_DAILY.relative_to(ROOT)),
            "all_loss_day_trade_details_csv": str(OUT_ALL_LOSS.relative_to(ROOT)),
            "source_model_overlay_summary_csv": str(OUT_SOURCE.relative_to(ROOT)),
            "selected_trade_rows_csv": str(OUT_SELECTED.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
        "verdict": {
            "status": "research_promising_shadow_only",
            "live_ready": False,
            "reason": "payoff-aligned target and source routing look directionally useful, but PIT non-GFS forecast replay is incomplete and post-6/23 forward validation is still required.",
        },
    }
    OUT_JSON.write_text(json.dumps(finite_or_none(payload), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    OUT_MD.write_text(render_md(payload, variants, daily, source), encoding="utf-8")
    print(json.dumps(finite_or_none(payload["verdict"]), indent=2, ensure_ascii=False))
    print(OUT_MD.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
