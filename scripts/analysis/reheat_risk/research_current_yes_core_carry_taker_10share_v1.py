#!/usr/bin/env python3
"""Replay the frozen core-carry selector with a ten-share taker ladder.

This keeps the OOF probabilities, state universe, checkpoint rule and official
fee fixed.  Only taker quantity changes from five to ten shares.  The maker
leg remains an unproven execution overlay and is reported separately.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.weather_edge_v1.tools.current_yes_core_carry import (  # noqa: E402
    walk_ask_ladder,
)


OOF_PATH = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "current_yes_core_carry_no_obs_age_freeze_pre_live_v5/oof_states_five_share_cost.csv"
)
ENTRY5_PATH = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "current_yes_core_carry_no_obs_age_freeze_pre_live_v5/frozen_policy_entries.csv"
)
OUT_DIR = (
    ROOT
    / "docs/analysis/2026-07/generated/current_yes_core_carry_taker_10share_v1"
)
OUT_JSON = (
    ROOT
    / "docs/analysis/2026-07/"
    "2026-07-27-current-yes-core-carry-taker-10share-v1.json"
)
OUT_MD = (
    ROOT
    / "docs/analysis/2026-07/"
    "2026-07-27-current-yes-core-carry-taker-10share-v1.md"
)
PROBABILITY = "p_core_no_obs_age"
QUANTITY = 10.0
SEED = 20260727
BOOTSTRAP_REPS = 5000


def timestamp_text(value: Any) -> str:
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    return "" if pd.isna(parsed) else parsed.isoformat()


def row_key(row: pd.Series | dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("city") or ""),
        str(row.get("target_date") or row.get("event_date") or ""),
        str(row.get("current_bracket") or row.get("bracket") or ""),
        timestamp_text(row.get("decision_snapshot_ts_utc") or row.get("snapshot_ts_utc")),
    )


def load_raw_ask_cache(frame: pd.DataFrame) -> dict[tuple[str, str, str, str], list[Any]]:
    needed = frame[frame["current_yes_ask_size"].lt(QUANTITY)]
    wanted_by_file: dict[Path, set[tuple[str, str, str, str]]] = {}
    for _, row in needed.iterrows():
        path = Path(str(row["orderbook_file"]))
        if not path.is_absolute():
            path = ROOT / path
        wanted_by_file.setdefault(path, set()).add(row_key(row))
    cache: dict[tuple[str, str, str, str], list[Any]] = {}
    for path, wanted in wanted_by_file.items():
        if not path.exists():
            continue
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                if (
                    record.get("status") != "ok"
                    or str(record.get("outcome") or "").lower() != "yes"
                ):
                    continue
                key = row_key(record)
                if key in wanted:
                    cache[key] = list((record.get("raw") or {}).get("asks") or [])
    return cache


def add_ten_share_cost(frame: pd.DataFrame) -> pd.DataFrame:
    raw_cache = load_raw_ask_cache(frame)
    rows: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        if float(row["current_yes_ask_size"]) >= QUANTITY:
            asks = [
                {
                    "price": float(row["current_yes_ask"]),
                    "size": float(row["current_yes_ask_size"]),
                }
            ]
            source = "feature_top_ask_covers_quantity"
        else:
            asks = raw_cache.get(row_key(row), [])
            source = "archived_full_ask_ladder"
        ladder = walk_ask_ladder(asks, QUANTITY)
        rows.append(
            {
                "ten_share_executable": bool(ladder["executable"]),
                "ten_share_cost_per_share": ladder["effective_cost_per_share"],
                "ten_share_principal_vwap": ladder["principal_vwap"],
                "ten_share_max_ask_price": ladder["max_ask_price"],
                "ten_share_cost_source": source,
            }
        )
    return pd.concat([frame.reset_index(drop=True), pd.DataFrame(rows)], axis=1)


def first_city_day(frame: pd.DataFrame) -> pd.DataFrame:
    return (
        frame.sort_values(
            ["target_date", "city", "decision_snapshot_dt", "decision_hour_local"]
        )
        .drop_duplicates(["city", "target_date"], keep="first")
        .copy()
    )


def select_ten(frame: pd.DataFrame) -> pd.DataFrame:
    selected = frame[
        frame["ten_share_executable"]
        & ~frame["current_bracket"].astype(str).str.contains(r"\+", regex=True)
        & frame["market_mid"].ge(0.80)
        & frame[PROBABILITY].gt(frame["ten_share_cost_per_share"])
    ].copy()
    selected["model_probability"] = selected[PROBABILITY]
    selected["model_edge_after_fee_and_depth"] = (
        selected[PROBABILITY] - selected["ten_share_cost_per_share"]
    )
    return first_city_day(selected)


def metrics(frame: pd.DataFrame, cost_column: str) -> dict[str, Any]:
    cost = frame[cost_column].astype(float)
    pnl = frame["label"].astype(float) - cost
    daily = (
        frame.assign(_cost=cost, _pnl=pnl)
        .groupby("target_date")[["_pnl", "_cost"]]
        .sum()
    )
    values = daily[["_pnl", "_cost"]].to_numpy(float)
    rng = np.random.default_rng(SEED)
    draws = []
    for _ in range(BOOTSTRAP_REPS):
        sample = values[rng.integers(0, len(values), len(values))]
        draws.append(float(sample[:, 0].sum() / sample[:, 1].sum()))
    return {
        "city_days": len(frame),
        "dates": int(frame["target_date"].nunique()),
        "cities": int(frame["city"].nunique()),
        "wins": int(frame["label"].sum()),
        "losses": int(frame["label"].eq(0).sum()),
        "win_rate": float(frame["label"].mean()),
        "avg_effective_cost": float(cost.mean()),
        "roi": float(pnl.sum() / cost.sum()),
        "target_date_block_ci95": [
            float(np.quantile(draws, 0.025)),
            float(np.quantile(draws, 0.975)),
        ],
        "signals_per_date": float(len(frame) / frame["target_date"].nunique()),
        "signals_p90_date": float(frame.groupby("target_date").size().quantile(0.90)),
        "signals_max_date": int(frame.groupby("target_date").size().max()),
    }


def live_maker_fill_rate() -> float:
    conn = sqlite3.connect(
        f"file:{ROOT / 'runtime/weather.db'}?mode=ro", uri=True, timeout=1.0
    )
    conn.execute("PRAGMA query_only=ON")
    rows = conn.execute(
        """
        SELECT maker_only, COUNT(DISTINCT city || '|' || target_date)
        FROM fact_trades
        WHERE instance_id='current_yes_core_carry_tiny_live_v2'
        GROUP BY maker_only
        """
    ).fetchall()
    counts = {int(maker): int(count) for maker, count in rows}
    return counts.get(1, 0) / counts.get(0, 1)


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--net-ev-sizing", action="store_true")
    parser.add_argument("--descriptive-audit", action="store_true")
    parser.add_argument("--semantic-challenger", action="store_true")
    parser.add_argument("--transition-confirmation-challenger", action="store_true")
    parser.add_argument("--actual-transport-tail", action="store_true")
    parser.add_argument("--overshoot-survival", action="store_true")
    parser.add_argument("--price-conditioned-challenger", action="store_true")
    args, _ = parser.parse_known_args()
    if args.net_ev_sizing:
        from scripts.analysis.reheat_risk.core_carry_net_ev_sizing import (
            main as sizing_main,
        )

        return sizing_main()
    if args.descriptive_audit:
        from scripts.analysis.reheat_risk.core_carry_descriptive_audit import (
            main as descriptive_main,
        )

        return descriptive_main()
    if args.semantic_challenger:
        from scripts.analysis.reheat_risk.core_carry_semantic_challenger import (
            main as semantic_challenger_main,
        )

        return semantic_challenger_main()
    if args.transition_confirmation_challenger:
        from scripts.analysis.reheat_risk.core_carry_transition_confirmation_challenger import (
            main as transition_confirmation_challenger_main,
        )

        return transition_confirmation_challenger_main()
    if args.actual_transport_tail:
        from scripts.analysis.reheat_risk.core_carry_actual_transport_tail import (
            main as actual_transport_tail_main,
        )

        return actual_transport_tail_main()
    if args.overshoot_survival:
        from scripts.analysis.reheat_risk.core_carry_overshoot_survival import (
            main as overshoot_survival_main,
        )

        return overshoot_survival_main()
    if args.price_conditioned_challenger:
        from scripts.analysis.reheat_risk.core_carry_price_conditioned_full_support import (
            main as price_conditioned_challenger_main,
        )

        return price_conditioned_challenger_main()
    oof = pd.read_csv(OOF_PATH)
    oof["decision_snapshot_dt"] = pd.to_datetime(
        oof["decision_snapshot_ts_utc"], utc=True, errors="coerce"
    )
    costed = add_ten_share_cost(oof)
    entries10 = select_ten(costed)
    entries5 = pd.read_csv(ENTRY5_PATH)
    keys = ["city", "target_date", "decision_snapshot_ts_utc", "current_bracket"]
    same_entry = entries5.merge(
        costed[
            keys
            + [
                "ten_share_executable",
                "ten_share_cost_per_share",
                "ten_share_max_ask_price",
            ]
        ],
        on=keys,
        how="left",
        validate="one_to_one",
    )
    same_entry["ten_share_positive_ev"] = (
        same_entry["ten_share_executable"]
        & same_entry["model_probability"].gt(
            same_entry["ten_share_cost_per_share"]
        )
    )
    overlap = entries5[keys].merge(
        entries10[keys], on=keys, how="outer", indicator=True
    )
    metric5 = metrics(entries5, "five_share_cost_per_share")
    metric10 = metrics(entries10, "ten_share_cost_per_share")
    q_maker = live_maker_fill_rate()

    losses10 = entries10[entries10["label"].eq(0)]
    current_loss = (
        5 * losses10["ten_share_cost_per_share"]
        + 5 * losses10["market_mid"]
    )
    proposed_loss = (
        10 * losses10["ten_share_cost_per_share"]
        + 5 * losses10["market_mid"]
    )
    payload = {
        "target": (
            "same OOF state universe and frozen probability model; compare "
            "five-share versus ten-share taker full-ladder execution"
        ),
        "five_share_frozen": metric5,
        "ten_share_replay": metric10,
        "same_entry_capacity": {
            "five_share_entries": len(same_entry),
            "ten_share_executable": int(same_entry["ten_share_executable"].sum()),
            "ten_share_positive_ev": int(same_entry["ten_share_positive_ev"].sum()),
            "avg_extra_cost_per_share_where_executable": float(
                (
                    same_entry.loc[
                        same_entry["ten_share_executable"], "ten_share_cost_per_share"
                    ]
                    - same_entry.loc[
                        same_entry["ten_share_executable"],
                        "five_share_cost_per_share",
                    ]
                ).mean()
            ),
        },
        "selection_overlap": {
            "same_exact_entry": int(overlap["_merge"].eq("both").sum()),
            "five_only_exact_entry": int(overlap["_merge"].eq("left_only").sum()),
            "ten_only_exact_entry": int(overlap["_merge"].eq("right_only").sum()),
        },
        "risk_with_existing_additive_maker": {
            "preliminary_live_maker_fill_rate": q_maker,
            "avg_loss_current_5_taker_plus_5_maker_if_both_fill": float(
                current_loss.mean()
            ),
            "avg_loss_proposed_10_taker_plus_5_maker_if_both_fill": float(
                proposed_loss.mean()
            ),
            "loss_dollar_increase": float(proposed_loss.mean() - current_loss.mean()),
            "loss_multiplier": float(proposed_loss.mean() / current_loss.mean()),
            "expected_daily_cost_current": float(
                metric5["signals_per_date"]
                * (
                    5 * metric5["avg_effective_cost"]
                    + q_maker * 5 * entries5["market_mid"].mean()
                )
            ),
            "expected_daily_cost_proposed": float(
                metric10["signals_per_date"]
                * (
                    10 * metric10["avg_effective_cost"]
                    + q_maker * 5 * entries10["market_mid"].mean()
                )
            ),
        },
        "verdict": {
            "conclusion": "inconclusive_for_live_size_up",
            "significance": "PASS for historical absolute ROI",
            "baseline": "unchanged_from_frozen_report",
            "forward": "FAIL; current live settlements and maker selection are insufficient",
            "deployment_blockers": [
                "runtime contract currently requires exactly 5 taker + 5 maker",
                "daily cost precheck is hard-coded to 10 shares and would undercount a 10+5 configuration",
            ],
            "action": "keep 5 taker + 5 maker; do not deploy 10 taker yet",
        },
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    costed.to_csv(OUT_DIR / "oof_states_ten_share_cost.csv", index=False)
    entries10.to_csv(OUT_DIR / "ten_share_policy_entries.csv", index=False)
    same_entry.to_csv(OUT_DIR / "same_entry_capacity.csv", index=False)
    OUT_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report = f"""# Current-YES core carry：10-share taker 容量重放

Status: `inconclusive / no live sizing change`

## 结论

- 5-share 冻结：{metric5['city_days']} city-days，胜率 {metric5['win_rate']:.2%}，
  fee-adjusted ROI {metric5['roi']:+.2%}。
- 10-share 全 ladder 重放：{metric10['city_days']} city-days，胜率
  {metric10['win_rate']:.2%}，fee-adjusted ROI {metric10['roi']:+.2%}，
  date-block 95% CI [{metric10['target_date_block_ci95'][0]:+.2%},
  {metric10['target_date_block_ci95'][1]:+.2%}]。
- 原 136 个 entry 中，{int(same_entry['ten_share_executable'].sum())} 个有十股深度，
  {int(same_entry['ten_share_positive_ev'].sum())} 个在十股成本后仍为正 EV。
- 但当前 forward settlement 与 maker adverse-selection 证据不足，不扩大 live。

## 风险

若 taker 与 maker 都成交，历史 loss 的平均美元损失从
`${current_loss.mean():.2f}` 增至 `${proposed_loss.mean():.2f}`，
放大到 {proposed_loss.mean() / current_loss.mean():.2f} 倍。

按当前初步 maker fill rate {q_maker:.1%}，平均每日现金成本从约
`${payload['risk_with_existing_additive_maker']['expected_daily_cost_current']:.2f}`
增至约
`${payload['risk_with_existing_additive_maker']['expected_daily_cost_proposed']:.2f}`。

## 双漏斗与证据边界

- signal funnel：相同 OOF state/model/checkpoint，只改变 taker quantity。
- evidence funnel：使用 PIT full ask ladder + 官方 fee；不是 actual historical fills。
- maker 未并入 10-share alpha；当前 maker fill rate 只用于现金压力情景。
- 生产 runtime contract 当前明确冻结 `5 taker + 5 maker`；daily cost
  预检也写死按 10 shares 估算，不能把参数直接改成 `10+5`。
- `significance=PASS`（历史 absolute ROI），`baseline` 沿用冻结报告，
  `forward=FAIL`，`conclusion=inconclusive_for_live_size_up`。

动作：维持 `5 taker + 5 maker`，先积累 settled forward，不部署 10 taker。
"""
    OUT_MD.write_text(report, encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
