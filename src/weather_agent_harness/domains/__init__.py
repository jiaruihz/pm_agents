from .generic import GenericDomain
from .production_audit import ProductionAuditDomain, production_task_spec
from .registry import DomainRegistry, build_domain_registry
from .strategy_research import StrategyResearchDomain, strategy_task_spec

__all__ = [
    "DomainRegistry",
    "GenericDomain",
    "ProductionAuditDomain",
    "StrategyResearchDomain",
    "build_domain_registry",
    "production_task_spec",
    "strategy_task_spec",
]
