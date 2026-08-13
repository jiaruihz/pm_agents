"""Deterministic request routing; model judgment supplies the profile, not the level."""

from __future__ import annotations

from ..contracts import RiskLevel, stable_hash
from .contracts import RequestProfile, RouteDecision, RouteLevel


class RequestRouter:
    _CONTROLLED = {
        RiskLevel.PRODUCTION_CHANGE,
        RiskLevel.LIVE_FUNDS,
        RiskLevel.DESTRUCTIVE,
    }

    def classify(self, profile: RequestProfile) -> RouteDecision:
        reasons: list[str] = []
        if profile.risk in self._CONTROLLED:
            level = RouteLevel.CONTROLLED
            reasons.append(f"risk={profile.risk.value} requires controlled authority")
        elif (
            profile.explicit_multi_agent
            or profile.independent_workstreams >= 2
            or profile.needs_independent_review
        ):
            level = RouteLevel.MULTI_AGENT
            if profile.explicit_multi_agent:
                reasons.append("multi-agent execution was explicitly requested")
            if profile.independent_workstreams >= 2:
                reasons.append("at least two independent workstreams are available")
            if profile.needs_independent_review:
                reasons.append("independent review is required")
        elif (
            profile.explicit_harness
            or profile.estimated_stages >= 3
            or profile.needs_iteration
            or profile.needs_resume
        ):
            level = RouteLevel.SINGLE_HARNESS
            reasons.append("task needs persistent multi-step execution")
        else:
            level = RouteLevel.DIRECT
            reasons.append("task is bounded and does not need persistent orchestration")
        return RouteDecision(
            level=level,
            reasons=tuple(reasons),
            profile_hash=stable_hash(profile.model_dump(mode="json")),
        )


__all__ = ["RequestRouter"]
