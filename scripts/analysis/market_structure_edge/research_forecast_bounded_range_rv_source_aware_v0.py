#!/usr/bin/env python3
"""Source-aware forecast-bounded Range RV research v0.

This continues Range RV as an expression layer on top of the reusable
forecast-quality/source-aware base.  The decision-set grain is:

    city + event_date + forecast_source/model_version + decision_snapshot_ts_utc

The script uses fact_signal_candidates through the forecast-quality base
builder, joins the settlement source registry, and evaluates fixed compact
2/3/4-bracket range expressions.  It does not change N100/live config and does
not publish proxy results as live edge.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from scripts.analysis.forecast_quality import research_forecast_quality_base_v0 as fq_base  # noqa: E402
from scripts.analysis.forecast_quality import research_forecast_quality_source_adjusted_v0 as fq_source  # noqa: E402
from scripts.analysis.observed_max import research_settlement_source_registry_v0 as source_registry  # noqa: E402

import research_range_rv_scanner as scanner  # noqa: E402
import research_range_rv_variant_lab_v03 as variants  # noqa: E402


TARGET_METRIC = "source_aware_forecast_bounded_range_rv_expression_alpha_v0"
DB_DEFAULT = ROOT / "runtime" / "weather.db"
OUT_JSON_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-15-forecast-bounded-range-rv-source-aware-v0.json"
OUT_MD_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-15-forecast-bounded-range-rv-source-aware-v0.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=str(DB_DEFAULT))
    parser.add_argument("--out-json", default=str(OUT_JSON_DEFAULT))
    parser.add_argument("--out-md", default=str(OUT_MD_DEFAULT))
    parser.add_argument("--orderbook-glob", default=str(scanner.ORDERBOOK_GLOB_DEFAULT))
    parser.add_argument("--bootstrap-iters", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260615)
    parser.add_argument("--skip-orderbook", action="store_true")
    return parser.parse_args()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def pct(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "NA"
    return f"{value * 100:+.1f}%"


def fmt_ci(value: list[float | None] | None) -> str:
    if not value or value[0] is None or value[1] is None:
        return "NA"
    return f"[{pct(value[0])}, {pct(value[1])}]"


def table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def centered_indices(mode_i: int, n: int, width: int) -> list[int] | None:
    if n < width:
        return None
    start = min(max(0, mode_i - width // 2), n - width)
    return list(range(start, start + width))


def normalize(values: list[float]) -> list[float]:
    total = sum(max(0.0, x) for x in values)
    if total <= 0.0:
        return [0.0 for _ in values]
    return [max(0.0, x) / total for x in values]


def leg_cost(side: str, market_yes_price: float) -> float:
    return market_yes_price if side == "BUY_YES" else 1.0 - market_yes_price


def range_yes_cost(inside: list[dict[str, Any]]) -> float:
    return sum(float(row["market_yes_price"]) for row in inside)


def outside_no_gross_cost(outside: list[dict[str, Any]]) -> float:
    return sum(1.0 - float(row["market_yes_price"]) for row in outside)


def outside_no_effective_cost(outside: list[dict[str, Any]]) -> float:
    if not outside:
        return float("inf")
    return outside_no_gross_cost(outside) - (len(outside) - 1)


def decision_items_from_row(ds: pd.Series) -> list[dict[str, Any]]:
    legs = json.loads(ds["legs_json"])
    out = []
    for leg in legs:
        row = {
            **leg,
            "city": ds["city"],
            "city_pool": ds["city_pool"],
            "event_date": str(ds["event_date"]),
            "forecast_source": ds["forecast_source"],
            "model_version": ds["model_version"],
            "decision_snapshot_ts_utc": ds["decision_snapshot_ts_utc"],
            "eligible": 0,
            "paper_ordered": 0,
            "live_filled": 0,
        }
        out.append(row)
    return out


def source_sensitive_city(row: pd.Series) -> int:
    if row["source_bucket"] != "source_sensitive_confirmed":
        return 0
    return int(row["city"] in {"HongKong", "Jakarta"} or str(row["settlement_source_class"]) == "official_station_diff_confirmed")


def make_expression(
    *,
    ds: pd.Series,
    items: list[dict[str, Any]],
    width: int,
    expression_policy: str,
) -> dict[str, Any] | None:
    idxs = centered_indices(int(ds["mode_i"]), len(items), width)
    if idxs is None:
        return None
    inside = [items[i] for i in idxs]
    inside_brackets = {str(row["bracket"]) for row in inside}
    outside = [row for row in items if str(row["bracket"]) not in inside_brackets]
    model_norm = normalize([float(row["model_p_yes"]) for row in items])
    market_norm = normalize([float(row["market_yes_price"]) for row in items])
    model_mass = sum(model_norm[i] for i in idxs)
    market_mass = sum(market_norm[i] for i in idxs)
    yes_cost = range_yes_cost(inside)
    no_eff_cost = outside_no_effective_cost(outside)

    if expression_policy == "inside_yes":
        expression = "inside_yes"
        legs = [dict(row, side="BUY_YES") for row in inside]
        effective_cost = yes_cost
    elif expression_policy == "cheaper":
        if outside and no_eff_cost < yes_cost:
            expression = "outside_no"
            legs = [dict(row, side="BUY_NO") for row in outside]
            effective_cost = no_eff_cost
        else:
            expression = "inside_yes"
            legs = [dict(row, side="BUY_YES") for row in inside]
            effective_cost = yes_cost
    else:
        raise ValueError(expression_policy)

    if not legs or any(leg.get("final_yes") is None for leg in legs):
        return None

    score = model_mass - effective_cost
    algorithm = f"forecast_bounded_w{width}_{expression_policy}"
    row = variants.make_candidate(
        algorithm=algorithm,
        decision_items=items,
        legs=legs,
        score=score,
        threshold=0.0,
        components={
            "width": width,
            "expression_policy": expression_policy,
            "expression": expression,
            "inside_brackets": [row["bracket"] for row in inside],
            "outside_legs": len(outside),
            "model_mass_norm": model_mass,
            "market_mass_norm": market_mass,
            "inside_yes_cost": yes_cost,
            "outside_no_effective_cost": None if not outside else no_eff_cost,
            "effective_range_cost": effective_cost,
        },
    )
    row["selected"] = int(score > 0.0 and 0.0 < effective_cost <= 0.95)
    row["effective_range_cost"] = effective_cost
    row["range_model_mass_norm"] = model_mass
    row["range_market_mass_norm"] = market_mass
    row["expression"] = expression
    row["forecast_quality_low"] = int(ds["forecast_quality_low"])
    row["forecast_quality_medium_plus"] = int(ds["forecast_quality_medium_plus"])
    row["city_model_reliable"] = int(ds["city_model_reliable"])
    row["source_bucket"] = ds["source_bucket"]
    row["settlement_source_class"] = ds["settlement_source_class"]
    row["official_station_or_feed"] = ds["official_station_or_feed"]
    row["mapping_rule"] = ds["mapping_rule"]
    row["source_sensitive"] = source_sensitive_city(ds)
    return row


def generate_rows(decision_sets: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _, ds in decision_sets.iterrows():
        items = decision_items_from_row(ds)
        for width in (2, 3, 4):
            for policy in ("inside_yes", "cheaper"):
                row = make_expression(ds=ds, items=items, width=width, expression_policy=policy)
                if row is not None and row.get("decision_dt") is not None:
                    rows.append(row)
    return rows


def split_rows(rows: list[dict[str, Any]], dates: set[str]) -> list[dict[str, Any]]:
    return [row for row in rows if str(row["event_date"]) in dates]


def filter_rows(rows: list[dict[str, Any]], row_filter: str) -> list[dict[str, Any]]:
    if row_filter == "no_filter":
        return list(rows)
    if row_filter == "exclude_forecast_quality_low":
        return [row for row in rows if int(row.get("forecast_quality_low") or 0) == 0]
    if row_filter == "source_bucket_default_wu":
        return [row for row in rows if row.get("source_bucket") == "default_wu"]
    if row_filter == "source_sensitive_diagnostic":
        return [row for row in rows if int(row.get("source_sensitive") or 0) == 1]
    raise ValueError(row_filter)


def summarize_eval(
    rows: list[dict[str, Any]],
    *,
    source: str,
    train_dates: set[str],
    holdout_dates: set[str],
    iters: int,
    seed: int,
) -> list[dict[str, Any]]:
    out = []
    filters = ["no_filter", "exclude_forecast_quality_low", "source_bucket_default_wu"]
    algorithms = sorted({row["algorithm"] for row in rows})
    for algo_idx, algorithm in enumerate(algorithms):
        family = [row for row in rows if row["algorithm"] == algorithm]
        for filter_idx, row_filter in enumerate(filters):
            filtered_family = filter_rows(family, row_filter)
            train_family = split_rows(filtered_family, train_dates)
            holdout_family = split_rows(filtered_family, holdout_dates)
            train_selected = [row for row in train_family if int(row.get("selected") or 0) == 1]
            holdout_selected = [row for row in holdout_family if int(row.get("selected") or 0) == 1]
            train = variants.summarize_eval(
                train_selected,
                train_family,
                source,
                seed + algo_idx * 1000 + filter_idx * 100,
                iters,
            )
            holdout = variants.summarize_eval(
                holdout_selected,
                holdout_family,
                source,
                seed + algo_idx * 1000 + filter_idx * 100 + 50,
                iters,
            )
            out.append(
                {
                    "algorithm": algorithm,
                    "row_filter": row_filter,
                    "source": source,
                    "family_rows": len(filtered_family),
                    "train": train,
                    "holdout": holdout,
                    "gates": scanner.gate_result(train, holdout),
                }
            )
    return out


def source_sensitive_summary(rows: list[dict[str, Any]], *, source: str, train_dates: set[str], holdout_dates: set[str]) -> list[dict[str, Any]]:
    out = []
    diagnostic = filter_rows(rows, "source_sensitive_diagnostic")
    for algorithm in sorted({row["algorithm"] for row in diagnostic}):
        family = [row for row in diagnostic if row["algorithm"] == algorithm]
        holdout = split_rows(family, holdout_dates)
        selected_holdout = [row for row in holdout if int(row.get("selected") or 0) == 1]
        summary = scanner.summarize(selected_holdout, source=source)
        out.append(
            {
                "algorithm": algorithm,
                "rows": len(family),
                "cities": len({row["city"] for row in family}),
                "holdout_selected_rows": summary["rows"],
                "holdout_dates": summary["active_event_dates"],
                "holdout_roi": summary["taker_roi"],
                "bucket_note": "source_sensitive; excluded from generic conclusion",
            }
        )
    return out


def source_bucket_counts(decision_sets: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for bucket, group in decision_sets.groupby("source_bucket", dropna=False):
        rows.append(
            {
                "source_bucket": str(bucket),
                "decision_sets": int(len(group)),
                "cities": int(group["city"].nunique()),
                "holdout_decision_sets": int(group[group["event_date"].astype(str) >= "2026-05-29"].shape[0]),
            }
        )
    return sorted(rows, key=lambda x: x["decision_sets"], reverse=True)


def result_table_rows(results: list[dict[str, Any]]) -> list[list[Any]]:
    rows = []
    for item in results:
        train = item["train"]
        holdout = item["holdout"]
        gates = item["gates"]
        rows.append(
            [
                f"`{item['algorithm']}`",
                f"`{item['row_filter']}`",
                item["family_rows"],
                train["selected"]["rows"],
                train["selected"]["active_event_dates"],
                pct(train["selected"]["taker_roi"]),
                pct(train["excess_roi"]),
                fmt_ci(train["excess_roi_ci95_cluster_by_event_date"]),
                holdout["selected"]["rows"],
                holdout["selected"]["active_event_dates"],
                pct(holdout["selected"]["taker_roi"]),
                pct(holdout["excess_roi"]),
                fmt_ci(holdout["excess_roi_ci95_cluster_by_event_date"]),
                pct(holdout["selected"]["drop_top5_taker_roi"]),
                f"`{gates['significance']}/{gates['baseline']}/{gates['forward']} -> {gates['verdict']}`",
            ]
        )
    return rows


def source_sensitive_rows(rows: list[dict[str, Any]]) -> list[list[Any]]:
    return [
        [
            f"`{row['algorithm']}`",
            row["rows"],
            row["cities"],
            row["holdout_selected_rows"],
            row["holdout_dates"],
            pct(row["holdout_roi"]),
            row["bucket_note"],
        ]
        for row in rows
    ]


def bucket_table_rows(rows: list[dict[str, Any]]) -> list[list[Any]]:
    return [[row["source_bucket"], row["decision_sets"], row["cities"], row["holdout_decision_sets"]] for row in rows]


def best_rows(results: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    return sorted(
        results,
        key=lambda item: (
            item["holdout"]["excess_roi"] if item["holdout"]["excess_roi"] is not None else -999.0,
            item["holdout"]["selected"]["active_event_dates"] or 0,
            item["holdout"]["selected"]["rows"] or 0,
        ),
        reverse=True,
    )[:limit]


def final_verdict(proxy_results: list[dict[str, Any]], orderbook_results: list[dict[str, Any]]) -> dict[str, str]:
    def passes(item: dict[str, Any]) -> bool:
        gates = item["gates"]
        return (
            item["row_filter"] == "source_bucket_default_wu"
            and gates["significance"] == "PASS"
            and gates["baseline"] == "PASS"
            and gates["forward"] == "PASS"
            and (item["holdout"]["selected"]["rows"] or 0) >= 20
            and (item["holdout"]["selected"]["active_event_dates"] or 0) >= 5
            and (item["holdout"]["selected"]["drop_top5_taker_roi"] or 0.0) > 0.0
        )

    proxy_pass = {item["algorithm"] for item in proxy_results if passes(item)}
    orderbook_pass = {item["algorithm"] for item in orderbook_results if passes(item)}
    confirmed = sorted(proxy_pass & orderbook_pass)
    if confirmed:
        return {
            "significance": "PASS",
            "baseline": "PASS",
            "forward": "PASS",
            "conclusion": "shadow_candidate",
            "reason": f"default_wu algorithms passed proxy and orderbook gates: {confirmed}",
        }
    return {
        "significance": "FAIL",
        "baseline": "FAIL",
        "forward": "FAIL",
        "conclusion": "inconclusive",
        "reason": "No default_wu Range RV expression passed proxy and time-aligned orderbook gates with support/top5 stress.",
    }


def render_md(payload: dict[str, Any]) -> str:
    proxy_best = best_rows(payload["proxy_results"])
    orderbook_best = best_rows(payload["orderbook_results"])
    lines = [
        "# Forecast-Bounded Range RV Source-Aware v0",
        "",
        f"> generated_at_utc: `{payload['generated_at_utc']}`",
        f"> target_metric: `{TARGET_METRIC}`",
        f"> DB: `{payload['db_path']}`",
        "> Scope: opportunity/proxy + time-aligned orderbook research only; no N100/live config changed; no live action.",
        "",
        "## 数据快照",
        "",
        "- 数据源: `runtime/weather.db.fact_signal_candidates`; forecast-quality/source labels from `research_forecast_quality_source_adjusted_v0` helpers.",
        f"- DB last_modified: `{payload['db_last_modified_utc']}`.",
        f"- fact_signal_candidates rows: `{payload['self_check']['candidate_coverage']['rows']}`.",
        f"- decision_sets used: `{payload['funnel']['decision_sets']}` at `city + event_date + forecast_source/model_version + decision_snapshot_ts_utc` grain.",
        f"- strategy rows: `{payload['funnel']['strategy_rows']}` across `{payload['funnel']['algorithms']}` algorithms.",
        f"- train: `{payload['split']['train_start']}` -> `{payload['split']['train_end']}` ({payload['split']['train_dates']} event_dates).",
        f"- holdout: `{payload['split']['holdout_start']}` -> `{payload['split']['holdout_end']}` ({payload['split']['holdout_dates']} event_dates).",
        f"- CLOB coverage gate: `{payload['clob_gate']['gate_pass']}`; this report still does not publish live_real PnL/ROI/rank/curve.",
        "",
        "### 强制 5 行 SQL 自检",
        "",
        "```json",
        json.dumps(payload["self_check"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## Target Metric",
        "",
        "`source_aware_forecast_bounded_range_rv_expression_alpha_v0` = fixed compact Range RV expressions around the forecast mode, evaluated after joining forecast-quality and settlement-source labels at the same decision-set grain.",
        "",
        "The three consumer rows per algorithm are exactly: `no_filter`, `exclude_forecast_quality_low`, and `source_bucket_default_wu`. HK/Jakarta/station-diff rows are marked `source_sensitive` and excluded from the generic `default_wu` conclusion.",
        "",
        "## Source Bucket Funnel",
        "",
        table(["source_bucket", "decision_sets", "cities", "holdout_decision_sets"], bucket_table_rows(payload["source_bucket_counts"])),
        "",
        "## Decision-Price Proxy: Top Rows",
        "",
        "Proxy rows are not live edge; they are the same decision-price counterfactual layer used for opportunity research.",
        "",
        table(
            [
                "algorithm",
                "row_filter",
                "family",
                "train rows",
                "train dates",
                "train ROI",
                "train excess",
                "train excess CI",
                "holdout rows",
                "holdout dates",
                "holdout ROI",
                "holdout excess",
                "holdout excess CI",
                "top5 removed",
                "gates",
            ],
            result_table_rows(proxy_best),
        ),
        "",
        "## Time-Aligned Orderbook: Top Rows",
        "",
        "Orderbook rows require every selected leg to match an orderbook snapshot with `snapshot_ts_utc <= decision_snapshot_ts_utc`.",
        "",
        table(
            [
                "algorithm",
                "row_filter",
                "family",
                "train rows",
                "train dates",
                "train ROI",
                "train excess",
                "train excess CI",
                "holdout rows",
                "holdout dates",
                "holdout ROI",
                "holdout excess",
                "holdout excess CI",
                "top5 removed",
                "gates",
            ],
            result_table_rows(orderbook_best),
        ),
        "",
        "## Source-Sensitive Diagnostic",
        "",
        "These rows include HK/Jakarta/station-diff source-sensitive cities. They are shown to prevent accidental pooling into generic claims.",
        "",
        table(
            ["algorithm", "rows", "cities", "holdout selected", "holdout dates", "holdout ROI", "note"],
            source_sensitive_rows(payload["source_sensitive_orderbook"]),
        ),
        "",
        "## Findings",
        "",
        "- The data grain is now aligned with the forecast-quality base: source/model are part of the decision-set key.",
        "- `source_bucket_default_wu` is the only generic denominator in the final verdict. The all-source `no_filter` rows are diagnostic only because they include source-sensitive and unresolved settlement-basis cities.",
        "- `exclude_forecast_quality_low` does not become an alpha proof by itself; it is a soft stratification row against the no-filter family baseline.",
        "- Any positive proxy row remains only an opportunity result. The orderbook table is stricter, but still not a live fill result.",
        "",
        "## Three-Gate Verdict",
        "",
        table(
            ["gate", "status", "reason"],
            [
                ["significance", payload["verdict"]["significance"], payload["verdict"]["reason"]],
                ["baseline", payload["verdict"]["baseline"], payload["verdict"]["reason"]],
                ["forward", payload["verdict"]["forward"], payload["verdict"]["reason"]],
            ],
        ),
        "",
        f"`significance={payload['verdict']['significance']}`, `baseline={payload['verdict']['baseline']}`, `forward={payload['verdict']['forward']}`, `conclusion={payload['verdict']['conclusion']}`.",
        "",
        "Plain-English conclusion: forecast-bounded Range RV is cleaner after the source-aware base fix, but this run still cannot be promoted beyond research/shadow instrumentation. No live config change.",
        "",
    ]
    return "\n".join(lines)


def load_clob_gate() -> dict[str, Any]:
    path = ROOT / "runtime" / "_dashboard_logs" / "clob_fill_coverage_gate.json"
    if not path.exists():
        return {"gate_pass": None, "path": str(path), "error": "missing"}
    data = json.loads(path.read_text())
    return {
        "path": str(path),
        "gate_pass": data.get("gate_pass"),
        "fail_reasons": data.get("fail_reasons"),
        "fact_trades_live_real": data.get("fact_trades_live_real"),
    }


def main() -> None:
    args = parse_args()
    db_path = Path(args.db_path)
    conn = fq_base.connect_ro(db_path)
    try:
        self_check = fq_base.mandatory_self_check(conn)
        candidates = fq_base.load_candidates(conn)
        city_counts = source_registry.fact_city_counts(conn)
    finally:
        conn.close()

    registry = source_registry.add_fact_counts(source_registry.build_registry(), city_counts)
    decision_sets = fq_base.build_decision_sets(candidates)
    train_dates, holdout_dates, split = fq_base.split_dates(decision_sets)
    decision_sets = fq_base.add_historical_features(fq_base.add_cross_model_features(decision_sets))
    decision_sets, thresholds = fq_base.add_quality_labels(decision_sets, train_dates)
    decision_sets = fq_source.attach_source_registry(decision_sets, registry)
    strategy_rows = generate_rows(decision_sets)

    orderbook_coverage: dict[str, Any] = {"status": "skipped"}
    if not args.skip_orderbook:
        orderbook_coverage = variants.attach_orderbook(strategy_rows, args.orderbook_glob)

    proxy_metric_rows = variants.metric_rows(strategy_rows, "proxy")
    orderbook_metric_rows = variants.metric_rows(strategy_rows, "orderbook")
    proxy_results = summarize_eval(
        proxy_metric_rows,
        source="decision_market_proxy",
        train_dates=train_dates,
        holdout_dates=holdout_dates,
        iters=args.bootstrap_iters,
        seed=args.seed + 1000,
    )
    orderbook_results = summarize_eval(
        orderbook_metric_rows,
        source="time_aligned_orderbook",
        train_dates=train_dates,
        holdout_dates=holdout_dates,
        iters=args.bootstrap_iters,
        seed=args.seed + 2000,
    )
    source_sensitive_orderbook = source_sensitive_summary(
        orderbook_metric_rows,
        source="time_aligned_orderbook",
        train_dates=train_dates,
        holdout_dates=holdout_dates,
    )
    verdict = final_verdict(proxy_results, orderbook_results)

    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": TARGET_METRIC,
        "db_path": str(db_path),
        "db_last_modified_utc": datetime.fromtimestamp(db_path.stat().st_mtime, timezone.utc).isoformat(),
        "self_check": self_check,
        "clob_gate": load_clob_gate(),
        "split": split,
        "thresholds": thresholds,
        "parameters": {
            "bootstrap_iters": args.bootstrap_iters,
            "seed": args.seed,
            "orderbook_glob": args.orderbook_glob,
            "skip_orderbook": args.skip_orderbook,
            "fixed_widths": [2, 3, 4],
            "expression_policies": ["inside_yes", "cheaper"],
            "row_filters_per_algorithm": ["no_filter", "exclude_forecast_quality_low", "source_bucket_default_wu"],
        },
        "funnel": {
            "fact_signal_candidates_loaded": int(len(candidates)),
            "decision_sets": int(len(decision_sets)),
            "strategy_rows": int(len(strategy_rows)),
            "algorithms": int(len({row["algorithm"] for row in strategy_rows})),
            "orderbook_metric_rows": int(len(orderbook_metric_rows)),
        },
        "source_bucket_counts": source_bucket_counts(decision_sets),
        "orderbook_coverage": orderbook_coverage,
        "proxy_results": proxy_results,
        "orderbook_results": orderbook_results,
        "source_sensitive_orderbook": source_sensitive_orderbook,
        "verdict": verdict,
        "limitations": [
            "Proxy rows are decision-price counterfactuals, not live fills.",
            "Orderbook matching is time-aligned but still historical executable proxy, not a submitted order ledger.",
            "HK/Jakarta/station-diff rows remain source-sensitive until official source adapters are used.",
            "No Range RV result here changes N100/live config.",
        ],
    }
    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    scanner.write_json(out_json, payload)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(render_md(payload), encoding="utf-8")
    print(json.dumps({"out_json": str(out_json), "out_md": str(out_md), "verdict": verdict, "funnel": payload["funnel"]}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
