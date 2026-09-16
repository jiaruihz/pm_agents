"""HeadB METAR reversal tmax/regime overlay comparison v1.

This is research-only. It keeps HeadB independent from HeadA and from the
tmax_distribution live/shadow policy. The goal is to compare clean mechanism
overlays on the existing HeadB denominators:

- regime/context overlays from the intraday weather regime atlas
- clean tmax distribution probabilities from P5, where available
- market ask-band distribution for the HeadB expressions

It does not change live configs or place orders.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
HEADB_TRADES = ROOT / "docs/analysis/2026-07/generated/metar_reversal_daily_breakdown_v1/headb_trade_rows.csv"
REGIME_ROWS = ROOT / "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/intraday_weather_regime_state_rows.csv"
TMAX_P5 = ROOT / "docs/analysis/2026-07/generated/tmax_distribution_p5_walk_forward_execution_replay_v1/opportunities.csv"
GATE_JSON = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"
DB_PATH = ROOT / "runtime/weather.db"

OUT_DIR = ROOT / "docs/analysis/2026-07/generated/metar_reversal_tmax_regime_overlay_v1"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-05-metar-reversal-tmax-regime-overlay-v1.md"

RNG_SEED = 20260705
N_BOOT = 5000
RECENT_START = "2026-06-21"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pct(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return ""
    return f"{100.0 * float(value):+.1f}%"


def num(value: float | int | None, digits: int = 3) -> str:
    if value is None or pd.isna(value):
        return ""
    return f"{float(value):.{digits}f}"


def money(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return ""
    return f"{float(value):+.2f}"


def block_bootstrap_ci(daily: pd.DataFrame) -> tuple[float | None, float | None]:
    if daily["target_date"].nunique() < 3:
        return (None, None)
    rng = np.random.default_rng(RNG_SEED)
    pnl = daily["exec_fee_pnl"].to_numpy(dtype=float)
    rows = daily["rows"].to_numpy(dtype=float)
    vals: list[float] = []
    for _ in range(N_BOOT):
        idx = rng.integers(0, len(daily), len(daily))
        denom = rows[idx].sum()
        if denom > 0:
            vals.append(float(pnl[idx].sum() / denom))
    return (float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5)))


def md_table(df: pd.DataFrame, cols: list[str], max_rows: int | None = None) -> str:
    view = df[cols].copy()
    if max_rows is not None:
        view = view.head(max_rows)
    pct_cols = {
        "win_rate",
        "exec_fee_roi",
        "ci_low",
        "ci_high",
        "recent_exec_fee_roi",
        "top5_removed_exec_fee_roi",
        "retained_vs_base",
        "avg_tmax_d1_yes_edge_exec",
        "avg_tmax_current_overprice",
        "tmax_overlap_rate",
    }
    num_cols = {
        "avg_entry_raw",
        "avg_entry_stress",
        "avg_p_current",
        "avg_p_d1",
    }
    money_cols = {"exec_fee_pnl", "max_daily_loss"}
    for col in view.columns:
        if col in pct_cols:
            view[col] = view[col].map(pct)
        elif col in num_cols:
            view[col] = view[col].map(lambda x: num(x, 3))
        elif col in money_cols:
            view[col] = view[col].map(money)
    view = view.astype(str)
    lines = [
        "| " + " | ".join(view.columns) + " |",
        "| " + " | ".join(["---"] * len(view.columns)) + " |",
    ]
    for row in view.itertuples(index=False, name=None):
        lines.append("| " + " | ".join(str(x) for x in row) + " |")
    return "\n".join(lines)


def load_headb() -> pd.DataFrame:
    trades = pd.read_csv(HEADB_TRADES, low_memory=False)
    trades["target_date"] = trades["target_date"].astype(str)
    trades["decision_hour_local"] = trades["decision_hour_local"].astype(int)
    trades["win"] = trades["win"].astype(bool)
    trades["exec_fee_pnl_per_1"] = trades["exec_fee_pnl_per_1"].astype(float)
    trades["entry_raw"] = trades["entry_raw"].astype(float)
    trades["entry_stress"] = trades["entry_stress"].astype(float)
    return trades


def regime_features() -> pd.DataFrame:
    cols = [
        "city",
        "target_date",
        "decision_hour_local",
        "day_regime",
        "intraday_state",
        "running_max_state",
        "solar_window",
        "moisture_cloud_regime",
        "wind_regime",
        "city_family",
        "forecast_source",
        "temp_trend_3h_f",
        "relative_humidity_pct",
        "wind_speed_kt",
        "minutes_since_running_max",
        "forecast_peak_delta_hours_local",
        "forecast_to_current_upper_native",
        "forecast_to_d1_upper_native",
        "forecast_to_d2_upper_native",
    ]
    df = pd.read_csv(REGIME_ROWS, usecols=[c for c in cols if c in pd.read_csv(REGIME_ROWS, nrows=0).columns], low_memory=False)
    df["target_date"] = df["target_date"].astype(str)
    df["decision_hour_local"] = df["decision_hour_local"].astype(int)
    return df.drop_duplicates(["city", "target_date", "decision_hour_local"])


def tmax_clean_probs() -> pd.DataFrame:
    p5 = pd.read_csv(TMAX_P5, low_memory=False)
    p5 = p5[p5["method"].eq("loo_no_city_source_blend")].copy()
    p5["target_date"] = p5["target_date"].astype(str)
    p5["decision_hour_local"] = p5["decision_hour_local"].astype(int)
    idx = ["city", "target_date", "decision_hour_local"]
    prob = p5.pivot_table(index=idx, columns="expression", values="p_win", aggfunc="first").reset_index()
    scope = p5.groupby(idx, as_index=False)["scope"].first()
    prob = prob.merge(scope, on=idx, how="left")
    prob = prob.rename(columns={"current_yes": "tmax_p_current", "d1_no": "tmax_p_not_d1"})
    if "tmax_p_not_d1" in prob.columns:
        prob["tmax_p_d1"] = 1.0 - prob["tmax_p_not_d1"].astype(float)
    else:
        prob["tmax_p_d1"] = np.nan
    return prob


def enrich() -> pd.DataFrame:
    trades = load_headb()
    regime = regime_features()
    tmax = tmax_clean_probs()
    rows = trades.merge(regime, on=["city", "target_date", "decision_hour_local"], how="left")
    rows = rows.merge(tmax, on=["city", "target_date", "decision_hour_local"], how="left")
    rows["current_yes_ref"] = rows["current_yes_ask"].where(rows["current_yes_ask"].notna(), rows["current_high_yes_ask"])
    rows["trend3h_positive"] = rows["temp_trend_3h_f"].astype(float) > 0.0
    rows["no_trend3h_flat"] = rows["temp_trend_3h_f"].abs() > 0.1
    rows["day_open_runway"] = rows["day_regime"].eq("day_open_runway")
    rows["active_warming_or_reheat"] = rows["intraday_state"].isin(["active_warming", "reheating_after_dip"])
    rows["tmax_d1_yes_edge_exec"] = rows["tmax_p_d1"] - rows["entry_stress"]
    rows["tmax_current_overprice"] = rows["current_yes_ref"] - rows["tmax_p_current"]
    rows["tmax_d1_edge_pos"] = rows["tmax_d1_yes_edge_exec"] > 0.0
    rows["tmax_current_overprice_pos"] = rows["tmax_current_overprice"] > 0.0
    rows["tmax_both_pos"] = rows["tmax_d1_edge_pos"] & rows["tmax_current_overprice_pos"]
    rows["ask_band"] = pd.cut(
        rows["entry_raw"],
        bins=[0.0, 0.05, 0.10, 0.20, 0.30, 0.40, 1.01],
        labels=["0-5c", "5-10c", "10-20c", "20-30c", "30-40c", "40c+"],
        include_lowest=True,
        right=False,
    )
    return rows


def summarize_rows(rows: pd.DataFrame, group: dict[str, object]) -> dict[str, object]:
    if rows.empty:
        return {
            **group,
            "rows": 0,
            "dates": 0,
            "cities": 0,
            "win_rate": np.nan,
            "avg_entry_raw": np.nan,
            "avg_entry_stress": np.nan,
            "exec_fee_pnl": 0.0,
            "exec_fee_roi": np.nan,
            "ci_low": np.nan,
            "ci_high": np.nan,
            "top5_removed_exec_fee_roi": np.nan,
            "max_daily_loss": np.nan,
            "recent_rows": 0,
            "recent_exec_fee_roi": np.nan,
            "avg_p_current": np.nan,
            "avg_p_d1": np.nan,
            "avg_tmax_d1_yes_edge_exec": np.nan,
            "avg_tmax_current_overprice": np.nan,
        }
    daily = (
        rows.groupby("target_date", as_index=False)
        .agg(rows=("city", "size"), exec_fee_pnl=("exec_fee_pnl_per_1", "sum"))
        .assign(exec_fee_roi=lambda x: x["exec_fee_pnl"] / x["rows"])
    )
    ci_low, ci_high = block_bootstrap_ci(daily)
    pnl_sorted = rows["exec_fee_pnl_per_1"].sort_values(ascending=False)
    top5_removed = np.nan
    if len(rows) > 5:
        top5_removed = float((rows["exec_fee_pnl_per_1"].sum() - pnl_sorted.head(5).sum()) / (len(rows) - 5))
    recent = rows[rows["target_date"] >= RECENT_START]
    return {
        **group,
        "rows": int(len(rows)),
        "dates": int(rows["target_date"].nunique()),
        "cities": int(rows["city"].nunique()),
        "win_rate": float(rows["win"].mean()),
        "avg_entry_raw": float(rows["entry_raw"].mean()),
        "avg_entry_stress": float(rows["entry_stress"].mean()),
        "exec_fee_pnl": float(rows["exec_fee_pnl_per_1"].sum()),
        "exec_fee_roi": float(rows["exec_fee_pnl_per_1"].mean()),
        "ci_low": ci_low,
        "ci_high": ci_high,
        "top5_removed_exec_fee_roi": top5_removed,
        "max_daily_loss": float(daily["exec_fee_pnl"].min()),
        "recent_rows": int(len(recent)),
        "recent_exec_fee_roi": float(recent["exec_fee_pnl_per_1"].mean()) if len(recent) else np.nan,
        "avg_p_current": float(rows["tmax_p_current"].mean()) if rows["tmax_p_current"].notna().any() else np.nan,
        "avg_p_d1": float(rows["tmax_p_d1"].mean()) if rows["tmax_p_d1"].notna().any() else np.nan,
        "avg_tmax_d1_yes_edge_exec": float(rows["tmax_d1_yes_edge_exec"].mean()) if rows["tmax_d1_yes_edge_exec"].notna().any() else np.nan,
        "avg_tmax_current_overprice": float(rows["tmax_current_overprice"].mean()) if rows["tmax_current_overprice"].notna().any() else np.nan,
    }


def base_summary(rows: pd.DataFrame) -> pd.DataFrame:
    recs = []
    for strategy, g in rows.groupby("strategy", sort=False):
        recs.append(summarize_rows(g, {"strategy": strategy}))
    return pd.DataFrame(recs)


def ask_band_summary(rows: pd.DataFrame) -> pd.DataFrame:
    recs = []
    for (strategy, ask_band), g in rows.groupby(["strategy", "ask_band"], observed=False):
        if len(g):
            recs.append(summarize_rows(g, {"strategy": strategy, "ask_band": str(ask_band)}))
    return pd.DataFrame(recs)


def daily_summary(rows: pd.DataFrame) -> pd.DataFrame:
    return (
        rows.groupby(["strategy", "target_date"], as_index=False, sort=True)
        .agg(
            rows=("city", "size"),
            cities=("city", "nunique"),
            wins=("win", "sum"),
            avg_entry_raw=("entry_raw", "mean"),
            exec_fee_pnl=("exec_fee_pnl_per_1", "sum"),
        )
        .assign(
            win_rate=lambda x: x["wins"] / x["rows"],
            exec_fee_roi=lambda x: x["exec_fee_pnl"] / x["rows"],
        )
    )


def variant_summary(rows: pd.DataFrame) -> pd.DataFrame:
    variants = [
        ("base", lambda x: pd.Series(True, index=x.index), "no overlay"),
        ("trend3h_positive", lambda x: x["trend3h_positive"], "3h trend still positive"),
        ("day_open_runway", lambda x: x["day_open_runway"], "regime atlas day_open_runway only"),
        (
            "trend3h_positive_and_day_open_runway",
            lambda x: x["trend3h_positive"] & x["day_open_runway"],
            "both sustained trend and open-runway regime",
        ),
        (
            "tmax_overlap_base",
            lambda x: x["tmax_p_d1"].notna(),
            "same denominator where clean tmax P5 probability exists",
        ),
        (
            "tmax_d1_edge_exec_gt0",
            lambda x: x["tmax_d1_edge_pos"],
            "clean tmax p(d1) exceeds stressed d1 YES entry",
        ),
        (
            "tmax_d1_edge_and_current_overprice_gt0",
            lambda x: x["tmax_both_pos"],
            "tmax supports d1 YES and says current is overpriced",
        ),
    ]
    recs = []
    base_counts = rows.groupby("strategy").size().to_dict()
    for strategy, g in rows.groupby("strategy", sort=False):
        for variant, fn, desc in variants:
            if variant.startswith("tmax_") and not strategy.endswith("_d1_yes"):
                mask = pd.Series(False, index=g.index)
            else:
                mask = fn(g).fillna(False)
            sg = g.loc[mask].copy()
            rec = summarize_rows(
                sg,
                {
                    "strategy": strategy,
                    "variant": variant,
                    "description": desc,
                    "base_rows": int(base_counts[strategy]),
                },
            )
            rec["retained_vs_base"] = rec["rows"] / rec["base_rows"] if rec["base_rows"] else np.nan
            recs.append(rec)
    return pd.DataFrame(recs)


def write_report(rows: pd.DataFrame, base: pd.DataFrame, ask_bands: pd.DataFrame, variants: pd.DataFrame, daily: pd.DataFrame) -> None:
    tmax_cov = (
        rows.groupby("strategy", as_index=False)
        .agg(rows=("city", "size"), tmax_overlap=("tmax_p_d1", lambda s: int(s.notna().sum())))
        .assign(tmax_overlap_rate=lambda x: x["tmax_overlap"] / x["rows"])
    )
    best_clean = variants[
        variants["variant"].isin(
            [
                "trend3h_positive",
                "day_open_runway",
                "trend3h_positive_and_day_open_runway",
                "tmax_overlap_base",
                "tmax_d1_edge_exec_gt0",
                "tmax_d1_edge_and_current_overprice_gt0",
            ]
        )
    ].copy()
    best_clean = best_clean[best_clean["rows"] > 0].copy()
    best_clean = best_clean.sort_values(["strategy", "rows"], ascending=[True, False])
    lines = [
        "# HeadB METAR Reversal Tmax/Regime Overlay v1",
        "",
        f"Generated: `{now_utc()}`",
        "",
        "## Verdict",
        "",
        "`metar_reversal` remains research/shadow-only. The overlays are useful as telemetry and mechanism ranking, not live approval.",
        "",
        "Main read:",
        "",
        "- Overall HeadB point estimates are positive, but date-block CIs still cross 0 and top-5-winner removal stays negative.",
        "- Regime overlays help explain which rows are physically cleaner, but they do not fix the main blocker: recent state frequency is near zero and entry-time depth is thin.",
        "- Clean tmax probability overlap starts at 2026-06-02, so tmax overlays are a smaller denominator. Treat them as mechanism diagnostics, not an in-sample selector.",
        "",
        "## Data Snapshot",
        "",
        f"- `runtime/weather.db` mtime: `{datetime.fromtimestamp(DB_PATH.stat().st_mtime, timezone.utc).isoformat()}`.",
        f"- CLOB coverage gate file: `{GATE_JSON.relative_to(ROOT)}`.",
        f"- HeadB trade rows: `{HEADB_TRADES.relative_to(ROOT)}` rows={len(rows):,}.",
        f"- Regime atlas join coverage: {int(rows['day_regime'].notna().sum())}/{len(rows)} rows.",
        "",
        "## Current Overall Performance",
        "",
        md_table(
            base,
            [
                "strategy",
                "rows",
                "dates",
                "cities",
                "win_rate",
                "avg_entry_stress",
                "exec_fee_pnl",
                "exec_fee_roi",
                "ci_low",
                "ci_high",
                "top5_removed_exec_fee_roi",
                "max_daily_loss",
                "recent_rows",
                "recent_exec_fee_roi",
            ],
        ),
        "",
        "## Market / Ask Distribution",
        "",
        md_table(
            ask_bands.sort_values(["strategy", "ask_band"]),
            ["strategy", "ask_band", "rows", "dates", "win_rate", "avg_entry_raw", "exec_fee_roi", "ci_low", "ci_high"],
        ),
        "",
        "## Tmax Coverage",
        "",
        md_table(tmax_cov, ["strategy", "rows", "tmax_overlap", "tmax_overlap_rate"]),
        "",
        "## Clean Overlay Comparison",
        "",
        md_table(
            best_clean,
            [
                "strategy",
                "variant",
                "rows",
                "retained_vs_base",
                "dates",
                "win_rate",
                "avg_entry_stress",
                "exec_fee_roi",
                "ci_low",
                "ci_high",
                "top5_removed_exec_fee_roi",
                "avg_p_current",
                "avg_p_d1",
                "avg_tmax_d1_yes_edge_exec",
                "avg_tmax_current_overprice",
            ],
        ),
        "",
        "## Daily Rows",
        "",
        md_table(
            daily,
            ["strategy", "target_date", "rows", "cities", "wins", "win_rate", "avg_entry_raw", "exec_fee_pnl", "exec_fee_roi"],
            max_rows=120,
        ),
        "",
        "## Interpretation",
        "",
        "- `trend3h_positive` is the cleanest shared mechanism overlay to carry forward: it asks for sustained warming, not just a one-hour uptick.",
        "- `day_open_runway` is interpretable, but on this HeadB denominator it is still mostly a sample-shape tag; do not make it a hard gate without forward rows.",
        "- `tmax_d1_edge_exec_gt0` is the right probability confirmation idea, but it only covers the 2026-06-02+ subset. It should be written into the shadow journal as `p_d1`, `d1_yes_edge`, and `current_overprice`, then judged forward.",
        "- Same-denominator `current_bracket_no` remains worse than d1 YES. The optimization direction should refine d1 YES entry quality, not switch HeadB into a NO route.",
        "",
        "## Generated Artifacts",
        "",
        f"- `{(OUT_DIR / 'headb_enriched_trades.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'headb_base_summary.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'headb_ask_band_summary.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'headb_variant_summary.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'headb_daily_summary.csv').relative_to(ROOT)}`",
        "",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = enrich()
    base = base_summary(rows)
    ask_bands = ask_band_summary(rows)
    variants = variant_summary(rows)
    daily = daily_summary(rows)

    rows.to_csv(OUT_DIR / "headb_enriched_trades.csv", index=False)
    base.to_csv(OUT_DIR / "headb_base_summary.csv", index=False)
    ask_bands.to_csv(OUT_DIR / "headb_ask_band_summary.csv", index=False)
    variants.to_csv(OUT_DIR / "headb_variant_summary.csv", index=False)
    daily.to_csv(OUT_DIR / "headb_daily_summary.csv", index=False)
    write_report(rows, base, ask_bands, variants, daily)

    print(f"wrote {OUT_MD.relative_to(ROOT)}")
    print(base[["strategy", "rows", "dates", "win_rate", "exec_fee_roi", "ci_low", "ci_high"]].to_string(index=False))
    print()
    print(
        variants[
            variants["variant"].isin(["trend3h_positive", "day_open_runway", "tmax_d1_edge_exec_gt0"])
        ][["strategy", "variant", "rows", "win_rate", "exec_fee_roi", "ci_low", "ci_high"]].to_string(index=False)
    )


if __name__ == "__main__":
    main()
