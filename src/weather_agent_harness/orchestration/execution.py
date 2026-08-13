"""Compile declarative roles into execution profiles Codex can enforce."""

from __future__ import annotations

from .contracts import EffectiveExecutionProfile, RoleSpec, WorkOrder


_NATIVE_AGENT_SANDBOX = {
    "luna_verifier": "read-only",
    "terra_worker": "workspace-write",
}


def compile_execution_profile(
    role: RoleSpec, order: WorkOrder
) -> EffectiveExecutionProfile:
    expected = _NATIVE_AGENT_SANDBOX.get(role.name)
    if expected is None:
        raise ValueError(
            f"role has no enforceable Codex agent_type mapping: {role.name}"
        )
    if role.sandbox != expected:
        raise ValueError(
            f"role sandbox is not enforced by agent_type {role.name}: "
            f"declared {role.sandbox}, native {expected}"
        )
    if role.allowed_tools:
        raise ValueError(
            "per-WorkOrder allowed_tools cannot be enforced by the current "
            "Codex spawn boundary"
        )
    if role.writable_roots:
        raise ValueError(
            "dynamic writable_roots require a predeclared Codex permissions profile"
        )
    if role.network_access:
        raise ValueError(
            "per-WorkOrder network access cannot be enabled by the current dispatch boundary"
        )
    if role.sandbox == "read-only" and order.write_owners:
        raise ValueError("read-only work cannot declare write owners")
    return EffectiveExecutionProfile(
        agent_type=role.name,
        sandbox_mode=role.sandbox,
        network_access=False,
    )


__all__ = ["compile_execution_profile"]
