from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping


def _parse_utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_data_update_lifecycle_fields(
    *,
    data_source: str,
    data_epoch_ref: str,
    data_epoch_ts_utc: str | datetime,
    next_data_update_due_utc: str | datetime,
    cancel_buffer_sec: int = 90,
    now: datetime | None = None,
) -> dict[str, Any]:
    source = str(data_source or "").strip()
    epoch_ref = str(data_epoch_ref or "").strip()
    if not source:
        raise ValueError("data_source is required")
    if not epoch_ref:
        raise ValueError("data_epoch_ref is required")
    epoch_ts = _parse_utc(data_epoch_ts_utc)
    update_due = _parse_utc(next_data_update_due_utc)
    buffer_sec = max(0, int(cancel_buffer_sec))
    cancel_at = update_due - timedelta(seconds=buffer_sec)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if update_due <= epoch_ts:
        raise ValueError("next_data_update_due_utc must be after data_epoch_ts_utc")
    if cancel_at <= current:
        raise ValueError("inside pre-data-update blackout; maker quote is not allowed")
    return {
        "data_update_source": source,
        "data_epoch_ref": epoch_ref,
        "data_epoch_ts_utc": epoch_ts.isoformat(),
        "next_data_update_due_utc": update_due.isoformat(),
        "cancel_before_data_update_utc": cancel_at.isoformat(),
        "expires_at_utc": cancel_at.isoformat(),
        "cancel_buffer_sec": buffer_sec,
        "cancel_reason": "pre_data_update",
        "post_update_reprice_required": True,
    }


def attach_data_update_lifecycle(
    signal: Mapping[str, Any],
    **kwargs: Any,
) -> dict[str, Any]:
    return {**dict(signal), **build_data_update_lifecycle_fields(**kwargs)}
