from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionProfile:
    name: str
    execution_policy: str
    order_lifecycle_policy: str
    cancel_buffer_sec: int
    maker_only: bool


_PROFILES = {
    "taker_now_v1": ExecutionProfile(
        name="taker_now_v1",
        execution_policy="taker_top_ask_v1",
        order_lifecycle_policy="taker_now",
        cancel_buffer_sec=0,
        maker_only=False,
    ),
    "single_side_maker_v1": ExecutionProfile(
        name="single_side_maker_v1",
        execution_policy="maker_queue_v2",
        order_lifecycle_policy="maker_until_data_update",
        cancel_buffer_sec=90,
        maker_only=True,
    ),
}


def execution_profile_names() -> tuple[str, ...]:
    return tuple(_PROFILES)


def get_execution_profile(name: str) -> ExecutionProfile:
    key = str(name or "").strip().lower()
    try:
        return _PROFILES[key]
    except KeyError as exc:
        raise ValueError(f"unknown execution profile: {name!r}") from exc
