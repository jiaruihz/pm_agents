"""
eval_city_day_basket.py

Offline replay comparing three decision rules on settled candidates:
  1. raw_single:     per-bracket independent edge using model_p_yes (legacy).
  2. blended_single: per-bracket independent edge using p_yes_used (blender).
  3. basket:         city-day basket builder over blended p_yes_used.

For each rule we compute fills, total PnL, ROI, win rate, worst-case loss
distribution, and missed-profit / avoided-loss attribution relative to the
basket rule.

Data source: runtime/weather.db / fact_signal_candidates restricted to
settled rows with complete decision-window data.

Caveats (also written into the eval doc):
  - decision_entry_price for BUY_NO rows in this table is "1 - yes_bid proxy",
    not an independent NO book.
  - candidates table holds one best-decision snapshot per (city, target_date,
    bracket, side) — we cannot evaluate intra-day forecast jump / side flip
    here; those checks need raw snapshot replay (PR3 shadow).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd

from weather_dashboard.basket import (
    BasketConfig,
    BasketInput,
    BracketCandidate,
    build_city_day_basket,
)
from weather_dashboard.blend import BlendConfig, blend_probability, load_default_config
from src.strategies.runtime.production import load_production_spec

ROOT = Path(__file__).resolve().parents[3]
DB_DEFAULT = ROOT / "runtime" / "weather.db"
OUT_DEFAULT = (
    load_production_spec().research_artifact_root
    / "active"
    / "legacy_city_day_basket"
)


# ---------------------------------------------------------------------------
# Pnl primitive
# ---------------------------------------------------------------------------

def _leg_pnl(side: str, entry_price: float, notional: float, final_yes: float) -> float:
    """PnL of one $notional leg given side, entry price, and final_yes (0/1)."""
    if side == "BUY_YES":
        # win when final_yes == 1
        if final_yes >= 0.5:
            return notional * (1.0 - entry_price) / entry_price
        return -notional
    # BUY_NO
    if final_yes >= 0.5:
        return -notional
    return notional * (1.0 - entry_price) / entry_price


# ---------------------------------------------------------------------------
# Per-rule "decisions" — a list of legs (city, target_date, bracket, side,
# entry_price, notional, final_yes).
# ---------------------------------------------------------------------------

@dataclass
class Leg:
    city: str
    target_date: str
    bracket: str
    side: str  # BUY_YES | BUY_NO
    entry_price: float
    notional: float
    final_yes: float
    p_yes_raw: float
    p_yes_used: float

    @property
    def pnl(self) -> float:
        return _leg_pnl(self.side, self.entry_price, self.notional, self.final_yes)

    @property
    def is_win(self) -> bool:
        return self.pnl > 0

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.city, self.target_date, self.bracket, self.side)


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

def _decide_raw_single(df: pd.DataFrame, edge_threshold: float,
                       notional: float) -> list[Leg]:
    """Pick rows where signed edge (side-aware) > threshold using model_p_yes."""
    legs: list[Leg] = []
    for r in df.itertuples():
        p = r.model_p_yes
        entry = r.decision_entry_price
        if r.side == "BUY_YES":
            edge = p - entry
        else:
            edge = (1.0 - p) - entry
        if edge > edge_threshold:
            legs.append(Leg(
                city=r.city, target_date=r.event_date, bracket=str(r.bracket),
                side=r.side, entry_price=float(entry), notional=notional,
                final_yes=float(r.final_yes), p_yes_raw=float(p), p_yes_used=float(p),
            ))
    return legs


def _decide_market_only(df: pd.DataFrame, edge_threshold: float,
                        notional: float) -> list[Leg]:
    """Baseline that ignores the raw model entirely: p_yes_used = market_yes_price.

    Used to test whether blended_single's PnL comes from real raw-model alpha
    or just from the entry-price-vs-market-mid spread artifact. If market_only
    matches blended_single, the blender adds no alpha and "edge" is mostly
    half-spread.
    """
    legs: list[Leg] = []
    for r in df.itertuples():
        p = float(r.market_yes_price)
        entry = r.decision_entry_price
        if r.side == "BUY_YES":
            edge = p - entry
        else:
            edge = (1.0 - p) - entry
        if edge > edge_threshold:
            legs.append(Leg(
                city=r.city, target_date=r.event_date, bracket=str(r.bracket),
                side=r.side, entry_price=float(entry), notional=notional,
                final_yes=float(r.final_yes),
                p_yes_raw=float(r.model_p_yes), p_yes_used=p,
            ))
    return legs


def _decide_blended_single(df: pd.DataFrame, blend_cfg: BlendConfig,
                           edge_threshold: float, notional: float) -> list[Leg]:
    """Same as raw_single but with p_yes_used from the blender."""
    legs: list[Leg] = []
    for r in df.itertuples():
        result = blend_probability(
            city=r.city,
            model_p_yes_raw=float(r.model_p_yes),
            market_implied_p_yes=float(r.market_yes_price),
            config=blend_cfg,
        )
        p = result.p_yes_used
        entry = r.decision_entry_price
        if r.side == "BUY_YES":
            edge = p - entry
        else:
            edge = (1.0 - p) - entry
        if edge > edge_threshold:
            legs.append(Leg(
                city=r.city, target_date=r.event_date, bracket=str(r.bracket),
                side=r.side, entry_price=float(entry), notional=notional,
                final_yes=float(r.final_yes),
                p_yes_raw=float(r.model_p_yes), p_yes_used=float(p),
            ))
    return legs


def _decide_basket(df: pd.DataFrame, blend_cfg: BlendConfig,
                   basket_cfg: BasketConfig) -> list[Leg]:
    """Group by (city, target_date), build BracketCandidates, call basket builder."""
    legs: list[Leg] = []
    grouped = df.groupby(["city", "event_date"])
    for (city, target_date), grp in grouped:
        # Per-bracket: choose one side per bracket for the candidate input. We
        # send a bracket once and let the basket builder pick yes/no via
        # yes_best_ask / no_best_ask.
        by_bracket: dict[str, dict] = {}
        for r in grp.itertuples():
            bracket = str(r.bracket)
            slot = by_bracket.setdefault(bracket, {
                "p_yes_raw": float(r.model_p_yes),
                "market_yes_price": float(r.market_yes_price),
                "yes_best_ask": None,
                "no_best_ask": None,
                "final_yes": float(r.final_yes),
            })
            entry = float(r.decision_entry_price)
            if r.side == "BUY_YES":
                slot["yes_best_ask"] = entry
            else:
                slot["no_best_ask"] = entry

        # Build BracketCandidates with blended p_yes_used.
        cands: list[BracketCandidate] = []
        final_yes_by_bracket: dict[str, float] = {}
        for bracket, slot in by_bracket.items():
            br = blend_probability(
                city=city, model_p_yes_raw=slot["p_yes_raw"],
                market_implied_p_yes=slot["market_yes_price"], config=blend_cfg,
            )
            cands.append(BracketCandidate(
                bracket_id=f"{city}_{target_date}_{bracket}",
                bracket_label=bracket,
                final_temp_key=bracket,
                p_yes_used=br.p_yes_used,
                yes_best_ask=slot["yes_best_ask"],
                no_best_ask=slot["no_best_ask"],
            ))
            final_yes_by_bracket[bracket] = slot["final_yes"]

        # Provide full bracket universe as possible final temps so payoff EV
        # has somewhere to put residual probability.
        plan = build_city_day_basket(BasketInput(
            city=str(city), target_date=str(target_date),
            snapshot_ts=str(target_date) + "T_eval",
            candidates=cands,
            config=basket_cfg,
            existing_city_day_exposure=0.0,
            possible_final_temps=list(final_yes_by_bracket.keys()),
        ))
        if plan.decision not in ("TRADE", "REDUCE"):
            continue
        for sl in plan.selected_legs:
            final_yes = final_yes_by_bracket.get(sl.bracket_label, 0.0)
            legs.append(Leg(
                city=str(city), target_date=str(target_date), bracket=sl.bracket_label,
                side=sl.side, entry_price=sl.entry_price, notional=sl.notional_usd,
                final_yes=float(final_yes),
                p_yes_raw=float(by_bracket[sl.bracket_label]["p_yes_raw"]),
                p_yes_used=next(c.p_yes_used for c in cands
                                if c.bracket_label == sl.bracket_label),
            ))
    return legs


# ---------------------------------------------------------------------------
# Roll-up
# ---------------------------------------------------------------------------

@dataclass
class RuleSummary:
    rule: str
    n_legs: int
    n_wins: int
    win_rate: float
    total_cost_usd: float
    total_pnl_usd: float
    roi: float
    avg_pnl_usd: float
    worst_single_leg_pnl: float
    best_single_leg_pnl: float
    roi_excl_top1: float
    roi_excl_top5: float
    by_side: dict[str, dict[str, float]]
    by_city: dict[str, dict[str, float]] = field(default_factory=dict)


def _summarize(rule: str, legs: list[Leg]) -> RuleSummary:
    if not legs:
        return RuleSummary(
            rule=rule, n_legs=0, n_wins=0, win_rate=0.0,
            total_cost_usd=0.0, total_pnl_usd=0.0, roi=0.0, avg_pnl_usd=0.0,
            worst_single_leg_pnl=0.0, best_single_leg_pnl=0.0,
            roi_excl_top1=0.0, roi_excl_top5=0.0, by_side={}, by_city={},
        )
    pnls = sorted([l.pnl for l in legs], reverse=True)
    costs = sum(l.notional for l in legs)
    total_pnl = sum(pnls)

    def _roi(_cost: float, _pnl: float) -> float:
        return _pnl / _cost if _cost > 0 else 0.0

    excl1 = sum(pnls[1:]) if len(pnls) > 1 else 0.0
    excl5 = sum(pnls[5:]) if len(pnls) > 5 else 0.0
    # Subtract the *notional* of the removed legs from the cost denominator.
    # Approximation: removed legs have similar avg notional.
    avg_notional = costs / len(legs)
    cost_excl1 = costs - avg_notional
    cost_excl5 = costs - 5 * avg_notional

    by_side: dict[str, dict[str, float]] = {}
    for side in ("BUY_YES", "BUY_NO"):
        side_legs = [l for l in legs if l.side == side]
        if not side_legs:
            continue
        by_side[side] = {
            "n_legs": len(side_legs),
            "n_wins": sum(1 for l in side_legs if l.is_win),
            "win_rate": sum(1 for l in side_legs if l.is_win) / len(side_legs),
            "total_cost_usd": sum(l.notional for l in side_legs),
            "total_pnl_usd": sum(l.pnl for l in side_legs),
        }

    by_city: dict[str, dict[str, float]] = {}
    cities = sorted(set(l.city for l in legs))
    for city in cities:
        cl = [l for l in legs if l.city == city]
        by_city[city] = {
            "n_legs": len(cl),
            "total_cost_usd": sum(l.notional for l in cl),
            "total_pnl_usd": sum(l.pnl for l in cl),
            "win_rate": sum(1 for l in cl if l.is_win) / len(cl),
        }

    return RuleSummary(
        rule=rule, n_legs=len(legs),
        n_wins=sum(1 for l in legs if l.is_win),
        win_rate=sum(1 for l in legs if l.is_win) / len(legs),
        total_cost_usd=costs, total_pnl_usd=total_pnl,
        roi=_roi(costs, total_pnl),
        avg_pnl_usd=total_pnl / len(legs),
        worst_single_leg_pnl=min(pnls), best_single_leg_pnl=max(pnls),
        roi_excl_top1=_roi(cost_excl1, excl1),
        roi_excl_top5=_roi(cost_excl5, excl5),
        by_side=by_side, by_city=by_city,
    )


def _attribution(reference: list[Leg], baseline: list[Leg]) -> dict[str, float]:
    """Compare baseline (e.g. raw_single) to reference (basket).

    missed_profit: legs baseline picked & WON that reference did NOT pick.
    avoided_loss: legs baseline picked & LOST that reference did NOT pick.
    shared_profit: legs both picked & won.
    shared_loss:   legs both picked & lost.
    """
    ref_keys = {l.key for l in reference}
    missed_profit = 0.0
    avoided_loss = 0.0
    shared_profit = 0.0
    shared_loss = 0.0
    for l in baseline:
        if l.key in ref_keys:
            if l.is_win:
                shared_profit += l.pnl
            else:
                shared_loss += l.pnl
        else:
            if l.is_win:
                missed_profit += l.pnl
            else:
                avoided_loss += -l.pnl  # report as positive $ avoided
    return {
        "missed_profit_usd": missed_profit,
        "avoided_loss_usd": avoided_loss,
        "shared_profit_usd": shared_profit,
        "shared_loss_usd": shared_loss,
        "net_basket_vs_baseline_usd": (
            avoided_loss - missed_profit
        ),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _load_settled(db_path: Path, min_date: str | None) -> pd.DataFrame:
    sql = """
        SELECT city, event_date, bracket, side,
               model_p_yes, market_yes_price,
               decision_entry_price, final_yes
        FROM fact_signal_candidates
        WHERE settlement_status = 'settled'
          AND decision_window_missing = 0
          AND model_p_yes IS NOT NULL
          AND market_yes_price IS NOT NULL
          AND decision_entry_price IS NOT NULL
          AND final_yes IS NOT NULL
    """
    if min_date:
        sql += f" AND event_date >= '{min_date}'"
    with sqlite3.connect(str(db_path)) as c:
        df = pd.read_sql_query(sql, c)
    return df


def _print_summary(name: str, s: RuleSummary) -> None:
    print(f"\n--- {name} ---")
    print(f"  n_legs={s.n_legs}  n_wins={s.n_wins}  win_rate={s.win_rate:.3f}")
    print(f"  total_cost=${s.total_cost_usd:.2f}  total_pnl=${s.total_pnl_usd:+.2f}  "
          f"ROI={s.roi*100:+.2f}%")
    print(f"  avg_pnl=${s.avg_pnl_usd:+.3f}  "
          f"best=${s.best_single_leg_pnl:+.2f}  worst=${s.worst_single_leg_pnl:+.2f}")
    print(f"  ROI excl top-1 leg: {s.roi_excl_top1*100:+.2f}%")
    print(f"  ROI excl top-5 legs: {s.roi_excl_top5*100:+.2f}%")
    for side, st in s.by_side.items():
        print(f"  {side}: n={st['n_legs']}  win_rate={st['win_rate']:.3f}  "
              f"pnl=${st['total_pnl_usd']:+.2f}  cost=${st['total_cost_usd']:.2f}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Offline replay of city-day basket rule.")
    ap.add_argument("--db", type=Path, default=DB_DEFAULT)
    ap.add_argument("--min-date", type=str, default=None,
                    help="Only include event_date >= this YYYY-MM-DD.")
    ap.add_argument("--edge-threshold", type=float, default=0.03,
                    help="Single-leg rule edge threshold (default 0.03).")
    ap.add_argument("--leg-notional", type=float, default=5.0,
                    help="Single-leg notional (default $5).")
    ap.add_argument("--basket-small-notional", type=float, default=3.0,
                    help="Basket small leg notional (default $3).")
    ap.add_argument("--basket-normal-notional", type=float, default=5.0,
                    help="Basket normal leg notional (default $5).")
    ap.add_argument("--basket-city-day-cap", type=float, default=15.0,
                    help="Basket city-day notional cap (default $15).")
    ap.add_argument("--basket-max-no-legs", type=int, default=3,
                    help="Max basket legs per city-day (default 3).")
    ap.add_argument("--basket-edge-small-threshold", type=float, default=0.03,
                    help="Basket small edge threshold (default 0.03).")
    ap.add_argument("--basket-edge-normal-threshold", type=float, default=0.06,
                    help="Basket normal edge threshold (default 0.06).")
    ap.add_argument("--basket-prefer-no-over-yes", dest="basket_prefer_no_over_yes",
                    action="store_true", default=True,
                    help="Prefer BUY_NO legs in basket selection (default).")
    ap.add_argument("--basket-no-prefer-no-over-yes", dest="basket_prefer_no_over_yes",
                    action="store_false",
                    help="Rank basket BUY_YES and BUY_NO by edge together.")
    ap.add_argument("--out-dir", type=Path, default=OUT_DEFAULT)
    args = ap.parse_args()

    df = _load_settled(args.db, args.min_date)
    print(f"Loaded {len(df)} settled candidate rows "
          f"(date range {df['event_date'].min()} -> {df['event_date'].max()})")

    blend_cfg = load_default_config()
    basket_cfg = BasketConfig(
        single_leg_notional_small=args.basket_small_notional,
        single_leg_notional_normal=args.basket_normal_notional,
        city_day_notional_cap=args.basket_city_day_cap,
        max_no_legs_per_city_day=args.basket_max_no_legs,
        edge_small_threshold=args.basket_edge_small_threshold,
        edge_normal_threshold=args.basket_edge_normal_threshold,
        prefer_no_over_yes=args.basket_prefer_no_over_yes,
    )

    raw_legs = _decide_raw_single(df, args.edge_threshold, args.leg_notional)
    market_legs = _decide_market_only(df, args.edge_threshold, args.leg_notional)
    blended_legs = _decide_blended_single(df, blend_cfg, args.edge_threshold,
                                          args.leg_notional)
    basket_legs = _decide_basket(df, blend_cfg, basket_cfg)

    raw_s = _summarize("raw_single", raw_legs)
    market_s = _summarize("market_only", market_legs)
    blended_s = _summarize("blended_single", blended_legs)
    basket_s = _summarize("basket", basket_legs)

    for name, s in [("raw_single", raw_s),
                    ("market_only", market_s),
                    ("blended_single", blended_s),
                    ("basket", basket_s)]:
        _print_summary(name, s)

    attr_basket_vs_raw = _attribution(basket_legs, raw_legs)
    attr_basket_vs_blended = _attribution(basket_legs, blended_legs)

    print("\n--- attribution: basket vs raw_single ---")
    for k, v in attr_basket_vs_raw.items():
        print(f"  {k}: ${v:+.2f}")
    print("\n--- attribution: basket vs blended_single ---")
    for k, v in attr_basket_vs_blended.items():
        print(f"  {k}: ${v:+.2f}")

    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "n_input_rows": int(len(df)),
        "date_range": [str(df["event_date"].min()), str(df["event_date"].max())],
        "edge_threshold": args.edge_threshold,
        "leg_notional": args.leg_notional,
        "rules": {
            "raw_single": asdict(raw_s),
            "market_only": asdict(market_s),
            "blended_single": asdict(blended_s),
            "basket": asdict(basket_s),
        },
        "attribution_basket_vs_raw": attr_basket_vs_raw,
        "attribution_basket_vs_blended": attr_basket_vs_blended,
        "blend_config": {
            "global_alpha": blend_cfg.global_alpha,
            "global_beta": blend_cfg.global_beta,
            "blacklist_alpha": blend_cfg.blacklist_alpha,
            "blacklist": sorted(blend_cfg.raw_model_blacklist),
        },
        "basket_config": {
            "single_leg_notional_small": basket_cfg.single_leg_notional_small,
            "single_leg_notional_normal": basket_cfg.single_leg_notional_normal,
            "city_day_notional_cap": basket_cfg.city_day_notional_cap,
            "daily_notional_cap": basket_cfg.daily_notional_cap,
            "max_no_legs_per_city_day": basket_cfg.max_no_legs_per_city_day,
            "edge_small_threshold": basket_cfg.edge_small_threshold,
            "edge_normal_threshold": basket_cfg.edge_normal_threshold,
            "prefer_no_over_yes": basket_cfg.prefer_no_over_yes,
        },
    }

    today = dt.date.today()
    out_dir = args.out_dir / today.strftime("%Y-%m")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{today.isoformat()}-city-day-basket-eval.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nReport written to {out_path}")


if __name__ == "__main__":
    main()
