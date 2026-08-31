"""Released shadow-policy constructors for Alpha Capital Agent V1."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from .universe import EligibilityPolicy, build_eligibility_policy


# The release clock is bound to the controlled market20 evidence capture.  The
# values below are an exact code release of the former /private/tmp selector;
# changing any value requires a new named policy and frozen-forward evidence.
MARKET20_V1_RELEASED_AT = datetime(
    2026, 8, 28, 16, 34, 31, tzinfo=timezone.utc
)


def market20_v1_policy() -> EligibilityPolicy:
    return build_eligibility_policy(
        policy_name="market20_v1",
        run_id="aca-policy-market20-v1",
        created_at=MARKET20_V1_RELEASED_AT,
        source_version="market20_selector_release_v1",
        min_liquidity_enter=Decimal("750"),
        min_volume_enter=Decimal("2500"),
        max_spread_enter=Decimal("0.12"),
        min_executable_depth_enter=Decimal("0"),
        min_hours_to_deadline=24,
        min_question_chars=8,
        min_rule_text_chars=80,
        near_band_fraction=Decimal("0.20"),
        liquidity_exit_ratio=Decimal("0.80"),
        volume_exit_ratio=Decimal("0.80"),
        spread_exit_ratio=Decimal("1.25"),
        entry_confirmations=2,
        confirmation_interval_seconds=300,
        entry_dwell_seconds=600,
        near_interval_seconds=300,
        dormant_interval_seconds=1800,
        structural_interval_seconds=21600,
        terminal_interval_seconds=86400,
        max_facts_age_seconds=300,
    )
