"""
research_city_day_distribution_quality.py

Evaluate city-day temperature probability distributions used by basket research.

The optimizer work showed that combo enumeration can raise headline ROI while
making tail dependence worse. Before adding more objectives, this script checks
whether the underlying city-day distribution is reliable enough.

Offline only. Source: fact_signal_candidates representative T-22~24h snapshot.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd

from scripts.analysis.eval_city_day_basket import DB_DEFAULT, OUT_DEFAULT  # noqa: E402
from weather_dashboard.blend import blend_probability, load_default_config  # noqa: E402


def _load_rows() -> pd.DataFrame:
    sql = """
        SELECT city, event_date, bracket,
               model_p_yes, market_yes_price, final_yes,
               live_filled
        FROM fact_signal_candidates
        WHERE settlement_status='settled'
          AND decision_window_missing=0
          AND model_p_yes IS NOT NULL
          AND market_yes_price IS NOT NULL
          AND final_yes IS NOT NULL
    """
    with sqlite3.connect(str(DB_DEFAULT)) as conn:
        df = pd.read_sql_query(sql, conn)
    return df.drop_duplicates(["city", "event_date", "bracket"]).reset_index(drop=True)


def _slice(df: pd.DataFrame, name: str) -> pd.DataFrame:
    if name == "full":
        return df
    if name == "train_pre_2026_05_26":
        return df[df["event_date"] < "2026-05-26"].reset_index(drop=True)
    if name == "holdout_from_2026_05_26":
        return df[df["event_date"] >= "2026-05-26"].reset_index(drop=True)
    if name == "recent_from_2026_06_01":
        return df[df["event_date"] >= "2026-06-01"].reset_index(drop=True)
    if name == "live_filled_only":
        # Opportunity subset diagnostic only; live_filled is side-level in raw table,
        # so after bracket dedup this means any live-filled row existed for bracket.
        return df[df["live_filled"] == 1].reset_index(drop=True)
    raise ValueError(name)


def _normalize(values: dict[str, float]) -> dict[str, float]:
    clipped = {k: max(1e-9, min(1.0, float(v))) for k, v in values.items()}
    total = sum(clipped.values())
    return {k: v / total for k, v in clipped.items()} if total else {}


def _entropy(probs: dict[str, float]) -> float:
    if not probs:
        return 0.0
    h = -sum(p * math.log(p) for p in probs.values() if p > 0)
    return h / math.log(len(probs)) if len(probs) > 1 else 0.0


def _dist_for_group(grp: pd.DataFrame, kind: str) -> dict[str, float]:
    blend_cfg = load_default_config()
    raw = {}
    market = {}
    blend = {}
    city = str(grp["city"].iloc[0])
    for r in grp.itertuples():
        bracket = str(r.bracket)
        raw[bracket] = float(r.model_p_yes)
        market[bracket] = float(r.market_yes_price)
        br = blend_probability(
            city=city,
            model_p_yes_raw=float(r.model_p_yes),
            market_implied_p_yes=float(r.market_yes_price),
            config=blend_cfg,
        )
        blend[bracket] = br.p_yes_used
    if kind == "uniform":
        n = len(raw)
        return {k: 1.0 / n for k in raw} if n else {}
    if kind == "raw_norm":
        return _normalize(raw)
    if kind == "market_norm":
        return _normalize(market)
    if kind == "blend_norm":
        return _normalize(blend)
    if kind == "dist_blend_norm":
        raw_n = _normalize(raw)
        market_n = _normalize(market)
        alpha = 0.30
        if city in blend_cfg.raw_model_blacklist:
            alpha = blend_cfg.blacklist_alpha
        return {k: alpha * raw_n[k] + (1.0 - alpha) * market_n[k] for k in raw_n}
    raise ValueError(kind)


def _evaluate(df: pd.DataFrame) -> dict:
    kinds = ["uniform", "raw_norm", "market_norm", "blend_norm", "dist_blend_norm"]
    agg = {
        kind: {
            "n_city_days": 0,
            "n_brackets": 0,
            "log_loss_sum": 0.0,
            "brier_sum": 0.0,
            "top1_hits": 0,
            "winner_prob_sum": 0.0,
            "winner_rank_sum": 0.0,
            "entropy_sum": 0.0,
            "skipped_no_unique_winner": 0,
        }
        for kind in kinds
    }
    for (_city, _date), grp in df.groupby(["city", "event_date"]):
        winners = [str(r.bracket) for r in grp.itertuples() if float(r.final_yes) >= 0.5]
        for kind in kinds:
            st = agg[kind]
            if len(winners) != 1:
                st["skipped_no_unique_winner"] += 1
                continue
            winner = winners[0]
            probs = _dist_for_group(grp, kind)
            if winner not in probs:
                st["skipped_no_unique_winner"] += 1
                continue
            p_win = max(1e-9, probs[winner])
            top = max(probs, key=probs.get)
            brier = 0.0
            for b, p in probs.items():
                y = 1.0 if b == winner else 0.0
                brier += (p - y) ** 2
            rank = 1 + sum(1 for p in probs.values() if p > probs[winner])
            st["n_city_days"] += 1
            st["n_brackets"] += len(probs)
            st["log_loss_sum"] += -math.log(p_win)
            st["brier_sum"] += brier
            st["top1_hits"] += int(top == winner)
            st["winner_prob_sum"] += p_win
            st["winner_rank_sum"] += rank
            st["entropy_sum"] += _entropy(probs)
    out = {}
    for kind, st in agg.items():
        n = st["n_city_days"]
        out[kind] = {
            "n_city_days": n,
            "avg_brackets": st["n_brackets"] / n if n else 0.0,
            "log_loss": st["log_loss_sum"] / n if n else 0.0,
            "brier_multiclass": st["brier_sum"] / n if n else 0.0,
            "top1_accuracy": st["top1_hits"] / n if n else 0.0,
            "avg_winner_prob": st["winner_prob_sum"] / n if n else 0.0,
            "avg_winner_rank": st["winner_rank_sum"] / n if n else 0.0,
            "avg_entropy_norm": st["entropy_sum"] / n if n else 0.0,
            "skipped_no_unique_winner": st["skipped_no_unique_winner"],
        }
    return out


def _write_md(report: dict, out_path: Path) -> None:
    lines = [
        "# City-Day Distribution Quality Research — 2026-06-06",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> DB: `{report['db_path']}`",
        "> Scope: offline distribution diagnostic only; no N100/live behavior changed.",
        "",
        "## Question",
        "",
        "Before improving basket objectives, check whether the city-day temperature distribution is reliable enough.",
        "",
        "Distributions:",
        "",
        "- `uniform`: no-information baseline over listed brackets.",
        "- `raw_norm`: normalize raw model bracket probabilities.",
        "- `market_norm`: normalize market yes prices.",
        "- `blend_norm`: per-bracket blend then normalize.",
        "- `dist_blend_norm`: normalize raw and market first, then blend distributions.",
        "",
        "Lower log loss / Brier is better; higher top1 accuracy / winner probability is better.",
        "",
        "## Results",
        "",
        "| slice | dist | n | logloss | brier | top1 | winner p | winner rank | entropy |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for slice_name, dists in report["slices"].items():
        for kind, st in dists.items():
            lines.append(
                f"| {slice_name} | {kind} | {st['n_city_days']} | "
                f"{st['log_loss']:.4f} | {st['brier_multiclass']:.4f} | "
                f"{st['top1_accuracy']*100:.1f}% | {st['avg_winner_prob']:.4f} | "
                f"{st['avg_winner_rank']:.2f} | {st['avg_entropy_norm']:.3f} |"
            )
    lines.extend([
        "",
        "## Quant Read",
        "",
        "- If market-normalized distributions dominate holdout, basket objective should stay market-anchored.",
        "- If blend distributions improve only full/train but not holdout/recent, using them in an optimizer can amplify overfit.",
        "- Distribution quality should be checked before adding more basket objective complexity.",
        "",
        "Production remains unchanged.",
    ])
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    df = _load_rows()
    slices = [
        "full",
        "train_pre_2026_05_26",
        "holdout_from_2026_05_26",
        "recent_from_2026_06_01",
        "live_filled_only",
    ]
    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "db_path": str(DB_DEFAULT),
        "slices": {name: _evaluate(_slice(df, name)) for name in slices},
    }
    today = dt.date.today()
    out_dir = OUT_DEFAULT / today.strftime("%Y-%m")
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{today.isoformat()}-city-day-distribution-quality.json"
    md_path = out_dir / f"{today.isoformat()}-city-day-distribution-quality.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _write_md(report, md_path)
    for slice_name, dists in report["slices"].items():
        print(f"\n== {slice_name} ==")
        for kind, st in dists.items():
            print(
                f"{kind:16s} n={st['n_city_days']:4d} logloss={st['log_loss']:.4f} "
                f"brier={st['brier_multiclass']:.4f} top1={st['top1_accuracy']*100:5.1f}% "
                f"winner_p={st['avg_winner_prob']:.4f}"
            )
    print(f"\nJSON written to {json_path}")
    print(f"Markdown written to {md_path}")


if __name__ == "__main__":
    main()
