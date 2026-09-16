"""Authority policy for typed harness actions."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import ActionRequest, RiskLevel, TaskSpec


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    requires_authority: bool
    reason: str


class PolicyEngine:
    _ALWAYS_EXPLICIT = {
        RiskLevel.PRODUCTION_CHANGE,
        RiskLevel.LIVE_FUNDS,
        RiskLevel.DESTRUCTIVE,
    }

    def evaluate(
        self,
        task: TaskSpec,
        risk: RiskLevel,
        action: ActionRequest | None = None,
    ) -> PolicyDecision:
        if action is not None and action.action_hash in task.authority.explicit_action_grants:
            return PolicyDecision(True, False, "exact action fingerprint is explicitly granted")
        if risk not in self._ALWAYS_EXPLICIT and risk in task.authority.explicit_grants:
            return PolicyDecision(True, False, f"explicit grant covers {risk.value}")
        if risk in self._ALWAYS_EXPLICIT:
            return PolicyDecision(
                False,
                True,
                f"{risk.value} requires an explicit per-task grant",
            )
        if risk in task.authority.auto_execute:
            return PolicyDecision(True, False, f"TaskSpec permits {risk.value}")
        return PolicyDecision(
            False,
            True,
            f"TaskSpec does not permit autonomous {risk.value}",
        )


__all__ = ["PolicyDecision", "PolicyEngine"]
