from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Dict, List

from pmm.config import PMMConfig


@dataclass(frozen=True)
class TokenContext:
    token_ids: List[str]
    reference_token_ids: List[str]
    all_token_ids: List[str]


@dataclass
class HistoryContext:
    history_window_sec: int
    window_points: int
    mid_history: Dict[str, deque[float]]


def build_token_context(config: PMMConfig) -> TokenContext:
    token_ids = list(config.market.token_ids)
    reference_token_ids = [
        x for x in config.alpha_reference_token_ids if x and x not in token_ids
    ]
    all_token_ids = token_ids + reference_token_ids
    return TokenContext(
        token_ids=token_ids,
        reference_token_ids=reference_token_ids,
        all_token_ids=all_token_ids,
    )


def build_history_context(config: PMMConfig, all_token_ids: List[str]) -> HistoryContext:
    history_window_sec = max(config.circuit_breaker_window_sec, config.alpha_window_sec)
    window_points = max(
        2,
        int(max(1, history_window_sec) / max(0.1, config.tick_interval_sec)),
    )
    mid_history: Dict[str, deque[float]] = {
        token_id: deque(maxlen=window_points) for token_id in all_token_ids
    }
    return HistoryContext(
        history_window_sec=history_window_sec,
        window_points=window_points,
        mid_history=mid_history,
    )
