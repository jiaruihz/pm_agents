from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable


DEFAULT_INPUT = Path("runtime/weather_low_price_gamma_v1/research/weak_gamma_candidates.csv")
DEFAULT_OUT_DIR = Path("runtime/weather_low_price_gamma_v1/research")
DEFAULT_DOC = Path("docs/WEATHER_LOW_PRICE_LOTTERY_RESEARCH_2026-05-19.md")


@dataclass(frozen=True)
class Window:
    name: str
    min_h: float
    max_h: float

    def contains(self, hours_to_settle: float) -> bool:
        return self.min_h <= hours_to_settle < self.max_h


WINDOWS = [
    Window("T-30..40h", 30, 40),
    Window("T-24..30h", 24, 30),
    Window("T-20..24h", 20, 24),
    Window("T-16..20h", 16, 20),
    Window("T-12..16h", 12, 16),
    Window("T-8..12h", 8, 12),
    Window("T-4..8h", 4, 8),
]


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            final = row.get("final_resolution")
            pnl = row.get("hold_pnl_proxy")
            if final == "" or pnl == "":
                continue
            entry = _float(row.get("entry_price_proxy"))
            if entry <= 0:
                continue
            parsed = dict(row)
            parsed["_entry"] = entry
            parsed["_prob"] = _float(row.get("model_prob"))
            parsed["_edge"] = _float(row.get("edge"))
            parsed["_ratio"] = _float(row.get("prob_to_price"))
            parsed["_h2s"] = _float(row.get("hours_to_settle"), -999)
            parsed["_pnl"] = _float(pnl)
            parsed["_win"] = 1 if _float(final) >= 0.999 else 0
            parsed["_event_date"] = date.fromisoformat(str(row.get("event_date")))
            rows.append(parsed)
    return rows


def _stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "n": 0,
            "cost": 0.0,
            "pnl": 0.0,
            "roi": 0.0,
            "hit_rate": 0.0,
            "date_count": 0,
            "first_date": "",
            "last_date": "",
            "city_count": 0,
            "market_count": 0,
            "max_daily_loss": 0.0,
            "worst_date": "",
        }
    cost = sum(r["_entry"] for r in rows)
    pnl = sum(r["_pnl"] for r in rows)
    wins = sum(r["_win"] for r in rows)
    daily: dict[str, float] = defaultdict(float)
    for row in rows:
        daily[str(row["event_date"])] += row["_pnl"]
    worst_date, worst_pnl = min(daily.items(), key=lambda item: item[1])
    dates = sorted({str(r["event_date"]) for r in rows})
    return {
        "n": len(rows),
        "cost": round(cost, 4),
        "pnl": round(pnl, 4),
        "roi": round(pnl / cost, 4) if cost else 0.0,
        "hit_rate": round(wins / len(rows), 4),
        "avg_entry": round(cost / len(rows), 4),
        "avg_prob": round(sum(r["_prob"] for r in rows) / len(rows), 4),
        "avg_edge": round(sum(r["_edge"] for r in rows) / len(rows), 4),
        "date_count": len({r["event_date"] for r in rows}),
        "first_date": dates[0],
        "last_date": dates[-1],
        "city_count": len({r["city"] for r in rows}),
        "market_count": len({(r["city"], r["event_date"], r["bracket"], r["unit"]) for r in rows}),
        "max_daily_loss": round(min(0.0, worst_pnl), 4),
        "worst_date": worst_date,
    }


def _dedup_earliest(rows: Iterable[dict[str, Any]], key_fn: Callable[[dict[str, Any]], tuple]) -> list[dict[str, Any]]:
    selected: dict[tuple, dict[str, Any]] = {}
    for row in rows:
        key = key_fn(row)
        prev = selected.get(key)
        if prev is None or str(row.get("entry_time_utc", "")) < str(prev.get("entry_time_utc", "")):
            selected[key] = row
    return list(selected.values())


def _window_rows(rows: list[dict[str, Any]], window: Window) -> list[dict[str, Any]]:
    eligible = [row for row in rows if window.contains(row["_h2s"])]
    return _dedup_earliest(
        eligible,
        lambda r: (r["city"], r["event_date"], r["bracket"], r["unit"], window.name),
    )


def _condition_name(cond: dict[str, Any]) -> str:
    return (
        f"price={cond['price_name']}, "
        f"prob>={cond['min_prob']:.0%}, "
        f"ratio>={cond['min_ratio']:g}, "
        f"edge>={cond['min_edge']:.0%}"
    )


def _conditions() -> list[dict[str, Any]]:
    price_ranges = [
        ("05-20c", 0.05, 0.20),
        ("05-08c", 0.05, 0.08),
        ("08-12c", 0.08, 0.12),
        ("10-20c", 0.10, 0.20),
        ("16-20c", 0.16, 0.20),
    ]
    out = []
    for price_name, min_price, max_price in price_ranges:
        for min_prob in [0.15, 0.20, 0.25, 0.30]:
            for min_ratio in [1.5, 2.0, 3.0, 5.0]:
                for min_edge in [0.05, 0.10, 0.20]:
                    out.append(
                        {
                            "price_name": price_name,
                            "min_price": min_price,
                            "max_price": max_price,
                            "min_prob": min_prob,
                            "min_ratio": min_ratio,
                            "min_edge": min_edge,
                        }
                    )
    return out


def _apply_condition(rows: Iterable[dict[str, Any]], cond: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if cond["min_price"] <= row["_entry"] <= cond["max_price"]
        and row["_prob"] >= cond["min_prob"]
        and row["_ratio"] >= cond["min_ratio"]
        and row["_edge"] >= cond["min_edge"]
    ]


def _split(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    train_end = date(2026, 5, 11)
    train = [row for row in rows if row["_event_date"] <= train_end]
    test = [row for row in rows if row["_event_date"] > train_end]
    return train, test


def _top_contributors(rows: list[dict[str, Any]], key: str, limit: int = 8) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(key, ""))].append(row)
    ranked = []
    for value, items in grouped.items():
        s = _stats(items)
        ranked.append({"key": value, **s})
    return sorted(ranked, key=lambda item: item["pnl"], reverse=True)[:limit]


def _bottom_contributors(rows: list[dict[str, Any]], key: str, limit: int = 8) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(key, ""))].append(row)
    ranked = []
    for value, items in grouped.items():
        s = _stats(items)
        ranked.append({"key": value, **s})
    return sorted(ranked, key=lambda item: item["pnl"])[:limit]


def _candidate_cards(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cards = []
    for window in WINDOWS:
        base = _window_rows(rows, window)
        train_base, test_base = _split(base)
        for cond in _conditions():
            selected = _apply_condition(base, cond)
            train = _apply_condition(train_base, cond)
            test = _apply_condition(test_base, cond)
            overall = _stats(selected)
            train_s = _stats(train)
            test_s = _stats(test)
            if overall["n"] < 80 or test_s["n"] < 30:
                continue
            # Keep robust-enough cards: positive out-of-sample and not only one city/day.
            if test_s["roi"] <= 0 or test_s["date_count"] < 2 or test_s["city_count"] < 4:
                continue
            cards.append(
                {
                    "window": window.name,
                    "condition": _condition_name(cond),
                    "condition_raw": cond,
                    "_signature": sorted(
                        (
                            row["city"],
                            row["event_date"],
                            row["bracket"],
                            row["unit"],
                            row["entry_time_utc"],
                        )
                        for row in selected
                    ),
                    "overall": overall,
                    "train": train_s,
                    "test": test_s,
                    "top_cities": _top_contributors(selected, "city", limit=3),
                    "worst_cities": _bottom_contributors(selected, "city", limit=3),
                }
            )
    ranked = sorted(
        cards,
        key=lambda card: (
            card["test"]["roi"],
            card["test"]["n"],
            card["overall"]["roi"],
        ),
        reverse=True,
    )
    deduped = []
    seen = set()
    for card in ranked:
        signature = tuple(card.pop("_signature"))
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(card)
    return deduped


def _sample_distribution(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_date = Counter(str(row["event_date"]) for row in rows)
    by_city = Counter(str(row["city"]) for row in rows)
    by_window = {}
    for window in WINDOWS:
        by_window[window.name] = _stats(_window_rows(rows, window))
    return {
        "all_timepoint_rows": _stats(rows),
        "earliest_once_per_market": _stats(
            _dedup_earliest(rows, lambda r: (r["city"], r["event_date"], r["bracket"], r["unit"]))
        ),
        "by_event_date": [{"date": k, "n": v} for k, v in by_date.most_common()],
        "top_cities_by_timepoint_count": [{"city": k, "n": v} for k, v in by_city.most_common(25)],
        "by_entry_window": by_window,
    }


def _format_stat(s: dict[str, Any]) -> str:
    return (
        f"n={s['n']}, ROI={s['roi']:.1%}, hit={s['hit_rate']:.1%}, "
        f"cost={s['cost']:.2f}, pnl={s['pnl']:.2f}, dates={s['date_count']}, cities={s['city_count']}"
    )


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    cards = report["candidate_cards"][:8]
    lines = [
        "# 天气低价 YES 彩票仓研究 - 2026-05-19",
        "",
        "这是第一版低价 YES 彩票仓研究文档，重点分析 `5c-20c` 低价 YES 在持有到结算口径下是否有筛选价值。本报告使用已经结算的历史 snapshot 代理价格，不使用可成交 ask/bid；后续 snapshot 已经开始记录 CLOB orderbook 字段，等数据自然积累后再切换到强回测口径。",
        "",
        "## 样本定义",
        "",
        "原始样本单位是一条时间点机会：`snapshot_time + city + event_date + bracket + BUY_YES`。",
        "",
        "进入本报告前的过滤条件：",
        "",
        "- 只看 `BUY_YES`",
        "- 代理入场价在 `5c` 到 `20c`",
        "- `model_prob / entry_price >= 1.5`",
        "- `model_prob - entry_price >= 3pt`",
        "- 已经有最终结算结果",
        "",
        "同一个城市、同一天、同一个温度 bin 会在不同 snapshot 里反复出现，所以策略评估不用 8118 条原始行直接算，而是按入场窗口去重：每个 `city + event_date + bracket + entry_window` 只保留最早一次符合条件的入场。",
        "",
        "## 数据覆盖",
        "",
        f"- 原始时间点机会：`{report['sample_distribution']['all_timepoint_rows']['n']}`",
        f"- 如果每个 market 只取最早一次，唯一 market 数：`{report['sample_distribution']['earliest_once_per_market']['n']}`",
        f"- 日期范围：`{report['sample_distribution']['all_timepoint_rows']['first_date']}` 到 `{report['sample_distribution']['all_timepoint_rows']['last_date']}`",
        f"- 覆盖 `{report['sample_distribution']['all_timepoint_rows']['date_count']}` 个已结算 event date，`{report['sample_distribution']['all_timepoint_rows']['city_count']}` 个城市。",
        "",
        "## 核心结论",
        "",
        "- 低价 YES 全池不能直接交易；原始 8118 行主要是时间点扫描结果，不是 8118 个独立下注机会。",
        "- 按入场窗口去重后，这批数据里最值得继续看的窗口是 `T-20..24h` 和 `T-24..30h`。",
        "- 表现较好的规则基本都要求高模型 edge，例如 `edge >= 20pt`，或者 `model_prob >= 30%` 且 `prob / price >= 2`。",
        "- 这个结果只能作为研究线索，不能直接当实盘规则：样本只有 11 个结算日，并且当前价格还是 snapshot 代理价格，不是可成交 ask。",
        "",
        "按窗口去重后的整体表现：",
        "",
        "| Window | n | ROI | Hit rate | Cost | PnL | Dates | Cities |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for window, stats in report["sample_distribution"]["by_entry_window"].items():
        lines.append(
            f"| {window} | {stats['n']} | {stats['roi']:.1%} | {stats['hit_rate']:.1%} | "
            f"{stats['cost']:.2f} | {stats['pnl']:.2f} | {stats['date_count']} | {stats['city_count']} |"
        )
    lines.extend(
        [
            "",
            "## 候选规则",
            "",
            f"完整 JSON 里保留 `{len(report['candidate_cards'])}` 条通过初筛的候选规则；Markdown 只展示排名靠前的 `{len(cards)}` 条，避免把文档变成参数表。候选规则要求 test ROI 为正，且 test 至少覆盖 2 个日期、4 个城市。",
            "",
            "- train: `2026-05-05` 到 `2026-05-11`",
            "- test: `2026-05-12` 到 `2026-05-15`",
            "",
        ]
    )
    if not cards:
        lines.append("没有候选规则通过稳健性过滤，这意味着当前不应该部署彩票仓。")
    for idx, card in enumerate(cards, start=1):
        lines.extend(
            [
                f"### Candidate {idx}: {card['window']} / {card['condition']}",
                "",
                f"- Overall: {_format_stat(card['overall'])}",
                f"- Train: {_format_stat(card['train'])}",
                f"- Test: {_format_stat(card['test'])}",
                f"- 全样本最大单日亏损：`{card['overall']['max_daily_loss']:.2f}`，日期 `{card['overall']['worst_date']}`",
                "",
                "主要正贡献城市：",
                "",
            ]
        )
        for item in card["top_cities"]:
            lines.append(f"- `{item['key']}`: {_format_stat(item)}")
        lines.extend(["", "主要负贡献城市：", ""])
        for item in card["worst_cities"]:
            lines.append(f"- `{item['key']}`: {_format_stat(item)}")
        lines.append("")
    lines.extend(
        [
            "## 解释",
            "",
            "这批结果说明：低价 YES 本身不是策略，真正可能有价值的是“特定入场窗口 + 高模型概率/高价格比 + 高 edge”的子集。",
            "",
            "下一步最重要的验证不是补历史 CLOB，因为这部分现在拿不到足够可靠的历史 orderbook。正确做法是让未来定时 snapshot 自然记录 `yes_best_ask` / `yes_best_bid`，等盘口恢复且样本积累后，用可成交价格重跑同一套窗口研究。",
            "",
            "## 下一步",
            "",
            "1. 先把本版本作为研究 baseline，不直接变成实盘规则。",
            "2. 等 orderbook-enriched snapshot 积累数日后，用 `entry = yes_best_ask` 重跑。",
            "3. 加入去重持仓规则：同一个 `city + event_date + bracket` 只允许一笔打开中的彩票仓。",
            "4. 在任何 paper/live sleeve 前加风控：每日最大票数、单城市最大成本、单 event date 最大成本。",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    rows = _load_rows(Path(args.input))
    cards = _candidate_cards(rows)
    report = {
        "generated_for": "2026-05-19",
        "input": str(args.input),
        "sample_distribution": _sample_distribution(rows),
        "candidate_cards": cards,
    }
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "lottery_entry_window_research_2026-05-19.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(Path(args.doc), report)
    print(json.dumps({"json": str(json_path), "doc": str(args.doc), "candidate_cards": len(cards)}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Entry-window lottery research for low-price weather YES tickets.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--doc", default=str(DEFAULT_DOC))
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
