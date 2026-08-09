"""Pure zero-notional state machine for Tokyo METAR stop/re-entry shadow.

This module has no exchange client and cannot place orders.  It separates
signal invalidation, executable sizing, net exposure, and gross turnover so a
future live implementation cannot confuse cumulative buys with open position.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal


ActionKind = Literal[
    "hold",
    "official_confirmed_hold",
    "shadow_sell",
    "shadow_sell_unexecutable",
    "shadow_rebuy",
    "shadow_rebuy_unexecutable",
]


@dataclass(frozen=True)
class TokyoStopReentryPolicy:
    reversal_threshold_c: float = 0.7
    reentry_margin_c: float = 0.7
    max_reentry_ask: float = 0.97
    min_executable_shares: float = 1.0
    max_open_shares: float = 15.0
    max_gross_bought_shares: float = 30.0
    max_stop_cycles: int = 1


@dataclass
class TokyoStopReentryState:
    target_date: str
    bracket: int
    entry_source_obs_ts_utc: str
    open_shares: float
    gross_bought_shares: float
    gross_sold_shares: float = 0.0
    stopped_shares_available_to_rebuy: float = 0.0
    stop_cycles: int = 0
    last_stop_metar_report_ts_utc: str = ""
    last_stop_source_obs_ts_utc: str = ""
    last_reentry_source_obs_ts_utc: str = ""
    official_confirmed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ShadowAction:
    kind: ActionKind
    shares: float
    reason: str
    metadata: dict[str, Any]


def _available_depth(size: float | None) -> float:
    return max(0.0, float(size or 0.0))


def evaluate_nonconfirming_metar(
    state: TokyoStopReentryState,
    policy: TokyoStopReentryPolicy,
    *,
    metar_report_ts_utc: str,
    metar_running_max_bracket: int,
    jma_peak_since_entry_c: float,
    jma_latest_c: float,
    latest_source_obs_ts_utc: str,
    best_bid: float | None,
    bid_size: float | None,
) -> ShadowAction:
    """Evaluate the first new routine METAR after entry/re-entry."""
    if metar_running_max_bracket > state.bracket:
        state.official_confirmed = True
        return ShadowAction(
            "official_confirmed_hold",
            0.0,
            "settlement_facing_running_max_left_previous_bracket",
            {"metar_running_max_bracket": metar_running_max_bracket},
        )
    if state.official_confirmed:
        return ShadowAction("hold", 0.0, "already_officially_confirmed", {})
    if metar_report_ts_utc <= state.last_stop_metar_report_ts_utc:
        return ShadowAction("hold", 0.0, "metar_report_not_new", {})
    reversal = max(0.0, float(jma_peak_since_entry_c) - float(jma_latest_c))
    if reversal + 1e-9 < policy.reversal_threshold_c:
        return ShadowAction(
            "hold",
            0.0,
            "metar_nonconfirm_without_large_jma_reversal",
            {"jma_reversal_c": reversal},
        )
    if state.stop_cycles >= policy.max_stop_cycles:
        return ShadowAction(
            "hold",
            0.0,
            "stop_cycle_cap_reached",
            {"jma_reversal_c": reversal, "stop_cycles": state.stop_cycles},
        )
    executable = min(state.open_shares, _available_depth(bid_size))
    if best_bid is None or executable + 1e-9 < policy.min_executable_shares:
        return ShadowAction(
            "shadow_sell_unexecutable",
            0.0,
            "large_reversal_but_no_executable_bid_depth",
            {
                "jma_reversal_c": reversal,
                "requested_shares": state.open_shares,
                "best_bid": best_bid,
                "bid_size": bid_size,
            },
        )
    state.open_shares -= executable
    state.gross_sold_shares += executable
    state.stopped_shares_available_to_rebuy += executable
    state.stop_cycles += 1
    state.last_stop_metar_report_ts_utc = metar_report_ts_utc
    state.last_stop_source_obs_ts_utc = latest_source_obs_ts_utc
    return ShadowAction(
        "shadow_sell",
        executable,
        "metar_nonconfirm_and_jma_reversal_threshold_met",
        {
            "jma_reversal_c": reversal,
            "best_bid": best_bid,
            "requested_shares": state.open_shares + executable,
            "remaining_open_shares": state.open_shares,
        },
    )


def evaluate_reentry(
    state: TokyoStopReentryState,
    policy: TokyoStopReentryPolicy,
    *,
    source_obs_ts_utc: str,
    source_temp_c: float,
    best_ask: float | None,
    ask_size: float | None,
) -> ShadowAction:
    """Restore only shares previously sold after a distinct post-stop signal."""
    if state.official_confirmed:
        return ShadowAction("hold", 0.0, "already_officially_confirmed", {})
    if state.stopped_shares_available_to_rebuy <= 1e-9:
        return ShadowAction("hold", 0.0, "no_stopped_shares_to_restore", {})
    if source_obs_ts_utc <= state.last_stop_source_obs_ts_utc:
        return ShadowAction("hold", 0.0, "source_observation_not_after_stop_anchor", {})
    threshold = state.bracket + policy.reentry_margin_c
    if source_temp_c + 1e-9 < threshold:
        return ShadowAction(
            "hold",
            0.0,
            "post_stop_source_signal_not_reconfirmed",
            {"source_temp_c": source_temp_c, "required_temp_c": threshold},
        )
    if best_ask is None or best_ask > policy.max_reentry_ask + 1e-9:
        return ShadowAction(
            "shadow_rebuy_unexecutable",
            0.0,
            "reentry_ask_missing_or_above_cap",
            {"best_ask": best_ask, "max_reentry_ask": policy.max_reentry_ask},
        )
    open_capacity = max(0.0, policy.max_open_shares - state.open_shares)
    gross_capacity = max(0.0, policy.max_gross_bought_shares - state.gross_bought_shares)
    executable = min(
        state.stopped_shares_available_to_rebuy,
        open_capacity,
        gross_capacity,
        _available_depth(ask_size),
    )
    if executable + 1e-9 < policy.min_executable_shares:
        return ShadowAction(
            "shadow_rebuy_unexecutable",
            0.0,
            "reentry_depth_or_turnover_capacity_insufficient",
            {
                "ask_size": ask_size,
                "open_capacity": open_capacity,
                "gross_capacity": gross_capacity,
            },
        )
    state.open_shares += executable
    state.gross_bought_shares += executable
    state.stopped_shares_available_to_rebuy -= executable
    state.last_reentry_source_obs_ts_utc = source_obs_ts_utc
    return ShadowAction(
        "shadow_rebuy",
        executable,
        "distinct_post_stop_source_signal_reconfirmed",
        {
            "best_ask": best_ask,
            "remaining_rebuy_shares": state.stopped_shares_available_to_rebuy,
            "open_shares": state.open_shares,
            "gross_bought_shares": state.gross_bought_shares,
        },
    )
