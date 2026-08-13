#!/usr/bin/env python3
"""Render the frozen full-ladder distribution-arbitrage audit.

The expensive raw scan is intentionally frozen as a JSON evidence snapshot.
This renderer validates its execution contract and produces the durable human
report used by the strategy registry.  Research-only; it never places orders.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INPUT = ROOT / "runtime/analysis_snapshots/full_ladder_distribution_arb_final_20260714.json"


def pct(value: float | None) -> str:
    return "NA" if value is None else f"{100.0 * float(value):+.3f}%"


def money(value: float | None) -> str:
    return "NA" if value is None else f"${float(value):.5f}"


def row(label: str, family: dict) -> str:
    fwd = family["forward"]
    return (
        f"| {label} | {int(fwd['n'])} | {int(fwd['dates'])} | "
        f"{money(fwd['cost_5share'])} | {money(fwd['locked_profit_5share'])} | {pct(fwd['roi'])} |"
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument(
        "--report",
        type=Path,
        help="optional explicit durable report path; stdout when omitted",
    )
    args = parser.parse_args(argv)

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    params = payload["parameters"]
    required = {
        "event_not_past": True,
        "fee_rate": 0.05,
        "local_date_saved_else_pit_timezone_inferred": True,
        "max_fetch_span_seconds": 30,
        "min_depth": 5.0,
        "q": 5.0,
        "train_cutoff": "2026-06-16",
    }
    if params != required:
        raise ValueError(f"execution contract drift: expected {required!r}, got {params!r}")
    families = payload["families"]
    for name in (
        "all_no_buy",
        "leave_one_out_no_buy",
        "best_no_subset_buy",
        "binary_pair_buy",
        "binary_pair_sell_mint",
        "all_yes_buy",
    ):
        if name not in families:
            raise ValueError(f"missing family: {name}")

    all_yes = families["all_yes_buy"]
    report = "\n".join(
        [
            "# Full-Ladder Distribution Arbitrage v1",
            "",
            "> 目标：换掉单档预测思路，穷举 complete-set / leave-one-out / any-k / mint-sell 表达，检查是否存在费用后、可执行、forward 的确定性结构利润。Research-only；zero notional。",
            "",
            "## 数据与执行口径",
            "",
            f"- `{int(payload['files'])}` 个 PIT orderbook snapshot，覆盖 `{min(payload['coverage'])}..{max(payload['coverage'])}`；event 在城市本地日尚未结束。",
            "- 只保留连续完整 ladder（含 top tail）、同 event 各腿 fetch span `<=30s`、每腿 top depth `>=5 shares`；固定 5 shares。",
            "- entry 使用同 snapshot ask/bid 与官方 Weather taker fee；train `<2026-06-16`，forward `>=2026-06-16`；所有入选篮子 settlement audit 均为 exact-one-in-ladder。",
            "",
            "## Forward 结果",
            "",
            "| expression | opportunities | dates | 5-share cost | locked profit | ROI |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
            row("BUY all NO", families["all_no_buy"]),
            row("BUY NO leave-one-out", families["leave_one_out_no_buy"]),
            row("BUY best any-k NO subset", families["best_no_subset_buy"]),
            row("BUY binary YES+NO", families["binary_pair_buy"]),
            row("mint then sell binary pair", families["binary_pair_sell_mint"]),
            "",
            "- all-NO forward 只剩 Karachi 2026-07-05 一次，locked profit 仅 `$0.01473`；每腿增加 `0.1c` cushion 即归零。",
            "- leave-one-out / best-subset forward 都只有 Tokyo 2026-06-20 与 Karachi 2026-07-05 两次，利润分别 `$0.01330` / `$0.01805`；`0.05c/leg` 级别的最小摩擦已足够清空。",
            "- binary BUY 在 `976,102` 个可执行 condition observations 中 fee 后为 `0`；mint-sell 只有 Shenzhen 2026-06-07 一次 train 机会（+0.797%），forward 为 `0`。",
            "",
            "## 为什么 all-YES / synthetic 不算新 alpha",
            "",
            f"- fee-aware top-of-book 筛选的 all-YES forward 有 `{int(all_yes['forward']['n'])}` 次、点估 ROI `{pct(all_yes['forward']['roi'])}`，但每腿 `0.5c` friction 后全样本机会归零，而且全腿 CLOB 成交不是 atomic。旧 270-basket 分母加官方 fee 后总体 ROI 为 -0.423%，两者共同说明这里只有瞬时 top-of-book pennies，不是可部署套利。",
            "- NegRisk 可把 `NO_i` 原子转换为所有 `YES_j (j != i)`；因此 `sell NO_i + buy other YES`、`sell all NO` 等 synthetic 只是 all-YES/all-NO 的等价执行，不应重复记作独立策略。NegRisk conversion 可以 atomic，但随后多腿 CLOB fill 仍不 atomic。",
            "",
            "## 结论",
            "",
            "- **full-ladder 结构方向否决 live。** 最好的 NO-side forward 只有 1–2 个事件和 1–2 美分总利润，对最小价格漂移不稳；早期 train 的漂亮数字集中在 5/19–5/21 launch-stage 宽盘口，6/16 后不复现。",
            "- 该搜索没有找到新的独立 alpha。后续不再扩展 synthetic 组合；策略研究应转向有时间优势、且能按 source/city 校准错误概率的单腿信息事件。",
            "",
            f"> frozen evidence: `{args.input}`",
            "",
        ]
    )
    if args.report is None:
        print(report)
    else:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report, encoding="utf-8")
        print(args.report)


if __name__ == "__main__":
    main()
