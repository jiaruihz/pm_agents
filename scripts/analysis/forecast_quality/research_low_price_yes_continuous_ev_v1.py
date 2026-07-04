#!/usr/bin/env python3
"""HeadA low-price YES continuous EV diagnostic v1.

This is a research-only follow-up to the HeadA/HeadB forward plan.  It tests
whether as-of station-bias plus continuous bracket distance adds selection
power on the current HeadA denominator.  It does not change the live selector.
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
from sklearn.linear_model import LogisticRegression

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.forecast_quality.research_low_price_yes_heada_refinement_v1 import (  # noqa: E402
    RECENT_START,
    TRAIN_END,
    date_block_ci,
    load_base,
    simulate_execution,
    summarize_perf,
)

OUT_DIR = ROOT / "docs/analysis/2026-07/generated/low_price_yes_continuous_ev_v1"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-05-low-price-yes-continuous-ev-v1.md"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-05-low-price-yes-continuous-ev-v1.json"

RNG_SEED = 20260705
N_BOOT = 5000

NUMERIC_FEATURES = [
    "entry",
    "edge",
    "raw_dist_br",
    "adj_dist_p50_br",
    "bias_n_asof",
    "bias_mean_asof",
    "bias_p90_asof",
    "hot_tail_pct_asof",
    "cold_tail_pct_asof",
]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def to_float(value: Any, default: float = math.nan) -> float:
    try:
        out = float(value)
    except Exception:
        return default
    return out if math.isfinite(out) else default


def fmt_pct(value: Any, *, signed: bool = True) -> str:
    x = to_float(value)
    if not math.isfinite(x):
        return ""
    return f"{x * 100:+.1f}%" if signed else f"{x * 100:.1f}%"


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_ready(v) for v in value]
    if isinstance(value, tuple):
        return [json_ready(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        out = float(value)
        return out if math.isfinite(out) else None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def fill_numeric(frame: pd.DataFrame, train: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, float]]:
    out = frame.copy()
    medians: dict[str, float] = {}
    for col in NUMERIC_FEATURES:
        values = pd.to_numeric(train[col], errors="coerce")
        median = float(values.median()) if values.notna().any() else 0.0
        medians[col] = median
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(median)
    return out, medians


def fit_continuous_model(base: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    train_raw = base[base["target_date"].astype(str) <= TRAIN_END].copy()
    scored, medians = fill_numeric(base, train_raw)
    train = scored[scored["target_date"].astype(str) <= TRAIN_END].copy()
    x_train = train[NUMERIC_FEATURES].to_numpy(float)
    mu = x_train.mean(axis=0)
    sd = x_train.std(axis=0)
    sd[sd <= 0] = 1.0
    y_train = train["win"].astype(int).to_numpy()
    model = LogisticRegression(C=0.2, solver="liblinear", max_iter=1000, random_state=RNG_SEED)
    model.fit((x_train - mu) / sd, y_train)

    x_all = scored[NUMERIC_FEATURES].to_numpy(float)
    scored["p_continuous_ev_v1"] = model.predict_proba((x_all - mu) / sd)[:, 1]
    scored["ev_continuous_v1"] = scored["p_continuous_ev_v1"] - scored["entry"]
    artifact = {
        "features": NUMERIC_FEATURES,
        "medians": medians,
        "mu": mu.tolist(),
        "sd": sd.tolist(),
        "coef": model.coef_[0].tolist(),
        "intercept": float(model.intercept_[0]),
        "train_rows": int(len(train)),
        "train_wins": int(y_train.sum()),
        "train_end": TRAIN_END,
        "note": "research-only diagnostic; no city identity; threshold set only for same-count A/B vs current dist>0 baseline",
    }
    return scored, artifact


def add_periods(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["period"] = np.where(
        out["target_date"].astype(str) <= TRAIN_END,
        "train_le_2026_06_20",
        np.where(out["target_date"].astype(str) >= RECENT_START, "recent_ge_2026_06_21", "gap"),
    )
    return out


def execution_rows(frame: pd.DataFrame, *, label: str) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    sim = simulate_execution(frame)
    sim = sim[
        (sim["sizing"].eq("price_tier_6_8_10_shares"))
        & (sim["entry_profile"].eq("taker_weather_fee"))
        & (sim["exit_policy"].eq("hold"))
    ].copy()
    sim["selector"] = label
    return sim


def paired_excess_ci(a: pd.DataFrame, b: pd.DataFrame) -> tuple[float | None, float | None, float | None]:
    da = a.groupby("target_date").agg(cost=("cost", "sum"), pnl=("pnl", "sum"))
    db = b.groupby("target_date").agg(cost=("cost", "sum"), pnl=("pnl", "sum"))
    dates = sorted(set(da.index) | set(db.index))
    if len(dates) < 3:
        return None, None, None
    ca = da.reindex(dates).fillna(0.0)
    cb = db.reindex(dates).fillna(0.0)
    point = (ca["pnl"].sum() / ca["cost"].sum()) - (cb["pnl"].sum() / cb["cost"].sum())
    rng = np.random.default_rng(RNG_SEED)
    vals: list[float] = []
    pnl_a, cost_a = ca["pnl"].to_numpy(float), ca["cost"].to_numpy(float)
    pnl_b, cost_b = cb["pnl"].to_numpy(float), cb["cost"].to_numpy(float)
    for _ in range(N_BOOT):
        idx = rng.integers(0, len(dates), len(dates))
        a_cost = float(cost_a[idx].sum())
        b_cost = float(cost_b[idx].sum())
        if a_cost > 0 and b_cost > 0:
            vals.append(float(pnl_a[idx].sum() / a_cost - pnl_b[idx].sum() / b_cost))
    return float(point), float(np.quantile(vals, 0.025)), float(np.quantile(vals, 0.975))


def same_count_threshold(train: pd.DataFrame) -> float:
    base_count = int(train["hot_tail_boundary_v1"].sum())
    if base_count <= 0:
        return math.inf
    vals = train["ev_continuous_v1"].dropna().sort_values(ascending=False).to_numpy(float)
    if len(vals) < base_count:
        return float(vals[-1]) if len(vals) else math.inf
    return float(vals[base_count - 1])


def selector_frames(scored: pd.DataFrame, theta: float) -> dict[str, pd.DataFrame]:
    return {
        "current_dist_gt0": scored[scored["hot_tail_boundary_v1"]].copy(),
        "continuous_same_count": scored[scored["ev_continuous_v1"] >= theta].copy(),
        "intersection": scored[scored["hot_tail_boundary_v1"] & (scored["ev_continuous_v1"] >= theta)].copy(),
        "continuous_only": scored[(~scored["hot_tail_boundary_v1"]) & (scored["ev_continuous_v1"] >= theta)].copy(),
        "dist_only": scored[scored["hot_tail_boundary_v1"] & (scored["ev_continuous_v1"] < theta)].copy(),
    }


def summarize_selectors(scored: pd.DataFrame, theta: float) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    frames = selector_frames(scored, theta)
    rows: list[dict[str, Any]] = []
    exec_frames: dict[str, pd.DataFrame] = {}
    for name, frame in frames.items():
        exec_frame = execution_rows(frame, label=name)
        exec_frames[name] = exec_frame
        for period, mask in {
            "full": pd.Series(True, index=exec_frame.index),
            "train_le_2026_06_20": exec_frame["target_date"].astype(str) <= TRAIN_END if not exec_frame.empty else pd.Series([], dtype=bool),
            "recent_ge_2026_06_21": exec_frame["target_date"].astype(str) >= RECENT_START if not exec_frame.empty else pd.Series([], dtype=bool),
        }.items():
            g = exec_frame[mask].copy() if not exec_frame.empty else exec_frame
            rec = summarize_perf(g, label=name, period=period)
            rows.append(rec)
    return pd.DataFrame(rows), exec_frames


def decile_table(scored: pd.DataFrame) -> pd.DataFrame:
    train = scored[scored["target_date"].astype(str) <= TRAIN_END].dropna(subset=["p_continuous_ev_v1"]).copy()
    train["decile"] = pd.qcut(train["p_continuous_ev_v1"], 10, labels=False, duplicates="drop")
    out = train.groupby("decile", as_index=False).agg(
        rows=("win", "size"),
        win_rate=("win", "mean"),
        avg_p=("p_continuous_ev_v1", "mean"),
        avg_ask=("entry", "mean"),
        avg_raw_dist=("raw_dist_br", "mean"),
        avg_adj_dist=("adj_dist_p50_br", "mean"),
    )
    return out


def grouped_table(scored: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for group_col in ["raw_dist_band", "adj_dist_band", "price_band", "book_state_v1"]:
        for key, g in scored.groupby(group_col, dropna=False):
            exec_frame = execution_rows(g, label=str(key))
            if exec_frame.empty:
                continue
            rec = summarize_perf(exec_frame, label=str(key), period="full")
            rec["group"] = group_col
            rec["bucket"] = str(key)
            rows.append(rec)
    return pd.DataFrame(rows)


def md_table(df: pd.DataFrame, cols: list[str] | None = None) -> str:
    if df.empty:
        return "(empty)"
    use = df[cols].copy() if cols else df.copy()
    out = ["| " + " | ".join(use.columns) + " |", "|" + "|".join(["---"] * len(use.columns)) + "|"]
    for _, row in use.iterrows():
        cells: list[str] = []
        for col in use.columns:
            value = row[col]
            if isinstance(value, float):
                if not math.isfinite(value):
                    cells.append("")
                elif col.startswith("roi") or col in {"win_rate", "avg_entry", "avg_ask", "avg_p", "avg_raw_dist", "avg_adj_dist"}:
                    cells.append(f"{value:.3f}")
                else:
                    cells.append(f"{value:.3f}")
            else:
                cells.append(str(value))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    base = add_periods(load_base())
    scored, artifact = fit_continuous_model(base)
    theta = same_count_threshold(scored[scored["target_date"].astype(str) <= TRAIN_END])
    artifact["theta_same_count_vs_dist_gt0"] = theta

    summary, exec_frames = summarize_selectors(scored, theta)
    deciles = decile_table(scored)
    groups = grouped_table(scored)

    current = exec_frames["current_dist_gt0"]
    continuous = exec_frames["continuous_same_count"]
    excess = {}
    for period, mask_cur, mask_cont in [
        (
            "train_le_2026_06_20",
            current["target_date"].astype(str) <= TRAIN_END,
            continuous["target_date"].astype(str) <= TRAIN_END,
        ),
        (
            "recent_ge_2026_06_21",
            current["target_date"].astype(str) >= RECENT_START,
            continuous["target_date"].astype(str) >= RECENT_START,
        ),
        ("full", pd.Series(True, index=current.index), pd.Series(True, index=continuous.index)),
    ]:
        point, lo, hi = paired_excess_ci(continuous[mask_cont].copy(), current[mask_cur].copy())
        excess[period] = {"point": point, "ci_low": lo, "ci_high": hi}

    mono_rho = float(deciles["decile"].corr(deciles["win_rate"], method="spearman")) if len(deciles) >= 5 else math.nan
    cont_train = continuous[continuous["target_date"].astype(str) <= TRAIN_END]
    cont_ci = date_block_ci(cont_train)
    acceptance = {
        "decile_spearman": mono_rho,
        "decile_spearman_pass": bool(math.isfinite(mono_rho) and mono_rho >= 0.6),
        "continuous_train_ci": cont_ci,
        "continuous_train_ci_pass": bool(cont_ci[0] is not None and cont_ci[0] > 0),
        "excess_vs_current_train": excess["train_le_2026_06_20"],
        "excess_vs_current_train_pass": bool(
            excess["train_le_2026_06_20"]["ci_low"] is not None
            and excess["train_le_2026_06_20"]["ci_low"] > 0
        ),
    }
    acceptance["all_pass"] = bool(
        acceptance["decile_spearman_pass"]
        and acceptance["continuous_train_ci_pass"]
        and acceptance["excess_vs_current_train_pass"]
    )

    summary.to_csv(OUT_DIR / "selector_summary.csv", index=False)
    deciles.to_csv(OUT_DIR / "train_deciles.csv", index=False)
    groups.to_csv(OUT_DIR / "mechanism_groups.csv", index=False)
    scored.to_csv(OUT_DIR / "scored_rows.csv", index=False)
    payload = {
        "generated_at_utc": now_utc(),
        "artifact": artifact,
        "acceptance": acceptance,
        "excess": excess,
        "summary": summary.to_dict(orient="records"),
        "deciles": deciles.to_dict(orient="records"),
        "data_snapshot": {
            "rows": int(len(scored)),
            "dates": int(scored["target_date"].nunique()),
            "min_target_date": str(scored["target_date"].min()),
            "max_target_date": str(scored["target_date"].max()),
            "train_end": TRAIN_END,
            "recent_start": RECENT_START,
        },
    }
    OUT_JSON.write_text(json.dumps(json_ready(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    cols = [
        "label",
        "period",
        "rows",
        "dates",
        "cities",
        "win_rate",
        "avg_entry",
        "avg_cost",
        "roi",
        "roi_ci_low",
        "roi_ci_high",
        "losing_days",
        "le_minus50pct_days",
        "max_daily_loss_usd",
    ]
    main_summary = summary[
        summary["label"].isin(["current_dist_gt0", "continuous_same_count", "continuous_only", "dist_only"])
    ].copy()
    lines = [
        "# Low-Price YES Continuous EV v1",
        "",
        f"Generated: {now_utc()}",
        "",
        "## Verdict",
        "",
        "```text",
        f"significance={'PASS' if acceptance['continuous_train_ci_pass'] else 'FAIL'}",
        f"baseline={'PASS' if acceptance['excess_vs_current_train_pass'] else 'FAIL'}",
        "forward=NA (diagnostic on existing HeadA denominator; no live action)",
        "conclusion=inconclusive" if not acceptance["all_pass"] else "conclusion=shadow_candidate",
        "```",
        "",
        "人话结论：连续 station-bias / adjusted-distance 现在只能当解释层，不能替代 live 选择器。",
        "这版同票数 A/B 没有证明它比现行 `dist>0` 更好；它能帮我们看清哪些票更像真钱 tail，",
        "但还不能拿来调仓或加新 gate。",
        "",
        "## Data Snapshot",
        "",
        f"- Denominator: current HeadA edge>=0.20, ask 5-20c, settled binary rows from integrated-tail/refinement layer.",
        f"- Rows / dates / cities: {len(scored)} / {scored['target_date'].nunique()} / {scored['city'].nunique()}.",
        f"- Target dates: {scored['target_date'].min()} .. {scored['target_date'].max()}; train <= {TRAIN_END}; recent >= {RECENT_START}.",
        "- Main replay: `price_tier_6_8_10_shares`, official Weather taker fee, hold-to-settlement.",
        "",
        "## Same-Count A/B",
        "",
        f"- Threshold rule: pick the same number of train tickets as current `dist>0`; theta={theta:+.4f}.",
        f"- Train excess continuous vs current: {fmt_pct(excess['train_le_2026_06_20']['point'])} "
        f"CI [{fmt_pct(excess['train_le_2026_06_20']['ci_low'])}, {fmt_pct(excess['train_le_2026_06_20']['ci_high'])}].",
        f"- Recent excess continuous vs current: {fmt_pct(excess['recent_ge_2026_06_21']['point'])} "
        f"CI [{fmt_pct(excess['recent_ge_2026_06_21']['ci_low'])}, {fmt_pct(excess['recent_ge_2026_06_21']['ci_high'])}].",
        "",
        md_table(main_summary[cols], cols),
        "",
        "## Calibration Shape",
        "",
        f"- Train decile Spearman: {mono_rho:+.3f}.",
        "",
        md_table(deciles),
        "",
        "## Mechanism Buckets",
        "",
        md_table(groups[["group", "bucket", "rows", "dates", "win_rate", "avg_entry", "roi", "roi_ci_low", "roi_ci_high"]]),
        "",
        "## Action",
        "",
        "- Do not change HeadA live selector from this result.",
        "- Keep current `dist>0 + price_tier_6_8_10_shares + maker-first + hold` forward probe.",
        "- Use this score as telemetry only; the useful next research is partial-fill/queue modeling, because current forward pain is more fill/maker-rate than probability ranking.",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(OUT_MD), "json": str(OUT_JSON), "acceptance": acceptance, "excess": excess}, indent=2, default=str))


if __name__ == "__main__":
    main()
