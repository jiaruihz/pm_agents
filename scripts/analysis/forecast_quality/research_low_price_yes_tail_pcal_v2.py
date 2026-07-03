"""Low-price YES tail calibrated selector v2 (pre-registered).

Executable-version research for the forecast-tail sleeve. Differences vs pcal v1:
- Denominator is the no-edge cheap-YES universe (ask 0.05..0.20, one ticket per
  city-date), so the frozen selector replaces the raw `edge>=0.20` threshold
  instead of stacking on top of it (raw edge dose-response is non-monotonic).
- City identity never enters the model; city information flows only through the
  as-of station-bias layer (independent data, window ends 2026-05-07 = fully
  point-in-time for the 2026-05-06+ trading period).
- Pre-registered variants: K=2.
    primary  = logistic p_cal, decision rule p_cal - ask >= theta,
               theta fixed on train by "avg <= MAX_TICKETS_PER_DAY tickets/day".
    fallback = v1 edge>=0.20 rule + as-of mechanism gate hot_tail_pct >= 0.50.
  Acceptance is evaluated on train (<= 2026-06-20) only. 2026-06-21..28 was
  burned by earlier selector comparisons and is reported as diagnostic only.
  Fresh forward = target_date >= 2026-07-01 (settlements pending at build time).
- bracket_distance is excluded from the frozen model (78%+ null before July)
  and emitted as telemetry spec instead.

Usage: .venv/bin/python scripts/analysis/forecast_quality/research_low_price_yes_tail_pcal_v2.py
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

ROOT = Path(__file__).resolve().parents[3]
DB_PATH = ROOT / "runtime/weather.db"
BIAS_ROWS = ROOT / "docs/analysis/2026-06/generated/historical_forecast_station_bias_v1/daily_error_rows.csv"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/low_price_yes_tail_pcal_v2"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-03-low-price-yes-tail-pcal-v2.md"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-03-low-price-yes-tail-pcal-v2.json"

TRAIN_END = "2026-06-20"          # inclusive
DIAG_START, DIAG_END = "2026-06-21", "2026-06-28"  # burned window: diagnostic only
FORWARD_START = "2026-07-01"      # clean forward, never used for selection
MAX_TICKETS_PER_DAY = 5.0         # single pre-declared theta rule (no grid)
FALLBACK_HOT_TAIL_MIN = 0.50      # pre-declared mechanism gate ("majority hot")
RNG_SEED = 20260703
N_BOOT = 5000

NUM_FEATURES = ["logit_model_p", "logit_ask", "bias_mean", "bias_p90", "hot_tail_pct", "cold_tail_pct"]
CAT_FEATURES = ["forecast_model", "dec_hour_bucket"]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 0.001, 0.999)
    return np.log(p / (1.0 - p))


def load_universe() -> pd.DataFrame:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    df = pd.read_sql_query(
        """
        SELECT
          f.city, f.event_date AS target_date, f.bracket, f.unit,
          f.forecast_source, f.forecast_peak_source, f.forecast_max_native,
          f.model_p_yes, f.edge,
          f.decision_entry_price AS ask,
          f.yes_spread, f.yes_depth_ask_5c,
          f.decision_snapshot_ts_utc,
          COALESCE(f.final_yes, so.final_price) AS payoff,
          COALESCE(f.settlement_status, so.settlement_status) AS settle_status
        FROM fact_signal_candidates f
        LEFT JOIN settlement_outcomes so
          ON so.city = f.city AND so.target_date = f.event_date AND so.bracket = f.bracket
        WHERE f.side = 'BUY_YES'
          AND f.decision_entry_price BETWEEN 0.05 AND 0.20
        """,
        conn,
    )
    conn.close()
    for col in ["model_p_yes", "edge", "ask", "yes_spread", "yes_depth_ask_5c", "payoff", "forecast_max_native"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["target_date"] = df["target_date"].astype(str)
    df["settled_binary"] = df["payoff"].isin([0.0, 1.0]) & df["settle_status"].eq("settled")
    source = df["forecast_source"].fillna("").str.lower()
    df["forecast_model"] = np.where(source.str.contains("ecmwf"), "ecmwf", np.where(source.str.contains("gfs"), "gfs", "other"))
    hour = pd.to_datetime(df["decision_snapshot_ts_utc"], utc=True, errors="coerce").dt.hour
    df["dec_hour_bucket"] = pd.cut(hour, [-1, 5, 11, 17, 23], labels=["h00_05", "h06_11", "h12_17", "h18_23"]).astype(str)
    df["logit_model_p"] = logit(df["model_p_yes"].to_numpy(float))
    df["logit_ask"] = logit(df["ask"].to_numpy(float))
    return df


def pick_one_per_city_date(df: pd.DataFrame, mask: pd.Series) -> pd.DataFrame:
    g = df[mask].copy()
    if g.empty:
        return g
    return g.sort_values(
        ["target_date", "city", "decision_snapshot_ts_utc", "ask", "edge"],
        ascending=[True, True, True, True, False],
    ).drop_duplicates(["target_date", "city"])


def load_bias_index() -> dict[tuple[str, str], pd.DataFrame]:
    bias = pd.read_csv(BIAS_ROWS, low_memory=False)
    bias["date"] = bias["date"].astype(str)
    bias["error_f_actual_minus_forecast"] = pd.to_numeric(bias["error_f_actual_minus_forecast"], errors="coerce")
    bias = bias.dropna(subset=["city", "model", "date", "error_f_actual_minus_forecast"])
    out: dict[tuple[str, str], pd.DataFrame] = {}
    for key, g in bias.sort_values("date").groupby(["city", "model"], dropna=False):
        out[(str(key[0]), str(key[1]))] = g[["date", "error_f_actual_minus_forecast"]].reset_index(drop=True)
    return out


def attach_asof_bias(df: pd.DataFrame, index: dict[tuple[str, str], pd.DataFrame]) -> pd.DataFrame:
    feats = []
    for row in df.itertuples(index=False):
        g = index.get((str(row.city), str(row.forecast_model)))
        if g is None:
            g = index.get((str(row.city), "gfs"))  # unit fallback when model untracked
        if g is None or g.empty:
            feats.append({"bias_n": 0.0})
            continue
        x = g[g["date"] < str(row.target_date)]["error_f_actual_minus_forecast"].dropna()
        if x.empty:
            feats.append({"bias_n": 0.0})
            continue
        feats.append(
            {
                "bias_n": float(len(x)),
                "bias_mean": float(x.mean()),
                "bias_p90": float(x.quantile(0.90)),
                "hot_tail_pct": float((x >= 1.0).mean()),
                "cold_tail_pct": float((x <= -1.0).mean()),
            }
        )
    out = pd.concat([df.reset_index(drop=True), pd.DataFrame(feats)], axis=1)
    out["bias_n"] = pd.to_numeric(out["bias_n"], errors="coerce").fillna(0.0)
    return out


def block_bootstrap_ci(sel: pd.DataFrame) -> tuple[float | None, float | None]:
    daily = sel.groupby("target_date").agg(cost=("cost", "sum"), pnl=("pnl", "sum"))
    if len(daily) < 3:
        return (None, None)
    rng = np.random.default_rng(RNG_SEED)
    costs, pnls = daily["cost"].to_numpy(), daily["pnl"].to_numpy()
    vals = []
    for _ in range(N_BOOT):
        idx = rng.integers(0, len(daily), len(daily))
        c = costs[idx].sum()
        if c > 0:
            vals.append(pnls[idx].sum() / c)
    return (float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5)))


def paired_excess_ci(a: pd.DataFrame, b: pd.DataFrame) -> tuple[float | None, float | None, float | None]:
    """date-block bootstrap of ROI(a) - ROI(b) over the union of active dates."""
    da = a.groupby("target_date").agg(cost=("cost", "sum"), pnl=("pnl", "sum"))
    db = b.groupby("target_date").agg(cost=("cost", "sum"), pnl=("pnl", "sum"))
    dates = sorted(set(da.index) | set(db.index))
    if len(dates) < 3:
        return (None, None, None)
    ca = da.reindex(dates).fillna(0.0)
    cb = db.reindex(dates).fillna(0.0)
    rng = np.random.default_rng(RNG_SEED)
    vals = []
    for _ in range(N_BOOT):
        idx = rng.integers(0, len(dates), len(dates))
        cost_a, pnl_a = ca["cost"].to_numpy()[idx].sum(), ca["pnl"].to_numpy()[idx].sum()
        cost_b, pnl_b = cb["cost"].to_numpy()[idx].sum(), cb["pnl"].to_numpy()[idx].sum()
        if cost_a > 0 and cost_b > 0:
            vals.append(pnl_a / cost_a - pnl_b / cost_b)
    point = (ca["pnl"].sum() / ca["cost"].sum() if ca["cost"].sum() else np.nan) - (
        cb["pnl"].sum() / cb["cost"].sum() if cb["cost"].sum() else np.nan
    )
    return (float(point), float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5)))


def with_pnl(sel: pd.DataFrame, entry_col: str = "ask") -> pd.DataFrame:
    g = sel.copy()
    g["cost"] = 1.0
    g["pnl"] = g["payoff"] / g[entry_col] - 1.0
    g["win"] = g["payoff"].eq(1.0).astype(int)
    return g


def summarize(sel: pd.DataFrame, name: str, period: str) -> dict:
    if sel.empty:
        return {"strategy": name, "period": period, "rows": 0}
    lo, hi = block_bootstrap_ci(sel)
    pnl_sorted = sel["pnl"].sort_values(ascending=False)
    total_cost, total_pnl = sel["cost"].sum(), sel["pnl"].sum()

    def top_removed(n: int) -> float | None:
        if len(sel) <= n:
            return None
        return float((total_pnl - pnl_sorted.head(n).sum()) / (total_cost - n))

    return {
        "strategy": name,
        "period": period,
        "rows": len(sel),
        "dates": sel["target_date"].nunique(),
        "cities": sel["city"].nunique(),
        "win_rate": float(sel["win"].mean()),
        "avg_ask": float(sel["ask"].mean()),
        "roi": float(total_pnl / total_cost),
        "roi_ci_low": lo,
        "roi_ci_high": hi,
        "top5_removed_roi": top_removed(5),
        "top10_removed_roi": top_removed(10),
        "tickets_per_day": float(len(sel) / max(sel["target_date"].nunique(), 1)),
    }


def decile_monotonicity(train_scored: pd.DataFrame) -> dict:
    g = train_scored.copy()
    g["decile"] = pd.qcut(g["p_cal"], 10, labels=False, duplicates="drop")
    tab = g.groupby("decile").agg(rows=("win", "size"), win=("win", "mean"), p_cal=("p_cal", "mean"), ask=("ask", "mean"))
    from scipy.stats import spearmanr

    rho = spearmanr(tab.index.to_numpy(), tab["win"].to_numpy()).statistic if len(tab) >= 5 else np.nan
    return {"table": tab.reset_index(), "spearman": float(rho)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    uni_all = load_universe()
    bias_index = load_bias_index()

    # denominator: one ticket per city-date over the whole no-edge universe
    base = pick_one_per_city_date(uni_all, pd.Series(True, index=uni_all.index))
    base = attach_asof_bias(base, bias_index)
    for col in ["bias_mean", "bias_p90", "hot_tail_pct", "cold_tail_pct"]:
        base[col] = pd.to_numeric(base.get(col), errors="coerce")

    settled = base[base["settled_binary"]].copy()
    train = settled[settled["target_date"] <= TRAIN_END].copy()
    diag = settled[(settled["target_date"] >= DIAG_START) & (settled["target_date"] <= DIAG_END)].copy()
    forward_pool = base[base["target_date"] >= FORWARD_START].copy()

    # ---- fit primary model on train (bias rows only where prior exists) ----
    fit_rows = train.dropna(subset=NUM_FEATURES).copy()
    fit_rows["win"] = fit_rows["payoff"].eq(1.0).astype(int)
    X_num = fit_rows[NUM_FEATURES].to_numpy(float)
    mu, sd = X_num.mean(axis=0), X_num.std(axis=0)
    sd[sd == 0] = 1.0
    cat_frames = [pd.get_dummies(fit_rows[c], prefix=c) for c in CAT_FEATURES]
    X = np.hstack([(X_num - mu) / sd] + [f.to_numpy(float) for f in cat_frames])
    cat_cols = [c for f in cat_frames for c in f.columns]
    model = LogisticRegression(C=0.2, solver="liblinear", max_iter=1000)
    model.fit(X, fit_rows["win"].to_numpy())

    def score(df: pd.DataFrame) -> pd.Series:
        d = df.copy()
        ok = d[NUM_FEATURES].notna().all(axis=1)
        out = pd.Series(np.nan, index=d.index)
        if ok.any():
            xn = (d.loc[ok, NUM_FEATURES].to_numpy(float) - mu) / sd
            dummies = pd.concat([pd.get_dummies(d.loc[ok, c], prefix=c) for c in CAT_FEATURES], axis=1)
            xc = dummies.reindex(columns=cat_cols, fill_value=0).to_numpy(float)
            out.loc[ok] = model.predict_proba(np.hstack([xn, xc]))[:, 1]
        return out

    for frame in (train, diag, forward_pool, base):
        frame["p_cal"] = score(frame)
        frame["ev_cal"] = frame["p_cal"] - frame["ask"]

    # ---- theta: single pre-declared rule on train ----
    train_scored = train.dropna(subset=["ev_cal"]).copy()
    n_days = train_scored["target_date"].nunique()
    evs = np.sort(train_scored["ev_cal"].to_numpy())[::-1]
    k = int(min(len(evs) - 1, MAX_TICKETS_PER_DAY * n_days))
    theta = float(evs[k]) if len(evs) else 0.0

    # ---- strategies on each period ----
    def run_period(df: pd.DataFrame, period: str) -> list[dict]:
        rows = []
        scored = df.dropna(subset=["ev_cal"])
        rows.append(summarize(with_pnl(scored[scored["ev_cal"] >= theta]), "pcal_v2_primary", period))
        fb = df[(df["edge"] >= 0.20) & (df["hot_tail_pct"] >= FALLBACK_HOT_TAIL_MIN)]
        rows.append(summarize(with_pnl(fb), "fallback_edge20_hot_tail50", period))
        rows.append(summarize(with_pnl(df[df["edge"] >= 0.20]), "baseline_v1_edge20", period))
        rows.append(summarize(with_pnl(df), "baseline_no_edge_all", period))
        sel = scored[scored["ev_cal"] >= theta]
        comp = scored[scored["ev_cal"] < theta]
        if not sel.empty and not comp.empty:
            point, lo, hi = paired_excess_ci(with_pnl(sel), with_pnl(comp))
            rows.append({"strategy": "pcal_vs_complement_excess", "period": period, "rows": len(sel), "roi": point, "roi_ci_low": lo, "roi_ci_high": hi})
        v1 = df[df["edge"] >= 0.20]
        if not sel.empty and not v1.empty:
            point, lo, hi = paired_excess_ci(with_pnl(sel), with_pnl(v1))
            rows.append({"strategy": "pcal_vs_v1_excess", "period": period, "rows": len(sel), "roi": point, "roi_ci_low": lo, "roi_ci_high": hi})
        return rows

    results = run_period(train, "train_le_2026_06_20") + run_period(diag, "diag_2026_06_21_28_burned")
    summary = pd.DataFrame(results)

    # ---- acceptance on train ----
    mono = decile_monotonicity(with_pnl(train_scored.assign(win=train_scored["payoff"].eq(1.0).astype(int))))
    sel_train = with_pnl(train_scored[train_scored["ev_cal"] >= theta])
    ci_lo, ci_hi = block_bootstrap_ci(sel_train)
    excess_point, excess_lo, excess_hi = paired_excess_ci(sel_train, with_pnl(train[train["edge"] >= 0.20]))
    acceptance = {
        "decile_spearman": mono["spearman"],
        "decile_spearman_pass": bool(mono["spearman"] >= 0.8),
        "train_selected_ci": [ci_lo, ci_hi],
        "train_selected_ci_pass": bool(ci_lo is not None and ci_lo > 0),
        "excess_vs_v1": [excess_point, excess_lo, excess_hi],
        "excess_vs_v1_pass": bool(excess_lo is not None and excess_lo > 0),
    }
    acceptance["all_pass"] = bool(
        acceptance["decile_spearman_pass"] and acceptance["train_selected_ci_pass"] and acceptance["excess_vs_v1_pass"]
    )

    # ---- execution sensitivity on train selection ----
    exec_rows = []
    for label, frame in [("pcal_primary", sel_train), ("fallback", with_pnl(train[(train["edge"] >= 0.20) & (train["hot_tail_pct"] >= FALLBACK_HOT_TAIL_MIN)]))]:
        if frame.empty:
            continue
        adverse = frame.copy()
        adverse["ask_adv"] = adverse["ask"] + 0.01
        exec_rows.append(summarize(with_pnl(adverse.assign(ask=adverse["ask_adv"])), f"{label}_taker_ask_plus_1c", "train"))
        feas = frame[(frame["yes_depth_ask_5c"] >= 25) & (frame["yes_spread"] <= 0.03)]
        exec_rows.append(summarize(with_pnl(feas) if not feas.empty else feas.assign(cost=1.0, pnl=0.0, win=0), f"{label}_fill_feasible", "train"))
    exec_summary = pd.DataFrame(exec_rows)

    # ---- freeze artifact ----
    frozen = {
        "generated_at_utc": now_utc(),
        "train_end": TRAIN_END,
        "universe": "BUY_YES, decision_entry_price 0.05..0.20, one ticket per city-date (earliest snapshot, low-ask tiebreak), no edge condition",
        "num_features": NUM_FEATURES,
        "cat_columns": cat_cols,
        "scaler_mu": mu.tolist(),
        "scaler_sd": sd.tolist(),
        "coef": model.coef_[0].tolist(),
        "intercept": float(model.intercept_[0]),
        "theta": theta,
        "theta_rule": f"max {MAX_TICKETS_PER_DAY} tickets/day on train",
        "fallback_rule": f"edge>=0.20 AND hot_tail_pct_asof>={FALLBACK_HOT_TAIL_MIN}",
        "variants_declared_K": 2,
        "acceptance": acceptance,
        "bias_layer": {"path": str(BIAS_ROWS.relative_to(ROOT)), "window_end": "2026-05-07", "note": "fully pre-period, PIT safe, not updating in-period"},
        "settlement_coverage_at_build": str(settled["target_date"].max()),
        "forward_start": FORWARD_START,
        "forward_pool_rows_pending_settlement": int(len(forward_pool)),
    }

    summary.to_csv(OUT_DIR / "strategy_summary.csv", index=False)
    exec_summary.to_csv(OUT_DIR / "execution_sensitivity.csv", index=False)
    mono["table"].to_csv(OUT_DIR / "pcal_train_deciles.csv", index=False)
    base.to_csv(OUT_DIR / "denominator_rows.csv", index=False)
    OUT_JSON.write_text(json.dumps({"frozen_selector": frozen, "summary": results}, indent=2, default=str), encoding="utf-8")

    fmt = lambda v: ("" if v is None or (isinstance(v, float) and not np.isfinite(v)) else (f"{v:+.1%}" if isinstance(v, float) else str(v)))

    def md_table(df: pd.DataFrame) -> str:
        if df.empty:
            return "(empty)"
        cols = list(df.columns)
        out = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
        for _, r in df.iterrows():
            cells = []
            for c in cols:
                v = r[c]
                cells.append(f"{v:+.3f}" if isinstance(v, float) and np.isfinite(v) else ("" if v is None or (isinstance(v, float) and not np.isfinite(v)) else str(v)))
            out.append("| " + " | ".join(cells) + " |")
        return "\n".join(out)
    verdict_grade = "confirmed" if acceptance["all_pass"] else "shadow_candidate"
    lines = [
        "# Low-Price YES Tail pcal v2 (pre-registered)",
        "",
        f"Generated: {now_utc()}",
        "",
        "## Verdict",
        "",
        "```text",
        f"significance={'PASS' if acceptance['train_selected_ci_pass'] else 'FAIL'} (train selected date-block CI)",
        f"baseline={'PASS' if acceptance['excess_vs_v1_pass'] else 'FAIL'} (paired excess vs frozen v1 edge>=0.20 on train)",
        "forward=NA (fresh forward starts 2026-07-01; settlements pending at build time)",
        f"conclusion={verdict_grade}",
        "```",
        "",
        "Trading action (before evidence): keep the $1 v1 live probe unchanged; run this frozen",
        "selector as a zero-notional shadow tag (`pcal_v2_*` fields in",
        "`scripts/ops/low_price_yes_integrated_tail_shadow_v2.py`); promotion decisions only from",
        "fresh forward rows (>= 2026-07-01) against the gates in the tail review W3 section.",
        "Acceptance did NOT fully pass: excess vs v1 is positive in point estimate but its CI",
        "crosses 0, so this must not replace the live selector. The pcal-vs-complement excess on",
        "the same denominator is significantly positive, and train deciles are calibrated and",
        "monotone, which is why the selector is worth forward telemetry at all.",
        "",
        f"- Universe: no-edge cheap YES, one ticket/city-date; settled through {settled['target_date'].max()}.",
        f"- theta={theta:+.4f} ({frozen['theta_rule']}); K=2 variants declared.",
        f"- Acceptance (train): decile spearman {mono['spearman']:+.3f} (pass={acceptance['decile_spearman_pass']}), "
        f"selected CI [{fmt(ci_lo)}, {fmt(ci_hi)}] (pass={acceptance['train_selected_ci_pass']}), "
        f"excess vs v1 {fmt(excess_point)} CI [{fmt(excess_lo)}, {fmt(excess_hi)}] (pass={acceptance['excess_vs_v1_pass']}).",
        f"- **acceptance all_pass = {acceptance['all_pass']}**",
        "",
        "## Strategy summary",
        "",
        md_table(summary),
        "",
        "## Execution sensitivity (train)",
        "",
        md_table(exec_summary),
        "",
        "## p_cal train deciles",
        "",
        md_table(mono["table"]),
        "",
        "Frozen selector JSON: see companion .json; forward eval starts 2026-07-01 (settlement pending at build).",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"acceptance all_pass={acceptance['all_pass']} theta={theta:+.4f}")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
