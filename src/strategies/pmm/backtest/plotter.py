from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

import matplotlib.pyplot as plt


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _extract_ticks(metrics: List[Dict[str, Any]]) -> List[int]:
    return [int(x.get("tick", 0)) for x in metrics]


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def plot_scenario(result_dir: str, out_dir: str | None = None) -> Dict[str, Any]:
    result_path = Path(result_dir)
    metrics_path = result_path / "metrics.jsonl"
    actions_path = result_path / "actions.jsonl"
    summary_path = result_path / "summary.json"
    if not metrics_path.exists():
        raise FileNotFoundError(f"metrics not found: {metrics_path}")
    if not summary_path.exists():
        raise FileNotFoundError(f"summary not found: {summary_path}")

    metrics = _read_jsonl(metrics_path)
    actions = _read_jsonl(actions_path)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    scenario_id = str(summary.get("scenario_id", result_path.name))

    plot_root = Path(out_dir) if out_dir else (result_path / "plots")
    _ensure_dir(plot_root)

    manifest: Dict[str, Any] = {
        "scenario_id": scenario_id,
        "result_dir": str(result_path),
        "charts": [],
    }

    ticks = _extract_ticks(metrics)
    equity = [float(x.get("equity", 0.0)) for x in metrics]
    pnl = [float(x.get("pnl", 0.0)) for x in metrics]

    # 1) Equity + PnL
    fig, ax1 = plt.subplots(figsize=(11, 5))
    ax2 = ax1.twinx()
    ax1.plot(ticks, equity, label="equity", color="#1f77b4", linewidth=1.8)
    ax2.plot(ticks, pnl, label="pnl", color="#ff7f0e", linewidth=1.5, linestyle="--")
    ax1.set_title(f"{scenario_id} - Equity & PnL")
    ax1.set_xlabel("tick")
    ax1.set_ylabel("equity")
    ax2.set_ylabel("pnl")
    ax1.grid(alpha=0.25)
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="best")
    f1 = plot_root / "equity_pnl.png"
    fig.tight_layout()
    fig.savefig(f1, dpi=140)
    plt.close(fig)
    manifest["charts"].append(
        {
            "file": str(f1),
            "title": "Equity and PnL over ticks",
            "source_files": [str(metrics_path)],
            "series": [
                {"name": "equity", "field": "equity", "path": "metrics.jsonl[*].equity"},
                {"name": "pnl", "field": "pnl", "path": "metrics.jsonl[*].pnl"},
            ],
        }
    )

    # 2) Mid + final quotes by token
    token_ids: List[str] = list(summary.get("token_ids", []))
    if token_ids:
        fig, axes = plt.subplots(
            nrows=len(token_ids),
            ncols=1,
            figsize=(11, max(4, 3 * len(token_ids))),
            sharex=True,
        )
        if len(token_ids) == 1:
            axes = [axes]
        for ax, token_id in zip(axes, token_ids):
            mids = [float(x.get("mids", {}).get(token_id, 0.0)) for x in metrics]
            bids = [float(x.get("final_quotes", {}).get(token_id, {}).get("bid", 0.0)) for x in metrics]
            asks = [float(x.get("final_quotes", {}).get(token_id, {}).get("ask", 0.0)) for x in metrics]
            ax.plot(ticks, mids, label=f"{token_id[:8]} mid", linewidth=1.8)
            ax.plot(ticks, bids, label=f"{token_id[:8]} bid", linewidth=1.2, linestyle="--")
            ax.plot(ticks, asks, label=f"{token_id[:8]} ask", linewidth=1.2, linestyle=":")
            ax.set_ylabel("price")
            ax.grid(alpha=0.25)
            ax.legend(loc="best", fontsize=8)
        axes[-1].set_xlabel("tick")
        fig.suptitle(f"{scenario_id} - Mid and Final Quotes")
        f2 = plot_root / "prices_quotes.png"
        fig.tight_layout()
        fig.savefig(f2, dpi=140)
        plt.close(fig)
        manifest["charts"].append(
            {
                "file": str(f2),
                "title": "Mid and final bid/ask by token",
                "source_files": [str(metrics_path)],
                "series": [
                    {"name": "mids", "field": "mids.<token_id>", "path": "metrics.jsonl[*].mids"},
                    {
                        "name": "final_quotes_bid",
                        "field": "final_quotes.<token_id>.bid",
                        "path": "metrics.jsonl[*].final_quotes",
                    },
                    {
                        "name": "final_quotes_ask",
                        "field": "final_quotes.<token_id>.ask",
                        "path": "metrics.jsonl[*].final_quotes",
                    },
                ],
            }
        )

    # 3) Positions + USDC
    if token_ids:
        fig, ax1 = plt.subplots(figsize=(11, 5))
        for token_id in token_ids:
            series = [float(x.get("positions", {}).get(token_id, 0.0)) for x in metrics]
            ax1.plot(ticks, series, label=f"pos:{token_id[:8]}", linewidth=1.4)
        ax1.set_xlabel("tick")
        ax1.set_ylabel("position size")
        ax1.grid(alpha=0.25)
        ax2 = ax1.twinx()
        usdc = [float(x.get("usdc_balance", 0.0)) for x in metrics]
        ax2.plot(ticks, usdc, label="usdc_balance", color="#111111", linestyle="--")
        ax2.set_ylabel("usdc")
        lines1, labels1 = ax1.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax1.legend(lines1 + lines2, labels1 + labels2, loc="best")
        ax1.set_title(f"{scenario_id} - Positions and USDC")
        f3 = plot_root / "positions_usdc.png"
        fig.tight_layout()
        fig.savefig(f3, dpi=140)
        plt.close(fig)
        manifest["charts"].append(
            {
                "file": str(f3),
                "title": "Position inventory and USDC balance",
                "source_files": [str(metrics_path)],
                "series": [
                    {"name": "positions", "field": "positions.<token_id>", "path": "metrics.jsonl[*].positions"},
                    {"name": "usdc_balance", "field": "usdc_balance", "path": "metrics.jsonl[*].usdc_balance"},
                ],
            }
        )

    # 4) Action timeline (count by type per tick)
    action_count_by_tick: Dict[int, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    action_types: List[str] = []
    for act in actions:
        tick = int(act.get("tick", 0))
        typ = str(act.get("type", "unknown"))
        action_count_by_tick[tick][typ] += 1
        if typ not in action_types:
            action_types.append(typ)
    if action_count_by_tick:
        ticks_action = sorted(action_count_by_tick.keys())
        fig, ax = plt.subplots(figsize=(11, 4.8))
        bottom = [0] * len(ticks_action)
        palette = ["#4C78A8", "#F58518", "#54A24B", "#EECA3B", "#B279A2", "#FF9DA6"]
        for idx, typ in enumerate(sorted(action_types)):
            vals = [int(action_count_by_tick[t].get(typ, 0)) for t in ticks_action]
            ax.bar(ticks_action, vals, bottom=bottom, label=typ, color=palette[idx % len(palette)], width=0.8)
            bottom = [a + b for a, b in zip(bottom, vals)]
        ax.set_title(f"{scenario_id} - Actions Timeline")
        ax.set_xlabel("tick")
        ax.set_ylabel("count")
        ax.grid(axis="y", alpha=0.25)
        ax.legend(loc="best")
        f4 = plot_root / "actions_timeline.png"
        fig.tight_layout()
        fig.savefig(f4, dpi=140)
        plt.close(fig)
        manifest["charts"].append(
            {
                "file": str(f4),
                "title": "Action counts by tick and type",
                "source_files": [str(actions_path)],
                "series": [
                    {"name": "action_type_counts", "field": "type", "path": "actions.jsonl[*].type", "agg": "count by tick"}
                ],
            }
        )

    manifest_path = plot_root / "plot_manifest.json"
    _write_json(manifest_path, manifest)
    return {
        "scenario_id": scenario_id,
        "plots_dir": str(plot_root),
        "manifest": str(manifest_path),
        "charts": [x["file"] for x in manifest["charts"]],
    }


def plot_all(results_dir: str, out_dir: str | None = None) -> Dict[str, Any]:
    root = Path(results_dir)
    report_path = root / "summary_all.json"
    if not report_path.exists():
        raise FileNotFoundError(f"summary_all not found: {report_path}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    scenarios = list(report.get("scenarios", []))
    if not scenarios:
        raise ValueError("summary_all.json has no scenarios")

    plot_root = Path(out_dir) if out_dir else (root / "plots")
    _ensure_dir(plot_root)

    # pnl ranking
    sorted_by_pnl = sorted(scenarios, key=lambda x: float(x.get("pnl_end", 0.0)))
    names = [str(x.get("scenario_id", "")) for x in sorted_by_pnl]
    pnl_vals = [float(x.get("pnl_end", 0.0)) for x in sorted_by_pnl]
    fig, ax = plt.subplots(figsize=(12, max(4.5, 0.35 * len(names))))
    colors = ["#D62728" if v < 0 else "#2CA02C" for v in pnl_vals]
    ax.barh(names, pnl_vals, color=colors)
    ax.set_title("PNL Ranking by Scenario")
    ax.set_xlabel("pnl_end")
    ax.grid(axis="x", alpha=0.25)
    f1 = plot_root / "summary_pnl_ranking.png"
    fig.tight_layout()
    fig.savefig(f1, dpi=140)
    plt.close(fig)

    # drawdown ranking
    sorted_by_dd = sorted(scenarios, key=lambda x: float(x.get("max_drawdown", 0.0)), reverse=True)
    dd_names = [str(x.get("scenario_id", "")) for x in sorted_by_dd]
    dd_vals = [float(x.get("max_drawdown", 0.0)) for x in sorted_by_dd]
    fig, ax = plt.subplots(figsize=(12, max(4.5, 0.35 * len(dd_names))))
    ax.barh(dd_names, dd_vals, color="#9467BD")
    ax.set_title("Max Drawdown by Scenario")
    ax.set_xlabel("max_drawdown")
    ax.grid(axis="x", alpha=0.25)
    f2 = plot_root / "summary_drawdown_ranking.png"
    fig.tight_layout()
    fig.savefig(f2, dpi=140)
    plt.close(fig)

    # fills vs orders
    fills = [int(x.get("total_fills", 0)) for x in scenarios]
    orders = [int(x.get("total_placed", 0)) for x in scenarios]
    pnl = [float(x.get("pnl_end", 0.0)) for x in scenarios]
    fig, ax = plt.subplots(figsize=(9, 6))
    scatter = ax.scatter(orders, fills, c=pnl, cmap="coolwarm", s=70, alpha=0.85)
    ax.set_title("Fills vs Placed Orders (color = pnl)")
    ax.set_xlabel("total_placed")
    ax.set_ylabel("total_fills")
    ax.grid(alpha=0.25)
    cbar = fig.colorbar(scatter, ax=ax)
    cbar.set_label("pnl_end")
    f3 = plot_root / "summary_fills_vs_orders.png"
    fig.tight_layout()
    fig.savefig(f3, dpi=140)
    plt.close(fig)

    manifest = {
        "results_dir": str(root),
        "charts": [
            {
                "file": str(f1),
                "title": "PNL ranking",
                "source_files": [str(report_path)],
                "series": [{"name": "pnl_end", "path": "summary_all.json.scenarios[*].pnl_end"}],
            },
            {
                "file": str(f2),
                "title": "Max drawdown ranking",
                "source_files": [str(report_path)],
                "series": [{"name": "max_drawdown", "path": "summary_all.json.scenarios[*].max_drawdown"}],
            },
            {
                "file": str(f3),
                "title": "Fills vs orders (color by pnl)",
                "source_files": [str(report_path)],
                "series": [
                    {"name": "total_fills", "path": "summary_all.json.scenarios[*].total_fills"},
                    {"name": "total_placed", "path": "summary_all.json.scenarios[*].total_placed"},
                    {"name": "pnl_end", "path": "summary_all.json.scenarios[*].pnl_end"},
                ],
            },
        ],
    }
    manifest_path = plot_root / "plot_manifest.json"
    _write_json(manifest_path, manifest)
    return {
        "plots_dir": str(plot_root),
        "manifest": str(manifest_path),
        "charts": [str(f1), str(f2), str(f3)],
    }


def plot_compare_all(compare_dir: str, out_dir: str | None = None) -> Dict[str, Any]:
    root = Path(compare_dir)
    matrix_path = root / "compare_matrix.csv"
    aggregate_path = root / "compare_aggregate_by_profile.csv"
    if not matrix_path.exists():
        raise FileNotFoundError(f"compare matrix not found: {matrix_path}")
    if not aggregate_path.exists():
        raise FileNotFoundError(f"compare aggregate not found: {aggregate_path}")

    with matrix_path.open("r", encoding="utf-8") as f:
        matrix_rows = list(csv.DictReader(f))
    with aggregate_path.open("r", encoding="utf-8") as f:
        aggregate_rows = list(csv.DictReader(f))
    if not matrix_rows or not aggregate_rows:
        raise ValueError("compare csv is empty")

    plot_root = Path(out_dir) if out_dir else (root / "plots")
    _ensure_dir(plot_root)

    # 1) profile average pnl ranking
    ranked = sorted(aggregate_rows, key=lambda x: float(x.get("avg_pnl_end", 0.0)))
    names = [str(x.get("profile_name", "")) for x in ranked]
    pnl_vals = [float(x.get("avg_pnl_end", 0.0)) for x in ranked]
    colors = ["#D62728" if v < 0 else "#2CA02C" for v in pnl_vals]

    fig, ax = plt.subplots(figsize=(10, max(4, 0.5 * len(names))))
    ax.barh(names, pnl_vals, color=colors)
    ax.set_title("Average PnL by Profile")
    ax.set_xlabel("avg_pnl_end")
    ax.grid(axis="x", alpha=0.25)
    f1 = plot_root / "compare_avg_pnl_by_profile.png"
    fig.tight_layout()
    fig.savefig(f1, dpi=140)
    plt.close(fig)

    # 2) heatmap: scenario x profile pnl
    scenarios = sorted({str(x.get("scenario_id", "")) for x in matrix_rows})
    profiles = sorted({str(x.get("profile_name", "")) for x in matrix_rows})
    scenario_index = {name: i for i, name in enumerate(scenarios)}
    profile_index = {name: i for i, name in enumerate(profiles)}
    grid: List[List[float]] = [[0.0 for _ in profiles] for _ in scenarios]
    for row in matrix_rows:
        s = str(row.get("scenario_id", ""))
        p = str(row.get("profile_name", ""))
        if s not in scenario_index or p not in profile_index:
            continue
        grid[scenario_index[s]][profile_index[p]] = float(row.get("pnl_end", 0.0))

    fig, ax = plt.subplots(figsize=(max(8, 1.5 * len(profiles)), max(5, 0.35 * len(scenarios))))
    im = ax.imshow(grid, aspect="auto", cmap="coolwarm")
    ax.set_title("PnL Matrix (Scenario x Profile)")
    ax.set_xticks(range(len(profiles)))
    ax.set_xticklabels(profiles, rotation=45, ha="right")
    ax.set_yticks(range(len(scenarios)))
    ax.set_yticklabels(scenarios)
    cbar = fig.colorbar(im, ax=ax)
    cbar.set_label("pnl_end")
    f2 = plot_root / "compare_pnl_heatmap.png"
    fig.tight_layout()
    fig.savefig(f2, dpi=140)
    plt.close(fig)

    # 3) fills vs placed by profile
    fig, ax = plt.subplots(figsize=(10, 6))
    palette = ["#4C78A8", "#F58518", "#54A24B", "#EECA3B", "#B279A2", "#FF9DA6"]
    for idx, profile in enumerate(profiles):
        rows = [x for x in matrix_rows if str(x.get("profile_name", "")) == profile]
        placed = [int(float(x.get("total_placed", 0) or 0)) for x in rows]
        fills = [int(float(x.get("total_fills", 0) or 0)) for x in rows]
        ax.scatter(
            placed,
            fills,
            label=profile,
            s=60,
            alpha=0.85,
            color=palette[idx % len(palette)],
        )
    ax.set_title("Fills vs Placed by Profile")
    ax.set_xlabel("total_placed")
    ax.set_ylabel("total_fills")
    ax.grid(alpha=0.25)
    ax.legend(loc="best")
    f3 = plot_root / "compare_fills_vs_placed.png"
    fig.tight_layout()
    fig.savefig(f3, dpi=140)
    plt.close(fig)

    manifest = {
        "compare_dir": str(root),
        "charts": [
            {
                "file": str(f1),
                "title": "Average PnL ranking by strategy profile",
                "source_files": [str(aggregate_path)],
                "series": [{"name": "avg_pnl_end", "path": "compare_aggregate_by_profile.csv.avg_pnl_end"}],
            },
            {
                "file": str(f2),
                "title": "Scenario/profile PnL heatmap",
                "source_files": [str(matrix_path)],
                "series": [{"name": "pnl_end", "path": "compare_matrix.csv.pnl_end"}],
            },
            {
                "file": str(f3),
                "title": "Fills vs placed by profile",
                "source_files": [str(matrix_path)],
                "series": [
                    {"name": "total_placed", "path": "compare_matrix.csv.total_placed"},
                    {"name": "total_fills", "path": "compare_matrix.csv.total_fills"},
                ],
            },
        ],
    }
    manifest_path = plot_root / "plot_manifest.json"
    _write_json(manifest_path, manifest)
    return {
        "plots_dir": str(plot_root),
        "manifest": str(manifest_path),
        "charts": [str(f1), str(f2), str(f3)],
    }
