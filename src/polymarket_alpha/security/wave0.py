"""P0-11 Wave-0: offline capability policy and source-tree audit.

This module deliberately performs no monkeypatching, process interception, or
network I/O.  It defines the fail-closed policy and makes unsafe source
dependencies visible before Alpha entrypoints are introduced.  Concrete
transport, receipts, and OS/process enforcement belong to later P0-11 work.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


# These are capability roots, not a claim that similarly named repository
# modules are unsafe.  Alpha P0 code must not import any of them directly.
NETWORK_IMPORT_ROOTS = frozenset(
    {
        "_socket",
        "aiohttp",
        "ftplib",
        "http",
        "httpx",
        "pycurl",
        "requests",
        "smtplib",
        "socket",
        "telnetlib",
        "urllib",
        "urllib3",
        "websocket",
        "websockets",
        "xmlrpc",
    }
)
PROCESS_IMPORT_ROOTS = frozenset({"subprocess", "multiprocessing"})
DYNAMIC_IMPORT_ROOTS = frozenset({"ctypes", "importlib", "runpy"})
EXECUTION_IMPORT_PREFIXES = (
    "py_clob_client",
    "src.platform.execution",
    "src.strategies.rule_lawyer.auto_order",
    "strategies.rule_lawyer.auto_order",
)
OS_PROCESS_CALLS = frozenset(
    {
        "os.execl",
        "os.execle",
        "os.execlp",
        "os.execlpe",
        "os.execv",
        "os.execve",
        "os.execvp",
        "os.execvpe",
        "os.popen",
        "os.posix_spawn",
        "os.posix_spawnp",
        "os.spawnl",
        "os.spawnle",
        "os.spawnlp",
        "os.spawnlpe",
        "os.spawnv",
        "os.spawnve",
        "os.spawnvp",
        "os.spawnvpe",
        "os.system",
        "pty.spawn",
    }
)
SUBPROCESS_CALLS = frozenset(
    {
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "subprocess.Popen",
        "subprocess.run",
    }
)
DYNAMIC_IMPORT_CALLS = frozenset(
    {
        "__import__",
        "builtins.__import__",
        "importlib.import_module",
        "importlib.reload",
        "importlib.util.module_from_spec",
        "importlib.util.spec_from_file_location",
        "runpy.run_module",
        "runpy.run_path",
    }
)
ASYNC_PROCESS_CALLS = frozenset(
    {
        "asyncio.create_subprocess_exec",
        "asyncio.create_subprocess_shell",
    }
)
PROCESS_CONSTRUCTORS = frozenset(
    {
        "concurrent.futures.ProcessPoolExecutor",
        "multiprocessing.Process",
    }
)


@dataclass(frozen=True)
class CapabilityDecision:
    """A fail-closed decision made by the offline policy."""

    allowed: bool
    capability: str
    reason: str


class OfflineCapabilityPolicy:
    """P0 offline profile: network and child-process capability are denied."""

    profile = "OFFLINE_IMPLEMENTATION_ONLY"

    def network(self, *_: object, **__: object) -> CapabilityDecision:
        return CapabilityDecision(False, "network", "offline profile denies all network access")

    def process(self, *_: object, **__: object) -> CapabilityDecision:
        return CapabilityDecision(False, "process", "offline profile denies child-process and shell execution")

    def dynamic_import(self, *_: object, **__: object) -> CapabilityDecision:
        return CapabilityDecision(False, "dynamic_import", "offline profile denies dynamic module loading")

    def execution(self, *_: object, **__: object) -> CapabilityDecision:
        return CapabilityDecision(False, "execution", "P0 never grants order, signing, or credential capability")


@dataclass(frozen=True)
class CapabilityViolation:
    path: Path
    line: int
    code: str
    detail: str


@dataclass(frozen=True)
class SourceTreeAudit:
    root: Path
    violations: tuple[CapabilityViolation, ...]

    @property
    def passed(self) -> bool:
        return not self.violations


def _root(module: str) -> str:
    return module.split(".", 1)[0]


def _is_execution_module(module: str) -> bool:
    return any(module == prefix or module.startswith(prefix + ".") for prefix in EXECUTION_IMPORT_PREFIXES)


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else None
    return None


class _CapabilityVisitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.violations: list[CapabilityViolation] = []
        self.aliases: dict[str, str] = {}

    def _add(self, node: ast.AST, code: str, detail: str) -> None:
        self.violations.append(CapabilityViolation(self.path, node.lineno, code, detail))

    def _check_module(self, node: ast.AST, module: str) -> None:
        root = _root(module)
        if root in NETWORK_IMPORT_ROOTS:
            self._add(node, "NETWORK_IMPORT", module)
        elif root in PROCESS_IMPORT_ROOTS:
            self._add(node, "PROCESS_IMPORT", module)
        elif root in DYNAMIC_IMPORT_ROOTS:
            self._add(node, "DYNAMIC_IMPORT", module)
        elif _is_execution_module(module):
            self._add(node, "EXECUTION_IMPORT", module)

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            module = alias.name
            self.aliases[alias.asname or _root(module)] = module
            self._check_module(node, module)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        module = node.module or ""
        for alias in node.names:
            imported = f"{module}.{alias.name}" if module else alias.name
            self.aliases[alias.asname or alias.name] = imported
        self._check_module(node, module)
        self.generic_visit(node)

    def _resolve_call(self, node: ast.AST) -> str | None:
        dotted = _dotted_name(node)
        if not dotted:
            return None
        head, sep, tail = dotted.partition(".")
        replacement = self.aliases.get(head)
        return f"{replacement}.{tail}" if replacement and sep else (replacement or dotted)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        target = self._resolve_call(node.func)
        if target in DYNAMIC_IMPORT_CALLS:
            self._add(node, "DYNAMIC_IMPORT_CALL", target)
        elif target in OS_PROCESS_CALLS or target in SUBPROCESS_CALLS or target in ASYNC_PROCESS_CALLS:
            self._add(node, "PROCESS_CALL", target)
        elif target in PROCESS_CONSTRUCTORS:
            self._add(node, "PROCESS_CONSTRUCTOR", target)
        elif target in {"eval", "exec"}:
            self._add(node, "DYNAMIC_CODE_CALL", target)
        self.generic_visit(node)


def _python_files(root: Path) -> Iterable[Path]:
    return sorted(path for path in root.rglob("*.py") if path.is_file())


def audit_source_tree(root: str | Path) -> SourceTreeAudit:
    """Audit a supplied Python source tree without importing or executing it.

    Invalid Python is itself a failed audit because it cannot be reliably
    inspected.  Existing repository code is intentionally not scanned here as
    a global pass gate; callers must explicitly provide their owned tree.
    """

    resolved_root = Path(root).resolve()
    violations: list[CapabilityViolation] = []
    for path in _python_files(resolved_root):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            violations.append(
                CapabilityViolation(path, exc.lineno or 0, "SYNTAX_ERROR", exc.msg)
            )
            continue
        visitor = _CapabilityVisitor(path)
        visitor.visit(tree)
        violations.extend(visitor.violations)
    return SourceTreeAudit(resolved_root, tuple(violations))
