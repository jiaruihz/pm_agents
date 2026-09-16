#!/usr/bin/env python3
"""Review regime-routed NO date-lineage and route-validity fixes.

This script compares the historical regime-routed research sample before and
after fixing the current-NO route definition.  The market-date mix bug is a
live candidate construction lineage bug, so it is audited from runtime
candidate logs instead of the already city/date/hour-normalized historical
atlas.
"""

from __future__ import annotations

import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
ANALYSIS_DIR = ROOT / "scripts/analysis/reheat_risk"
if str(ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(ANALYSIS_DIR))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import research_regime_routed_no_expression_v1 as research  # noqa: E402
from scripts.ops import regime_routed_no_tiny_live as live  # noqa: E402
from weather_data_feed.production_paths import historical_strategy_snapshots  # noqa: E402

OUT_DIR = ROOT / "docs/analysis/2026-06/generated/regime_routed_no_guard_patch_20260628"
OUT_JSON = OUT_DIR / "summary.json"
OUT_HIST = OUT_DIR / "historical_before_after.csv"
OUT_DAILY = OUT_DIR / "historical_daily_before_after.csv"
OUT_LIVE_AUDIT = OUT_DIR / "live_runtime_guard_audit.csv"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-28-regime-routed-no-guard-patch-review.md"

SELECTED = ROOT / "docs/analysis/2026-06/generated/regime_routed_no_expression_v1/selected_trade_details.csv"
LIVE_RUNTIME = ROOT / "runtime/weather_edge_v1/remote_pm_agent/regime_routed_no_tiny_live_v1"
SNAPSHOT_DIR = historical_strategy_snapshots()
MAIN_VARIANT = "routed_capped_d2_no_relaxed70_best_ask"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pct(value: Any) -> str:
    if value is None:
        return "NA"
    try:
        val = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(val):
        return "NA"
    return f"{100.0 * val:+.1f}%"


def finite(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [finite(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    return value


def date_bootstrap_roi(frame: pd.DataFrame, *, cost_col: str, pnl_col: str, n: int = 5000, seed: int = 20260628) -> tuple[float | None, float | None]:
    clean = frame[[cost_col, pnl_col, "target_date"]].dropna().copy()
    if clean.empty or clean["target_date"].nunique() < 3:
        return None, None
    daily = clean.groupby("target_date", as_index=False).agg(cost=(cost_col, "sum"), pnl=(pnl_col, "sum"))
    rng = np.random.default_rng(seed)
    vals = []
    dates = daily["target_date"].to_numpy()
    for _ in range(n):
        draw = rng.choice(dates, size=len(dates), replace=True)
        sample = daily.set_index("target_date").loc[draw]
        cost = float(sample["cost"].sum())
        pnl = float(sample["pnl"].sum())
        if cost:
            vals.append(pnl / cost)
    if not vals:
        return None, None
    return float(np.quantile(vals, 0.025)), float(np.quantile(vals, 0.975))


def summarize(frame: pd.DataFrame, name: str) -> dict[str, Any]:
    if frame.empty:
        return {
            "slice": name,
            "rows": 0,
            "dates": 0,
            "cities": 0,
            "wins": 0,
            "win_rate": None,
            "cost_usd": 0.0,
            "pnl_usd": 0.0,
            "roi": None,
            "weighted_cost_usd": 0.0,
            "weighted_pnl_usd": 0.0,
            "weighted_roi": None,
            "weighted_roi_ci_low": None,
            "weighted_roi_ci_high": None,
        }
    cost = float(frame["stake_cost_usd"].sum())
    pnl = float(frame["stake_profit_usd"].sum())
    weight = pd.to_numeric(frame["soft_balanced"], errors="coerce").fillna(1.0)
    weighted_cost = float((frame["stake_cost_usd"] * weight).sum())
    weighted_pnl = float((frame["stake_profit_usd"] * weight).sum())
    tmp = frame.assign(weighted_cost_usd=frame["stake_cost_usd"] * weight, weighted_profit_usd=frame["stake_profit_usd"] * weight)
    ci_low, ci_high = date_bootstrap_roi(tmp, cost_col="weighted_cost_usd", pnl_col="weighted_profit_usd")
    return {
        "slice": name,
        "rows": int(len(frame)),
        "dates": int(frame["target_date"].nunique()),
        "cities": int(frame["city"].nunique()),
        "wins": int(pd.to_numeric(frame["payoff"], errors="coerce").sum()),
        "win_rate": float(pd.to_numeric(frame["payoff"], errors="coerce").mean()),
        "cost_usd": cost,
        "pnl_usd": pnl,
        "roi": pnl / cost if cost else None,
        "weighted_cost_usd": weighted_cost,
        "weighted_pnl_usd": weighted_pnl,
        "weighted_roi": weighted_pnl / weighted_cost if weighted_cost else None,
        "weighted_roi_ci_low": ci_low,
        "weighted_roi_ci_high": ci_high,
    }


def add_guard_flags(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out = research.add_soft_weights(out)
    current_route = out["route_leg"].astype(str).eq("runway_current_no") | out["expression"].astype(str).eq("current_bracket_no")
    out["current_no_route_valid"] = (~current_route) | (
        out["running_max_state"].astype(str).eq("fresh_running_high")
        & out["intraday_state"].astype(str).isin(["active_warming", "fresh_high"])
    )
    out["route_invalid_reason"] = np.where(out["current_no_route_valid"], "", "current_no_state_not_runway")
    return out


def historical_review() -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    df = pd.read_csv(SELECTED, low_memory=False)
    main = df[df["variant"].eq(MAIN_VARIANT)].copy()
    main = add_guard_flags(main)
    before = main.copy()
    after = main[main["current_no_route_valid"]].copy()
    removed = main[~main["current_no_route_valid"]].copy()
    rows = [
        summarize(before, "historical_main_before_guard"),
        summarize(before, "historical_main_after_date_lineage_patch"),
        summarize(after, "historical_main_after_current_no_route_validity_fix"),
        summarize(removed, "historical_removed_by_current_no_route_validity_fix"),
    ]
    for route, group in main.groupby("route_leg", dropna=False):
        rows.append(summarize(group, f"route_before::{route}"))
        rows.append(summarize(group[group["current_no_route_valid"]], f"route_after::{route}"))
        rows.append(summarize(group[~group["current_no_route_valid"]], f"route_removed::{route}"))

    daily_rows = []
    for label, frame in [("before", before), ("after", after), ("removed", removed)]:
        for date, group in frame.groupby("target_date"):
            row = summarize(group, f"daily::{label}")
            row["target_date"] = date
            row["guard_slice"] = label
            daily_rows.append(row)
    hist = pd.DataFrame(rows)
    daily = pd.DataFrame(daily_rows)
    payload = {
        "source_file": str(SELECTED.relative_to(ROOT)),
        "variant": MAIN_VARIANT,
        "date_min": str(main["target_date"].min()),
        "date_max": str(main["target_date"].max()),
        "dates": int(main["target_date"].nunique()),
        "cities": int(main["city"].nunique()),
        "rows": int(len(main)),
        "date_lineage_patch_historical_rows_changed": 0,
        "date_lineage_patch_historical_note": "No historical A/B change: selected_trade_details is already normalized to city/date/hour, so the live date-lineage bug is only measurable from runtime candidate logs.",
        "current_no_route_validity_removed_rows": int((~main["current_no_route_valid"]).sum()),
        "current_no_route_validity_removed_dates": int(main.loc[~main["current_no_route_valid"], "target_date"].nunique()),
        "current_no_route_validity_removed_cities": int(main.loc[~main["current_no_route_valid"], "city"].nunique()),
        "removed_state_counts": (
            main.loc[~main["current_no_route_valid"]]
            .groupby(["running_max_state", "intraday_state"], dropna=False)
            .size()
            .sort_values(ascending=False)
            .head(20)
            .reset_index(name="rows")
            .to_dict("records")
        ),
    }
    return hist, daily, payload


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def live_runtime_audit() -> tuple[pd.DataFrame, dict[str, Any]]:
    rows = read_jsonl(LIVE_RUNTIME / "blocked_candidates.jsonl") + read_jsonl(LIVE_RUNTIME / "live_orders.jsonl")
    if not rows:
        return pd.DataFrame(), {"rows": 0}
    frame = pd.DataFrame(rows)
    for col in ["city", "target_date", "event_slug", "question", "route_leg", "expression", "running_max_state", "intraday_state"]:
        if col not in frame:
            frame[col] = ""
    frame["market_event_date_reparsed"] = frame.apply(live.market_event_date_for_record, axis=1)
    frame["market_date_mismatch_reparsed"] = (
        frame["target_date"].astype(str).ne("")
        & frame["market_event_date_reparsed"].astype(str).ne("")
        & frame["target_date"].astype(str).ne(frame["market_event_date_reparsed"].astype(str))
    )
    frame["current_no_route_valid_reparsed"] = frame.apply(live.current_no_runway_state_ok, axis=1)
    frame["created_date_utc"] = frame.get("created_at_utc", pd.Series("", index=frame.index)).astype(str).str.slice(0, 10)
    recent = frame[frame["created_date_utc"].ge("2026-06-27")].copy()
    key_cols = ["created_date_utc", "city", "target_date", "event_slug", "bracket", "route_leg"]
    existing = [c for c in key_cols if c in recent.columns]
    unique_recent = recent.drop_duplicates(existing) if existing else recent
    audit = {
        "runtime_dir": str(LIVE_RUNTIME.relative_to(ROOT)),
        "raw_rows": int(len(frame)),
        "recent_rows_since_2026_06_27": int(len(recent)),
        "recent_unique_candidates": int(len(unique_recent)),
        "recent_market_date_mismatch_unique": int(unique_recent["market_date_mismatch_reparsed"].sum()) if len(unique_recent) else 0,
        "recent_current_no_route_invalid_unique": int((~unique_recent["current_no_route_valid_reparsed"].astype(bool)).sum()) if len(unique_recent) else 0,
        "recent_mismatch_by_city": dict(Counter(unique_recent.loc[unique_recent["market_date_mismatch_reparsed"], "city"].astype(str))),
        "recent_current_no_route_invalid_by_city": dict(
            Counter(unique_recent.loc[~unique_recent["current_no_route_valid_reparsed"].astype(bool), "city"].astype(str))
        ),
    }
    cols = [
        "created_at_utc",
        "city",
        "target_date",
        "event_slug",
        "question",
        "bracket",
        "route_leg",
        "execution_skip_reason",
        "running_max_state",
        "intraday_state",
        "minutes_since_running_max",
        "market_event_date_reparsed",
        "market_date_mismatch_reparsed",
        "current_no_route_valid_reparsed",
    ]
    cols = [c for c in cols if c in unique_recent.columns]
    return unique_recent[cols].sort_values(cols[:3]), audit


def raw_snapshot_lineage_sanity(limit: int = 30) -> dict[str, Any]:
    paths = sorted(SNAPSHOT_DIR.glob("snapshot_2026062*.json"))[-limit:]
    raw_rows = 0
    raw_row_internal_date_mismatch = 0
    snapshots_with_city_multi_target = 0
    mixed_city_groups = 0
    examples: list[dict[str, Any]] = []
    for path in paths:
        try:
            _, records = live.load_snapshot(path)
        except Exception:
            continue
        if records.empty:
            continue
        records = records.copy()
        records["market_event_date_reparsed"] = records.apply(live.market_event_date_for_record, axis=1)
        bad = records[
            records["market_event_date_reparsed"].astype(str).ne("")
            & records["market_event_date_reparsed"].astype(str).ne(records["target_date"].astype(str))
        ]
        raw_rows += int(len(records))
        raw_row_internal_date_mismatch += int(len(bad))
        mixed_in_snapshot = 0
        for city, sub in records.groupby("city"):
            target_dates = sorted(sub["target_date"].astype(str).dropna().unique().tolist())
            if len(target_dates) > 1:
                mixed_in_snapshot += 1
                if len(examples) < 8:
                    examples.append({"snapshot": path.name, "city": str(city), "target_dates": target_dates})
        if mixed_in_snapshot:
            snapshots_with_city_multi_target += 1
            mixed_city_groups += mixed_in_snapshot
    return {
        "snapshot_dir": str(SNAPSHOT_DIR.relative_to(ROOT)),
        "snapshots_checked": int(len(paths)),
        "raw_rows": raw_rows,
        "raw_row_internal_date_mismatch": raw_row_internal_date_mismatch,
        "snapshots_with_city_multi_target": snapshots_with_city_multi_target,
        "mixed_city_groups": mixed_city_groups,
        "examples": examples,
    }


def write_report(payload: dict[str, Any], hist: pd.DataFrame, live_audit: pd.DataFrame) -> None:
    rows = {row["slice"]: row for row in payload["historical_rows"]}

    def row_line(key: str) -> str:
        row = rows[key]
        return (
            f"| {key} | {row['rows']} | {row['dates']} | {row['cities']} | {row['wins']} | "
            f"{pct(row['win_rate'])} | {pct(row['roi'])} | {pct(row['weighted_roi'])} | "
            f"[{pct(row['weighted_roi_ci_low'])}, {pct(row['weighted_roi_ci_high'])}] |"
        )

    lines = [
        "# Regime-Routed NO Date-Lineage And Route-Validity Review",
        "",
        "## Data Snapshot",
        "",
        f"- Generated UTC: `{payload['generated_at_utc']}`.",
        f"- Historical research file: `{payload['historical']['source_file']}`.",
        f"- Historical range: `{payload['historical']['date_min']}`..`{payload['historical']['date_max']}`; dates={payload['historical']['dates']}; cities={payload['historical']['cities']}; rows={payload['historical']['rows']}.",
        f"- Live runtime audit source: `{payload['live_runtime']['runtime_dir']}`; recent window starts `2026-06-27`.",
        "",
        "## Code Review Result",
        "",
        "- Confirmed bug: raw snapshot market rows are internally date-consistent, but the live runner grouped rows by city only, so a city with today and tomorrow markets in one snapshot could build a candidate using today's observation state and a tomorrow orderbook row.",
        f"- Recent raw snapshot sanity: checked `{payload['raw_snapshot_sanity']['snapshots_checked']}` snapshots / `{payload['raw_snapshot_sanity']['raw_rows']}` rows; raw row internal date mismatches=`{payload['raw_snapshot_sanity']['raw_row_internal_date_mismatch']}`, but city-level multi-target groups=`{payload['raw_snapshot_sanity']['mixed_city_groups']}` across `{payload['raw_snapshot_sanity']['snapshots_with_city_multi_target']}` snapshots.",
        "- Confirmed strategy bug: `day_open_runway/day_marginal_runway` are day-level forecast-space labels and can be stale at the current observation level. The current-NO route definition is now fixed so stale/fade states do not produce `runway_current_no` candidates.",
        "- Confirmed lineage gap: historical executor `live_orders.jsonl` rows did not preserve `route_leg/expression`, so filled-order guard counterfactuals must be reconstructed from candidate/plan logs. New plans should carry those route fields.",
        "- Operational patch: the shell loop now logs non-zero runner exit codes instead of swallowing them with bare `|| true`. That is telemetry hardening, not an alpha change.",
        "",
        "## Historical A/B",
        "",
        "| slice | rows | dates | cities | wins | win_rate | exec_roi | weighted_roi | weighted_roi_95ci |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        row_line("historical_main_before_guard"),
        row_line("historical_main_after_date_lineage_patch"),
        row_line("historical_main_after_current_no_route_validity_fix"),
        row_line("historical_removed_by_current_no_route_validity_fix"),
        "",
        "## Interpretation",
        "",
        "- Correct date-lineage-only conclusion: historical backtest numbers do not change. The historical selected file is already city/date/hour normalized, so the live city-only grouping bug is not represented as a historical PnL row to remove.",
        "- Correct route-validity conclusion: fixing the current-NO route definition changes the historical expression. It removes stale/fade current-NO rows and lowers the historical point estimate because some of those invalid-shape rows happened to win in this short sample.",
        "- The market-date lineage fix has no meaningful historical A/B on this file because the file is already normalized to city/date/hour; its evidence is the live runtime audit of candidate-level row mixing, not broken raw market rows.",
        "- Combined conclusion: date-lineage fix improves live candidate safety but does not change historical PnL; route-validity fix changes the strategy expression and makes historical evidence weaker, not stronger.",
        "",
        "## Live Runtime Audit",
        "",
        f"- Recent unique runtime candidates since 2026-06-27: `{payload['live_runtime']['recent_unique_candidates']}`.",
        f"- Candidate-level market-date mismatches caused by city-only row mixing that the patch would block: `{payload['live_runtime']['recent_market_date_mismatch_unique']}`; by city: `{payload['live_runtime']['recent_mismatch_by_city']}`.",
        f"- Current-NO route-invalid runtime candidates that the route fix would not produce: `{payload['live_runtime']['recent_current_no_route_invalid_unique']}`; by city: `{payload['live_runtime']['recent_current_no_route_invalid_by_city']}`.",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    hist, daily, historical_payload = historical_review()
    live_audit, live_payload = live_runtime_audit()
    raw_snapshot_payload = raw_snapshot_lineage_sanity()
    hist.to_csv(OUT_HIST, index=False)
    daily.to_csv(OUT_DAILY, index=False)
    live_audit.to_csv(OUT_LIVE_AUDIT, index=False)
    payload = {
        "generated_at_utc": now_utc(),
        "historical": historical_payload,
        "raw_snapshot_sanity": raw_snapshot_payload,
        "historical_rows": hist.to_dict("records"),
        "live_runtime": live_payload,
        "outputs": {
            "historical_before_after": str(OUT_HIST.relative_to(ROOT)),
            "historical_daily_before_after": str(OUT_DAILY.relative_to(ROOT)),
            "live_runtime_guard_audit": str(OUT_LIVE_AUDIT.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
    }
    OUT_JSON.write_text(json.dumps(finite(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_report(finite(payload), hist, live_audit)
    print(json.dumps(finite(payload), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
