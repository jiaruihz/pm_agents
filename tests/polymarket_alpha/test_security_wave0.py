from __future__ import annotations

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.polymarket_alpha.security import OfflineCapabilityPolicy, audit_source_tree


def _write(tree: Path, name: str, source: str) -> Path:
    path = tree / name
    path.write_text(source, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("source", "expected_code"),
    [
        ("import requests\n", "NETWORK_IMPORT"),
        ("from httpx import Client\n", "NETWORK_IMPORT"),
        ("import websocket as ws\n", "NETWORK_IMPORT"),
        ("import socket\n", "NETWORK_IMPORT"),
        ("import http.client\n", "NETWORK_IMPORT"),
        ("from ftplib import FTP\n", "NETWORK_IMPORT"),
        ("import smtplib\n", "NETWORK_IMPORT"),
        ("import _socket\n", "NETWORK_IMPORT"),
        ("import subprocess\nsubprocess.run(['never-run'])\n", "PROCESS_IMPORT"),
        ("import os\nos.system('never-run')\n", "PROCESS_CALL"),
        ("import os\nos.posix_spawn('/bin/false', ['/bin/false'], {})\n", "PROCESS_CALL"),
        ("import asyncio\nasyncio.create_subprocess_shell('never-run')\n", "PROCESS_CALL"),
        ("import pty\npty.spawn(['/bin/false'])\n", "PROCESS_CALL"),
        ("from concurrent.futures import ProcessPoolExecutor\nProcessPoolExecutor()\n", "PROCESS_CONSTRUCTOR"),
        ("import importlib\nimportlib.import_module('src.platform.execution')\n", "DYNAMIC_IMPORT"),
        ("import ctypes\nctypes.CDLL('never-load')\n", "DYNAMIC_IMPORT"),
        ("__import__('src.strategies.rule_lawyer.auto_order')\n", "DYNAMIC_IMPORT_CALL"),
        ("from builtins import __import__ as loader\nloader('socket')\n", "DYNAMIC_IMPORT_CALL"),
        ("from src.platform.execution import order_client\n", "EXECUTION_IMPORT"),
        ("import py_clob_client\n", "EXECUTION_IMPORT"),
    ],
)
def test_audit_rejects_bypass_shaped_source_without_execution(
    tmp_path: Path, source: str, expected_code: str
) -> None:
    _write(tmp_path, "candidate.py", source)

    result = audit_source_tree(tmp_path)

    assert not result.passed
    assert expected_code in {violation.code for violation in result.violations}


def test_audit_tracks_alias_process_call(tmp_path: Path) -> None:
    _write(tmp_path, "candidate.py", "import os as operating_system\noperating_system.popen('never-run')\n")

    result = audit_source_tree(tmp_path)

    assert any(v.code == "PROCESS_CALL" and v.detail == "os.popen" for v in result.violations)


def test_audit_accepts_pure_local_source(tmp_path: Path) -> None:
    _write(tmp_path, "pure.py", "from dataclasses import dataclass\n@dataclass\nclass Value:\n    value: int\n")

    result = audit_source_tree(tmp_path)

    assert result.passed
    assert result.violations == ()


def test_invalid_source_fails_closed(tmp_path: Path) -> None:
    _write(tmp_path, "broken.py", "def no_colon()\n    pass\n")

    result = audit_source_tree(tmp_path)

    assert not result.passed
    assert result.violations[0].code == "SYNTAX_ERROR"


def test_offline_policy_denies_every_capability_by_default() -> None:
    policy = OfflineCapabilityPolicy()

    decisions = [
        policy.network("https://example.test"),
        policy.process("never-run"),
        policy.dynamic_import("anything"),
        policy.execution("order"),
    ]

    assert policy.profile == "OFFLINE_IMPLEMENTATION_ONLY"
    assert all(not decision.allowed for decision in decisions)
    assert {decision.capability for decision in decisions} == {
        "network",
        "process",
        "dynamic_import",
        "execution",
    }
