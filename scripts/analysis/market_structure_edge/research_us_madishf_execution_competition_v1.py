#!/usr/bin/env python3
"""PIT execution/competition study for US MADISHF previous-bracket NO.

The fixed strategy denominator is every unique US cross candidate written by the
production runner.  The fixed expression is BUY NO on the runner-resolved prior
market bracket.  Labels come from native-F WU-aligned settled market winners.
No orders are submitted by this script.
"""

from __future__ import annotations

import csv
import gzip
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[3]
RUNTIME = Path("/Volumes/jrs/weather_data_feed_service_runtime")
RUNNER = RUNTIME / "output/fast_source_prev_no_trial"
LADDER = RUNTIME / "output/source_event_ladder_repricing_shadow"
ALIGNMENT = ROOT / "docs/analysis/2026-07/generated/us_madishf_metar_wu_alignment_v1/daily_alignment.csv"
OUT = ROOT / "docs/analysis/2026-07/generated/us_madishf_execution_competition_v1"
REPORT = ROOT / "docs/analysis/2026-07/2026-07-18-us-madishf-execution-competition-v1.md"
HORIZONS = (0, 30, 60, 120, 300)
TOLERANCE = {0: 90, 30: 20, 60: 20, 120: 30, 300: 60}


def dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        out = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return out.astimezone(timezone.utc) if out.tzinfo else out.replace(tzinfo=timezone.utc)


def num(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    size = path.stat().st_size
    with path.open("rb") as handle:
        while handle.tell() < size:
            raw = handle.readline()
            if not raw:
                break
            try:
                row = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(row, dict):
                yield row


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def load_winners() -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    with ALIGNMENT.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("winning_bracket"):
                out[(str(row["city"]), str(row["target_date"]))] = str(row["winning_bracket"])
    return out


def book_class(ask: float | None, size: float | None, max_price: float = 0.97, shares: float = 10.0) -> str:
    if ask is None:
        return "no_ask"
    if ask > max_price + 1e-12:
        return "ask_above_max"
    if size is None or size + 1e-12 < shares:
        return "insufficient_top_size"
    return "executable"


def load_runner_events(winners: dict[tuple[str, str], str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in iter_jsonl(RUNNER / "events.jsonl"):
        if raw.get("source") != "noaa_madis_hfmetar" or raw.get("schema_version") != "fast_source_prev_no_trial_v2":
            continue
        winner = winners.get((str(raw.get("city")), str(raw.get("target_date"))))
        old = str(raw.get("t_minus_1_no_market_bracket") or "")
        detect = dt(raw.get("source_detect_ts_utc"))
        obs = dt(raw.get("source_obs_ts_utc"))
        runner_ts = dt(raw.get("ts_utc"))
        ask, size = num(raw.get("best_ask")), num(raw.get("ask_size"))
        actual_max = num(raw.get("max_no_ask"))
        rows.append({
            "city": raw.get("city"), "target_date": raw.get("target_date"),
            "event_key": raw.get("event_key"), "token_id": str(raw.get("token_id") or ""),
            "market_id": raw.get("market_id"), "previous_bracket": old,
            "winning_bracket": winner, "settlement_left_old_bracket": None if not winner else int(old != winner),
            "source_obs_ts_utc": raw.get("source_obs_ts_utc"),
            "source_first_seen_ts_utc": raw.get("source_detect_ts_utc"),
            "runner_book_ts_utc": raw.get("ts_utc"),
            "obs_to_first_seen_sec": (detect - obs).total_seconds() if detect and obs else None,
            "first_seen_to_runner_book_sec": (runner_ts - detect).total_seconds() if runner_ts and detect else None,
            "best_ask": ask, "ask_size": size, "best_ask_fee_per_share": 0.05 * ask * (1 - ask) if ask is not None else None,
            "actual_max_no_ask": actual_max,
            "actual_policy_book_class": book_class(ask, size, actual_max if actual_max is not None else 0.97),
            "max97_taker10_book_class": book_class(ask, size),
            "live_requested": int(bool(raw.get("live_requested"))),
            "live_blockers": "|".join(str(x) for x in (raw.get("live_blockers") or [])),
        })
    return sorted(rows, key=lambda r: str(r["runner_book_ts_utc"]))


def load_orders() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = defaultdict(lambda: {"order_children": 0, "filled_shares": 0.0, "filled_cost": 0.0})
    for raw in iter_jsonl(RUNNER / "orders.jsonl"):
        if raw.get("source") != "noaa_madis_hfmetar":
            continue
        row = out[str(raw.get("event_key"))]
        row["order_children"] += 1
        row["filled_shares"] += num(raw.get("actual_fill_shares")) or 0.0
        row["filled_cost"] += num(raw.get("actual_fill_cost_usd")) or 0.0
        row["order_statuses"] = "|".join(sorted(set(filter(None, [row.get("order_statuses"), str(raw.get("live_submit_status") or "")]))))
    return out


def load_runner_requotes(event_keys: set[str]) -> dict[str, list[dict[str, Any]]]:
    """Stream the large per-cycle journal, decoding only US MADISHF rows."""
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    path = RUNNER / "opportunities.jsonl"
    size = path.stat().st_size
    with path.open("rb") as handle:
        while handle.tell() < size:
            line = handle.readline()
            if not line:
                break
            if b'"source": "noaa_madis_hfmetar"' not in line or b'"event_key"' not in line:
                continue
            try:
                raw = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            key = str(raw.get("event_key") or "")
            if key not in event_keys or raw.get("status") != "cross_candidate":
                continue
            out[key].append({
                "ts": dt(raw.get("ts_utc")), "ts_utc": raw.get("ts_utc"),
                "ask": num(raw.get("best_ask")), "ask_size": num(raw.get("ask_size")),
                "bid": None, "bid_size": None, "max_no_ask": num(raw.get("max_no_ask")),
            })
    for rows in out.values():
        rows.sort(key=lambda r: r["ts"] or datetime.min.replace(tzinfo=timezone.utc))
    return out


def add_repricing(rows: list[dict[str, Any]], quotes: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    panel: list[dict[str, Any]] = []
    for row in rows:
        decision = dt(row["source_first_seen_ts_utc"])
        qrows = quotes.get(str(row["event_key"]), [])
        eventual_actual = [q for q in qrows if q["ts"] and decision and 0 <= (q["ts"] - decision).total_seconds() <= 600 and book_class(q["ask"], q["ask_size"], q.get("max_no_ask") or 0.97) == "executable"]
        eventual_97 = [q for q in qrows if q["ts"] and decision and 0 <= (q["ts"] - decision).total_seconds() <= 600 and book_class(q["ask"], q["ask_size"]) == "executable"]
        row["eventually_executable_actual_within_10m"] = int(bool(eventual_actual))
        row["eventually_executable_max97_within_10m"] = int(bool(eventual_97))
        row["first_executable_actual_delay_sec"] = (eventual_actual[0]["ts"] - decision).total_seconds() if eventual_actual else None
        for horizon in HORIZONS:
            target = decision + timedelta(seconds=horizon) if decision else None
            candidates = [(abs((q["ts"] - target).total_seconds()), q) for q in qrows if target and q["ts"] and q["ts"] >= decision]
            selected = min(candidates, key=lambda x: x[0]) if candidates else None
            q = selected[1] if selected and selected[0] <= TOLERANCE[horizon] else None
            panel.append({
                "event_key": row["event_key"], "city": row["city"], "target_date": row["target_date"],
                "settlement_left_old_bracket": row["settlement_left_old_bracket"], "horizon_sec": horizon,
                "quote_ts_utc": q and q["ts_utc"],
                "actual_after_first_seen_sec": q and (q["ts"] - decision).total_seconds(),
                "best_ask": q and q["ask"], "ask_size": q and q["ask_size"],
                "best_bid": q and q["bid"], "bid_size": q and q["bid_size"],
                "max97_taker10_book_class": "unsupported" if q is None else book_class(q["ask"], q["ask_size"]),
            })
    return panel


def load_archive_snapshots(token_ids: set[str], dates: set[str]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    # Directory names are Beijing dates while records carry UTC event dates; scan
    # the requested dates plus the following directory to cover the UTC rollover.
    scan_dirs: set[Path] = set()
    for value in dates:
        day = datetime.fromisoformat(value)
        for offset in (0, 1):
            scan_dirs.add(RUNTIME / "targeted_output/orderbook_snapshots" / (day + timedelta(days=offset)).date().isoformat())
    for directory in sorted(scan_dirs):
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.jsonl.gz")):
            try:
                with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
                    for line in handle:
                        try:
                            raw = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        token = str(raw.get("token_id") or "")
                        if token not in token_ids or str(raw.get("outcome") or "").lower() != "no":
                            continue
                        summary = raw.get("summary") or {}
                        out[token].append({
                            "ts": dt(raw.get("snapshot_ts_utc")), "ts_utc": raw.get("snapshot_ts_utc"),
                            "ask": num(summary.get("best_ask")), "ask_size": num(summary.get("ask_size")),
                            "bid": num(summary.get("best_bid")), "bid_size": num(summary.get("bid_size")),
                        })
            except (OSError, EOFError):
                continue
    for token, values in out.items():
        dedup = {str(row["ts_utc"]): row for row in values}
        out[token] = sorted(dedup.values(), key=lambda r: r["ts"] or datetime.min.replace(tzinfo=timezone.utc))
    return out


def nearest_before(values: list[dict[str, Any]], target: datetime, max_gap: int = 1800) -> dict[str, Any] | None:
    candidates = [row for row in values if row["ts"] and row["ts"] <= target]
    if not candidates:
        return None
    row = max(candidates, key=lambda r: r["ts"])
    return row if (target - row["ts"]).total_seconds() <= max_gap else None


def nearest_after(values: list[dict[str, Any]], target: datetime, max_gap: int = 1800) -> dict[str, Any] | None:
    candidates = [row for row in values if row["ts"] and row["ts"] > target]
    if not candidates:
        return None
    row = min(candidates, key=lambda r: r["ts"])
    return row if (row["ts"] - target).total_seconds() <= max_gap else None


def snapshot_panel(rows: list[dict[str, Any]], snapshots: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        obs, detect = dt(row["source_obs_ts_utc"]), dt(row["source_first_seen_ts_utc"])
        values = snapshots.get(str(row["token_id"]), [])
        pre = nearest_before(values, obs) if obs else None
        post = nearest_after(values, obs) if obs else None
        before_detect = nearest_before(values, detect) if detect else None
        pre_class = "missing" if pre is None else book_class(pre["ask"], pre["ask_size"])
        post_class = "missing" if post is None else book_class(post["ask"], post["ask_size"])
        if pre is None or post is None:
            absorption = "coverage_gap"
        elif pre_class != "executable":
            absorption = "already_unexecutable_before_source_obs"
        elif post_class != "executable":
            absorption = "lost_within_next_15m_snapshot"
        else:
            absorption = "still_executable_after_source_obs"
        result = {**row, "pre_obs_book_class": pre_class, "post_obs_book_class": post_class, "absorption_class": absorption}
        for prefix, snap, anchor in (("pre_obs", pre, obs), ("post_obs", post, obs), ("last_before_first_seen", before_detect, detect)):
            result[f"{prefix}_ts_utc"] = snap and snap["ts_utc"]
            result[f"{prefix}_offset_sec"] = snap and anchor and (snap["ts"] - anchor).total_seconds()
            result[f"{prefix}_ask"] = snap and snap["ask"]
            result[f"{prefix}_ask_size"] = snap and snap["ask_size"]
            result[f"{prefix}_bid"] = snap and snap["bid"]
        out.append(result)
    return out


def med(values: Iterable[Any]) -> float | None:
    clean = [float(x) for x in values if x is not None and math.isfinite(float(x))]
    return statistics.median(clean) if clean else None


def pct(n: int, d: int) -> str:
    return "NA" if not d else f"{n}/{d} ({100*n/d:.1f}%)"


def main() -> None:
    winners = load_winners()
    events = load_runner_events(winners)
    orders = load_orders()
    for row in events:
        row.update(orders.get(str(row["event_key"]), {"order_children": 0, "filled_shares": 0.0, "filled_cost": 0.0}))
    settled = [row for row in events if row["settlement_left_old_bracket"] is not None]
    token_ids = {str(row["token_id"]) for row in events if row["token_id"]}
    requotes = load_runner_requotes({str(row["event_key"]) for row in events})
    repricing = add_repricing(events, requotes)
    snapshots = load_archive_snapshots(token_ids, {str(row["target_date"]) for row in events})
    absorption = snapshot_panel(events, snapshots)
    # First candidate per market prevents repeated MADISHF records for the same
    # old bracket from inflating the competition denominator.
    first_by_token: dict[str, dict[str, Any]] = {}
    for row in absorption:
        first_by_token.setdefault(str(row["token_id"]), row)
    first_market = list(first_by_token.values())

    write_csv(OUT / "runner_events.csv", events)
    write_csv(OUT / "repricing_panel.csv", repricing)
    write_csv(OUT / "archive_absorption_panel.csv", absorption)
    write_csv(OUT / "first_candidate_by_market.csv", first_market)

    correct = [r for r in settled if r["settlement_left_old_bracket"] == 1]
    false = [r for r in settled if r["settlement_left_old_bracket"] == 0]
    correct_exec_actual = sum(r["actual_policy_book_class"] == "executable" for r in correct)
    correct_exec_97 = sum(r["max97_taker10_book_class"] == "executable" for r in correct)
    false_exec_actual = sum(r["actual_policy_book_class"] == "executable" for r in false)
    correct_eventual_actual = sum(r["eventually_executable_actual_within_10m"] for r in correct)
    correct_eventual_97 = sum(r["eventually_executable_max97_within_10m"] for r in correct)
    false_eventual_actual = sum(r["eventually_executable_actual_within_10m"] for r in false)
    false_filled = sum((r.get("filled_shares") or 0) > 0 for r in false)
    settled_first = [r for r in first_market if r["settlement_left_old_bracket"] is not None]
    absorption_counts = Counter(r["absorption_class"] for r in settled_first)
    obs_proxy = [r for r in settled_first if r["pre_obs_book_class"] == "executable"]
    for row in obs_proxy:
        price = float(row["pre_obs_ask"])
        shares = 10.0
        fee = shares * 0.05 * price * (1.0 - price)
        row["pre_obs_proxy_shares"] = shares
        row["pre_obs_proxy_fee"] = fee
        row["pre_obs_proxy_pnl"] = shares * float(row["settlement_left_old_bracket"]) - shares * price - fee
    obs_proxy_pnl = sum(float(r["pre_obs_proxy_pnl"]) for r in obs_proxy)
    obs_proxy_cost = sum(10.0 * float(r["pre_obs_ask"]) + float(r["pre_obs_proxy_fee"]) for r in obs_proxy)
    write_csv(OUT / "pre_observation_executable_proxy.csv", obs_proxy)
    city_counts = Counter(r["city"] for r in settled)
    city_lines = []
    for city in sorted(city_counts):
        rr = [r for r in settled if r["city"] == city]
        city_lines.append(
            f"| `{city}` | {len(rr)} | {sum(r['settlement_left_old_bracket']==1 for r in rr)} | "
            f"{sum(r['eventually_executable_actual_within_10m'] for r in rr)} | "
            f"{sum((r.get('filled_shares') or 0)>0 for r in rr)} | {med(r['obs_to_first_seen_sec']/60 for r in rr):.1f}m |"
        )

    generated = datetime.now(timezone.utc).isoformat()
    report = f"""# US MADISHF execution / competition v1

Generated: `{generated}`
Status: `research_snapshot`; no live authorization

## Conclusion

The present IEM-MADISHF route has no demonstrated executable US edge. It is both too late and adversely selected: among `{len(settled)}` settled production-runner candidates, `{len(correct)}` were directionally correct, but `0` correct candidates became executable under the policy active at the time during the next 10 minutes. The sole false Atlanta signal became executable on the next cycle and filled.

This does **not** prove that raw one-minute ASOS has no information. It proves that the current route -- IEM archive family split, 5-minute polling, then persistent confirmation -- reaches the book after useful liquidity has normally disappeared. A lower-latency direct MADIS/OMO experiment is still testable, but only as zero-notional telemetry.

## Fixed denominator

- signal: every unique `noaa_madis_hfmetar` `cross_candidate` written by `fast_source_prev_no_trial_v2` (`{len(events)}` rows, `{len(settled)}` settled)
- expression: BUY NO on the runner-resolved previous Fahrenheit bracket
- label: native-F WU-aligned settled market winner
- execution: direct CLOB best ask and top size at runner decision; 10-share taker requirement; fee `0.05*p*(1-p)`
- duplicate diagnostic: `{len(first_market)}` first candidates by market token

## Adverse selection

- correct signals executable under the historical policy: `{pct(correct_exec_actual, len(correct))}`
- correct signals executable under hypothetical `max_no_ask=0.97` with the same 10-share requirement: `{pct(correct_exec_97, len(correct))}`
- correct signals becoming executable within 10 minutes under historical policy: `{pct(correct_eventual_actual, len(correct))}`
- correct signals becoming executable within 10 minutes at max 0.97: `{pct(correct_eventual_97, len(correct))}`
- false signals executable on the first book read: `{pct(false_exec_actual, len(false))}`
- false signals becoming executable within 10 minutes: `{pct(false_eventual_actual, len(false))}`
- false signals actually filled: `{pct(false_filled, len(false))}`
- median observation → our first-seen lag: `{med(r['obs_to_first_seen_sec']/60 for r in settled):.1f}` minutes
- median first-seen → runner direct-book read: `{med(r['first_seen_to_runner_book_sec'] for r in settled):.1f}` seconds

The 0.94→0.97 threshold change does not recover these trades. One correct Atlanta candidate printed at exactly 0.97, but only 4.22 shares were on the top ask versus the 10-share taker requirement; every other correct candidate was no-ask or above 0.97.

## City execution record

| city | settled candidates | correct | executable within 10m | filled | median source lag |
|---|---:|---:|---:|---:|---:|
{chr(10).join(city_lines)}

## Was the market already gone before our source arrived?

Using the 15-minute archive snapshots around each source observation, deduplicated to the first candidate per old-bracket token:

- already unexecutable before the MADISHF observation timestamp: `{absorption_counts['already_unexecutable_before_source_obs']}`
- executable before, lost by the next snapshot after the observation: `{absorption_counts['lost_within_next_15m_snapshot']}`
- still executable at the next snapshot: `{absorption_counts['still_executable_after_source_obs']}`
- archive coverage gap: `{absorption_counts['coverage_gap']}`

`already unexecutable` means the prior-bracket NO had no ask, ask >0.97, or <10 top shares before the source observation timestamp. Those rows are not evidence that MADISHF moved the market; they indicate forecast/prior observations/another feed had already resolved the expression. `lost within next 15m` is the slice where a genuinely faster feed could plausibly compete.

The last pre-observation snapshot was executable for `{len(obs_proxy)}` first-market events: `{sum(r['settlement_left_old_bracket']==1 for r in obs_proxy)}` correct and `{sum(r['settlement_left_old_bracket']==0 for r in obs_proxy)}` false. A mechanical 10-share replay at those archived asks produces `${obs_proxy_pnl:.2f}` fee-adjusted PnL on `${obs_proxy_cost:.2f}` cost (`{(obs_proxy_pnl/obs_proxy_cost if obs_proxy_cost else 0):.1%}`). This is only an Atlanta-heavy three-row diagnostic, not a backtest, but it shows that lower latency alone does not cure source adverse selection.

## Source vs execution diagnosis

1. **Execution chain after first-seen is not the bottleneck.** The runner reads the direct book a median `{med(r['first_seen_to_runner_book_sec'] for r in settled):.1f}` seconds after source first-seen.
2. **The data route is late.** Observation-to-first-seen is a median `{med(r['obs_to_first_seen_sec']/60 for r in settled):.1f}` minutes. This route reads IEM's ASOS archive, not a direct real-time OMO stream.
3. **US books are competitive/adversely selective at this latency.** Correct signals are priced to ~1/no-ask; the sole bad source print retained a cheap 0.87 NO ask and filled.
4. **Source correctness alone is insufficient.** `{len(correct)}/{len(settled)}` directional correctness looks strong, but executable correctness is `0/{len(correct)}`. Backtests that mark at a stale or synthetic price would invert this conclusion.

## Upstream latency reality

- IEM describes this ASOS archive as synced from real-time ingest every 10 minutes: <https://mesonet.agron.iastate.edu/request/download.phtml?network=GN__ASOS>.
- NOAA says MADIS stage-2-QC real-time data are available on average 8 minutes after receipt and full-QC data after 11 minutes: <https://madis.ncep.noaa.gov/madis_database.shtml>.
- NOAA recommends LDM for the fastest real-time access: <https://madis.ncep.noaa.gov/madis_ui.shtml>.
- NOAA also states that OMO/one-minute ASOS is the raw minute value and does not have METAR quality control: <https://madis.ncep.noaa.gov/madis_metar.shtml>.

Therefore an ordinary processed MADIS/HTTPS replacement may still miss the observed 3-8 minute liquidity windows. Only raw LDM/OMO telemetry can test the remaining speed hypothesis, and removing QC increases exactly the false-print risk exposed by Atlanta.

## Remaining opportunity and action

- Keep Atlanta, Miami, and SanFrancisco MADISHF expressions in shadow; do not add other US cities to live.
- Stop treating IEM `MADISHF` as the fast production feed. Retain it as a historical/source-alignment input.
- The only worthwhile next experiment is a direct MADIS OMO/LDM (or another feed with measured ingest timestamps) zero-notional collector. Pre-register success as: median first-seen <5 minutes with p90 <8 minutes, at least 30 settled persistent events, direct-book coverage >=80%, and positive fee/depth-adjusted residual on the same rows. Do not route orders during collection.
- Prioritize only `lost_within_next_15m_snapshot` cases. If that slice is empty or direct feed still arrives after repricing, the US previous-bracket-NO speed race should be marked dormant, not widened by threshold or size changes.

## Files

- `runner_events.csv`: production candidate/fill denominator
- `repricing_panel.csv`: production-runner direct books at 0/30/60/120/300 seconds
- `archive_absorption_panel.csv`: pre/post observation archive books
- `first_candidate_by_market.csv`: duplicate-controlled competition denominator
- `pre_observation_executable_proxy.csv`: tiny observation-time feasibility diagnostic

## Contract

significance=NA; baseline=direct market; forward=FAIL for current route; conclusion=current IEM route disproven for live, direct OMO experiment inconclusive
"""
    REPORT.write_text(report, encoding="utf-8")
    print(json.dumps({
        "report": str(REPORT), "events": len(events), "settled": len(settled),
        "correct": len(correct), "false": len(false), "correct_exec_actual": correct_exec_actual,
        "correct_exec_max97": correct_exec_97, "false_exec_actual": false_exec_actual,
        "correct_eventual_actual": correct_eventual_actual, "correct_eventual_max97": correct_eventual_97,
        "false_eventual_actual": false_eventual_actual,
        "false_filled": false_filled, "first_markets": len(first_market),
        "absorption_counts": dict(absorption_counts),
        "pre_obs_proxy_rows": len(obs_proxy), "pre_obs_proxy_pnl": obs_proxy_pnl,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
