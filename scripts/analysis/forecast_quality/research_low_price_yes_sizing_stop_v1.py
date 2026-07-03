"""HeadA low-price YES sizing and stop replay v1.

This is a same-denominator follow-up to the HeadA TP replay. It keeps the
entry selector fixed and tests only execution overlays:

- price/edge-aware sizing that changes dollars at risk, not which rows qualify;
- live-like TP20 where a resting SELL @0.20 fills at 20c, not at the later max bid;
- simple time/salvage stops after the forecast peak window or near settlement.

The script intentionally uses a small pre-declared policy family. It is not a
threshold search.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
INPUT = ROOT / "docs/analysis/2026-07/generated/low_price_yes_integrated_tail_v2/enriched_rows.csv"
SNAPSHOT_DIR = ROOT / "runtime/weather_edge_v1/market_data/paper_snapshots"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/low_price_yes_sizing_stop_v1"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-03-low-price-yes-sizing-stop-v1.md"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-03-low-price-yes-sizing-stop-v1.json"

RNG_SEED = 20260703
N_BOOT = 5000


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_utc(value: Any) -> pd.Timestamp:
    return pd.to_datetime(value, utc=True, errors="coerce")


def to_float(value: Any, default: float = math.nan) -> float:
    try:
        if value is None or value == "":
            return default
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError):
        return default


def fmt_pct(x: Any, signed: bool = True) -> str:
    if x is None:
        return ""
    try:
        val = float(x) * 100.0
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(val):
        return ""
    return f"{val:+.1f}%" if signed else f"{val:.1f}%"


def fmt_usd(x: Any) -> str:
    try:
        val = float(x)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(val):
        return ""
    return f"${val:+.2f}"


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def load_base() -> pd.DataFrame:
    df = pd.read_csv(INPUT)
    required = [
        "condition_id",
        "city",
        "target_date",
        "bracket",
        "ask",
        "decision_snapshot_ts_utc",
        "payoff",
        "edge",
        "model_p_yes",
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise RuntimeError(f"input missing columns: {missing}")
    out = df.copy()
    out["entry_dt"] = parse_utc(out["decision_snapshot_ts_utc"])
    out["entry"] = pd.to_numeric(out["ask"], errors="coerce")
    out["payoff"] = pd.to_numeric(out["payoff"], errors="coerce")
    out = out[
        out["condition_id"].notna()
        & out["entry_dt"].notna()
        & out["entry"].between(0.05, 0.20, inclusive="both")
        & (pd.to_numeric(out["edge"], errors="coerce") >= 0.20)
        & out["payoff"].notna()
    ].copy()
    out["row_id"] = np.arange(len(out))
    for col in ["target_date", "city", "bracket", "condition_id"]:
        out[col] = out[col].astype(str)
    return out


@dataclass
class QuotePath:
    future_quotes: int = 0
    future_bid_quotes: int = 0
    first_future_ts_utc: str = ""
    last_future_ts_utc: str = ""
    max_future_yes_bid: float = math.nan
    max_bid_ts_utc: str = ""
    first_tp20_ts_utc: str = ""
    first_tp20_bid: float = math.nan
    first_tp30_ts_utc: str = ""
    first_tp30_bid: float = math.nan
    time_stop_ts_utc: str = ""
    time_stop_bid: float = math.nan
    time_stop_max_bid_so_far: float = math.nan
    late_salvage_ts_utc: str = ""
    late_salvage_bid: float = math.nan


def scan_paths(base: pd.DataFrame) -> pd.DataFrame:
    by_key: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    entry_dt: dict[int, pd.Timestamp] = {}
    stop_dt: dict[int, pd.Timestamp] = {}
    max_so_far: dict[int, float] = defaultdict(lambda: math.nan)
    paths: dict[int, QuotePath] = {}

    for row in base.itertuples(index=False):
        row_id = int(row.row_id)
        key = (str(row.condition_id), str(row.city), str(row.target_date))
        by_key[key].append(row_id)
        entry = row.entry_dt
        entry_dt[row_id] = entry
        peak_delta_h = to_float(getattr(row, "forecast_peak_delta_hours_local", math.nan))
        if math.isfinite(peak_delta_h):
            # "Peak window passed": two hours after forecast peak, or one hour
            # after entry if the forecast peak was already behind us.
            stop_h = max(1.0, peak_delta_h + 2.0)
        else:
            stop_h = 8.0
        stop_dt[row_id] = entry + pd.Timedelta(hours=stop_h)
        paths[row_id] = QuotePath()

    min_date = base["target_date"].min().replace("-", "")
    max_date = base["target_date"].max().replace("-", "")
    files = sorted(SNAPSHOT_DIR.glob("snapshot_*.json"))
    for snap_path in files:
        stamp = snap_path.name.removeprefix("snapshot_").removesuffix(".json")
        ymd = stamp.split("_", 1)[0]
        if ymd < min_date or ymd > max_date:
            continue
        try:
            payload = json.loads(snap_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        payload_ts = parse_utc(payload.get("ts_utc") or payload.get("snapshot_ts_utc"))
        records = payload.get("records")
        if not isinstance(records, list):
            continue
        for rec in records:
            if not isinstance(rec, dict):
                continue
            key = (
                str(rec.get("condition_id")),
                str(rec.get("city")),
                str(rec.get("target_date") or rec.get("event_date")),
            )
            row_ids = by_key.get(key)
            if not row_ids:
                continue
            ts = parse_utc(rec.get("snapshot_ts_utc") or rec.get("ts_utc"))
            if pd.isna(ts):
                ts = payload_ts
            if pd.isna(ts):
                continue
            settle_utc = parse_utc(rec.get("settle_utc"))
            hours_to_settle = to_float(rec.get("hours_to_settle"))
            if pd.notna(settle_utc) and ts >= settle_utc:
                continue
            if math.isfinite(hours_to_settle) and hours_to_settle < 0:
                continue
            bid = to_float(rec.get("yes_best_bid"))
            for row_id in row_ids:
                if ts <= entry_dt[row_id]:
                    continue
                path = paths[row_id]
                path.future_quotes += 1
                ts_s = ts.isoformat()
                if not path.first_future_ts_utc:
                    path.first_future_ts_utc = ts_s
                path.last_future_ts_utc = ts_s
                if not math.isfinite(bid):
                    continue
                path.future_bid_quotes += 1
                if not math.isfinite(max_so_far[row_id]) or bid > max_so_far[row_id]:
                    max_so_far[row_id] = bid
                if not math.isfinite(path.max_future_yes_bid) or bid > path.max_future_yes_bid:
                    path.max_future_yes_bid = bid
                    path.max_bid_ts_utc = ts_s
                if bid >= 0.20 and not path.first_tp20_ts_utc:
                    path.first_tp20_ts_utc = ts_s
                    path.first_tp20_bid = bid
                if bid >= 0.30 and not path.first_tp30_ts_utc:
                    path.first_tp30_ts_utc = ts_s
                    path.first_tp30_bid = bid
                if (
                    not path.time_stop_ts_utc
                    and ts >= stop_dt[row_id]
                    and math.isfinite(max_so_far[row_id])
                    and max_so_far[row_id] < 0.15
                    and bid >= 0.03
                ):
                    path.time_stop_ts_utc = ts_s
                    path.time_stop_bid = bid
                    path.time_stop_max_bid_so_far = max_so_far[row_id]
                if math.isfinite(hours_to_settle) and 0 <= hours_to_settle <= 3.0 and bid >= 0.02:
                    path.late_salvage_ts_utc = ts_s
                    path.late_salvage_bid = bid

    return pd.DataFrame([{"row_id": row_id, **vars(path)} for row_id, path in paths.items()])


def sizing_shares(row: pd.Series, sizing: str) -> float:
    entry = float(row["entry"])
    edge = to_float(row.get("edge"), 0.0)
    pcal_ev = to_float(row.get("p_cal_no_city_ev"), math.nan)
    model_p = to_float(row.get("model_p_yes"), math.nan)
    if sizing == "fixed_cash_0p80":
        return 0.80 / entry
    if sizing == "fixed_8_shares":
        return 8.0
    if sizing == "fixed_12_shares":
        return 12.0
    if sizing == "edge_scaled_8_shares":
        return 8.0 * min(1.5, max(0.5, edge / 0.30))
    if sizing == "pcal_scaled_8_shares":
        score = pcal_ev if math.isfinite(pcal_ev) else edge
        return 8.0 * min(1.5, max(0.5, score / 0.40))
    if sizing == "modelp_scaled_8_shares":
        score = model_p if math.isfinite(model_p) else 0.35
        return 8.0 * min(1.5, max(0.5, score / 0.40))
    if sizing == "payout25_cap5":
        return min(5.0, entry * 25.0) / entry
    raise RuntimeError(f"unknown sizing: {sizing}")


SIZING_POLICIES = [
    "fixed_cash_0p80",
    "fixed_8_shares",
    "fixed_12_shares",
    "edge_scaled_8_shares",
    "pcal_scaled_8_shares",
    "modelp_scaled_8_shares",
    "payout25_cap5",
]


EXIT_POLICIES = [
    "hold",
    "hold_plus_time_stop",
    "hold_plus_late_salvage",
    "hold_plus_time_stop_or_late_salvage",
    "tp20_maxbid_backtest_style",
    "tp20_live_fixed20",
    "tp20_live_fixed20_plus_late_salvage",
    "tp20_live_fixed20_plus_time_stop",
    "tp20_live_fixed20_plus_time_stop_or_late_salvage",
    "tp30_live_fixed30",
    "recover_stake_tp30_live_fixed30",
]


def exit_decision(row: pd.Series, policy: str, entry: float) -> tuple[float, str, bool, bool]:
    payoff = float(row["payoff"])
    max_bid = to_float(row.get("max_future_yes_bid"))
    if policy == "hold":
        return payoff, "settlement", False, False
    if policy in {"hold_plus_time_stop", "hold_plus_time_stop_or_late_salvage"}:
        stop_bid = to_float(row.get("time_stop_bid"))
        if math.isfinite(stop_bid):
            return max(0.0, stop_bid - 0.01), "time_stop", False, True
        if policy == "hold_plus_time_stop":
            return payoff, "settlement", False, False
    if policy in {"hold_plus_late_salvage", "hold_plus_time_stop_or_late_salvage"}:
        salvage_bid = to_float(row.get("late_salvage_bid"))
        if math.isfinite(salvage_bid):
            return max(0.0, salvage_bid - 0.01), "late_salvage", False, True
        return payoff, "settlement", False, False
    if policy == "tp20_maxbid_backtest_style":
        if math.isfinite(max_bid) and max_bid >= 0.20:
            return max_bid, "tp20_maxbid", True, False
        return payoff, "settlement", False, False
    if policy in {
        "tp20_live_fixed20",
        "tp20_live_fixed20_plus_late_salvage",
        "tp20_live_fixed20_plus_time_stop",
        "tp20_live_fixed20_plus_time_stop_or_late_salvage",
    }:
        if bool(row.get("first_tp20_ts_utc")):
            return 0.20, "tp20_fixed20", True, False
        if policy in {"tp20_live_fixed20_plus_time_stop", "tp20_live_fixed20_plus_time_stop_or_late_salvage"}:
            stop_bid = to_float(row.get("time_stop_bid"))
            if math.isfinite(stop_bid):
                return max(0.0, stop_bid - 0.01), "time_stop", False, True
        if policy in {"tp20_live_fixed20_plus_late_salvage", "tp20_live_fixed20_plus_time_stop_or_late_salvage"}:
            salvage_bid = to_float(row.get("late_salvage_bid"))
            if math.isfinite(salvage_bid):
                return max(0.0, salvage_bid - 0.01), "late_salvage", False, True
        return payoff, "settlement", False, False
    if policy == "tp30_live_fixed30":
        if bool(row.get("first_tp30_ts_utc")):
            return 0.30, "tp30_fixed30", True, False
        return payoff, "settlement", False, False
    if policy == "recover_stake_tp30_live_fixed30":
        if bool(row.get("first_tp30_ts_utc")) and 0.30 > entry:
            return 0.30, "recover_stake_tp30", True, False
        return payoff, "settlement", False, False
    raise RuntimeError(f"unknown exit policy: {policy}")


def apply_replay(frame: pd.DataFrame) -> pd.DataFrame:
    recs: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        entry = float(row["entry"])
        payoff = float(row["payoff"])
        for sizing in SIZING_POLICIES:
            shares = sizing_shares(row, sizing)
            if sizing.startswith("fixed_") and sizing.endswith("_shares"):
                shares = max(5.0, shares)
            cost = shares * entry
            for policy in EXIT_POLICIES:
                exit_px, exit_mode, tp_hit, stop_hit = exit_decision(row, policy, entry)
                if policy == "recover_stake_tp30_live_fixed30" and exit_mode == "recover_stake_tp30":
                    shares_sold = min(shares, cost / exit_px)
                    pnl = shares_sold * (exit_px - entry) + (shares - shares_sold) * (payoff - entry)
                else:
                    pnl = shares * (exit_px - entry)
                recs.append(
                    {
                        "row_id": int(row["row_id"]),
                        "city": row["city"],
                        "target_date": row["target_date"],
                        "bracket": row["bracket"],
                        "forecast_source": row.get("forecast_source", ""),
                        "period": row.get("period", ""),
                        "entry": entry,
                        "payoff": payoff,
                        "win": payoff >= 0.5,
                        "edge": row.get("edge", math.nan),
                        "model_p_yes": row.get("model_p_yes", math.nan),
                        "p_cal_no_city_ev": row.get("p_cal_no_city_ev", math.nan),
                        "source_aware_v3": truthy(row.get("source_aware_v3")),
                        "decision_snapshot_ts_utc": row.get("decision_snapshot_ts_utc", ""),
                        "future_quotes": int(row.get("future_quotes") or 0),
                        "future_bid_quotes": int(row.get("future_bid_quotes") or 0),
                        "max_future_yes_bid": row.get("max_future_yes_bid", math.nan),
                        "max_bid_ts_utc": row.get("max_bid_ts_utc", ""),
                        "first_tp20_ts_utc": row.get("first_tp20_ts_utc", ""),
                        "first_tp30_ts_utc": row.get("first_tp30_ts_utc", ""),
                        "time_stop_ts_utc": row.get("time_stop_ts_utc", ""),
                        "time_stop_bid": row.get("time_stop_bid", math.nan),
                        "late_salvage_ts_utc": row.get("late_salvage_ts_utc", ""),
                        "late_salvage_bid": row.get("late_salvage_bid", math.nan),
                        "sizing": sizing,
                        "shares": shares,
                        "cost": cost,
                        "exit_policy": policy,
                        "exit_mode": exit_mode,
                        "exit_price": exit_px,
                        "tp_hit": tp_hit,
                        "stop_hit": stop_hit,
                        "pnl": pnl,
                        "roi": pnl / cost if cost > 0 else math.nan,
                    }
                )
    return pd.DataFrame(recs)


def date_block_ci(daily: pd.DataFrame) -> tuple[float | None, float | None]:
    if len(daily) < 3:
        return (None, None)
    rng = np.random.default_rng(RNG_SEED)
    pnl = daily["pnl"].to_numpy(dtype=float)
    cost = daily["cost"].to_numpy(dtype=float)
    vals = []
    for _ in range(N_BOOT):
        idx = rng.integers(0, len(pnl), len(pnl))
        c = float(cost[idx].sum())
        vals.append(float(pnl[idx].sum() / c) if c > 0 else math.nan)
    vals = np.asarray([x for x in vals if math.isfinite(x)])
    return (float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5)))


def summarize(rows: pd.DataFrame, period_name: str, period_mask: pd.Series) -> pd.DataFrame:
    out: list[dict[str, Any]] = []
    g0 = rows[period_mask].copy()
    for (sizing, exit_policy), g in g0.groupby(["sizing", "exit_policy"], dropna=False):
        if g.empty:
            continue
        daily = g.groupby("target_date", as_index=False).agg(
            rows=("row_id", "count"),
            cost=("cost", "sum"),
            pnl=("pnl", "sum"),
        )
        daily["roi"] = daily["pnl"] / daily["cost"]
        ci_low, ci_high = date_block_ci(daily)
        cost = float(g["cost"].sum())
        pnl = float(g["pnl"].sum())
        out.append(
            {
                "period": period_name,
                "sizing": sizing,
                "exit_policy": exit_policy,
                "rows": int(len(g)),
                "dates": int(g["target_date"].nunique()),
                "cities": int(g["city"].nunique()),
                "win_rate_final": float(g["win"].mean()),
                "avg_entry": float(g["entry"].mean()),
                "avg_cost": float(g["cost"].mean()),
                "total_cost": cost,
                "tp_hit_rate": float(g["tp_hit"].mean()),
                "stop_hit_rate": float(g["stop_hit"].mean()),
                "roi": pnl / cost if cost > 0 else math.nan,
                "pnl": pnl,
                "roi_ci_low": ci_low,
                "roi_ci_high": ci_high,
                "losing_days": int((daily["pnl"] < 0).sum()),
                "le_minus50pct_days": int((daily["roi"] <= -0.5).sum()),
                "max_daily_loss_usd": float(daily["pnl"].min()),
                "max_daily_loss_roi": float(daily["roi"].min()),
                "path_coverage": float((g["future_bid_quotes"] > 0).mean()),
            }
        )
    return pd.DataFrame(out)


def make_markdown(summary: pd.DataFrame, replay: pd.DataFrame, base: pd.DataFrame, paths: pd.DataFrame) -> str:
    full = summary[summary["period"] == "full"].copy()
    recent = summary[summary["period"] == "recent_ge_2026_06_21"].copy()
    forward = summary[summary["period"] == "closed_forward_2026_06_27_30"].copy()

    def pick(df: pd.DataFrame, sizing: str, policy: str) -> dict[str, Any]:
        sub = df[(df["sizing"] == sizing) & (df["exit_policy"] == policy)]
        return sub.iloc[0].to_dict() if not sub.empty else {}

    focus_pairs = [
        ("fixed_cash_0p80", "hold"),
        ("fixed_cash_0p80", "hold_plus_time_stop_or_late_salvage"),
        ("fixed_cash_0p80", "tp20_maxbid_backtest_style"),
        ("fixed_cash_0p80", "tp20_live_fixed20"),
        ("fixed_cash_0p80", "tp20_live_fixed20_plus_time_stop_or_late_salvage"),
        ("fixed_8_shares", "hold"),
        ("fixed_8_shares", "hold_plus_time_stop_or_late_salvage"),
        ("fixed_8_shares", "tp20_live_fixed20"),
        ("edge_scaled_8_shares", "hold_plus_time_stop_or_late_salvage"),
        ("edge_scaled_8_shares", "tp20_live_fixed20"),
        ("pcal_scaled_8_shares", "hold_plus_time_stop_or_late_salvage"),
        ("pcal_scaled_8_shares", "tp20_live_fixed20"),
        ("payout25_cap5", "hold_plus_time_stop_or_late_salvage"),
        ("payout25_cap5", "tp20_live_fixed20"),
    ]

    def table(df: pd.DataFrame, pairs: list[tuple[str, str]]) -> str:
        lines = [
            "| sizing | exit | rows | dates | avg cost | TP hit | stop hit | ROI | CI | losing days | <=-50% days | max daily loss |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for sizing, policy in pairs:
            r = pick(df, sizing, policy)
            if not r:
                continue
            ci = f"[{fmt_pct(r.get('roi_ci_low'))}, {fmt_pct(r.get('roi_ci_high'))}]"
            lines.append(
                "| "
                + " | ".join(
                    [
                        sizing,
                        policy,
                        str(int(r["rows"])),
                        str(int(r["dates"])),
                        f"${float(r['avg_cost']):.2f}",
                        fmt_pct(r["tp_hit_rate"], signed=False),
                        fmt_pct(r["stop_hit_rate"], signed=False),
                        fmt_pct(r["roi"]),
                        ci,
                        str(int(r["losing_days"])),
                        str(int(r["le_minus50pct_days"])),
                        f"{fmt_usd(r['max_daily_loss_usd'])} / {fmt_pct(r['max_daily_loss_roi'])}",
                    ]
                )
                + " |"
            )
        return "\n".join(lines)

    backtest = pick(full, "fixed_cash_0p80", "tp20_maxbid_backtest_style")
    live_like = pick(full, "fixed_cash_0p80", "tp20_live_fixed20")
    stop_like = pick(full, "fixed_cash_0p80", "tp20_live_fixed20_plus_time_stop_or_late_salvage")
    stop_no_tp = pick(full, "fixed_cash_0p80", "hold_plus_time_stop_or_late_salvage")
    current_hold = pick(full, "fixed_cash_0p80", "hold")

    live_entries = replay[
        (replay["target_date"] == "2026-07-03")
        & (replay["sizing"] == "fixed_cash_0p80")
        & (replay["exit_policy"] == "tp20_live_fixed20")
    ]

    leakage_checks = {
        "rows": int(len(base)),
        "decision_ts_missing": int(base["entry_dt"].isna().sum()),
        "future_path_rows": int((paths["future_quotes"] > 0).sum()),
        "future_bid_path_rows": int((paths["future_bid_quotes"] > 0).sum()),
        "first_tp20_rows": int((paths["first_tp20_ts_utc"].astype(str) != "").sum()),
        "time_stop_rows": int((paths["time_stop_ts_utc"].astype(str) != "").sum()),
        "late_salvage_rows": int((paths["late_salvage_ts_utc"].astype(str) != "").sum()),
    }

    return f"""# HeadA Low-Price YES Sizing / Stop Replay v1

Generated: {now_utc()}

## Verdict

This is an execution-layer audit for HeadA (`forecast_tail_low_price_yes`), not a new entry alpha search.

```text
conclusion=inconclusive_for_live_change
entry_selector=unchanged
live_action=do_not_size_up; fix token-resolution plumbing first; keep any sizing/stop changes in shadow until fresh-forward fills
```

Plain English: today's miss does not by itself disprove HeadA, but the replay found a real mismatch. The earlier TP replay credited `TP20` at the later maximum executable bid. The live overlay mostly pre-places `SELL @0.20`, so a true live-like replay should cap that exit at 20c. That makes the TP overlay much less magical and explains why the live sleeve can feel worse than the headline replay.

## Data Snapshot

- Input denominator: `{INPUT.relative_to(ROOT)}`.
- Rows: {len(base)} candidates; dates {base['target_date'].min()}..{base['target_date'].max()}; cities {base['city'].nunique()}.
- Snapshot files: `{SNAPSHOT_DIR.relative_to(ROOT)}`; path rows with future quote {leakage_checks['future_path_rows']}/{len(base)}, with future bid {leakage_checks['future_bid_path_rows']}/{len(base)}.
- Path events found: TP20 rows {leakage_checks['first_tp20_rows']}, time-stop rows {leakage_checks['time_stop_rows']}, late-salvage rows {leakage_checks['late_salvage_rows']}.
- This replay uses only snapshots after `decision_snapshot_ts_utc` for exits. It does not change the entry selector.

## Main Same-Denominator Results

Full window:

{table(full, focus_pairs)}

Recent window (`target_date >= 2026-06-21`):

{table(recent, focus_pairs)}

Closed forward (`2026-06-27..2026-06-30`):

{table(forward, focus_pairs)}

## TP20 Replay Mismatch

| policy | ROI | TP hit | note |
| --- | ---: | ---: | --- |
| hold, current $0.80 cash sizing | {fmt_pct(current_hold.get('roi'))} | {fmt_pct(current_hold.get('tp_hit_rate'), signed=False)} | settlement-only baseline |
| old-style TP20 credited at max future bid | {fmt_pct(backtest.get('roi'))} | {fmt_pct(backtest.get('tp_hit_rate'), signed=False)} | optimistic versus pre-posted SELL @0.20 |
| live-like TP20 fixed sell at 20c | {fmt_pct(live_like.get('roi'))} | {fmt_pct(live_like.get('tp_hit_rate'), signed=False)} | closer to current live overlay |
| live-like TP20 plus simple stop/salvage | {fmt_pct(stop_like.get('roi'))} | {fmt_pct(stop_like.get('tp_hit_rate'), signed=False)} | tests whether stops rescue losers |
| hold plus simple stop/salvage, no TP20 | {fmt_pct(stop_no_tp.get('roi'))} | {fmt_pct(stop_no_tp.get('tp_hit_rate'), signed=False)} | tests your proposed conditional stop without capping winners |

The important point is not the exact point estimate. It is that `TP20 max-bid` and `SELL @0.20` are different execution products. The first is a path-trading oracle unless the live monitor cancels/requotes upward quickly enough. The current live behavior is closer to the fixed-20c line.

## Sizing Read

Fixed cash spends the same dollar amount on 5c and 15c tickets, which means the cheaper and usually farther-tail ticket gets more shares. Fixed-share sizing is closer to the original research thesis: each row gets similar maximum payout, and cash at risk rises with price/confidence. Edge/pcal-scaled fixed shares are directionally sensible, but this replay alone does not confirm them for live because they add score degrees of freedom.

## Stops Read

The tested stops are deliberately simple:

- `time_stop`: two hours after forecast peak, if the ticket never pumped to 15c and current bid is at least 3c, sell at bid minus 1c.
- `late_salvage`: inside the last three hours before settlement, if no TP20 happened and bid is at least 2c, sell at bid minus 1c.

If the combined stop line does not materially improve the live-like TP20 line, then stop-loss is mostly psychological comfort and spread leakage. If it improves drawdown without killing forward ROI, it is a candidate for shadow telemetry before live.

## Today Check

The live 2026-07-03 miss is not impossible under the historical distribution: final win rate is low and zero-win days exist. But today's experience is still useful because it exposed three concrete gaps:

1. Current live sizing is `fixed_cash_0p80`; the research champion was closer to fixed payout/shares.
2. Current TP20 pre-posts at 20c, while the first TP replay headline used max future bid after touch.
3. 2026-07-04 candidates are currently blocked by token resolution, so "not buying the lottery" can be a plumbing failure, not an alpha decision.

## Artifacts

- Script: `{Path(__file__).relative_to(ROOT)}`
- Replay rows: `{(OUT_DIR / 'replay_rows.csv').relative_to(ROOT)}`
- Summary: `{(OUT_DIR / 'summary.csv').relative_to(ROOT)}`
- JSON: `{OUT_JSON.relative_to(ROOT)}`
"""


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    base = load_base()
    paths = scan_paths(base)
    frame = base.merge(paths, on="row_id", how="left")
    replay = apply_replay(frame)
    dates = replay["target_date"].astype(str)
    periods = {
        "full": pd.Series(True, index=replay.index),
        "train_le_2026_06_20": dates <= "2026-06-20",
        "recent_ge_2026_06_21": dates >= "2026-06-21",
        "closed_forward_2026_06_27_30": (dates >= "2026-06-27") & (dates <= "2026-06-30"),
    }
    summary = pd.concat([summarize(replay, name, mask) for name, mask in periods.items()], ignore_index=True)

    replay.to_csv(OUT_DIR / "replay_rows.csv", index=False)
    summary.to_csv(OUT_DIR / "summary.csv", index=False)
    paths.to_csv(OUT_DIR / "paths.csv", index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "input": str(INPUT.relative_to(ROOT)),
        "rows": int(len(base)),
        "date_min": str(base["target_date"].min()),
        "date_max": str(base["target_date"].max()),
        "cities": int(base["city"].nunique()),
        "summary": json.loads(summary.to_json(orient="records")),
        "path_coverage": {
            "future_quote_rows": int((paths["future_quotes"] > 0).sum()),
            "future_bid_rows": int((paths["future_bid_quotes"] > 0).sum()),
            "tp20_rows": int((paths["first_tp20_ts_utc"].astype(str) != "").sum()),
            "tp30_rows": int((paths["first_tp30_ts_utc"].astype(str) != "").sum()),
            "time_stop_rows": int((paths["time_stop_ts_utc"].astype(str) != "").sum()),
            "late_salvage_rows": int((paths["late_salvage_ts_utc"].astype(str) != "").sum()),
        },
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    OUT_MD.write_text(make_markdown(summary, replay, base, paths), encoding="utf-8")
    print(f"wrote {OUT_MD.relative_to(ROOT)}")
    print(f"wrote {OUT_JSON.relative_to(ROOT)}")
    print(f"wrote {(OUT_DIR / 'summary.csv').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
