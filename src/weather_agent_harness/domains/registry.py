"""Task-type plugin resolution with a portable declarative fallback."""

from __future__ import annotations

from collections.abc import Callable

from ..contracts import TaskSpec
from .base import DomainController
from .generic import GenericDomain
from .production_audit import PRODUCTION_TASK_TYPE, ProductionAuditDomain
from .strategy_research import STRATEGY_TASK_TYPE, StrategyResearchDomain


class DomainRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, Callable[[], DomainController]] = {}

    def register(
        self, task_type: str, factory: Callable[[], DomainController]
    ) -> None:
        if task_type in self._factories:
            raise ValueError(f"duplicate domain task type: {task_type}")
        self._factories[task_type] = factory

    def resolve(self, task: TaskSpec) -> DomainController:
        factory = self._factories.get(task.task_type)
        if factory is not None:
            return factory()
        return GenericDomain(task.domain)


def build_domain_registry() -> DomainRegistry:
    registry = DomainRegistry()
    registry.register(PRODUCTION_TASK_TYPE, ProductionAuditDomain)
    registry.register(STRATEGY_TASK_TYPE, StrategyResearchDomain)
    return registry


__all__ = ["DomainRegistry", "build_domain_registry"]
