#!/usr/bin/env python3
"""HeadA low-price YES alpha decomposition v1.

This is a synthesis layer for the forecast-tail lottery sleeve.  It does not
fit a new live selector.  It answers the current research questions on top of
the durable HeadA artifacts:

- whether the probability score ranks winners;
- what the ECMWF outage counterfactual can and cannot prove;
- which next research modules are evidence-backed: book/fill, expression,
  city/source/execution.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
GEN = ROOT / "docs/analysis/2026-07/generated"
OUT_DIR = GEN / "low_price_yes_alpha_decomposition_v1"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-06-low-price-yes-alpha-decomposition-v1.md"
OUT_JSON = OUT_DIR / "summary.json"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_csv(rel: str) -> pd.DataFrame:
    path = GEN / rel
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path)


def load_json(rel: str) -> dict[str, Any]:
    path = GEN / rel
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def num(value: Any, default: float = math.nan) -> float:
    try:
        out = float(value)
    except Exception:
        return default
    return out if math.isfinite(out) else default


def pct(value: Any, *, signed: bool = True) -> str:
    x = num(value)
    if not math.isfinite(x):
        return ""
    return f"{x * 100:+.1f}%" if signed else f"{x * 100:.1f}%"


def usd(value: Any) -> str:
    x = num(value)
    if not math.isfinite(x):
        return ""
    return f"${x:+.2f}"


def md_table(df: pd.DataFrame, cols: list[str], *, max_rows: int = 30) -> str:
    if df.empty:
        return "_No rows._"
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in df.head(max_rows).iterrows():
        cells: list[str] = []
        for col in cols:
            value = row.get(col, "")
            if col in {
                "win_rate",
                "avg_entry",
                "avg_ask",
                "avg_p",
                "roi",
                "roi_ci_low",
                "roi_ci_high",
                "top5_removed_roi",
                "delta_roi",
                "delta_ci_low",
                "delta_ci_high",
                "any_fill_rate",
                "full_fill_rate",
                "mean_fill_fraction",
                "settled_roi",
            }:
                cells.append(pct(value, signed=col not in {"win_rate", "avg_entry", "avg_ask", "avg_p", "any_fill_rate", "full_fill_rate", "mean_fill_fraction"}))
            elif col in {"cost", "pnl", "max_daily_loss_usd", "missed_winner_cost_usd", "settled_pnl_usd", "settled_cost_usd"}:
                cells.append(usd(value))
            elif isinstance(value, float):
                cells.append(f"{value:.3f}" if math.isfinite(value) else "")
            else:
                cells.append(str(value))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def decile_analysis(deciles: pd.DataFrame) -> dict[str, Any]:
    out = deciles.copy()
    rho = float(out["decile"].corr(out["win_rate"], method="spearman"))
    low = out[out["decile"].isin([0, 1, 2])].copy()
    high = out[out["decile"].isin([7, 8, 9])].copy()
    low_wr = float((low["win_rate"] * low["rows"]).sum() / low["rows"].sum())
    high_wr = float((high["win_rate"] * high["rows"]).sum() / high["rows"].sum())
    return {
        "decile_spearman": rho,
        "low_3_deciles_rows": int(low["rows"].sum()),
        "low_3_deciles_win_rate": low_wr,
        "high_3_deciles_rows": int(high["rows"].sum()),
        "high_3_deciles_win_rate": high_wr,
        "high_minus_low_win_rate": high_wr - low_wr,
        "top_decile_win_rate": float(out.loc[out["decile"].idxmax(), "win_rate"]),
        "bottom_decile_win_rate": float(out.loc[out["decile"].idxmin(), "win_rate"]),
    }


def selected_rows(df: pd.DataFrame, labels: list[str], periods: list[str] | None = None) -> pd.DataFrame:
    out = df[df["label"].isin(labels)].copy()
    if periods is not None and "period" in out.columns:
        out = out[out["period"].isin(periods)].copy()
    return out


def make_report() -> tuple[str, dict[str, Any]]:
    cont_deciles = load_csv("low_price_yes_continuous_ev_v1/train_deciles.csv")
    cont_summary = load_csv("low_price_yes_continuous_ev_v1/selector_summary.csv")
    mechanism_groups = load_csv("low_price_yes_mechanism_overlay_v1/group_slices.csv")
    expression_paired = load_csv("low_price_yes_mechanism_overlay_v1/expression_paired.csv")
    expression_overall = load_csv("low_price_yes_mechanism_overlay_v1/expression_overall.csv")
    clean_ab = load_csv("low_price_yes_clean_ev_expression_v1/same_row_ab.csv")
    city_source = load_csv("low_price_yes_city_source_fit_v1/source_group_summary.csv")
    book_source = load_csv("low_price_yes_city_source_fit_v1/book_source_summary.csv")
    maker_queue = load_csv("low_price_yes_maker_queue_v1/scenario_summary.csv")
    maker_fallback = load_csv("low_price_yes_maker_fallback_v1/fallback_policy_summary.csv")
    source_cf = load_json("low_price_yes_source_counterfactual_v1/summary.json")

    dec = decile_analysis(cont_deciles)

    score_ab = selected_rows(
        cont_summary,
        ["current_dist_gt0", "continuous_same_count", "intersection", "continuous_only", "dist_only"],
        ["full", "train_le_2026_06_20", "recent_ge_2026_06_21"],
    )

    book_rows = mechanism_groups[
        mechanism_groups["slice_type"].eq("book_state_v1") & mechanism_groups["period"].eq("full")
    ].copy()
    book_rows = book_rows.sort_values("rows", ascending=False)

    expr_rows = expression_paired[
        expression_paired["label"].isin(["selected_yes", "next_hotter_yes", "two_hotter_yes", "higher_plus_yes", "selected_plus_next_basket"])
    ].copy()
    expr_rows = expr_rows[expr_rows["period"].astype(str).str.startswith("same_denominator")].copy()

    clean_delta = clean_ab[clean_ab["label"].eq("selector_minus_same_rows_selected_yes")].copy()
    clean_delta = clean_delta[
        clean_delta["selector_name"].isin(["physics_same_count_cap20", "physics_ev_positive_cap20", "market_blend_same_count_cap20", "attention_blend_same_count_cap20"])
    ].copy()

    source_rows = city_source[city_source["period"].eq("full")].copy()
    source_rows = source_rows.sort_values(["forecast_model", "source_fit_bucket"])

    book_source_rows = book_source[book_source["period"].eq("full")].copy()
    book_source_rows = book_source_rows.sort_values(["source_fit_bucket", "book_state_v1"])

    queue_view = maker_queue.sort_values(["scenario_notional_usd", "scenario_model"]).copy()
    fallback_view = maker_fallback.sort_values(["ttl_min", "spread_cap", "price_cushion"]).head(12).copy()

    cf_hot = source_cf["selector_current_api_ecmwf_forecast_approx_hot_dist_gt0"]
    cf_no_dist = source_cf["selector_current_api_ecmwf_forecast_approx_no_dist_filter"]
    live_cf = source_cf["live_fills"]

    payload = {
        "generated_at_utc": now_utc(),
        "probability_ranking": dec,
        "source_counterfactual": {
            "pit_reconstructed": source_cf["meta"].get("pit_ecmwf_forecast_curve_reconstructed"),
            "warning": source_cf["meta"].get("current_api_forecast_approx_warning"),
            "live_fills": live_cf,
            "hot_dist_observed_dirty": cf_hot["observed_dirty"],
            "hot_dist_api_forecast_approx": cf_hot["api_forecast_approx"],
            "hot_dist_delta": cf_hot["delta_observed_to_api_forecast_approx"],
            "no_dist_observed_dirty": cf_no_dist["observed_dirty"],
            "no_dist_api_forecast_approx": cf_no_dist["api_forecast_approx"],
            "no_dist_delta": cf_no_dist["delta_observed_to_api_forecast_approx"],
        },
        "recommendation": {
            "live_change_now": "no selector/sizing size-up change from this synthesis",
            "shadow_additions": [
                "score_rank_decile ledger",
                "book_state/fill_probability EV cost",
                "same-row expression EV with selected_yes baseline",
                "city_source_execution decomposition",
                "PIT forecast provenance flag for outage windows",
            ],
        },
    }

    lines = [
        "# HeadA Low-Price YES Alpha Decomposition v1",
        "",
        f"Generated: `{payload['generated_at_utc']}`",
        "",
        "Scope: HeadA `forecast_tail_low_price_yes` only.  This report keeps the live sleeve definition fixed and decomposes the next alpha work into probability ranking, source/PIT integrity, book/fill execution, expression choice, and city/source/execution attribution.",
        "",
        "## Verdict",
        "",
        "```text",
        "probability_ranking=real_but_not_live_selector",
        "ecmwf_outage_counterfactual=partially_quantified_non_pit_for_forecast_max",
        "book_state=highest_value_next_work; needs fillability cost model",
        "expression=selected_yes remains baseline; hotter/plus/basket not promoted",
        "city_source=explanatory/telemetry, not raw city filter",
        "live_action=no new live selector or size-up from this report",
        "```",
        "",
        "人话结论：模型分数确实会排序，高分票比低分票更容易中；但“高分”还没有在同分母上稳定打赢现在的 `dist>0` 选择器。接下来不要再加硬 gate，应该把分数变成仓位/优先级的 shadow ledger，同时把盘口可成交性和 source/PIT 质量作为 EV 成本层。",
        "",
        "## 1. 概率模型有没有排序能力",
        "",
        f"- Train decile Spearman: `{dec['decile_spearman']:+.3f}`。",
        f"- 低 3 档：{dec['low_3_deciles_rows']} rows，命中 {pct(dec['low_3_deciles_win_rate'], signed=False)}。",
        f"- 高 3 档：{dec['high_3_deciles_rows']} rows，命中 {pct(dec['high_3_deciles_win_rate'], signed=False)}，比低 3 档高 {pct(dec['high_minus_low_win_rate'])}。",
        f"- bottom decile {pct(dec['bottom_decile_win_rate'], signed=False)}，top decile {pct(dec['top_decile_win_rate'], signed=False)}。",
        "",
        "这说明分数能排序，但还不是交易规则。原因是同票数 A/B 没过：continuous same-count 相对当前 `dist>0` 的 train excess 只有 +1.4pp，CI 跨 0；recent 还偏负。",
        "",
        md_table(cont_deciles, ["decile", "rows", "win_rate", "avg_p", "avg_ask", "avg_raw_dist", "avg_adj_dist"]),
        "",
        "### Selector A/B",
        "",
        md_table(score_ab, ["label", "period", "rows", "dates", "cities", "win_rate", "avg_entry", "roi", "roi_ci_low", "roi_ci_high", "losing_days", "le_minus50pct_days", "max_daily_loss_usd"], max_rows=30),
        "",
        "## 2. ECMWF outage 期间的反事实到底知道多少",
        "",
        "精确答案：我们本地没有 7/02-7/05 outage 窗口里当时决策时刻的 PIT ECMWF forecast curve，所以不能把当前 API 查回来的过去日期 forecast 当成严格回测。forecast 是能查，但缺的是当时那一版的 issue/run-time snapshot。",
        "",
        "可用答案：已经用 current API forecast-max 做了非 PIT 近似，只能估数量级和方向，不能当 confirmed alpha。这个近似显示确实会换一批票：",
        "",
        f"- live fills 全部：{live_cf['all']['settled']} settled / {live_cf['all']['wins']} wins / ROI {pct(live_cf['all']['roi_settled_cost'])}。",
        f"- source-aligned：{live_cf['aligned']['settled']} settled / {live_cf['aligned']['wins']} wins / ROI {pct(live_cf['aligned']['roi_settled_cost'])}。",
        f"- source-mismatch：{live_cf['mismatch']['settled']} settled / {live_cf['mismatch']['wins']} wins / ROI {pct(live_cf['mismatch']['roi_settled_cost'])}。",
        f"- `dist>0` current-API 近似：observed dirty {cf_hot['observed_dirty']['rows']} rows / settled win {pct(cf_hot['observed_dirty']['win_rate_settled'], signed=False)}；approx {cf_hot['api_forecast_approx']['rows']} rows / settled win {pct(cf_hot['api_forecast_approx']['win_rate_settled'], signed=False)}。",
        f"- `dist>0` 近似增删：新增 {cf_hot['delta_observed_to_api_forecast_approx']['added_count']}，剔除 {cf_hot['delta_observed_to_api_forecast_approx']['dropped_count']}，overlap {cf_hot['delta_observed_to_api_forecast_approx']['overlap_count']}。",
        "",
        "因此，outage 改变了短窗 forward 的交易集合和解释；但它不推翻 5/06..6/30 的主回测，因为主回测来自 stored/canonical historical denominator，不依赖这几天 Mac cache 的错误 source。",
        "",
        "## 3. Book State / Fillability",
        "",
        "Book state 的意思是：这个低价 YES 的盘口是真能买到的错价，还是 missing/thin/wide 导致历史 ask 看起来便宜但实际 fill 不了、或只有 stale quote。它不是天气特征，是执行和市场注意力特征。",
        "",
        md_table(book_rows, ["slice_value", "rows", "dates", "cities", "win_rate", "avg_entry", "roi", "roi_ci_low", "roi_ci_high", "top5_removed_roi", "losing_days"], max_rows=10),
        "",
        "历史上 missing/thin 看起来更强，但这恰好是最容易混入 stale quote 幻觉的地方。下一步应该建 `fill_probability * expected_edge - fee/slippage`，不是把 missing/thin 直接变 hard filter。",
        "",
        "Maker/size-up 现有证据：",
        "",
        md_table(queue_view, ["scenario_notional_usd", "scenario_model", "orders", "any_fill_rate", "full_fill_rate", "mean_fill_fraction", "median_first_fill_wait_min", "missed_winner_cost_usd", "settled_orders", "settled_roi"], max_rows=12),
        "",
        "Dynamic maker fallback 现在只能当 tiny live 执行 overlay，样本太少，不是 alpha 证据：",
        "",
        md_table(fallback_view, ["ttl_min", "spread_cap", "price_cushion", "orders", "fallback_taker_orders", "policy_roi", "baseline_roi", "pnl_delta_usd", "taker_fees_usd_total"], max_rows=12),
        "",
        "## 4. Expression / Overshoot",
        "",
        "Overshoot 确实存在，但同 snapshot 买更热腿、plus 或 basket 没有赢过原 selected YES。也就是说，问题不是“遇到 overshoot 就改买 hotter”，而是先估 `P(each expression wins) - ask - fee - fill_cost`。",
        "",
        md_table(expression_overall, ["label", "rows", "dates", "cities", "win_rate", "avg_entry", "roi", "roi_ci_low", "roi_ci_high", "top5_removed_roi"], max_rows=10),
        "",
        "同分母表达 A/B：",
        "",
        md_table(expr_rows, ["label", "period", "rows", "dates", "win_rate", "avg_entry", "roi", "roi_ci_low", "roi_ci_high", "paired_alt"], max_rows=12),
        "",
        "Clean EV selector 的同 row 对比也没确认替代表达：",
        "",
        md_table(clean_delta, ["selector_name", "rows", "dates", "delta_roi", "delta_ci_low", "delta_ci_high"], max_rows=10),
        "",
        "## 5. City / Source / Execution Decomposition",
        "",
        "城市不是一个可以直接 hard-filter 的 alpha。更干净的拆法是：这个城市 assigned source 的历史 forecast bias、当天 source/PIT 是否一致、盘口是否能成交、live fill 质量如何。",
        "",
        md_table(source_rows, ["forecast_model", "source_fit_bucket", "rows", "dates", "cities", "win_rate", "avg_entry", "roi", "roi_ci_low", "roi_ci_high", "top5_removed_roi"], max_rows=20),
        "",
        "Source fit 与 book state 交叉后，`feasible_book` 并不强，反而 missing/thin 的收益更高但不稳定。这支持“注意力错价 vs stale quote”作为下一层研究，而不是城市白名单。",
        "",
        md_table(book_source_rows, ["source_fit_bucket", "book_state_v1", "rows", "dates", "cities", "win_rate", "avg_entry", "roi", "roi_ci_low", "roi_ci_high"], max_rows=20),
        "",
        "## Next Actions",
        "",
        "1. `score_rank_decile` 进 shadow journal：每张 live/shadow 票记概率 decile、同 decile forward hit rate、按 decile 的 notional/PnL。",
        "2. 建 HeadA EV 成本层：`expected_value = p_win - ask - official_fee - slippage - nonfill_penalty`；book_state 进入成本，不当硬 gate。",
        "3. 做 PIT forecast provenance：每个 decision snapshot 记录 `forecast_run_id/issue_time/source_model/fallback_reason`，outage 窗口标 `non_pit_approx_only`。",
        "4. 表达层保持 selected YES baseline；next-hotter/plus/basket 只在同 row EV 明确超出时 shadow 选择，不 blanket 切。",
        "5. 城市/source 不做 raw whitelist；做 `city_source_execution_score`，由历史 bias、assigned-source match、live fill quality 三部分组成，用于解释和容量，不直接 live promote。",
        "",
        "## Current Decision",
        "",
        "不改 live selector，不 size-up。继续 tiny live + dynamic maker overlay；新增的是 shadow 记录和下一版 EV 研究脚本方向。",
    ]
    return "\n".join(lines) + "\n", payload


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    md, payload = make_report()
    OUT_MD.write_text(md, encoding="utf-8")
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(OUT_MD), "json": str(OUT_JSON), "verdict": payload["recommendation"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
