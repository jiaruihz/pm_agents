#!/usr/bin/env python3
"""Build canonical all-YES underround basket facts from orderbook snapshots.

Target grain:
    one row per (strategy_id, snapshot_ts_utc, event_date, city, event_slug)

The output is a basket-level fact JSONL plus a leg-level companion JSONL.  It is
intended to be the shared denominator for all-YES underround research, instead
of mixing live-prep reports, persistence scans, and forward-paper ledgers.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.market_structure_edge.research_all_yes_underround_live_prep_v0 import (  # noqa: E402
    connect_db,
    data_self_check,
    group_baskets,
    read_json,
    read_snapshot,
    snapshot_summary,
)
from scripts.analysis.market_structure_edge.all_yes_underround_settlement import (  # noqa: E402
    PM_HISTORY_DEFAULT,
    final_yes,
    load_city_bracket_settlements,
    load_condition_settlements,
    resolve_leg_settlement,
)
from scripts.ops.all_yes_underround_guards import BasketGuardConfig, check_candidate, decision_to_dict  # noqa: E402


STRATEGY_ID = "all_yes_underround_basket_v0"
OUT_DIR_DEFAULT = ROOT / "runtime" / "weather_edge_v1" / "all_yes_underround_basket_v0"
SNAPSHOT_ROOT_DEFAULT = ROOT / "runtime" / "weather_edge_v1" / "market_data" / "orderbook_snapshots"
DB_DEFAULT = ROOT / "runtime" / "weather.db"
GATE_DEFAULT = ROOT / "runtime" / "_dashboard_logs" / "clob_fill_coverage_gate.json"
REPORT_JSON_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-15-all-yes-underround-basket-facts-v0.json"
REPORT_MD_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-15-all-yes-underround-basket-facts-v0.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=str(DB_DEFAULT))
    parser.add_argument("--snapshot-root", default=str(SNAPSHOT_ROOT_DEFAULT))
    parser.add_argument("--pm-history-dir", default=str(PM_HISTORY_DEFAULT))
    parser.add_argument("--snapshot-date", action="append", help="YYYY-MM-DD folder to scan; repeatable. Defaults to all dates.")
    parser.add_argument("--snapshot-glob", default="orderbook_snapshot_*.jsonl.gz")
    parser.add_argument("--out-dir", default=str(OUT_DIR_DEFAULT))
    parser.add_argument("--out-baskets", default=None)
    parser.add_argument("--out-legs", default=None)
    parser.add_argument("--out-summary", default=None)
    parser.add_argument("--out-json", default=str(REPORT_JSON_DEFAULT))
    parser.add_argument("--out-md", default=str(REPORT_MD_DEFAULT))
    parser.add_argument("--gate-path", default=str(GATE_DEFAULT))
    parser.add_argument("--min-underround", type=float, default=0.02)
    parser.add_argument("--min-leg-count", type=int, default=5)
    parser.add_argument("--min-shares", type=float, default=5.0)
    parser.add_argument("--max-spread", type=float, default=0.05)
    parser.add_argument("--max-basket-cost-usd", type=float, default=5.0)
    parser.add_argument("--thresholds", default="0.005,0.01,0.02,0.03,0.05")
    parser.add_argument("--limit-files", type=int, default=None, help="Debug only: scan at most this many snapshot files.")
    parser.add_argument("--no-legs", action="store_true", help="Skip writing the leg-level JSONL.")
    return parser.parse_args()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_thresholds(text: str) -> list[float]:
    return [float(part.strip()) for part in text.split(",") if part.strip()]


def snapshot_files(root: Path, dates: list[str] | None, pattern: str) -> list[Path]:
    if dates:
        files: list[Path] = []
        for date in dates:
            files.extend(sorted((root / date).glob(pattern)))
        return sorted(files)
    return sorted(root.glob(f"*/{pattern}"))


def source_snapshot_meta(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "source_snapshot_path": str(path),
        "source_snapshot_size_bytes": stat.st_size,
        "source_snapshot_mtime_utc": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
    }


def basket_id(snapshot_ts_utc: Any, candidate: dict[str, Any]) -> str:
    return "|".join(
        [
            STRATEGY_ID,
            str(snapshot_ts_utc),
            str(candidate.get("event_date")),
            str(candidate.get("city")),
            str(candidate.get("event_slug")),
        ]
    )


def settle_basket(
    candidate: dict[str, Any],
    condition_settlements: dict[str, dict[str, Any]],
    city_bracket_settlements: dict[tuple[str, str, str], dict[str, Any]],
) -> dict[str, Any]:
    legs = list(candidate.get("legs_detail") or [])
    missing = 0
    unresolved = 0
    winners: list[dict[str, Any]] = []
    settled = 0
    for leg in legs:
        condition_id = str(leg.get("condition_id") or "")
        settlement = resolve_leg_settlement(
            leg=leg,
            city=candidate.get("city"),
            event_date=candidate.get("event_date"),
            condition_settlements=condition_settlements,
            city_bracket_settlements=city_bracket_settlements,
        )
        if settlement is None:
            missing += 1
            continue
        yes = final_yes(settlement.get("final_price"))
        if settlement.get("settlement_status") != "settled" or yes is None:
            unresolved += 1
            continue
        settled += 1
        if yes >= 0.5:
            winners.append(leg)

    if not legs:
        status = "missing_legs"
        winner_count = None
    elif missing or unresolved:
        status = "pending"
        winner_count = None
    else:
        winner_count = len(winners)
        status = "settled_exactly_one_winner" if winner_count == 1 else "settled_winner_count_anomaly"

    return {
        "settlement_eval_status": status,
        "settled_legs": settled,
        "missing_settlement_legs": missing,
        "unresolved_settlement_legs": unresolved,
        "winner_count": winner_count,
        "winner_brackets": [leg.get("bracket") for leg in winners],
    }


def threshold_stats(rows: list[dict[str, Any]], thresholds: list[float]) -> list[dict[str, Any]]:
    stats = []
    for threshold in thresholds:
        selected = [
            row
            for row in rows
            if row.get("quality_guard_pass")
            and row.get("underround") is not None
            and float(row["underround"]) >= threshold
        ]
        settled_exact = [row for row in selected if row.get("settlement_eval_status") == "settled_exactly_one_winner"]
        pnl = sum(float(row.get("unit_pnl") or 0.0) for row in settled_exact)
        cost = sum(float(row.get("total_yes_ask_cost") or 0.0) for row in settled_exact)
        stats.append(
            {
                "threshold": threshold,
                "candidate_observations": len(selected),
                "unique_events": len({row.get("opportunity_key") for row in selected}),
                "active_event_dates": len({row.get("event_date") for row in selected}),
                "settled_exactly_one_winner": len(settled_exact),
                "settled_cost_unit": round(cost, 6),
                "settled_pnl_unit": round(pnl, 6),
                "settled_roi": (pnl / cost) if cost else None,
            }
        )
    return stats


def buffer_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    selected = [row for row in rows if row.get("strategy_candidate")]
    buffers = [
        100.0 * float(row["underround"]) / int(row["legs"])
        for row in selected
        if row.get("underround") is not None and int(row.get("legs") or 0) > 0
    ]
    underrounds = [float(row["underround"]) for row in selected if row.get("underround") is not None]
    legs = [int(row["legs"]) for row in selected if row.get("legs") is not None]

    def quantile(values: list[float], q: float) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        return ordered[int(q * (len(ordered) - 1))]

    return {
        "strategy_candidate_observations": len(selected),
        "underround_min_median_p90_max": [
            min(underrounds),
            quantile(underrounds, 0.5),
            quantile(underrounds, 0.9),
            max(underrounds),
        ]
        if underrounds
        else None,
        "legs_min_median_max": [min(legs), quantile([float(v) for v in legs], 0.5), max(legs)] if legs else None,
        "per_leg_buffer_cents_min_median_p90_max": [
            min(buffers),
            quantile(buffers, 0.5),
            quantile(buffers, 0.9),
            max(buffers),
        ]
        if buffers
        else None,
        "survive_extra_cost_by_per_leg_cents": {
            str(cents): sum(
                1
                for row in selected
                if row.get("underround") is not None
                and float(row["underround"]) - int(row.get("legs") or 0) * (cents / 100.0) > 0
            )
            for cents in [0.1, 0.25, 0.5, 1.0]
        },
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")


def fmt_pct(value: Any) -> str:
    if value is None:
        return "NA"
    return f"{float(value) * 100:+.1f}%"


def write_md(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# All-YES Underround Basket Facts v0",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> target_metric: `{report['target_metric']}`",
        f"> contract: `{report['contract']['basket_grain']}`",
        "> Scope: canonical data refresh and research denominator; no live behavior changed.",
        "",
        "## Data Snapshot",
        "",
        f"- DB: `{report['db_path']}`",
        f"- fact built at: `{report['data_self_check']['fact_built_at_utc']}`",
        f"- Snapshot files scanned: `{report['snapshot_files_scanned']}` from `{report['snapshot_first']}` to `{report['snapshot_last']}`.",
        f"- Basket fact rows: `{report['summary']['basket_rows']}`; leg fact rows `{report['summary']['leg_rows']}`.",
        f"- Settlement outcomes loaded: `{report['settlement_source_summary']['city_bracket_settlements_loaded']}` from `{report['contract']['settlement_priority'][1]}`.",
        f"- CLOB fill coverage gate: `gate_pass={report['clob_fill_coverage_gate'].get('gate_pass')}`.",
        f"- Outputs: `{report['outputs']['baskets']}`, `{report['outputs'].get('legs')}`, `{report['outputs']['summary']}`.",
        "",
        "### Mandatory 5-Line SQL Self-Check",
        "",
        "```text",
        f"MAX(fact_built_at_utc) = {report['data_self_check']['fact_built_at_utc']}",
        f"fact_trades by trade_class = {report['data_self_check']['fact_trades_by_class']}",
        f"fact_trades by settlement_status = {report['data_self_check']['fact_trades_by_settlement']}",
        f"fact_signal_candidates coverage = {report['data_self_check']['fact_signal_candidates_coverage']}",
        f"CLOB orders with fills = {report['data_self_check']['clob_orders_with_fills']}",
        "```",
        "",
        "## Canonical Grain",
        "",
        "- `fact_baskets.jsonl`: one row per strategy/snapshot/city/event basket observation.",
        "- `fact_basket_legs.jsonl`: one row per basket leg, keyed by `basket_id + condition_id`.",
        "- `quality_guard_pass` means complete all-leg top-of-book representation without using live TTL.",
        "- `strategy_candidate` additionally applies the configured underround threshold.",
        "- Settlement is resolved by `settlements.condition_id` first, then DB `settlement_outcomes` keyed by `city + target_date + bracket`.",
        "- Forward paper/live readiness remains a separate evidence layer and must use TTL-valid paper/live ledgers.",
        "",
        "## Historical Funnel",
        "",
        f"- Snapshot events observed: `{report['summary']['basket_rows']}`.",
        f"- Quality guard pass observations: `{report['summary']['quality_guard_pass']}`.",
        f"- Strategy candidates at min_underround `{report['parameters']['min_underround']}`: `{report['summary']['strategy_candidates']}`.",
        f"- Unique strategy candidate events: `{report['summary']['strategy_candidate_unique_events']}`.",
        f"- Settled exactly-one-winner candidates: `{report['summary']['strategy_candidate_settled_exactly_one_winner']}`.",
        f"- Pending / missing-settlement candidates: `{report['summary']['strategy_candidate_pending']}`.",
        "",
        "## Threshold Results",
        "",
        "| threshold | observations | unique events | active dates | settled exact | unit ROI |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["threshold_stats"]:
        lines.append(
            f"| {row['threshold']:.3f} | {row['candidate_observations']} | {row['unique_events']} | "
            f"{row['active_event_dates']} | {row['settled_exactly_one_winner']} | {fmt_pct(row['settled_roi'])} |"
        )
    lines.extend(
        [
            "",
            "## Retail Cost Buffer",
            "",
            f"- Per-leg buffer cents min/median/p90/max: `{report['buffer_stats']['per_leg_buffer_cents_min_median_p90_max']}`.",
            f"- Survivors after extra per-leg cost: `{report['buffer_stats']['survive_extra_cost_by_per_leg_cents']}`.",
            "",
            "## Data Gaps",
            "",
        ]
    )
    for item in report["data_gaps"]:
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## Verdict",
            "",
            report["verdict_text"],
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    out_dir = Path(args.out_dir)
    out_baskets = Path(args.out_baskets) if args.out_baskets else out_dir / "fact_baskets.jsonl"
    out_legs = Path(args.out_legs) if args.out_legs else out_dir / "fact_basket_legs.jsonl"
    out_summary = Path(args.out_summary) if args.out_summary else out_dir / "summary.json"
    thresholds = parse_thresholds(args.thresholds)

    files = snapshot_files(Path(args.snapshot_root), args.snapshot_date, args.snapshot_glob)
    if args.limit_files is not None:
        files = files[: args.limit_files]
    if not files:
        raise FileNotFoundError(f"no snapshot files found under {args.snapshot_root}")

    conn = connect_db(Path(args.db_path))
    try:
        self_check = data_self_check(conn)
        condition_settlements = load_condition_settlements(conn)
        city_bracket_settlements = load_city_bracket_settlements(conn, args.pm_history_dir)
    finally:
        conn.close()

    gate = read_json(Path(args.gate_path))
    basket_args = SimpleNamespace(
        min_underround=args.min_underround,
        min_leg_count=args.min_leg_count,
        min_shares=args.min_shares,
        max_spread=args.max_spread,
        top_n=20,
    )
    guard_cfg = BasketGuardConfig(
        min_underround=args.min_underround,
        min_legs=args.min_leg_count,
        shares_per_leg=args.min_shares,
        max_basket_cost_usd=args.max_basket_cost_usd,
        max_yes_spread=args.max_spread,
        max_snapshot_age_seconds=None,
    )

    basket_rows: list[dict[str, Any]] = []
    leg_rows: list[dict[str, Any]] = []
    by_snapshot_date: dict[str, Counter[str]] = defaultdict(Counter)
    for path in files:
        rows = read_snapshot(path)
        snap = snapshot_summary(rows)
        snap_ts = snap.get("snapshot_ts_utc_max")
        meta = source_snapshot_meta(path)
        baskets = group_baskets(rows, basket_args)
        by_snapshot_date[path.parent.name]["snapshots"] += 1
        for candidate in baskets:
            bid = basket_id(snap_ts, candidate)
            quality_candidate = not any(
                blocker == "underround_below_threshold" for blocker in list(candidate.get("blockers") or [])
            )
            quality_blockers = [
                blocker for blocker in list(candidate.get("blockers") or []) if blocker != "underround_below_threshold"
            ]
            quality_guard_pass = not quality_blockers
            strategy_candidate = bool(
                quality_guard_pass
                and candidate.get("underround") is not None
                and float(candidate["underround"]) >= args.min_underround
            )
            guard = (
                decision_to_dict(check_candidate(cfg=guard_cfg, repo_root=ROOT, candidate=candidate))
                if strategy_candidate
                else None
            )
            settlement = settle_basket(candidate, condition_settlements, city_bracket_settlements)
            cost = float(candidate.get("total_yes_ask_cost") or 0.0)
            winner_count = settlement.get("winner_count")
            unit_payout = float(winner_count) if winner_count is not None else None
            unit_pnl = (unit_payout - cost) if unit_payout is not None else None
            basket_row = {
                "strategy_id": STRATEGY_ID,
                "basket_id": bid,
                "opportunity_key": "|".join(
                    [str(candidate.get("event_date")), str(candidate.get("city")), str(candidate.get("event_slug"))]
                ),
                "source_layer": "historical_orderbook_snapshot",
                "snapshot_ts_utc": snap_ts,
                "snapshot_date_folder": path.parent.name,
                "snapshot_rows": snap.get("rows"),
                **meta,
                "event_date": candidate.get("event_date"),
                "city": candidate.get("city"),
                "event_slug": candidate.get("event_slug"),
                "legs": candidate.get("legs"),
                "ask_legs": candidate.get("ask_legs"),
                "missing_asks": candidate.get("missing_asks"),
                "total_yes_ask_cost": candidate.get("total_yes_ask_cost"),
                "underround": candidate.get("underround"),
                "min_ask_size": candidate.get("min_ask_size"),
                "max_yes_spread": candidate.get("max_yes_spread"),
                "scanner_blockers": list(candidate.get("blockers") or []),
                "quality_blockers": quality_blockers,
                "quality_guard_pass": quality_guard_pass,
                "strategy_candidate": strategy_candidate,
                "guard": guard,
                "unit_payout": unit_payout,
                "unit_pnl": round(unit_pnl, 6) if unit_pnl is not None else None,
                **settlement,
            }
            basket_rows.append(basket_row)
            by_snapshot_date[path.parent.name]["basket_rows"] += 1
            if quality_guard_pass:
                by_snapshot_date[path.parent.name]["quality_guard_pass"] += 1
            if strategy_candidate:
                by_snapshot_date[path.parent.name]["strategy_candidates"] += 1
            if not args.no_legs:
                for index, leg in enumerate(candidate.get("legs_detail") or []):
                    condition_id = str(leg.get("condition_id") or "")
                    settlement_row = resolve_leg_settlement(
                        leg=leg,
                        city=candidate.get("city"),
                        event_date=candidate.get("event_date"),
                        condition_settlements=condition_settlements,
                        city_bracket_settlements=city_bracket_settlements,
                    )
                    leg_rows.append(
                        {
                            "strategy_id": STRATEGY_ID,
                            "basket_id": bid,
                            "leg_index": index,
                            "snapshot_ts_utc": snap_ts,
                            "event_date": candidate.get("event_date"),
                            "city": candidate.get("city"),
                            "event_slug": candidate.get("event_slug"),
                            "condition_id": condition_id,
                            "bracket": leg.get("bracket"),
                            "side": "BUY_YES",
                            "best_ask": leg.get("best_ask"),
                            "best_bid": leg.get("best_bid"),
                            "ask_size": leg.get("ask_size"),
                            "settlement_status": (settlement_row or {}).get("settlement_status"),
                            "final_price": (settlement_row or {}).get("final_price"),
                            "final_yes": final_yes((settlement_row or {}).get("final_price"))
                            if settlement_row
                            else None,
                        }
                    )

    write_jsonl(out_baskets, basket_rows)
    if not args.no_legs:
        write_jsonl(out_legs, leg_rows)

    strategy_rows = [row for row in basket_rows if row.get("strategy_candidate")]
    summary = {
        "generated_at_utc": now_utc(),
        "strategy_id": STRATEGY_ID,
        "parameters": {
            "min_underround": args.min_underround,
            "min_leg_count": args.min_leg_count,
            "min_shares": args.min_shares,
            "max_spread": args.max_spread,
            "max_basket_cost_usd": args.max_basket_cost_usd,
            "thresholds": thresholds,
            "pm_history_dir": str(Path(args.pm_history_dir)),
        },
        "snapshot_files_scanned": len(files),
        "snapshot_first": str(files[0]),
        "snapshot_last": str(files[-1]),
        "basket_rows": len(basket_rows),
        "leg_rows": len(leg_rows),
        "quality_guard_pass": sum(1 for row in basket_rows if row.get("quality_guard_pass")),
        "strategy_candidates": len(strategy_rows),
        "strategy_candidate_unique_events": len({row.get("opportunity_key") for row in strategy_rows}),
        "strategy_candidate_active_dates": len({row.get("event_date") for row in strategy_rows}),
        "strategy_candidate_settled_exactly_one_winner": sum(
            1 for row in strategy_rows if row.get("settlement_eval_status") == "settled_exactly_one_winner"
        ),
        "strategy_candidate_pending": sum(1 for row in strategy_rows if row.get("settlement_eval_status") == "pending"),
        "strategy_candidate_winner_count_anomaly": sum(
            1 for row in strategy_rows if row.get("settlement_eval_status") == "settled_winner_count_anomaly"
        ),
        "by_snapshot_date": {date: dict(counter) for date, counter in sorted(by_snapshot_date.items())},
    }
    threshold = threshold_stats(basket_rows, thresholds)
    buffers = buffer_stats(basket_rows)
    data_gaps = []
    if summary["strategy_candidate_pending"]:
        data_gaps.append(
            f"{summary['strategy_candidate_pending']} historical strategy-candidate observations still lack fully resolved settlement outcomes."
        )
    if not gate.get("gate_pass"):
        data_gaps.append("CLOB fill coverage gate is not passing, so live_real PnL must not be published.")
    if not data_gaps:
        data_gaps.append("No basket-fact blocking data gap found for historical orderbook replay; forward TTL paper remains separate.")

    verdict_text = (
        "Historical all-YES orderbook facts are now on a single basket grain. "
        "Use this output for opportunity, settlement, and retail-cost sensitivity; do not use it as live approval, "
        "because forward TTL-valid all-leg fills remain a separate evidence layer."
    )

    report = {
        "generated_at_utc": summary["generated_at_utc"],
        "target_metric": "all_yes_underround_basket_fact_refresh_v0",
        "db_path": str(Path(args.db_path)),
        "data_self_check": self_check,
        "clob_fill_coverage_gate": gate,
        "snapshot_files_scanned": len(files),
        "snapshot_first": str(files[0]),
        "snapshot_last": str(files[-1]),
        "contract": {
            "basket_grain": "strategy_id + snapshot_ts_utc + event_date + city + event_slug",
            "leg_grain": "basket_id + condition_id",
            "source_layers": ["historical_orderbook_snapshot", "forward_paper_ledger", "future_live_fok"],
            "settlement_priority": [
                "settlements.condition_id",
                "settlement_outcomes city/target_date/bracket",
                "pm_history city/date/bracket migration fallback",
            ],
        },
        "settlement_source_summary": {
            "condition_settlements_loaded": len(condition_settlements),
            "city_bracket_settlements_loaded": len(city_bracket_settlements),
        },
        "parameters": summary["parameters"],
        "summary": summary,
        "threshold_stats": threshold,
        "buffer_stats": buffers,
        "data_gaps": data_gaps,
        "outputs": {
            "baskets": str(out_baskets),
            "legs": None if args.no_legs else str(out_legs),
            "summary": str(out_summary),
        },
        "verdict_text": verdict_text,
    }
    out_summary.parent.mkdir(parents=True, exist_ok=True)
    out_summary.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    Path(args.out_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out_json).write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    write_md(Path(args.out_md), report)
    print(f"wrote {out_baskets}")
    if not args.no_legs:
        print(f"wrote {out_legs}")
    print(f"wrote {out_summary}")
    print(f"wrote {args.out_json}")
    print(f"wrote {args.out_md}")
    print(
        "summary="
        + json.dumps(
            {
                "snapshots": len(files),
                "basket_rows": len(basket_rows),
                "strategy_candidates": len(strategy_rows),
                "settled_exact": summary["strategy_candidate_settled_exactly_one_winner"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
