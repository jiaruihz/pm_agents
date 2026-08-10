"""Queue-conservative passive-fill replay for weather WebSocket tape.

This module is an execution-evidence companion to forecast repricing.  It
does not infer our fill from a quote touch.  A hypothetical best-bid order is
only marked filled after exchange-reported SELL volume at or below the quote
exceeds the displayed queue ahead plus our requested shares.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import csv
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import heapq
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from weather_data_feed.ws_incremental_book import (
    BookReconstructionError,
    IncrementalBookReconstructor,
    canonical_ws_frame_id,
    extract_market_trade_prints,
)


SCHEMA_VERSION = "forecast_repricing_tape_execution_v1"
HOLD_SECONDS = 60.0
POST_TTL_SECONDS = 60.0
SHARES = 5.0


def weather_fee_per_share(price: float) -> float:
    return 0.05 * float(price) * (1.0 - float(price))


def _timestamp(value: str) -> float:
    from datetime import datetime

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.timestamp()


def _receive_order(frame: Mapping[str, Any]) -> tuple[int, str]:
    value = frame.get("received_at_ns")
    if value is not None:
        return int(value), canonical_ws_frame_id(frame)
    return int(_timestamp(str(frame["received_at_utc"])) * 1_000_000_000), canonical_ws_frame_id(frame)


def _sell_value(snapshot: Any, shares: float) -> tuple[float | None, float]:
    remaining = float(shares)
    gross = 0.0
    fee = 0.0
    for price, size in snapshot.bids:
        take = min(remaining, float(size))
        gross += take * float(price)
        fee += take * weather_fee_per_share(float(price))
        remaining -= take
        if remaining <= 1e-12:
            return gross, fee
    return None, 0.0


@dataclass
class PassiveOrder:
    order_id: str
    token_id: str
    condition_id: str | None
    city: str | None
    target_date: str | None
    bracket: str | None
    outcome: str | None
    posted_at_utc: str
    posted_at_ts: float
    entry_bid: float
    entry_ask: float
    spread: float
    queue_ahead_shares: float
    entry_bid_depth: float
    entry_ask_depth: float
    feature_book_snapshot_id: str
    selector_version: str | None
    subscription_epoch_id: str
    recent_buy_volume_30s: float
    recent_sell_volume_30s: float
    sell_volume_at_or_below_quote: float = 0.0
    fill_trade_print_id: str | None = None
    filled_at_utc: str | None = None
    filled_at_ts: float | None = None
    exit_at_utc: str | None = None
    exit_book_snapshot_id: str | None = None
    exit_gross_proceeds: float | None = None
    exit_fee: float | None = None
    pnl: float | None = None
    roi: float | None = None
    adverse_cancel_at_utc: str | None = None
    adverse_cancel_reason: str | None = None
    dynamic_exit_at_utc: str | None = None
    dynamic_exit_book_snapshot_id: str | None = None
    dynamic_exit_gross_proceeds: float | None = None
    dynamic_exit_fee: float | None = None
    dynamic_pnl: float | None = None
    dynamic_roi: float | None = None
    status: str = "waiting_fill"
    coverage_blocker: str | None = None
    generation: int = 0

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["schema_version"] = SCHEMA_VERSION
        return row


def _message_token_ids(message: Any) -> set[str]:
    messages = message if isinstance(message, list) else [message]
    output: set[str] = set()
    for row in messages:
        if not isinstance(row, Mapping):
            continue
        token = row.get("asset_id")
        if token:
            output.add(str(token))
        for change in row.get("price_changes") or ():
            if isinstance(change, Mapping) and change.get("asset_id"):
                output.add(str(change["asset_id"]))
    return output


def replay_passive_orders(
    subscription_epochs: Sequence[Mapping[str, Any]],
    frames: Iterable[Mapping[str, Any]],
    *,
    shares: float = SHARES,
    post_ttl_seconds: float = POST_TTL_SECONDS,
    hold_seconds: float = HOLD_SECONDS,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Replay one non-overlapping passive order per subscribed YES token."""

    epochs = {
        str(row.get("subscription_epoch_id") or ""): dict(row)
        for row in subscription_epochs
        if row.get("subscription_epoch_id")
    }
    engine = IncrementalBookReconstructor(strict_best_parity=True)
    active_epoch_id: str | None = None
    active: dict[str, PassiveOrder] = {}
    completed: list[PassiveOrder] = []
    deadlines: list[tuple[float, str, int]] = []
    last_post_at: dict[str, float] = {}
    seen_trade_ids: set[str] = set()
    recent_tape: dict[str, list[tuple[float, str, float]]] = {}
    counters = {
        "input_frames": 0,
        "duplicate_frames": 0,
        "reconstruction_errors": 0,
        "trade_prints": 0,
        "sell_trade_prints": 0,
        "orders_posted": 0,
        "queue_conservative_fills": 0,
        "exit_scoreable": 0,
        "coverage_blocked": 0,
    }

    def finish(order: PassiveOrder, *, status: str, blocker: str | None = None) -> None:
        if active.get(order.token_id) is not order:
            return
        order.status = status
        order.coverage_blocker = blocker
        active.pop(order.token_id, None)
        completed.append(order)
        if blocker:
            counters["coverage_blocked"] += 1

    def close_for_epoch(next_epoch: Mapping[str, Any], now_utc: str) -> None:
        next_tokens = {str(value) for value in next_epoch.get("token_ids") or ()}
        carry = (
            str(next_epoch.get("reason") or "") == "selector_reconcile"
            and str(next_epoch.get("previous_subscription_epoch_id") or "") == active_epoch_id
        )
        for order in list(active.values()):
            if carry and order.token_id in next_tokens:
                continue
            finish(order, status="coverage_blocked", blocker="subscription_or_connection_ended")
        engine.activate_epoch(
            str(next_epoch["subscription_epoch_id"]),
            next_epoch.get("token_ids") or (),
            carry_forward=bool(carry and active_epoch_id is not None),
            producer_build_id=(
                str(next_epoch.get("producer_build_id"))
                if next_epoch.get("producer_build_id")
                else None
            ),
            selector_version=(
                str(next_epoch.get("selector_version"))
                if next_epoch.get("selector_version")
                else None
            ),
            capture_policy=next_epoch.get("capture_policy") or {},
            token_rows=next_epoch.get("token_rows") or {},
        )

    def finalize_due(now_ts: float, now_utc: str) -> None:
        while deadlines and deadlines[0][0] <= now_ts:
            _, token_id, generation = heapq.heappop(deadlines)
            order = active.get(token_id)
            if order is None or order.generation != generation:
                continue
            if order.status == "waiting_fill":
                finish(order, status="expired_unfilled")
                continue
            try:
                snapshot = engine.snapshot(
                    token_id,
                    observed_at_utc=now_utc,
                    requested_shares=shares,
                    book_role="execution_quote",
                )
            except BookReconstructionError:
                finish(order, status="coverage_blocked", blocker="exit_book_unavailable")
                continue
            gross, fee = _sell_value(snapshot, shares)
            if gross is None:
                finish(order, status="coverage_blocked", blocker="exit_bid_depth_below_shares")
                continue
            order.exit_at_utc = now_utc
            order.exit_book_snapshot_id = snapshot.execution_book_snapshot_id
            order.exit_gross_proceeds = gross
            order.exit_fee = fee
            cost = shares * order.entry_bid
            order.pnl = gross - fee - cost
            order.roi = order.pnl / cost if cost > 0 else None
            counters["exit_scoreable"] += 1
            finish(order, status="filled_exit_scoreable")

    for raw_frame in frames:
        frame = dict(raw_frame)
        counters["input_frames"] += 1
        epoch_id = str(frame.get("subscription_epoch_id") or "")
        epoch = epochs.get(epoch_id)
        if epoch is None:
            continue
        now_utc = str(frame.get("received_at_utc") or "")
        if not now_utc:
            continue
        now_ts = _timestamp(now_utc)
        if epoch_id != active_epoch_id:
            close_for_epoch(epoch, now_utc)
            active_epoch_id = epoch_id

        for trade in extract_market_trade_prints(frame):
            if trade.trade_print_id in seen_trade_ids:
                continue
            seen_trade_ids.add(trade.trade_print_id)
            counters["trade_prints"] += 1
            if trade.side == "SELL":
                counters["sell_trade_prints"] += 1
            recent_tape.setdefault(trade.token_id, []).append(
                (now_ts, trade.side, float(trade.size))
            )
            order = active.get(trade.token_id)
            if (
                order is not None
                and order.status == "waiting_fill"
                and trade.side == "SELL"
                and trade.price <= order.entry_bid + 1e-12
            ):
                order.sell_volume_at_or_below_quote += trade.size
                if order.sell_volume_at_or_below_quote + 1e-12 >= order.queue_ahead_shares + shares:
                    order.status = "filled_waiting_exit"
                    order.fill_trade_print_id = trade.trade_print_id
                    order.filled_at_utc = trade.received_at_utc
                    order.filled_at_ts = now_ts
                    order.generation += 1
                    heapq.heappush(
                        deadlines,
                        (now_ts + hold_seconds, order.token_id, order.generation),
                    )
                    counters["queue_conservative_fills"] += 1

        applied_before = engine.applied_frame_count
        try:
            updated = engine.apply_envelope(frame)
        except BookReconstructionError:
            counters["reconstruction_errors"] += 1
            finalize_due(now_ts, now_utc)
            continue
        if engine.applied_frame_count == applied_before:
            counters["duplicate_frames"] += 1
            finalize_due(now_ts, now_utc)
            continue
        epoch_rows = epoch.get("token_rows") or {}
        for token_id in updated:
            existing = active.get(token_id)
            if existing is not None:
                try:
                    live_snapshot = engine.snapshot(
                        token_id,
                        observed_at_utc=now_utc,
                        requested_shares=shares,
                        book_role="execution_quote",
                    )
                except BookReconstructionError:
                    continue
                if existing.status == "waiting_fill" and existing.adverse_cancel_at_utc is None:
                    if live_snapshot.best_bid is None or live_snapshot.best_bid < existing.entry_bid - 1e-12:
                        existing.adverse_cancel_at_utc = now_utc
                        existing.adverse_cancel_reason = "best_bid_below_posted_quote"
                    elif live_snapshot.best_ask is not None and live_snapshot.best_ask <= existing.entry_bid + 1e-12:
                        existing.adverse_cancel_at_utc = now_utc
                        existing.adverse_cancel_reason = "best_ask_crossed_posted_quote"
                elif (
                    existing.status == "filled_waiting_exit"
                    and existing.dynamic_exit_at_utc is None
                ):
                    gross, fee = _sell_value(live_snapshot, shares)
                    if gross is not None:
                        cost = shares * existing.entry_bid
                        pnl = gross - fee - cost
                        if pnl >= -1e-12:
                            existing.dynamic_exit_at_utc = now_utc
                            existing.dynamic_exit_book_snapshot_id = (
                                live_snapshot.execution_book_snapshot_id
                            )
                            existing.dynamic_exit_gross_proceeds = gross
                            existing.dynamic_exit_fee = fee
                            existing.dynamic_pnl = pnl
                            existing.dynamic_roi = pnl / cost if cost > 0 else None
                continue
            metadata = epoch_rows.get(token_id) or {}
            if str(metadata.get("outcome") or "").lower() != "yes":
                continue
            if now_ts - last_post_at.get(token_id, float("-inf")) < post_ttl_seconds:
                continue
            try:
                snapshot = engine.snapshot(
                    token_id,
                    observed_at_utc=now_utc,
                    requested_shares=shares,
                    book_role="model_feature",
                )
            except BookReconstructionError:
                continue
            if (
                snapshot.best_bid is None
                or snapshot.best_ask is None
                or snapshot.best_bid_size <= 0
                or snapshot.best_ask_size <= 0
            ):
                continue
            tape = [row for row in recent_tape.get(token_id, ()) if row[0] >= now_ts - 30.0]
            recent_tape[token_id] = tape
            buy_volume = sum(size for _, side, size in tape if side == "BUY")
            sell_volume = sum(size for _, side, size in tape if side == "SELL")
            order_id = f"{token_id}:{int(now_ts * 1_000_000)}"
            order = PassiveOrder(
                order_id=order_id,
                token_id=token_id,
                condition_id=(
                    str(metadata.get("condition_id")) if metadata.get("condition_id") else None
                ),
                city=(str(metadata.get("city")) if metadata.get("city") else None),
                target_date=(
                    str(metadata.get("event_date")) if metadata.get("event_date") else None
                ),
                bracket=(str(metadata.get("bracket")) if metadata.get("bracket") else None),
                outcome=(str(metadata.get("outcome")) if metadata.get("outcome") else None),
                posted_at_utc=now_utc,
                posted_at_ts=now_ts,
                entry_bid=float(snapshot.best_bid),
                entry_ask=float(snapshot.best_ask),
                spread=float(snapshot.best_ask - snapshot.best_bid),
                queue_ahead_shares=float(snapshot.best_bid_size),
                entry_bid_depth=float(snapshot.best_bid_size),
                entry_ask_depth=float(snapshot.best_ask_size),
                feature_book_snapshot_id=str(snapshot.feature_book_snapshot_id),
                selector_version=snapshot.selector_version,
                subscription_epoch_id=epoch_id,
                recent_buy_volume_30s=buy_volume,
                recent_sell_volume_30s=sell_volume,
            )
            active[token_id] = order
            last_post_at[token_id] = now_ts
            heapq.heappush(deadlines, (now_ts + post_ttl_seconds, token_id, order.generation))
            counters["orders_posted"] += 1
        finalize_due(now_ts, now_utc)

    for order in list(active.values()):
        finish(order, status="coverage_blocked", blocker="capture_window_ended")
    rows = [order.to_dict() for order in completed]
    counters["orders_completed"] = len(rows)
    counters["independent_target_dates"] = len(
        {row["target_date"] for row in rows if row.get("target_date")}
    )
    counters["exit_scoreable_target_dates"] = len(
        {
            row["target_date"]
            for row in rows
            if row.get("target_date") and row.get("status") == "filled_exit_scoreable"
        }
    )
    return rows, counters


def summarize_policies(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Report fixed, mechanism-led policies without holdout threshold mining."""

    definitions = {
        "all_queue_conservative": lambda row: True,
        "tight_spread_le_3c": lambda row: float(row["spread"]) <= 0.03 + 1e-12,
        "light_queue_le_25": lambda row: float(row["queue_ahead_shares"]) <= 25.0,
        "tight_and_light": lambda row: (
            float(row["spread"]) <= 0.03 + 1e-12
            and float(row["queue_ahead_shares"]) <= 25.0
        ),
        "tight_light_supportive_tape": lambda row: (
            float(row["spread"]) <= 0.03 + 1e-12
            and float(row["queue_ahead_shares"]) <= 25.0
            and float(row["recent_buy_volume_30s"]) >= float(row["recent_sell_volume_30s"])
        ),
        "adverse_cancel": lambda row: not (
            row.get("adverse_cancel_at_utc")
            and row.get("filled_at_utc")
            and str(row["adverse_cancel_at_utc"]) <= str(row["filled_at_utc"])
        ),
        "adverse_cancel_tight_light": lambda row: (
            not (
                row.get("adverse_cancel_at_utc")
                and row.get("filled_at_utc")
                and str(row["adverse_cancel_at_utc"]) <= str(row["filled_at_utc"])
            )
            and float(row["spread"]) <= 0.03 + 1e-12
            and float(row["queue_ahead_shares"]) <= 25.0
        ),
    }
    scoreable = [row for row in rows if row.get("status") == "filled_exit_scoreable"]
    output: list[dict[str, Any]] = []
    for name, selected in definitions.items():
        subset = [row for row in scoreable if selected(row)]
        for exit_policy in ("fixed60", "dynamic_first_nonnegative_else_60"):
            cost = sum(float(row["entry_bid"]) * SHARES for row in subset)
            values = [
                float(row["dynamic_pnl"])
                if exit_policy.startswith("dynamic") and row.get("dynamic_pnl") is not None
                else float(row["pnl"])
                for row in subset
            ]
            pnl = sum(values)
            output.append(
                {
                    "policy": name,
                    "exit_policy": exit_policy,
                    "fills": len(subset),
                    "dynamic_exits": sum(
                        exit_policy.startswith("dynamic") and row.get("dynamic_pnl") is not None
                        for row in subset
                    ),
                    "target_dates": len({row.get("target_date") for row in subset}),
                    "cities": len({row.get("city") for row in subset}),
                    "pnl": pnl,
                    "roi": pnl / cost if cost > 0 else None,
                    "positive_fill_rate": (
                        sum(value > 0 for value in values) / len(values) if values else None
                    ),
                }
            )
    return output


def _open_raw(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("r", encoding="utf-8", errors="replace")


def _next_raw_row(
    handle: Any, path: Path, line_number: int
) -> tuple[dict[str, Any] | None, int]:
    while True:
        line = handle.readline()
        if not line:
            return None, line_number
        line_number += 1
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(row, dict) or not row.get("received_at_utc"):
            continue
        row["_raw_path"] = str(path)
        row["_line_number"] = line_number
        return row, line_number


def _raw_order(row: Mapping[str, Any]) -> int:
    if row.get("received_at_ns") is not None:
        return int(row["received_at_ns"])
    return int(_timestamp(str(row["received_at_utc"])) * 1_000_000_000)


def merge_raw_frames(
    paths: Sequence[Path], *, start_ts: float, end_ts: float
) -> Iterable[dict[str, Any]]:
    """Chronologically merge rotated/gzipped shards without loading them in RAM."""

    handles: list[Any] = []
    lines: list[int] = []
    pending: list[tuple[int, int, dict[str, Any]]] = []
    try:
        for index, path in enumerate(paths):
            handle = _open_raw(path)
            handles.append(handle)
            row, line_number = _next_raw_row(handle, path, 0)
            lines.append(line_number)
            if row is not None:
                heapq.heappush(pending, (_raw_order(row), index, row))
        while pending:
            order, index, row = heapq.heappop(pending)
            timestamp = order / 1_000_000_000
            if start_ts <= timestamp <= end_ts:
                yield row
            next_row, line_number = _next_raw_row(
                handles[index], paths[index], lines[index]
            )
            lines[index] = line_number
            if next_row is not None:
                heapq.heappush(pending, (_raw_order(next_row), index, next_row))
    finally:
        for handle in handles:
            handle.close()


def _physical_paths(root: Path, start_utc: str, end_utc: str) -> list[Path]:
    start = datetime.fromisoformat(start_utc.replace("Z", "+00:00")).date()
    end = datetime.fromisoformat(end_utc.replace("Z", "+00:00")).date()
    output: list[Path] = []
    current = start
    while current <= end:
        output.extend(sorted((root / current.isoformat()).glob("*.jsonl*")))
        current += timedelta(days=1)
    return output


def _load_subscription_epochs(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted((root / "subscription_epochs").glob("*.jsonl")):
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                identity = str(row.get("subscription_epoch_id") or "")
                if not identity or identity in seen:
                    continue
                seen.add(identity)
                rows.append(row)
    return rows


def _write_rows(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def run_tape_research(
    *, ws_root: Path, start_utc: str, end_utc: str, output_dir: Path
) -> dict[str, Any]:
    """Run the fixed passive execution comparison and persist durable artifacts."""

    start_ts = _timestamp(start_utc)
    end_ts = _timestamp(end_utc)
    if end_ts <= start_ts:
        raise ValueError("end_utc must be after start_utc")
    paths = _physical_paths(ws_root, start_utc, end_utc)
    epochs = _load_subscription_epochs(ws_root)
    rows, replay = replay_passive_orders(
        epochs,
        merge_raw_frames(paths, start_ts=start_ts, end_ts=end_ts),
    )
    policies = summarize_policies(rows)
    by_date: list[dict[str, Any]] = []
    target_dates = sorted({row.get("target_date") for row in rows if row.get("target_date")})
    for target_date in target_dates:
        subset = [row for row in rows if row.get("target_date") == target_date]
        for policy in summarize_policies(subset):
            by_date.append({"target_date": target_date, **policy})
    filled = [row for row in rows if row.get("status") == "filled_exit_scoreable"]
    primary = next(
        row
        for row in policies
        if row["policy"] == "adverse_cancel_tight_light"
        and row["exit_policy"] == "dynamic_first_nonnegative_else_60"
    )
    status = "inconclusive_execution_evidence"
    if (
        primary["fills"] >= 30
        and primary["target_dates"] >= 5
        and primary["roi"] is not None
        and primary["roi"] > 0
    ):
        status = "shadow_candidate_execution_only"
    module_path = Path(__file__).resolve()
    summary = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "denominator_scope": {
            "physical_capture_start_utc": start_utc,
            "physical_capture_end_utc": end_utc,
            "raw_ws_paths": len(paths),
            "subscription_epochs_loaded": len(epochs),
            "universe": "one non-overlapping 60s best-bid post per reconstructable subscribed YES token",
            "fill_rule": "cumulative exchange SELL volume at/below quote >= visible queue ahead + 5 shares",
            "exit_rule": "60s after conservative fill, executable 5-share bid minus official Weather taker fee",
        },
        "signal_funnel": {
            "unit": "hypothetical passive order",
            "orders_posted": replay["orders_posted"],
            "queue_conservative_fills": replay["queue_conservative_fills"],
        },
        "evidence_funnel": {
            "unit": "raw frame / passive order",
            **replay,
            "filled_exit_scoreable_rows": len(filled),
        },
        "fixed_policy_comparison": policies,
        "target_date_slices": by_date,
        "readiness": {
            "pit_clocks": "PASS_exchange_and_receive_clocks",
            "ws_capture_policy": "PASS_policy_valid_but_selective_hot_strip",
            "incremental_book_reconstruction": "PASS_deterministic_reconstructor",
            "own_fill": "BLOCKED_no_own_order_lifecycle_queue_rule_is_conservative_counterfactual",
            "independent_target_dates": replay["exit_scoreable_target_dates"],
            "d1_forecast_candidate_overlap": "BLOCKED_current_WS_selector_is_not_D1_forecast_revision_universe",
        },
        "production": {"live_action": "none", "orders_changed": 0},
        "inputs": [str(path) for path in paths],
        "producer": {
            "entrypoint": "weather_model_evaluation.cli:forecast-repricing-tape",
            "source_path": str(module_path),
            "source_sha256": hashlib.sha256(module_path.read_bytes()).hexdigest(),
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_rows(output_dir / "passive_orders.csv", rows)
    _write_rows(output_dir / "policy_summary.csv", policies)
    _write_rows(output_dir / "target_date_policy_summary.csv", by_date)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )
    report = [
        "# Forecast repricing tape-confirmed passive execution v1",
        "",
        f"status={status}",
        "production: live_action=none; orders_changed=0",
        "",
        "## 固定口径",
        "",
        "每个已订阅 YES token 同时最多一张 60 秒 best-bid 假想挂单；只有真实 SELL tape 在该价或更低的累计成交量吃完可见 queue ahead 再加 5 股，才记保守成交。成交后 60 秒按 5 股 executable bid、官方 Weather taker fee 退出。quote touch 不算 fill。",
        "",
        "## 漏斗",
        "",
        f"- posts: {replay['orders_posted']}",
        f"- queue-conservative fills: {replay['queue_conservative_fills']}",
        f"- exit-scoreable: {replay['exit_scoreable']}",
        f"- exit-scoreable target dates: {replay['exit_scoreable_target_dates']}",
        "",
        "## 固定策略比较",
        "",
        "| policy | exit | fills | dynamic exits | dates | cities | PnL | ROI | positive fills |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in policies:
        roi = "NA" if row["roi"] is None else f"{100 * row['roi']:.2f}%"
        positive = (
            "NA"
            if row["positive_fill_rate"] is None
            else f"{100 * row['positive_fill_rate']:.1f}%"
        )
        report.append(
            f"| {row['policy']} | {row['exit_policy']} | {row['fills']} | {row['dynamic_exits']} | {row['target_dates']} | {row['cities']} | {row['pnl']:+.4f} | {roi} | {positive} |"
        )
    report.extend(
        [
            "",
            "## 证据边界",
            "",
            "这是选择性 hot-strip WS 的 execution transport 证据，不是 D-1 forecast candidate 的同分母回测；没有 own order lifecycle，保守 queue rule 仍是 counterfactual。独立 target dates 少于 5 时不得冻结盈利 gate。",
        ]
    )
    (output_dir / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    return summary
