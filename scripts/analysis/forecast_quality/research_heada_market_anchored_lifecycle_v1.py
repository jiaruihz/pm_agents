#!/usr/bin/env python3
"""Test HeadA entry-ticket lifecycle with PIT weather innovation and market prices.

This is deliberately an existing-ticket study.  Same-day observations are
never moved backward into the D-1 entry selector.  The script asks whether the
incremental weather update predicts later market repricing and whether a
predeclared sell/hold/add rule improves fee-adjusted value on the exact HeadA
ticket.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pandas as pd
from scipy.stats import norm


ROOT = Path(__file__).resolve().parents[3]
CURRENT = ROOT / "docs/analysis/2026-07/generated/heada_multisource_city_regime_v1/current_enriched.csv"
OOF = ROOT / "docs/analysis/2026-07/generated/forecast_innovation_morning_v1/oof_predictions.csv"
CHECKPOINTS = ROOT / "docs/analysis/2026-07/generated/forecast_innovation_morning_v1/checkpoint_rows.csv"
DEFAULT_DB = ROOT / "runtime/weather.db"
DEFAULT_OUT = ROOT / "docs/analysis/2026-07/generated/heada_market_anchored_lifecycle_v1"
DEFAULT_REPORT = ROOT / "docs/analysis/2026-07/2026-07-28-heada-market-anchored-lifecycle-v1.md"
DEFAULT_BLOCKED = (
    ROOT
    / "runtime/weather_edge_v1/low_price_yes_lottery_tiny_live_v1"
    / "blocked_candidates.jsonl"
)
CLOB_HISTORY_URL = "https://clob.polymarket.com/prices-history"
CHECKPOINT_HOURS = (9, 12)
WEATHER_SIGMA_F = 3.0
WEATHER_FEE_RATE = 0.05
EDGE_BUFFER = 0.02
HISTORY_MAX_AGE_MIN = 90
FUTURE_MAX_DELAY_MIN = 30
BOOTSTRAP_SAMPLES = 5_000
BOOTSTRAP_SEED = 20260728
THRESHOLDS = (0.01, 0.02, 0.05)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--current", type=Path, default=CURRENT)
    parser.add_argument("--oof", type=Path, default=OOF)
    parser.add_argument("--checkpoints", type=Path, default=CHECKPOINTS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--blocked-candidates", type=Path, default=DEFAULT_BLOCKED)
    parser.add_argument("--refresh-price-history", action="store_true")
    return parser.parse_args()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=2000")
    return conn


def exact_bounds_f(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    low_native = pd.to_numeric(frame["bracket_low_native"], errors="coerce")
    high_native = pd.to_numeric(frame["bracket_high_native"], errors="coerce")
    low = np.where(frame["unit"].eq("C"), (low_native - 0.5) * 1.8 + 32.0, low_native - 0.5)
    high = np.where(frame["unit"].eq("C"), (high_native + 0.5) * 1.8 + 32.0, high_native + 0.5)
    return low, high


def exact_probability(mu: pd.Series, low: np.ndarray, high: np.ndarray) -> np.ndarray:
    values = pd.to_numeric(mu, errors="coerce").to_numpy(float)
    return np.clip(
        norm.cdf((high - values) / WEATHER_SIGMA_F)
        - norm.cdf((low - values) / WEATHER_SIGMA_F),
        1e-5,
        1 - 1e-5,
    )


def load_exact_ticket_rows(args: argparse.Namespace) -> pd.DataFrame:
    current = pd.read_csv(args.current, low_memory=False)
    current["target_date"] = current["target_date"].astype(str)
    current["win"] = current["win"].astype(bool)
    checkpoints = pd.read_csv(args.checkpoints, low_memory=False)
    checkpoints = (
        checkpoints[checkpoints["checkpoint_hour_local"].isin(CHECKPOINT_HOURS)]
        .sort_values("tmax_state_id")
        .drop_duplicates(["city", "target_date", "checkpoint_hour_local"])
    )
    predictions = pd.read_csv(args.oof, low_memory=False)
    predictions = predictions[predictions["checkpoint_hour_local"].isin(CHECKPOINT_HOURS)]
    states = checkpoints.merge(
        predictions[
            [
                "tmax_state_id",
                "pred_rolling_bias_f",
                "pred_innovation_f",
                "pred_innovation_regime_f",
                "beta_innovation",
                "train_dates",
                "train_through_date",
            ]
        ],
        on="tmax_state_id",
        how="inner",
        validate="one_to_one",
    )
    rows = current.merge(
        states[
            [
                "tmax_state_id",
                "city",
                "target_date",
                "checkpoint_hour_local",
                "decision_ts_utc",
                "ladder_snapshot_id",
                "forecast_innovation_f",
                "pred_rolling_bias_f",
                "pred_innovation_f",
                "pred_innovation_regime_f",
                "beta_innovation",
                "train_dates",
                "train_through_date",
            ]
        ],
        on=["city", "target_date"],
        how="inner",
        validate="many_to_many",
    )
    low, high = exact_bounds_f(rows)
    rows["p_weather_prior"] = exact_probability(rows["pred_rolling_bias_f"], low, high)
    rows["p_weather_innovation"] = exact_probability(rows["pred_innovation_f"], low, high)
    rows["weather_probability_delta"] = (
        rows["p_weather_innovation"] - rows["p_weather_prior"]
    )

    quote_sql = """
        SELECT yes_token_id, yes_direct_bid, yes_direct_ask,
               yes_direct_bid_size, yes_direct_ask_size,
               yes_direct_depth_bid_5c, yes_direct_depth_ask_5c,
               yes_book_status, yes_book_fetched_at_utc
        FROM tmax_v2_ladder_rung_quotes
        WHERE ladder_snapshot_id = ? AND condition_id = ?
        LIMIT 1
    """
    quote_rows: list[tuple[Any, ...]] = []
    with connect_ro(args.db) as conn:
        for row in rows.itertuples(index=False):
            found = conn.execute(
                quote_sql, (str(row.ladder_snapshot_id), str(row.condition_id))
            ).fetchone()
            quote_rows.append(found or (None,) * 9)
    quote_columns = [
        "yes_token_id",
        "yes_direct_bid",
        "yes_direct_ask",
        "yes_direct_bid_size",
        "yes_direct_ask_size",
        "yes_direct_depth_bid_5c",
        "yes_direct_depth_ask_5c",
        "yes_book_status",
        "yes_book_fetched_at_utc",
    ]
    return pd.concat(
        [rows.reset_index(drop=True), pd.DataFrame(quote_rows, columns=quote_columns)],
        axis=1,
    )


def fetch_one_history(token_id: str) -> list[dict[str, Any]]:
    response = httpx.get(
        CLOB_HISTORY_URL,
        params={"market": token_id, "interval": "all", "fidelity": "1"},
        timeout=30.0,
    )
    response.raise_for_status()
    payload = response.json()
    return [
        {"token_id": token_id, "ts_unix": int(item["t"]), "price": float(item["p"])}
        for item in payload.get("history", [])
        if item.get("t") is not None and item.get("p") is not None
    ]


def load_or_fetch_history(
    tokens: list[str],
    cache: Path,
    *,
    refresh: bool,
) -> pd.DataFrame:
    if cache.exists() and not refresh:
        cached = pd.read_csv(cache, dtype={"token_id": str})
        if set(tokens).issubset(set(cached["token_id"].unique())):
            return cached[cached["token_id"].isin(tokens)].copy()
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch_one_history, token): token for token in tokens}
        for future in as_completed(futures):
            token = futures[future]
            try:
                records.extend(future.result())
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{token}: {type(exc).__name__}: {exc}")
    if errors:
        raise RuntimeError("price history backfill failed:\n" + "\n".join(errors))
    history = pd.DataFrame(records)
    history = history.sort_values(["token_id", "ts_unix"]).drop_duplicates(
        ["token_id", "ts_unix"], keep="last"
    )
    history.to_csv(cache, index=False)
    return history


def price_at(
    history: pd.DataFrame,
    *,
    target_ts: int,
    direction: str,
    tolerance_min: int,
) -> tuple[float, int | None, float | None]:
    if direction == "before":
        eligible = history[
            history["ts_unix"].le(target_ts)
            & history["ts_unix"].ge(target_ts - tolerance_min * 60)
        ]
        if eligible.empty:
            return math.nan, None, None
        row = eligible.iloc[-1]
        delay = (target_ts - int(row["ts_unix"])) / 60.0
    else:
        eligible = history[
            history["ts_unix"].ge(target_ts)
            & history["ts_unix"].le(target_ts + tolerance_min * 60)
        ]
        if eligible.empty:
            return math.nan, None, None
        row = eligible.iloc[0]
        delay = (int(row["ts_unix"]) - target_ts) / 60.0
    return float(row["price"]), int(row["ts_unix"]), float(delay)


def attach_price_proxy(rows: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    indexed = {token: group for token, group in history.groupby("token_id")}
    records: list[dict[str, Any]] = []
    for row in rows.itertuples(index=False):
        checkpoint = pd.Timestamp(row.decision_ts_utc).tz_convert("UTC")
        token_history = indexed.get(str(row.yes_token_id))
        record: dict[str, Any] = {}
        if token_history is None:
            records.append(record)
            continue
        at_price, at_ts, at_age = price_at(
            token_history,
            target_ts=int(checkpoint.timestamp()),
            direction="before",
            tolerance_min=HISTORY_MAX_AGE_MIN,
        )
        record.update(
            {
                "proxy_price_t": at_price,
                "proxy_price_t_unix": at_ts,
                "proxy_price_age_min": at_age,
            }
        )
        for minutes in (60, 180):
            price, ts_unix, delay = price_at(
                token_history,
                target_ts=int(checkpoint.timestamp()) + minutes * 60,
                direction="after",
                tolerance_min=FUTURE_MAX_DELAY_MIN,
            )
            record[f"proxy_price_plus_{minutes}m"] = price
            record[f"proxy_price_plus_{minutes}m_unix"] = ts_unix
            record[f"proxy_price_plus_{minutes}m_delay_min"] = delay
        records.append(record)
    return pd.concat([rows.reset_index(drop=True), pd.DataFrame(records)], axis=1)


def block_ci(frame: pd.DataFrame, column: str) -> tuple[float, float]:
    daily = frame.groupby("target_date")[column].mean().dropna().to_numpy(float)
    if not len(daily):
        return math.nan, math.nan
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    draws = rng.choice(daily, size=(BOOTSTRAP_SAMPLES, len(daily)), replace=True).mean(axis=1)
    return float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))


def block_sum_ci(frame: pd.DataFrame, column: str) -> tuple[float, float]:
    daily = frame.groupby("target_date")[column].sum().dropna().to_numpy(float)
    if not len(daily):
        return math.nan, math.nan
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    draws = rng.choice(daily, size=(BOOTSTRAP_SAMPLES, len(daily)), replace=True).mean(axis=1)
    return float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))


def proxy_scorecards(rows: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    quality: list[dict[str, Any]] = []
    lead_lag: list[dict[str, Any]] = []
    for hour, group in rows.groupby("checkpoint_hour_local"):
        valid = group[group["proxy_price_t"].notna()].copy()
        y = valid["win"].astype(float)
        baseline = valid["proxy_price_t"].clip(0.001, 0.999)
        for gamma in (0.0, 0.25, 0.5, 1.0):
            candidate = (
                baseline + gamma * valid["weather_probability_delta"]
            ).clip(0.001, 0.999)
            scored = valid[["target_date"]].copy()
            scored["brier_delta"] = (candidate - y) ** 2 - (baseline - y) ** 2
            scored["logloss_delta"] = -(
                y * np.log(candidate) + (1 - y) * np.log(1 - candidate)
            ) + (y * np.log(baseline) + (1 - y) * np.log(1 - baseline))
            brier_low, brier_high = block_ci(scored, "brier_delta")
            log_low, log_high = block_ci(scored, "logloss_delta")
            quality.append(
                {
                    "checkpoint_hour_local": int(hour),
                    "gamma": gamma,
                    "rows": len(valid),
                    "dates": valid["target_date"].nunique(),
                    "wins": int(y.sum()),
                    "market_brier": float(np.mean((baseline - y) ** 2)),
                    "candidate_brier": float(np.mean((candidate - y) ** 2)),
                    "brier_delta": float(scored["brier_delta"].mean()),
                    "brier_delta_ci_low": brier_low,
                    "brier_delta_ci_high": brier_high,
                    "logloss_delta": float(scored["logloss_delta"].mean()),
                    "logloss_delta_ci_low": log_low,
                    "logloss_delta_ci_high": log_high,
                }
            )
        for horizon in (60, 180):
            column = f"proxy_price_plus_{horizon}m"
            future = valid[valid[column].notna()].copy()
            future["price_move"] = future[column] - future["proxy_price_t"]
            if len(future) >= 2 and future["weather_probability_delta"].std() > 0:
                slope = float(
                    np.cov(
                        future["weather_probability_delta"],
                        future["price_move"],
                        ddof=1,
                    )[0, 1]
                    / np.var(future["weather_probability_delta"], ddof=1)
                )
                correlation = float(
                    future["weather_probability_delta"].corr(future["price_move"])
                )
            else:
                slope = math.nan
                correlation = math.nan
            future["signed_move"] = np.sign(future["weather_probability_delta"]) * future[
                "price_move"
            ]
            low, high = block_ci(future, "signed_move")
            lead_lag.append(
                {
                    "checkpoint_hour_local": int(hour),
                    "horizon_min": horizon,
                    "rows": len(future),
                    "dates": future["target_date"].nunique(),
                    "correlation": correlation,
                    "slope_price_move_per_1p_weather_delta": slope,
                    "mean_signed_move": float(future["signed_move"].mean()),
                    "signed_move_ci_low": low,
                    "signed_move_ci_high": high,
                    "directional_accuracy": float((future["signed_move"] > 0).mean()),
                }
            )
    return pd.DataFrame(quality), pd.DataFrame(lead_lag)


def lifecycle_scorecard(rows: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for hour, group in rows.groupby("checkpoint_hour_local"):
        direct = group[group["yes_direct_bid"].notna()].copy()
        direct["entry_fee"] = WEATHER_FEE_RATE * direct["ask"] * (1.0 - direct["ask"])
        direct["exit_fee"] = (
            WEATHER_FEE_RATE
            * direct["yes_direct_bid"]
            * (1.0 - direct["yes_direct_bid"])
        )
        direct["hold_pnl"] = (
            direct["win"].astype(float) - direct["ask"] - direct["entry_fee"]
        )
        direct["exit_pnl"] = (
            direct["yes_direct_bid"] - direct["exit_fee"] - direct["ask"] - direct["entry_fee"]
        )
        for threshold in THRESHOLDS:
            scored = direct.copy()
            scored["exit_signal"] = scored["weather_probability_delta"].le(-threshold)
            scored["policy_pnl"] = np.where(
                scored["exit_signal"], scored["exit_pnl"], scored["hold_pnl"]
            )
            scored["pnl_delta_vs_hold"] = scored["policy_pnl"] - scored["hold_pnl"]
            scored["pnl_delta_vs_exit_all"] = scored["policy_pnl"] - scored["exit_pnl"]
            low, high = block_sum_ci(scored, "pnl_delta_vs_hold")
            capital = float((scored["ask"] + scored["entry_fee"]).sum())
            records.append(
                {
                    "action": "sell_or_hold_direct_bid",
                    "checkpoint_hour_local": int(hour),
                    "threshold": threshold,
                    "opportunity_rows": len(direct),
                    "dates": direct["target_date"].nunique(),
                    "wins": int(direct["win"].sum()),
                    "action_rows": int(scored["exit_signal"].sum()),
                    "baseline_hold_pnl": float(scored["hold_pnl"].sum()),
                    "unconditional_exit_all_pnl": float(scored["exit_pnl"].sum()),
                    "policy_pnl": float(scored["policy_pnl"].sum()),
                    "pnl_delta_vs_hold": float(scored["pnl_delta_vs_hold"].sum()),
                    "pnl_delta_vs_exit_all": float(scored["pnl_delta_vs_exit_all"].sum()),
                    "date_mean_pnl_delta": float(
                        scored.groupby("target_date")["pnl_delta_vs_hold"].sum().mean()
                    ),
                    "date_mean_delta_ci_low": low,
                    "date_mean_delta_ci_high": high,
                    "policy_roi_on_entry_cost": (
                        float(scored["policy_pnl"].sum()) / capital if capital else math.nan
                    ),
                    "price_layer": "direct_executable_bid",
                }
            )

        add = group[group["yes_direct_ask"].notna()].copy()
        add["add_fee"] = (
            WEATHER_FEE_RATE
            * add["yes_direct_ask"]
            * (1.0 - add["yes_direct_ask"])
        )
        add["market_anchor_p"] = (
            add["proxy_price_t"] + add["weather_probability_delta"]
        ).clip(0.001, 0.999)
        for threshold in THRESHOLDS:
            add["add_signal"] = (
                add["weather_probability_delta"].ge(threshold)
                & (
                    add["market_anchor_p"]
                    - add["yes_direct_ask"]
                    - add["add_fee"]
                ).ge(EDGE_BUFFER)
            )
            selected = add[add["add_signal"]].copy()
            selected["add_pnl"] = (
                selected["win"].astype(float)
                - selected["yes_direct_ask"]
                - selected["add_fee"]
            )
            records.append(
                {
                    "action": "add_one_share_direct_ask",
                    "checkpoint_hour_local": int(hour),
                    "threshold": threshold,
                    "opportunity_rows": len(add),
                    "dates": add["target_date"].nunique(),
                    "wins": int(add["win"].sum()),
                    "action_rows": len(selected),
                    "baseline_hold_pnl": 0.0,
                    "unconditional_exit_all_pnl": math.nan,
                    "policy_pnl": float(selected["add_pnl"].sum()),
                    "pnl_delta_vs_hold": float(selected["add_pnl"].sum()),
                    "pnl_delta_vs_exit_all": math.nan,
                    "date_mean_pnl_delta": (
                        float(selected.groupby("target_date")["add_pnl"].sum().mean())
                        if not selected.empty
                        else 0.0
                    ),
                    "date_mean_delta_ci_low": math.nan,
                    "date_mean_delta_ci_high": math.nan,
                    "policy_roi_on_entry_cost": (
                        float(selected["add_pnl"].sum())
                        / float((selected["yes_direct_ask"] + selected["add_fee"]).sum())
                        if not selected.empty
                        else math.nan
                    ),
                    "price_layer": "direct_executable_ask",
                }
            )
    return pd.DataFrame(records)


def fresh_book_failure_impact(path: Path) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    if not path.exists():
        return pd.DataFrame()
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                str(row.get("created_at_utc") or "") >= "2026-07-27T00:00:00Z"
                and row.get("blocker") == "fresh_book_fetch_failed"
            ):
                records.append(row)
    if not records:
        return pd.DataFrame()
    frame = pd.DataFrame(records)
    keys = ["city", "event_date", "bracket", "token_id"]
    return (
        frame.groupby(keys, dropna=False, as_index=False)
        .agg(
            failed_attempts=("created_at_utc", "size"),
            first_failure_utc=("created_at_utc", "min"),
            last_failure_utc=("created_at_utc", "max"),
            snapshot_ask=("decision_entry_price", "last"),
            model_p_yes=("model_p_yes", "last"),
            edge=("edge", "last"),
            book_error=("book_error", "last"),
        )
        .sort_values(["event_date", "city", "bracket"])
    )


def md_table(frame: pd.DataFrame, columns: list[str]) -> str:
    if frame.empty:
        return "_无数据_"
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for _, row in frame.iterrows():
        cells = []
        for column in columns:
            value = row.get(column)
            if pd.isna(value):
                cells.append("NA")
            elif isinstance(value, (float, np.floating)):
                cells.append(f"{float(value):.5f}")
            else:
                cells.append(str(value))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def build_report(
    rows: pd.DataFrame,
    quality: pd.DataFrame,
    lead_lag: pd.DataFrame,
    lifecycle: pd.DataFrame,
    fresh_book_impact: pd.DataFrame,
    db_coverage: dict[str, Any],
) -> str:
    coverage = (
        rows.groupby("checkpoint_hour_local")
        .agg(
            overlap_rows=("city", "size"),
            dates=("target_date", "nunique"),
            proxy_price_rows=("proxy_price_t", "count"),
            direct_bid_rows=("yes_direct_bid", "count"),
            direct_ask_rows=("yes_direct_ask", "count"),
        )
        .reset_index()
    )
    primary_quality = quality[quality["gamma"].eq(1.0)]
    primary_lifecycle = lifecycle[lifecycle["threshold"].eq(0.02)]
    return f"""# HeadA market-anchored lifecycle v1

Generated: {now_utc()}

## 结论与交易动作

这轮把“升温路径有预测增量”推进到了同刻市场与可执行动作，不再停在天气模型自身分数。
主检验固定为：`market probability + 1.0 × weather probability delta`，动作阈值固定 `2c`；
其他 gamma/threshold 只作敏感性，不据此挑最漂亮的结果。

{md_table(primary_quality, ["checkpoint_hour_local", "rows", "dates", "market_brier", "candidate_brier", "brier_delta", "brier_delta_ci_low", "brier_delta_ci_high"])}

{md_table(primary_lifecycle, ["action", "checkpoint_hour_local", "opportunity_rows", "dates", "wins", "action_rows", "unconditional_exit_all_pnl", "policy_pnl", "pnl_delta_vs_hold", "pnl_delta_vs_exit_all", "date_mean_delta_ci_low", "date_mean_delta_ci_high", "policy_roi_on_entry_cost"])}

**当前裁决：没有可执行升级。** 09:00/12:00 的 market-anchored terminal Brier 点估均改善，但
主 gamma=1 的 target-date CI 都跨 0；12:00 的 weather delta 对未来 180 分钟 repricing
显著反向，提示市场可能先过冲再均值回归，不能把 terminal-score 点估直接当短线加仓信号。
direct-bid 子集只有 09:00 的 1 个 winner、12:00 仍为 0，因此“退出比持有少亏”不能归因于
weather selector，必须同时看
`unconditional_exit_all_pnl`。12:00 的 add 信号最终都输掉。price-history 是 midpoint-like public
proxy，不含 bid/ask/depth，不能包装成 guaranteed fill ROI。

## 研究对象与时钟

- estimand：已经在 D-1/target-day 09:00 前进入的 HeadA exact ticket，在当地 09:00 或 12:00
  收到 `actual warming - forecast warming` 后，是否应 sell / hold / add。
- weather increment：同 rows OOF `p_innovation - p_rolling_bias`，sigma={WEATHER_SIGMA_F:.1f}F。
- market anchor：checkpoint 之前最后一条 CLOB `/prices-history`，最大 age={HISTORY_MAX_AGE_MIN}m。
- execution：sell 用 direct YES bid；add 用 direct YES ask；Weather fee
  `0.05*p*(1-p)`；edge buffer={EDGE_BUFFER:.2f}。
- 不把当天 observation 回填到原 entry selector。

## 双漏斗

### Signal funnel

- frozen current HeadA：84 tickets / 8 target dates。
- 与 canonical 09/12 checkpoint 相交：

{md_table(coverage, ["checkpoint_hour_local", "overlap_rows", "dates", "proxy_price_rows", "direct_bid_rows", "direct_ask_rows"])}

### Evidence funnel

- exact token identity：来自同一个 `ladder_snapshot_id + condition_id` canonical rung。
- public price proxy：只恢复历史 repricing path，不证明当时可成交。
- direct bid/ask 缺失原因主要是 `orderbook_budget_exhausted` / `orderbook_scope_skipped`，
  是 coverage gap，不是策略筛除。
- settlement：current HeadA 固定 84/84。
- actual fills：0；本报告是 research replay。

### Fresh-book 污染窗口

7/27 起 runner 记录了 {int(fresh_book_impact["failed_attempts"].sum()) if not fresh_book_impact.empty else 0}
次 `fresh_book_fetch_failed`，对应 {len(fresh_book_impact)} 个 unique
`city/date/bracket/token`；HeadA 是 zero-notional shadow，所以受影响真实 order/fill/notional 均为 0，
但 would-live/fresh-book evidence 被污染。

{md_table(fresh_book_impact, ["city", "event_date", "bracket", "failed_attempts", "first_failure_utc", "last_failure_utc", "snapshot_ask", "model_p_yes", "edge", "book_error"])}

## Market residual / repricing lead-lag

如果 weather delta 真是市场尚未吸收的信息，它应同向预测未来 60/180 分钟价格变化：

{md_table(lead_lag, ["checkpoint_hour_local", "horizon_min", "rows", "dates", "correlation", "slope_price_move_per_1p_weather_delta", "mean_signed_move", "signed_move_ci_low", "signed_move_ci_high", "directional_accuracy"])}

## Market-anchored probability sensitivity

gamma=0 是同 rows market proxy；负 delta 才是改善：

{md_table(quality, ["checkpoint_hour_local", "gamma", "rows", "dates", "brier_delta", "brier_delta_ci_low", "brier_delta_ci_high", "logloss_delta", "logloss_delta_ci_low", "logloss_delta_ci_high"])}

## Fee-adjusted lifecycle sensitivity

{md_table(lifecycle, ["action", "checkpoint_hour_local", "threshold", "opportunity_rows", "dates", "wins", "action_rows", "baseline_hold_pnl", "unconditional_exit_all_pnl", "policy_pnl", "pnl_delta_vs_hold", "pnl_delta_vs_exit_all", "date_mean_delta_ci_low", "date_mean_delta_ci_high", "policy_roi_on_entry_cost"])}

## 八环复核

1. hypothesis：当天 warming innovation 可能先于 exact-ticket repricing。
2. universe：固定 current HeadA 84，不按城市/source/天气类型事后换分母。
3. time：entry 与 target-day checkpoint 分层；forecast/observation 均按 canonical available clock。
4. model：只用 OOF rolling-bias 与 innovation 差；market 是同刻 anchor。
5. probability：报告 Brier/logloss paired delta 与 target-date block CI。
6. execution：direct bid/ask 与 public proxy 分层；fee、spread、top size 不混。
7. robustness：gamma 0/0.25/0.5/1 与阈值 1c/2c/5c；主值预先固定 1、2c。
8. deployment：本报告不改 live；未通过三门只保留 zero-notional telemetry。

## 数据快照与治理

```json
{json.dumps(db_coverage, ensure_ascii=False, indent=2)}
```

已识别的工程缺口：

- HeadA tail telemetry 在 clean production checkout 依赖未跟踪的 generated CSV，导致整批
  `tail_telemetry_status=error`；本轮已改为 versioned deployable calibration bundle，并加 contract test。
- canonical direct book 在 12:00 严重缺失，根因是 snapshot orderbook 全局 budget/scoping。
  2026-07-28 已在 integrated-tail zero-notional runner 内按当轮 HeadA exact token 每 5 分钟优先抓
  direct/proxy bid/ask/top size/5c depth，保持所有 candidate/missing/failure rows；相邻两档仍由
  canonical full-ladder collector 补充。

## 三门

- significance/probability：见主 gamma=1 paired CI。
- fee-adjusted execution：见 2c direct-book lifecycle。
- fresh frozen forward：尚未满 15 个 settled target dates，FAIL/NA。

状态只能是 `shadow_candidate` 或 `inconclusive`；不得据少量当前日期直接升 live。
"""


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = load_exact_ticket_rows(args)
    tokens = sorted(rows["yes_token_id"].dropna().astype(str).unique())
    history = load_or_fetch_history(
        tokens,
        args.output_dir / "clob_price_history_proxy.csv",
        refresh=args.refresh_price_history,
    )
    rows = attach_price_proxy(rows, history)
    quality, lead_lag = proxy_scorecards(rows)
    lifecycle = lifecycle_scorecard(rows)
    fresh_book_impact = fresh_book_failure_impact(args.blocked_candidates)
    with connect_ro(args.db) as conn:
        db_coverage = {
            "db": str(args.db),
            "tmax_state_target_date_min": conn.execute(
                "SELECT MIN(target_date) FROM tmax_v2_canonical_states"
            ).fetchone()[0],
            "tmax_state_target_date_max": conn.execute(
                "SELECT MAX(target_date) FROM tmax_v2_canonical_states"
            ).fetchone()[0],
            "tmax_state_rows": conn.execute(
                "SELECT COUNT(*) FROM tmax_v2_canonical_states"
            ).fetchone()[0],
            "settlement_target_date_max": conn.execute(
                "SELECT MAX(target_date) FROM settlement_outcomes WHERE settlement_status='settled'"
            ).fetchone()[0],
            "current_input": str(args.current.relative_to(ROOT)),
            "checkpoint_input": str(args.checkpoints.relative_to(ROOT)),
            "oof_input": str(args.oof.relative_to(ROOT)),
            "price_history_endpoint": CLOB_HISTORY_URL,
            "price_history_tokens": len(tokens),
            "price_history_rows": len(history),
        }
    rows.to_csv(args.output_dir / "exact_ticket_checkpoint_rows.csv", index=False)
    quality.to_csv(args.output_dir / "market_anchor_probability_scorecard.csv", index=False)
    lead_lag.to_csv(args.output_dir / "price_lead_lag_scorecard.csv", index=False)
    lifecycle.to_csv(args.output_dir / "lifecycle_scorecard.csv", index=False)
    fresh_book_impact.to_csv(args.output_dir / "fresh_book_failure_impact.csv", index=False)
    summary = {
        "generated_at_utc": now_utc(),
        "rows": len(rows),
        "dates": int(rows["target_date"].nunique()),
        "tokens": len(tokens),
        "quality": quality.to_dict("records"),
        "lead_lag": lead_lag.to_dict("records"),
        "lifecycle": lifecycle.to_dict("records"),
        "fresh_book_failure_impact": fresh_book_impact.to_dict("records"),
        "db_coverage": db_coverage,
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    args.report.write_text(
        build_report(rows, quality, lead_lag, lifecycle, fresh_book_impact, db_coverage),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
