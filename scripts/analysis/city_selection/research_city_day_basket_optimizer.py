"""
research_city_day_basket_optimizer.py

Research prototype for the core city-day basket algorithm.

This compares the current PR2b heuristic basket (edge-ranked top-N) with two
combo-enumeration optimizers:

  - combo_ev: choose the feasible leg combination with highest normalized EV.
  - combo_risk: choose highest EV + 0.5 * CVaR20, penalizing bad tail outcomes.
  - combo_guarded: same risk-aware objective, but filters extreme payout legs.
  - combo_policy_v1: fixed robust policy using blend EV plus market sanity.
  - combo_market_risk: candidate edge from blend, portfolio objective from
    market-normalized city-day distribution.
  - combo_market_tail: candidate edge from blend, but the objective requires
    positive market-normalized EV after removing the single best final-temp
    outcome.

It is intentionally offline-only. Data source is fact_signal_candidates, using
the existing T-22~24h representative decision snapshot. This is not production
code and does not touch N100/live behavior.
"""

from __future__ import annotations

import datetime as dt
import itertools
import json
import sqlite3
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd

from scripts.analysis.eval_city_day_basket import (  # noqa: E402
    DB_DEFAULT,
    OUT_DEFAULT,
    Leg,
    _attribution,
    _decide_basket,
    _decide_blended_single,
    _decide_market_only,
    _decide_raw_single,
    _summarize,
)
from weather_dashboard.basket import BasketConfig  # noqa: E402
from weather_dashboard.blend import blend_probability, load_default_config  # noqa: E402


@dataclass(frozen=True)
class ComboLeg:
    city: str
    target_date: str
    bracket: str
    side: str
    entry_price: float
    notional: float
    final_yes: float
    p_yes_raw: float
    p_yes_used: float
    edge: float

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.city, self.target_date, self.bracket, self.side)


@dataclass(frozen=True)
class ComboScore:
    expected_value: float
    cvar20: float
    worst_case: float
    score: float


def _load_rows(db_path: Path) -> pd.DataFrame:
    sql = """
        SELECT city, event_date, bracket, side,
               model_p_yes, market_yes_price,
               decision_entry_price, final_yes,
               live_filled, paper_ordered, eligible
        FROM fact_signal_candidates
        WHERE settlement_status = 'settled'
          AND decision_window_missing = 0
          AND model_p_yes IS NOT NULL
          AND market_yes_price IS NOT NULL
          AND decision_entry_price IS NOT NULL
          AND final_yes IS NOT NULL
    """
    with sqlite3.connect(str(db_path)) as conn:
        return pd.read_sql_query(sql, conn)


def _slice(df: pd.DataFrame, name: str) -> pd.DataFrame:
    if name == "full":
        return df.reset_index(drop=True)
    if name == "train_pre_2026_05_26":
        return df[df["event_date"] < "2026-05-26"].reset_index(drop=True)
    if name == "holdout_from_2026_05_26":
        return df[df["event_date"] >= "2026-05-26"].reset_index(drop=True)
    if name == "recent_from_2026_06_01":
        return df[df["event_date"] >= "2026-06-01"].reset_index(drop=True)
    if name == "live_filled_only":
        return df[df["live_filled"] == 1].reset_index(drop=True)
    raise ValueError(f"unknown slice: {name}")


def _leg_payoff(side: str, entry_price: float, notional: float, hit: bool) -> float:
    if side == "BUY_YES":
        return notional * (1.0 - entry_price) / entry_price if hit else -notional
    if side == "BUY_NO":
        return -notional if hit else notional * (1.0 - entry_price) / entry_price
    raise ValueError(f"unknown side: {side}")


def _size_for_edge(edge: float, small: float, normal: float, normal_threshold: float) -> float:
    return normal if edge >= normal_threshold else small


def _probability_distribution(bracket_to_p: dict[str, float]) -> dict[str, float]:
    """Normalize blended bracket probabilities into one city-day distribution."""
    clipped = {k: max(0.0, min(1.0, float(v))) for k, v in bracket_to_p.items()}
    total = sum(clipped.values())
    if total <= 0.0:
        n = len(clipped)
        return {k: 1.0 / n for k in clipped} if n else {}
    return {k: v / total for k, v in clipped.items()}


def _payoff_by_temp(legs: tuple[ComboLeg, ...], temps: list[str]) -> dict[str, float]:
    out = {t: 0.0 for t in temps}
    for leg in legs:
        for t in temps:
            out[t] += _leg_payoff(
                leg.side, leg.entry_price, leg.notional, hit=(leg.bracket == t)
            )
    return out


def _cvar20(payoff: dict[str, float], probs: dict[str, float]) -> float:
    """Probability-weighted average payoff in the worst 20% probability mass."""
    if not payoff:
        return 0.0
    remaining = 0.20
    acc = 0.0
    for temp, p in sorted(probs.items(), key=lambda kv: payoff.get(kv[0], 0.0)):
        if remaining <= 1e-12:
            break
        take = min(remaining, p)
        acc += payoff.get(temp, 0.0) * take
        remaining -= take
    denom = 0.20 - remaining
    return acc / denom if denom > 0 else min(payoff.values())


def _expected_value(payoff: dict[str, float], probs: dict[str, float]) -> float:
    return sum(probs[t] * payoff[t] for t in probs)


def _leave_best_out_ev(payoff: dict[str, float], probs: dict[str, float]) -> float:
    """Expected payoff after dropping the single most favorable temp outcome."""
    if len(payoff) <= 1:
        return min(payoff.values()) if payoff else 0.0
    best_temp = max(payoff, key=lambda t: payoff[t])
    remaining = {t: p for t, p in probs.items() if t != best_temp}
    denom = sum(remaining.values())
    if denom <= 0.0:
        return min(payoff.values())
    return sum((p / denom) * payoff[t] for t, p in remaining.items())


def _score_combo(
    legs: tuple[ComboLeg, ...],
    probs: dict[str, float],
    mode: str,
    cvar_weight: float,
) -> ComboScore:
    payoff = _payoff_by_temp(legs, list(probs))
    ev = _expected_value(payoff, probs)
    worst = min(payoff.values()) if payoff else 0.0
    cvar = _cvar20(payoff, probs)
    score = ev if mode == "ev" else ev + cvar_weight * cvar
    return ComboScore(expected_value=ev, cvar20=cvar, worst_case=worst, score=score)


def _score_market_tail(
    legs: tuple[ComboLeg, ...],
    probs_market: dict[str, float],
) -> ComboScore | None:
    """Tail objective: do not let one best outcome carry the whole basket."""
    payoff = _payoff_by_temp(legs, list(probs_market))
    ev_market = _expected_value(payoff, probs_market)
    cvar_market = _cvar20(payoff, probs_market)
    leave_best_out_ev = _leave_best_out_ev(payoff, probs_market)
    worst = min(payoff.values()) if payoff else 0.0

    if ev_market <= 0.0:
        return None
    if leave_best_out_ev <= 0.0:
        return None

    score = leave_best_out_ev + 0.25 * cvar_market
    return ComboScore(
        expected_value=ev_market,
        cvar20=cvar_market,
        worst_case=worst,
        score=score,
    )


def _score_policy_v1(
    legs: tuple[ComboLeg, ...],
    probs_blend: dict[str, float],
    probs_market: dict[str, float],
) -> ComboScore | None:
    """Fixed robust policy candidate.

    Principles:
      - optimize against blend-normalized distribution;
      - reject combinations that look too bad under market-normalized distribution;
      - require the worst 20% blend tail not to consume more than 85% of cost.

    These are ex-ante risk checks, not fitted parameters.
    """
    payoff = _payoff_by_temp(legs, list(probs_blend))
    cost = sum(leg.notional for leg in legs)
    ev_blend = _expected_value(payoff, probs_blend)
    ev_market = _expected_value(payoff, probs_market)
    cvar_blend = _cvar20(payoff, probs_blend)
    cvar_market = _cvar20(payoff, probs_market)
    worst = min(payoff.values()) if payoff else 0.0

    if ev_blend <= 0.0:
        return None
    if ev_market < -0.15 * cost:
        return None
    if cvar_blend < -0.85 * cost:
        return None
    if cvar_market < -1.00 * cost:
        return None

    score = ev_blend + 0.25 * ev_market + 0.25 * cvar_blend
    return ComboScore(expected_value=ev_blend, cvar20=cvar_blend, worst_case=worst, score=score)


def _to_eval_leg(leg: ComboLeg) -> Leg:
    return Leg(
        city=leg.city,
        target_date=leg.target_date,
        bracket=leg.bracket,
        side=leg.side,
        entry_price=leg.entry_price,
        notional=leg.notional,
        final_yes=leg.final_yes,
        p_yes_raw=leg.p_yes_raw,
        p_yes_used=leg.p_yes_used,
    )


def _candidate_legs_for_group(
    grp: pd.DataFrame,
    edge_threshold: float,
    small_notional: float,
    normal_notional: float,
    normal_threshold: float,
    max_win_multiple: float | None,
) -> tuple[list[ComboLeg], dict[str, dict[str, float]]]:
    blend_cfg = load_default_config()
    by_bracket: dict[str, dict] = {}
    city = str(grp["city"].iloc[0])
    target_date = str(grp["event_date"].iloc[0])

    for r in grp.itertuples():
        bracket = str(r.bracket)
        slot = by_bracket.setdefault(
            bracket,
            {
                "p_yes_raw": float(r.model_p_yes),
                "market_yes_price": float(r.market_yes_price),
                "yes_best_ask": None,
                "no_best_ask": None,
                "final_yes": float(r.final_yes),
            },
        )
        if r.side == "BUY_YES":
            slot["yes_best_ask"] = float(r.decision_entry_price)
        elif r.side == "BUY_NO":
            slot["no_best_ask"] = float(r.decision_entry_price)

    prob_inputs: dict[str, dict[str, float]] = {
        "raw": {},
        "market": {},
        "blend": {},
    }
    legs: list[ComboLeg] = []
    for bracket, slot in by_bracket.items():
        br = blend_probability(
            city=city,
            model_p_yes_raw=slot["p_yes_raw"],
            market_implied_p_yes=slot["market_yes_price"],
            config=blend_cfg,
        )
        p = br.p_yes_used
        prob_inputs["raw"][bracket] = float(slot["p_yes_raw"])
        prob_inputs["market"][bracket] = float(slot["market_yes_price"])
        prob_inputs["blend"][bracket] = p
        yes_ask = slot["yes_best_ask"]
        no_ask = slot["no_best_ask"]
        if yes_ask is not None:
            edge_yes = p - yes_ask
            if edge_yes > edge_threshold:
                notional = _size_for_edge(edge_yes, small_notional, normal_notional, normal_threshold)
                max_profit = notional * (1.0 - yes_ask) / yes_ask
                if max_win_multiple is not None and max_profit > max_win_multiple * notional:
                    pass
                else:
                    legs.append(
                        ComboLeg(
                            city=city,
                            target_date=target_date,
                            bracket=bracket,
                            side="BUY_YES",
                            entry_price=yes_ask,
                            notional=notional,
                            final_yes=float(slot["final_yes"]),
                            p_yes_raw=float(slot["p_yes_raw"]),
                            p_yes_used=p,
                            edge=edge_yes,
                        )
                    )
        if no_ask is not None:
            edge_no = (1.0 - p) - no_ask
            if edge_no > edge_threshold:
                notional = _size_for_edge(edge_no, small_notional, normal_notional, normal_threshold)
                max_profit = notional * (1.0 - no_ask) / no_ask
                if max_win_multiple is not None and max_profit > max_win_multiple * notional:
                    pass
                else:
                    legs.append(
                        ComboLeg(
                            city=city,
                            target_date=target_date,
                            bracket=bracket,
                            side="BUY_NO",
                            entry_price=no_ask,
                            notional=notional,
                            final_yes=float(slot["final_yes"]),
                            p_yes_raw=float(slot["p_yes_raw"]),
                            p_yes_used=p,
                            edge=edge_no,
                        )
                    )
    return legs, prob_inputs


def _valid_combo(legs: tuple[ComboLeg, ...]) -> bool:
    # Do not allow YES and NO on the same bracket in one basket.
    return len({leg.bracket for leg in legs}) == len(legs)


def _decide_combo_optimizer(
    df: pd.DataFrame,
    mode: str,
    edge_threshold: float = 0.03,
    small_notional: float = 3.0,
    normal_notional: float = 8.0,
    normal_threshold: float = 0.06,
    max_legs: int = 4,
    city_day_cap: float = 15.0,
    candidate_pool_size: int = 12,
    cvar_weight: float = 0.5,
    max_win_multiple: float | None = None,
) -> list[Leg]:
    out: list[Leg] = []
    for (_city, _event_date), grp in df.groupby(["city", "event_date"]):
        candidates, prob_inputs = _candidate_legs_for_group(
            grp,
            edge_threshold=edge_threshold,
            small_notional=small_notional,
            normal_notional=normal_notional,
            normal_threshold=normal_threshold,
            max_win_multiple=max_win_multiple,
        )
        if not candidates:
            continue
        probs = _probability_distribution(
            prob_inputs["market"] if mode in ("market_risk", "market_tail") else prob_inputs["blend"]
        )
        probs_market = _probability_distribution(prob_inputs["market"])
        if not probs or not probs_market:
            continue
        pool = sorted(candidates, key=lambda leg: leg.edge, reverse=True)[:candidate_pool_size]
        best_combo: tuple[ComboLeg, ...] | None = None
        best_score: ComboScore | None = None
        max_k = min(max_legs, len(pool))
        for k in range(1, max_k + 1):
            for combo in itertools.combinations(pool, k):
                if not _valid_combo(combo):
                    continue
                if mode == "policy_v1":
                    score = _score_policy_v1(combo, probs, probs_market)
                    if score is None:
                        continue
                elif mode == "market_tail":
                    score = _score_market_tail(combo, probs_market)
                    if score is None:
                        continue
                else:
                    score = _score_combo(combo, probs, mode=mode, cvar_weight=cvar_weight)
                if score.expected_value <= 0.0:
                    continue
                if abs(score.worst_case) > city_day_cap:
                    continue
                if best_score is None or score.score > best_score.score:
                    best_combo = combo
                    best_score = score
        if best_combo:
            out.extend(_to_eval_leg(leg) for leg in best_combo)
    return out


def _side_roi(summary: dict, side: str) -> float:
    st = summary.get("by_side", {}).get(side)
    if not st:
        return 0.0
    cost = st.get("total_cost_usd") or 0.0
    return (st.get("total_pnl_usd") or 0.0) / cost if cost else 0.0


def _evaluate_slice(df: pd.DataFrame) -> dict:
    blend_cfg = load_default_config()
    pr2b_cfg = BasketConfig(
        single_leg_notional_small=3.0,
        single_leg_notional_normal=8.0,
        city_day_notional_cap=15.0,
        max_no_legs_per_city_day=4,
        edge_small_threshold=0.03,
        edge_normal_threshold=0.06,
        prefer_no_over_yes=False,
    )

    raw = _decide_raw_single(df, edge_threshold=0.03, notional=5.0)
    market = _decide_market_only(df, edge_threshold=0.03, notional=5.0)
    blended = _decide_blended_single(df, blend_cfg, edge_threshold=0.03, notional=5.0)
    heuristic = _decide_basket(df, blend_cfg, pr2b_cfg)
    combo_ev = _decide_combo_optimizer(df, mode="ev")
    combo_risk = _decide_combo_optimizer(df, mode="risk")
    combo_guarded = _decide_combo_optimizer(df, mode="risk", max_win_multiple=12.0)
    combo_policy_v1 = _decide_combo_optimizer(df, mode="policy_v1")
    combo_market_risk = _decide_combo_optimizer(df, mode="market_risk")
    combo_market_tail = _decide_combo_optimizer(df, mode="market_tail")

    rules = {
        "raw_single": asdict(_summarize("raw_single", raw)),
        "market_only": asdict(_summarize("market_only", market)),
        "blended_single": asdict(_summarize("blended_single", blended)),
        "heuristic_pr2b": asdict(_summarize("heuristic_pr2b", heuristic)),
        "combo_ev": asdict(_summarize("combo_ev", combo_ev)),
        "combo_risk": asdict(_summarize("combo_risk", combo_risk)),
        "combo_guarded": asdict(_summarize("combo_guarded", combo_guarded)),
        "combo_policy_v1": asdict(_summarize("combo_policy_v1", combo_policy_v1)),
        "combo_market_risk": asdict(_summarize("combo_market_risk", combo_market_risk)),
        "combo_market_tail": asdict(_summarize("combo_market_tail", combo_market_tail)),
    }
    attrs = {
        name: _attribution(legs, blended)
        for name, legs in (
            ("heuristic_pr2b", heuristic),
            ("combo_ev", combo_ev),
            ("combo_risk", combo_risk),
            ("combo_guarded", combo_guarded),
            ("combo_policy_v1", combo_policy_v1),
            ("combo_market_risk", combo_market_risk),
            ("combo_market_tail", combo_market_tail),
        )
    }
    gates = {}
    blended_roi = rules["blended_single"]["roi"]
    for name in ("heuristic_pr2b", "combo_ev", "combo_risk", "combo_guarded", "combo_policy_v1", "combo_market_risk", "combo_market_tail"):
        s = rules[name]
        a = attrs[name]
        checks = {
            "missed_profit_lte_avoided_loss": a["missed_profit_usd"] <= a["avoided_loss_usd"],
            "roi_gte_blended_80pct": s["roi"] >= blended_roi * 0.8,
            "roi_excl_top5_nonnegative": s["roi_excl_top5"] >= 0.0,
            "buy_no_roi_nonnegative": _side_roi(s, "BUY_NO") >= 0.0,
        }
        gates[name] = {**checks, "gate_count": sum(checks.values())}
    return {
        "n_rows": int(len(df)),
        "date_range": [
            str(df["event_date"].min()) if len(df) else None,
            str(df["event_date"].max()) if len(df) else None,
        ],
        "rules": rules,
        "attr_vs_blended": attrs,
        "gates": gates,
    }


def _write_markdown(report: dict, out_path: Path) -> None:
    lines = [
        "# City-Day Basket Optimizer Research — 2026-06-06",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> DB: `{report['db_path']}`",
        "> Scope: offline research only; no N100/live behavior changed.",
        "",
        "## Research Question",
        "",
        "Can a normalized city-day distribution plus combo enumeration beat the current PR2b edge-ranked top-N heuristic more robustly?",
        "",
        "Algorithms compared:",
        "",
        "- `raw_single`: original raw model probability single-market rule.",
        "- `blended_single`: market-anchored probability, still single-market.",
        "- `heuristic_pr2b`: current PR2b basket, edge-ranked top-N.",
        "- `combo_ev`: enumerate feasible combinations and maximize normalized EV.",
        "- `combo_risk`: enumerate feasible combinations and maximize EV + 0.5 * CVaR20.",
        "- `combo_guarded`: same as `combo_risk`, but drops legs whose max win is > 12x notional.",
        "- `combo_policy_v1`: fixed robust policy using blend EV, market-normalized sanity checks, and CVaR floors.",
        "- `combo_market_risk`: candidates from blend edge, but objective uses market-normalized distribution.",
        "- `combo_market_tail`: market-normalized objective that requires positive EV even after removing the best single final-temp outcome.",
        "",
        "Research constraints:",
        "",
        "- Uses `fact_signal_candidates` T-22~24h representative snapshot, not full N100 snapshot replay.",
        "- Normalizes per-bracket `p_yes_used` into a city-day distribution for objective scoring.",
        "- Candidate pool limited to top 12 executable-edge legs per city-day; max selected legs = 4.",
        "- `combo_guarded` is a first tail-control test, not a tuned production rule.",
        "- BUY_NO book still has historical proxy limitations where source data lacks independent NO ask.",
        "",
        "## Slice Summary",
        "",
        "| slice | rule | n | cost | PnL | ROI | top5 ROI | missed | avoided | gates |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for slice_name, data in report["slices"].items():
        for rule in ("raw_single", "blended_single", "heuristic_pr2b", "combo_ev", "combo_risk", "combo_guarded", "combo_policy_v1", "combo_market_risk", "combo_market_tail"):
            s = data["rules"][rule]
            attr = data["attr_vs_blended"].get(rule)
            gates = data["gates"].get(rule)
            missed = attr["missed_profit_usd"] if attr else 0.0
            avoided = attr["avoided_loss_usd"] if attr else 0.0
            gate_count = gates["gate_count"] if gates else 0
            lines.append(
                f"| {slice_name} | {rule} | {s['n_legs']} | ${s['total_cost_usd']:.0f} | "
                f"${s['total_pnl_usd']:+.0f} | {s['roi']*100:+.2f}% | "
                f"{s['roi_excl_top5']*100:+.2f}% | ${missed:.0f} | ${avoided:.0f} | "
                f"{gate_count}/4 |"
            )
    full = report["slices"]["full"]["rules"]
    holdout = report["slices"]["holdout_from_2026_05_26"]["rules"]
    recent = report["slices"]["recent_from_2026_06_01"]["rules"]
    live_subset = report["slices"]["live_filled_only"]["rules"]
    lines.extend([
        "",
        "## Current Findings",
        "",
        f"- Full sample: `combo_risk` has the highest ROI "
        f"({full['combo_risk']['roi']*100:+.2f}%) but still fails the missed-profit gate.",
        f"- Holdout: `combo_risk` has positive headline ROI "
        f"({holdout['combo_risk']['roi']*100:+.2f}%) but top-5 removed ROI is "
        f"{holdout['combo_risk']['roi_excl_top5']*100:+.2f}%, worse than the heuristic.",
        f"- Recent slice: `combo_ev` underperforms the heuristic and `combo_risk` remains tail-dependent "
        f"(top-5 removed ROI {recent['combo_risk']['roi_excl_top5']*100:+.2f}%).",
        f"- Live-filled opportunity subset: `combo_risk` is the only basket variant with 3/4 gates, "
        f"but top-5 removed ROI is still {live_subset['combo_risk']['roi_excl_top5']*100:+.2f}%.",
        "- `combo_guarded` shows that a blunt high-payout filter is too destructive: it reduces full-sample PnL and does not fix holdout tail risk.",
        f"- `combo_policy_v1` tests a fixed robust objective. Full-sample ROI is "
        f"{full['combo_policy_v1']['roi']*100:+.2f}%, holdout ROI is "
        f"{holdout['combo_policy_v1']['roi']*100:+.2f}%, recent ROI is "
        f"{recent['combo_policy_v1']['roi']*100:+.2f}%.",
        f"- `combo_market_risk` directly tests the distribution-quality finding. Full-sample ROI is "
        f"{full['combo_market_risk']['roi']*100:+.2f}%, holdout ROI is "
        f"{holdout['combo_market_risk']['roi']*100:+.2f}%, recent ROI is "
        f"{recent['combo_market_risk']['roi']*100:+.2f}%.",
        f"- `combo_market_tail` is the strict tail-objective test. Full-sample ROI is "
        f"{full['combo_market_tail']['roi']*100:+.2f}%, holdout ROI is "
        f"{holdout['combo_market_tail']['roi']*100:+.2f}%, recent ROI is "
        f"{recent['combo_market_tail']['roi']*100:+.2f}%.",
        "",
        "## Quant Read",
        "",
        "- Combo enumeration is directionally useful for finding higher headline ROI, but it currently concentrates risk into fewer, higher-tail legs.",
        "- The core problem is not just top-N vs enumeration. The objective needs a better calibrated city-day temperature distribution and explicit tail controls.",
        "- Simple lottery-leg filtering is not enough. The next research step should improve the probability distribution and evaluate combinations with walk-forward objectives.",
        "- A viable candidate should improve holdout and recent tail-removed ROI, not only headline ROI.",
        "",
        "Production remains unchanged.",
    ])
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    df = _load_rows(DB_DEFAULT)
    slice_names = [
        "full",
        "train_pre_2026_05_26",
        "holdout_from_2026_05_26",
        "recent_from_2026_06_01",
        "live_filled_only",
    ]
    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "db_path": str(DB_DEFAULT),
        "slices": {name: _evaluate_slice(_slice(df, name)) for name in slice_names},
        "optimizer_config": {
            "candidate_pool_size": 12,
            "max_legs": 4,
            "city_day_cap": 15.0,
            "small_notional": 3.0,
            "normal_notional": 8.0,
            "edge_threshold": 0.03,
            "normal_threshold": 0.06,
            "cvar_weight": 0.5,
            "guarded_max_win_multiple": 12.0,
            "policy_v1_market_ev_floor": "-0.15 * cost",
            "policy_v1_blend_cvar_floor": "-0.85 * cost",
            "policy_v1_market_cvar_floor": "-1.00 * cost",
            "market_risk_distribution": "market_norm",
            "market_tail_rule": "market_norm EV remains positive after removing best final-temp outcome",
        },
    }

    today = dt.date.today()
    out_dir = OUT_DEFAULT / today.strftime("%Y-%m")
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{today.isoformat()}-city-day-basket-optimizer-research.json"
    md_path = out_dir / f"{today.isoformat()}-city-day-basket-optimizer-research.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _write_markdown(report, md_path)

    for slice_name, data in report["slices"].items():
        print(f"\n== {slice_name} ==")
        for rule in ("raw_single", "blended_single", "heuristic_pr2b", "combo_ev", "combo_risk", "combo_guarded", "combo_policy_v1", "combo_market_risk", "combo_market_tail"):
            s = data["rules"][rule]
            gates = data["gates"].get(rule)
            gate_count = gates["gate_count"] if gates else 0
            print(
                f"{rule:15s} n={s['n_legs']:4d} pnl={s['total_pnl_usd']:+8.2f} "
                f"roi={s['roi']*100:+7.2f}% top5={s['roi_excl_top5']*100:+7.2f}% "
                f"gates={gate_count}/4"
            )
    print(f"\nJSON written to {json_path}")
    print(f"Markdown written to {md_path}")


if __name__ == "__main__":
    main()
