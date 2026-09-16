#!/usr/bin/env python3
"""Quantify fast-source BUY_NO fills that exceeded their planned share cap."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.versioned_artifact_output import (  # noqa: E402
    prepare_new_run_output,
    resolve_run_output,
)

DEFAULT_DB = ROOT / "runtime/weather.db"
DEFAULT_ORDERS = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/output/fast_source_prev_no_trial/orders.jsonl"
)
ARTIFACT_FAMILY = "fast_source_share_cap_incident_v1"
TOLERANCE = 1e-5


def safe_float(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def order_id_from(row: dict[str, Any]) -> str:
    if row.get("order_id"):
        return str(row["order_id"])
    response = row.get("exchange_response") or {}
    if response.get("order_id"):
        return str(response["order_id"])
    return str((response.get("place") or {}).get("orderID") or "")


def load_order_caps(path: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        order_id = order_id_from(row)
        if not order_id or row.get("live_submit_status") != "submitted":
            continue
        desired = safe_float(row.get("desired_shares"))
        if desired is None:
            desired = safe_float(row.get("planned_shares"))
        if desired is None:
            desired = safe_float(row.get("size"))
        market_cap = safe_float(row.get("max_shares_per_market"))
        cap_values = [value for value in (desired, market_cap) if value is not None and value >= 0]
        out[order_id] = {
            "orders_jsonl_line": line_number,
            "planned_shares": desired,
            "max_shares_per_market": market_cap,
            "effective_share_cap": min(cap_values) if cap_values else None,
            "limit_price": safe_float(row.get("limit_price")),
            "best_ask": safe_float(row.get("best_ask")),
            "limit_price_policy": row.get("limit_price_policy"),
            "submitted_notional_usd": safe_float(row.get("submitted_notional_usd")),
        }
    return out


def canonical_fills(db_path: Path) -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    rows = conn.execute(
        """
        SELECT
            order_id,
            MIN(fill_ts_utc) AS fill_ts_utc,
            target_date,
            city,
            bracket,
            side,
            SUM(fill_qty) AS fill_qty,
            SUM(cost_usd) AS cost_usd,
            SUM(fees_usd) AS fees_usd,
            CASE WHEN SUM(fill_qty) > 0 THEN SUM(cost_usd) / SUM(fill_qty) END AS fill_price,
            MIN(settled) AS settled,
            MAX(final_yes) AS final_yes,
            SUM(pnl_usd_at_fill) AS pnl_usd_at_fill,
            COUNT(*) AS canonical_fill_rows
        FROM fact_trades
        WHERE instance_id = 'fast_source_prev_no_trial_v1'
          AND order_id IS NOT NULL
        GROUP BY order_id, target_date, city, bracket, side
        ORDER BY fill_ts_utc
        """
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def affected_rows(db_path: Path, orders_path: Path) -> list[dict[str, Any]]:
    caps = load_order_caps(orders_path)
    out: list[dict[str, Any]] = []
    for fill in canonical_fills(db_path):
        cap_row = caps.get(str(fill["order_id"]))
        if not cap_row or cap_row["effective_share_cap"] is None:
            continue
        actual = float(fill["fill_qty"] or 0.0)
        cap = float(cap_row["effective_share_cap"])
        if actual <= cap + TOLERANCE:
            continue
        price = float(fill["fill_price"] or 0.0)
        fees = float(fill["fees_usd"] or 0.0)
        fee_per_share = fees / actual if actual > 0 else 0.0
        excess = actual - cap
        counterfactual_cost = cap * price
        counterfactual_fees = cap * fee_per_share
        settled = bool(fill["settled"])
        counterfactual_pnl: float | None = None
        pnl_amplification: float | None = None
        if settled:
            buy_no_won = float(fill["final_yes"] or 0.0) == 0.0
            counterfactual_pnl = (cap if buy_no_won else 0.0) - counterfactual_cost - counterfactual_fees
            pnl_amplification = float(fill["pnl_usd_at_fill"] or 0.0) - counterfactual_pnl
        out.append(
            {
                **fill,
                **cap_row,
                "share_cap_excess_shares": excess,
                "share_multiple": actual / cap if cap > 0 else None,
                "excess_cost_usd": excess * price,
                "excess_fees_usd": excess * fee_per_share,
                "counterfactual_cost_usd": counterfactual_cost,
                "counterfactual_fees_usd": counterfactual_fees,
                "counterfactual_pnl_usd": counterfactual_pnl,
                "pnl_amplification_usd": pnl_amplification,
                "governance_label": "share_cap_affected",
            }
        )
    return out


def summarize(rows: list[dict[str, Any]], all_fills: list[dict[str, Any]]) -> dict[str, Any]:
    settled = [row for row in rows if row["settled"]]
    open_rows = [row for row in rows if not row["settled"]]
    all_settled = [row for row in all_fills if row["settled"]]
    actual_strategy_pnl = sum(float(row["pnl_usd_at_fill"] or 0.0) for row in all_settled)
    actual_strategy_cash = sum(float(row["cost_usd"] or 0.0) + float(row["fees_usd"] or 0.0) for row in all_settled)
    settled_excess_cash = sum(float(row["excess_cost_usd"]) + float(row["excess_fees_usd"]) for row in settled)
    amplification = sum(float(row["pnl_amplification_usd"] or 0.0) for row in settled)
    counterfactual_strategy_pnl = actual_strategy_pnl - amplification
    counterfactual_strategy_cash = actual_strategy_cash - settled_excess_cash
    return {
        "affected_window": [min(row["target_date"] for row in rows), max(row["target_date"] for row in rows)],
        "canonical_fill_count": len(all_fills),
        "affected_order_count": len(rows),
        "settled_affected_order_count": len(settled),
        "open_affected_order_count": len(open_rows),
        "excess_shares": sum(float(row["share_cap_excess_shares"]) for row in rows),
        "excess_cost_usd": sum(float(row["excess_cost_usd"]) for row in rows),
        "excess_fees_usd": sum(float(row["excess_fees_usd"]) for row in rows),
        "open_excess_cash_plus_fee_usd": sum(
            float(row["excess_cost_usd"]) + float(row["excess_fees_usd"]) for row in open_rows
        ),
        "settled_affected_actual_pnl_usd": sum(float(row["pnl_usd_at_fill"] or 0.0) for row in settled),
        "settled_affected_counterfactual_pnl_usd": sum(float(row["counterfactual_pnl_usd"] or 0.0) for row in settled),
        "settled_pnl_amplification_usd": amplification,
        "strategy_settled_actual_pnl_usd": actual_strategy_pnl,
        "strategy_settled_actual_cash_plus_fee_usd": actual_strategy_cash,
        "strategy_settled_actual_roi_pct": 100.0 * actual_strategy_pnl / actual_strategy_cash,
        "strategy_settled_share_capped_counterfactual_pnl_usd": counterfactual_strategy_pnl,
        "strategy_settled_share_capped_counterfactual_cash_plus_fee_usd": counterfactual_strategy_cash,
        "strategy_settled_share_capped_counterfactual_roi_pct": 100.0
        * counterfactual_strategy_pnl
        / counterfactual_strategy_cash,
    }


def fmt(value: Any, places: int = 6) -> str:
    return "" if value is None else f"{float(value):.{places}f}"


def render_report(summary: dict[str, Any], rows: list[dict[str, Any]], csv_path: Path) -> str:
    lines = [
        "# Fast-source BUY_NO share-cap incident v1",
        "",
        "结论：旧执行链把 FOK/FAK BUY 当成 share-sized order，但 CLOB 实际按 USDC maker amount 全额成交；价格改善会增加 shares。逐笔 canonical 复核发现 14 笔 fill 中 12 笔超过 planned/max share cap。",
        "",
        "## 影响半径",
        "",
        f"- 窗口：`{summary['affected_window'][0]}` 至 `{summary['affected_window'][1]}`。",
        f"- affected：{summary['affected_order_count']} orders；excess {summary['excess_shares']:.6f} shares，额外 cost ${summary['excess_cost_usd']:.6f}，额外 fee ${summary['excess_fees_usd']:.6f}。",
        f"- 已结算 affected 实际 PnL ${summary['settled_affected_actual_pnl_usd']:.6f}；按同 VWAP/同每股 fee 缩回 cap 的 size-only counterfactual 为 ${summary['settled_affected_counterfactual_pnl_usd']:.6f}，bug 净放大 PnL ${summary['settled_pnl_amplification_usd']:.6f}。负数表示 bug 让 PnL 更差。",
        f"- 全策略 11 笔已结算：实际 ${summary['strategy_settled_actual_pnl_usd']:.6f} / ROI {summary['strategy_settled_actual_roi_pct']:.3f}%；share-capped size-only counterfactual ${summary['strategy_settled_share_capped_counterfactual_pnl_usd']:.6f} / ROI {summary['strategy_settled_share_capped_counterfactual_roi_pct']:.3f}%。",
        f"- 2 笔未结算 affected 额外占用 cash+fee ${summary['open_excess_cash_plus_fee_usd']:.6f}；不把 open cost 写成亏损。",
        f"- 完整 order_id 与原始数值：`{csv_path}`。",
        "",
        "这个 counterfactual 只回答 size 放大造成多少，不声称 maker GTD 会得到相同 fill/VWAP。",
        "",
        "## 逐单清单",
        "",
        "| date | city | bracket | cap | actual | excess | px | actual pnl | capped pnl | amplification | order |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["target_date"]),
                    str(row["city"]),
                    str(row["bracket"]),
                    fmt(row["effective_share_cap"]),
                    fmt(row["fill_qty"]),
                    fmt(row["share_cap_excess_shares"]),
                    fmt(row["fill_price"]),
                    fmt(row["pnl_usd_at_fill"]),
                    fmt(row["counterfactual_pnl_usd"]),
                    fmt(row["pnl_amplification_usd"]),
                    f"`{row['order_id']}`",
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 根因与修复",
            "",
            "- [官方 CLOB order contract](https://docs.polymarket.com/trading/orders/create)：FOK/FAK 是 market order type；BUY 指定的是 USDC spend，price 只是 worst-price protection。旧代码即使通过 `OrderArgs(size=shares)` 签名，只要以 FOK post，撮合仍会花完 makerAmount。",
            "- 7/11 的 `max_no_ask → best_ask+cushion` 只缩小偏差；7/13 仍 10 → 10.444443，7/14 仍 10 → 10.348835。",
            "- 修复后的 runner 使用 one-tick-below-ask 的 `GTD + post_only` share order。它若会立即 crossing 就被拒；一旦 resting，full/partial fill 都受 signed size 限制。代价是即时 fill rate 会下降，这是硬 share cap 与 taker BUY 语义之间的真实取舍。",
            "- 同一 FOK helper 的 HKO hard-share caller 也已切到相同 post-only GTD 路径；当前没有 HKO live process 或历史 order，因此该 caller 的已发生影响为 0。",
            "- runner 的 `share_cap_health` 继续报告历史异常并默认持久化 pause；旧 incident 只能通过显式 `--acknowledge-historical-share-cap-incidents` 解锁。任何 post-fix actual fill 超 cap 同样会写 `share_cap_violation` 并阻止后续 intent，且不能用该 historical acknowledgement 清除。",
            "",
            "## 数据治理",
            "",
            "以上 12 笔保留真实 cash/PnL，不删除；凡研究 per-order sizing、cap compliance 或策略 ROI 时标记 `share_cap_affected` 并同时报告真实与 size-only counterfactual。",
            "",
            "本次只改本地代码与研究产物；未重启、未部署、未下真实订单。",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--orders", type=Path, default=DEFAULT_ORDERS)
    parser.add_argument("--run-id")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--report",
        type=Path,
        help="optional explicit durable report path; omitted by default",
    )
    args = parser.parse_args(argv)

    all_fills = canonical_fills(args.db)
    rows = affected_rows(args.db, args.orders)
    if not rows:
        raise RuntimeError("no share-cap-affected fast-source fills found")
    summary = summarize(rows, all_fills)
    output_dir = prepare_new_run_output(
        resolve_run_output(
            ARTIFACT_FAMILY,
            run_id=args.run_id,
            explicit_output=args.output_dir,
        )
    )
    csv_path = output_dir / "affected_orders.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(render_report(summary, rows, csv_path), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
