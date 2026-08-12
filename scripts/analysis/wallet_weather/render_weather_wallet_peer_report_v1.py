#!/usr/bin/env python3
"""Render the durable report for a peer scan plus whole-history wallet replay."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ratio_text(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def money_text(value: float | None) -> str:
    return "—" if value is None else f"${value:,.0f}"


def count_text(value: float | None) -> str:
    return "—" if value is None else f"{value:.0f}"


def address_text(wallet: str) -> str:
    return f"`{wallet[:8]}…{wallet[-4:]}`"


def peer_name(row: dict[str, Any]) -> str:
    names = [
        str(item.get("name") or "").strip()
        for item in row.get("leaderboard", {}).values()
        if isinstance(item, dict)
    ]
    return next((name for name in names if name and not name.lower().startswith("0x")), "")


def mechanism(row: dict[str, Any]) -> str:
    expression = row["copyability"]["dominant_expression"]
    target_day = row["replication_inputs"]["target_day_buy_cost_share"] or 0
    horizon = "D0 日内" if target_day >= 0.8 else "D-1/D0 混合" if target_day >= 0.4 else "偏 D-1"
    labels = {
        "yes_strip": "连续 YES bounded-range",
        "single_yes": "单档 YES selector",
        "no_only": "NO 排除/尾部",
        "mixed_yes_no": "YES/NO 状态切换",
    }
    effective_cities = row["city_profile"].get("effective_city_count") or 0
    city_scope = (
        "单城"
        if effective_cities <= 1.5
        else "少数城市"
        if effective_cities <= 4
        else "跨城市"
    )
    return f"{city_scope} {horizon} {labels.get(expression, expression)}"


def borrowing_direction(row: dict[str, Any]) -> str:
    expression = row["copyability"]["dominant_expression"]
    target_day = row["replication_inputs"]["target_day_buy_cost_share"] or 0
    if expression == "yes_strip":
        core = "把 exact-bracket 概率质量转成相邻 YES strip；分别研究底仓宽度与中心加权"
    elif expression == "single_yes":
        core = "研究单档 residual selector，并用相邻档总概率防止裸 exact 风险"
    elif expression == "no_only":
        core = "研究模型对某档的排除概率与 NO 价格，不复制临近结算的确定性腿"
    else:
        core = "研究完整 ladder 上的 YES/NO position-state router，不拆成单腿规则"
    if target_day >= 0.8:
        return core + "；放进 WCIR 的固定 checkpoint，避免追求秒级跟单"
    return core + "；优先接入现有 D-1 probability + first-seen/PIT 框架"


def historical_evidence(row: dict[str, Any]) -> tuple[str, int, list[str]]:
    ci_low, _ = row["stability"]["target_date_block_ci95"]
    checks = {
        "lifetime_positive": (row["lifetime"]["turnover_roi"] or 0) > 0,
        "ci_low_positive": ci_low is not None and ci_low > 0,
        "late_half_positive": (row["stability"]["late_half"]["turnover_roi"] or 0) > 0,
        "latest_30_positive": (
            row["stability"]["latest_30_target_dates"]["turnover_roi"] or 0
        ) > 0,
        "without_top_five_positive": (
            row["stability"]["without_top_five_pnl_dates"]["turnover_roi"] or 0
        ) > 0,
        "at_least_30_target_dates": row["coverage"]["independent_target_dates"] >= 30,
    }
    passed = [name for name, ok in checks.items() if ok]
    score = len(passed)
    if score >= 5 and checks["ci_low_positive"]:
        label = "历史较稳健"
    elif checks["lifetime_positive"] and score >= 3:
        label = "历史为正但不稳"
    else:
        label = "历史证据弱/为负"
    return label, score, passed


def decision(row: dict[str, Any], evidence_score: int) -> str:
    copyable = row["copyability"]["copyable_for_our_execution"]
    if copyable and evidence_score >= 5:
        return "优先做同分母 shadow"
    if copyable and evidence_score >= 3:
        return "次优 shadow 候选"
    if copyable:
        return "仅作对照/继续观察"
    return "只借机制，不复制执行"


def assessment_rows(peer: dict[str, Any], comparison: dict[str, Any]) -> list[dict[str, Any]]:
    profiles = {str(row["wallet"]).lower(): row for row in peer["wallets"]}
    rows: list[dict[str, Any]] = []
    for item in comparison["wallets"]:
        wallet = str(item["wallet"]).lower()
        profile = profiles.get(wallet, {})
        evidence_label, evidence_score, passed = historical_evidence(item)
        row = {
            "wallet": wallet,
            "name": peer_name(profile),
            "snapshot_id": item["snapshot_id"],
            "target_date_min": item["coverage"]["target_date_min"],
            "target_date_max": item["coverage"]["target_date_max"],
            "activity_rows": item["coverage"]["activity_rows"],
            "cashflow_complete_events": item["coverage"]["cashflow_complete_events"],
            "independent_target_dates": item["coverage"]["independent_target_dates"],
            "cities": item["coverage"]["cities"],
            "metadata_incomplete_portfolios": item["coverage"][
                "metadata_incomplete_portfolios"
            ],
            "buy_cost": item["lifetime"]["buy_cost"],
            "pnl": item["lifetime"]["pnl"],
            "turnover_roi": item["lifetime"]["turnover_roi"],
            "ci95_low": item["stability"]["target_date_block_ci95"][0],
            "ci95_high": item["stability"]["target_date_block_ci95"][1],
            "late_half_roi": item["stability"]["late_half"]["turnover_roi"],
            "latest_30_roi": item["stability"]["latest_30_target_dates"]["turnover_roi"],
            "without_top_five_roi": item["stability"]["without_top_five_pnl_dates"]["turnover_roi"],
            "median_event_buy_cost": item["replication_inputs"]["median_event_buy_cost"],
            "p90_event_buy_cost": item["replication_inputs"]["p90_event_buy_cost"],
            "median_unique_buy_transactions": item["replication_inputs"]["median_unique_buy_transactions"],
            "median_buy_sessions_gap_gt_5m": item["replication_inputs"]["median_buy_sessions_gap_gt_5m"],
            "median_buy_span_minutes": item["replication_inputs"]["median_buy_span_minutes"],
            "target_day_buy_cost_share": item["replication_inputs"]["target_day_buy_cost_share"],
            "buy_cost_share_by_target_day_offset": item["replication_inputs"][
                "buy_cost_share_by_target_day_offset"
            ],
            "buy_price_cost_weighted": item["replication_inputs"][
                "buy_price_cost_weighted"
            ],
            "buy_cost_share_ge_95c": item["replication_inputs"][
                "buy_cost_share_ge_95c"
            ],
            "buy_cost_share_ge_99c": item["replication_inputs"][
                "buy_cost_share_ge_99c"
            ],
            "sell_proceeds_share_ge_95c": item["replication_inputs"]["sell_proceeds_share_ge_95c"],
            "dominant_expression": item["copyability"]["dominant_expression"],
            "dominant_expression_share": item["copyability"]["dominant_expression_share"],
            "execution_style": item["copyability"]["execution_style"],
            "capital_style": item["copyability"]["capital_style"],
            "copyable_for_our_execution": item["copyability"]["copyable_for_our_execution"],
            "copyability_blockers": item["copyability"]["blockers"],
            "mechanism": mechanism(item),
            "borrowing_direction": borrowing_direction(item),
            "historical_evidence": evidence_label,
            "historical_evidence_score": evidence_score,
            "historical_checks_passed": passed,
            "decision": decision(item, evidence_score),
            "analysis_source": item["source"],
            "top_city": item["city_profile"]["top_city"],
            "top_city_buy_cost_share": item["city_profile"][
                "top_city_buy_cost_share"
            ],
            "top_3_buy_cost_share": item["city_profile"]["top_3_buy_cost_share"],
            "effective_city_count": item["city_profile"]["effective_city_count"],
            "top_cities": item["city_profile"]["top_cities"],
        }
        rows.append(row)
    return sorted(
        rows,
        key=lambda row: (
            row["copyable_for_our_execution"],
            row["historical_evidence_score"],
            row["turnover_roi"] or -999,
        ),
        reverse=True,
    )


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value
                    for key, value in row.items()
                }
            )


def write_markdown(path: Path, rows: list[dict[str, Any]], peer: dict[str, Any]) -> None:
    priority = [row for row in rows if row["decision"] == "优先做同分母 shadow"]
    secondary = [row for row in rows if row["decision"] == "次优 shadow 候选"]
    copyable = [row for row in rows if row["copyable_for_our_execution"]]
    total_activity = sum(int(row["activity_rows"]) for row in rows)
    total_events = sum(int(row["cashflow_complete_events"]) for row in rows)
    metadata_gaps = sum(int(row["metadata_incomplete_portfolios"]) for row in rows)
    lines = [
        "# 新 26 个 weather 钱包：低频与小资金可复制性审计",
        "",
        "Status: research-only / selected-fill evidence",
        "",
        f"Generated: {utc_now()}",
        "",
        "## 结论",
        "",
        f"本轮从 120 个未研究 leaderboard 地址中初筛 26 个并完成全历史 weather 回放；"
        f"合计 {total_activity:,} 条 weather activity、{total_events:,} 个 cashflow-complete city × target_date ladder；"
        f"另有 {metadata_gaps:,} 个 portfolio 缺完整 Gamma ladder metadata，已单列而非伪装完整。",
        "",
        f"执行与资金口径通过 {len(copyable)}/26；其中历史 selected-fill 证据较完整、"
        f"可进入同分母 shadow 的优先候选 {len(priority)} 个，次优候选 {len(secondary)} 个。"
        "这里的‘候选’不是地址跟单，更不是 live gate。",
        "",
        "真正应复制的是机制：用我们自己的 D-1/WCIR 概率生成 exact-bracket 分布，"
        "再用同刻 market residual、ask/depth/fee 决定是否表达。公开钱包成交只提供机制先验和对照标签。",
        "",
        "## 优先研究候选",
        "",
    ]
    for index, row in enumerate((priority + secondary)[:8], start=1):
        ci = f"[{ratio_text(row['ci95_low'])}, {ratio_text(row['ci95_high'])}]"
        lines.extend(
            [
                f"{index}. **{row['name'] or row['wallet']}** ({address_text(row['wallet'])}) — {row['decision']}",
                f"   - 形态：{row['mechanism']}；{row['borrowing_direction']}。",
                f"   - 城市：top city `{row['top_city']}` 占 "
                f"{ratio_text(row['top_city_buy_cost_share'])}，effective city count "
                f"{row['effective_city_count']:.1f}。",
                f"   - 执行：中位 {count_text(row['median_unique_buy_transactions'])} 笔 / "
                f"{count_text(row['median_buy_sessions_gap_gt_5m'])} sessions，event cost 中位 "
                f"{money_text(row['median_event_buy_cost'])}、P90 {money_text(row['p90_event_buy_cost'])}。",
                f"   - 历史：{row['cashflow_complete_events']} events / "
                f"{row['independent_target_dates']} dates，ROI {ratio_text(row['turnover_roi'])}，"
                f"target-date CI {ci}，{row['historical_evidence']}。",
            ]
        )
    if not priority and not secondary:
        lines.append("没有地址同时通过执行/资金口径与最低历史稳定性要求。")

    lines.extend(
        [
            "",
            "## 逐地址审计",
            "",
            "| 地址 / 名称 | 完整 events / dates | 主形态 | 中位交易笔数 | event cost 中位 / P90 | 历史 ROI / CI95 | 判断 |",
            "|---|---:|---|---:|---:|---:|---|",
        ]
    )
    for row in rows:
        name = f" / {row['name']}" if row["name"] else ""
        ci = f"{ratio_text(row['ci95_low'])}…{ratio_text(row['ci95_high'])}"
        blockers = "、".join(row["copyability_blockers"])
        verdict = row["decision"] + (f"（{blockers}）" if blockers else "")
        lines.append(
            f"| {address_text(row['wallet'])}{name} | "
            f"{row['cashflow_complete_events']} / {row['independent_target_dates']} | "
            f"{row['mechanism']} | {count_text(row['median_unique_buy_transactions'])} | "
            f"{money_text(row['median_event_buy_cost'])} / {money_text(row['p90_event_buy_cost'])} | "
            f"{ratio_text(row['turnover_roi'])} / {ci} | {verdict} |"
        )

    lines.extend(
        [
            "",
            "## 怎么转成我们的赚钱研究",
            "",
            "1. 对优先地址只提取 `horizon × ladder expression × sizing × exit` 机制标签，不用其公开成交作 trigger。",
            "2. D-1 地址接现有跨城市概率模型；D0 地址接 WCIR 固定 checkpoint。二者都输出完整 exact-bracket probability。",
            "3. 在固定机会分母上比较 `our probability`、`market baseline`、`wallet-inspired expression`，价格只用于 residual/EV，不用于平滑亏损。",
            "4. 用 first-seen、provider run timestamp、PIT book、官方 fee 和真实 depth 做 frozen replay；至少 15 个新 target dates 后再判断是否存在可交易 alpha。",
            "5. 高频、资本集中或 near-binary 地址保留为 mechanism/control，不删除，也不进入跟单或 live。",
            "",
            "## 证据边界",
            "",
            "- 初筛是 research-priority screen，不是 alpha gate；未入选的 94 个地址只完成近期 profile，没有伪装成全历史结论。",
            "- 钱包公开 timestamp 是 fill time，不是 signal/order-post time；看不到未成交单、私有模型和真实队列。",
            "- PnL 是 cashflow-complete resolved ladder 的历史 selected fills；没有我们的全机会分母与同刻 market baseline。",
            "- 每个钱包按 city × target_date 合并完整互斥 ladder；不把单腿盈利冒充整套策略。",
            "- 大 raw 保留在 JRS external-wallet research store，不写 canonical weather.db。",
            "",
            "## 可复跑产物",
            "",
            f"- peer scan schema: `{peer['schema_version']}`；snapshot `{peer['snapshot_utc']}`。",
            "- `wallet_assessments.csv`：逐地址可排序表。",
            "- `wallet_assessments.json`：完整指标、blocker、机制与研究动作。",
            "- comparison JSON/CSV：cashflow-complete whole-ladder 同口径输出。",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--peer-scan", type=Path, required=True)
    parser.add_argument("--comparison", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    peer = json.loads(args.peer_scan.read_text(encoding="utf-8"))
    comparison = json.loads(args.comparison.read_text(encoding="utf-8"))
    rows = assessment_rows(peer, comparison)
    if len(rows) < 20:
        raise RuntimeError(f"expected at least 20 fully replayed wallets, got {len(rows)}")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "weather_wallet_peer_assessment_v1",
        "generated_at_utc": utc_now(),
        "denominator_scope": comparison["grain"],
        "wallets": rows,
    }
    (output / "wallet_assessments.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_csv(output / "wallet_assessments.csv", rows)
    write_markdown(output / "report.md", rows, peer)
    print(
        json.dumps(
            {
                "status": "complete",
                "wallets": len(rows),
                "copyable": sum(row["copyable_for_our_execution"] for row in rows),
                "priority": sum(row["decision"] == "优先做同分母 shadow" for row in rows),
                "output": str(output),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
