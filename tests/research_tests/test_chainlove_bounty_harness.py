from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.weather_agent_harness.orchestration import (
    CodexDispatchAdapter,
    RoleSpec,
    compile_execution_profile,
)
from src.weather_agent_harness.orchestration.usage import find_codex_session
from src.weather_agent_harness.scenarios.chainlove_bounty import (
    build_bundle,
    snapshot_sha256,
    write_bundle,
)


def test_chainlove_bundle_routes_narrow_work_to_luna_and_review_to_terra(
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "open_pr_snapshot.json"
    snapshot.write_text("{}\n", encoding="utf-8")
    bundle = build_bundle(
        run_id="chainlove-test",
        repo="Chain-Love/chain-love",
        repo_path=tmp_path,
        base_sha="a" * 40,
        snapshot_path=snapshot,
        snapshot_sha256="b" * 64,
        snapshot_captured_at_utc="2026-08-14T00:00:00Z",
        reward_address="0x4B689c62992FCcC63525d32D70696E45190d260A",
    )
    roles = {item.name: item for item in bundle["roles"]}
    assert roles["luna_scanner"].requested_model == "gpt-5.6-luna"
    assert roles["luna_verifier"].requested_model == "gpt-5.6-luna"
    assert roles["luna_checker"].reasoning_effort == "low"
    assert roles["terra_reviewer"].requested_model == "gpt-5.6-terra"
    assert all(item.require_usage for item in roles.values())

    orders = {item.work_order_id: item for item in bundle["work_orders"]}
    assert orders["candidate_mcp"].depends_on == ()
    assert orders["candidate_services"].depends_on == ()
    assert orders["evidence_review"].depends_on == (
        "candidate_mcp",
        "candidate_services",
    )
    assert orders["implementation_review"].role == "luna_verifier"
    assert orders["publish_verification"].role == "luna_checker"
    assert orders["publish_verification"].max_attempts == 1
    instruction = CodexDispatchAdapter().instruction(
        orders["candidate_mcp"], roles["luna_scanner"]
    )
    assert instruction["task_name"] == "chainlove_test_candidate_mcp"
    for order in orders.values():
        profile = compile_execution_profile(roles[order.role], order)
        assert profile.sandbox_mode == "read-only"
        assert order.scope["collision_snapshot_sha256"] == "b" * 64

    write_bundle(bundle, tmp_path / "config")
    persisted_roles = json.loads((tmp_path / "config" / "roles.json").read_text())
    assert {item["requested_model"] for item in persisted_roles} == {
        "gpt-5.6-luna",
        "gpt-5.6-terra",
    }
    assert len(list((tmp_path / "config" / "work_orders").glob("*.json"))) == 5


def test_find_codex_session_by_spawned_agent_path(tmp_path: Path) -> None:
    older = tmp_path / "sessions" / "old.jsonl"
    newer = tmp_path / "sessions" / "new.jsonl"
    older.parent.mkdir(parents=True)
    for path, timestamp in (
        (older, "2026-08-14T00:00:00Z"),
        (newer, "2026-08-14T00:02:00Z"),
    ):
        path.write_text(
            json.dumps(
                {
                    "timestamp": timestamp,
                    "type": "session_meta",
                    "payload": {
                        "source": {
                            "subagent": {
                                "thread_spawn": {"agent_path": "/root/candidate_mcp"}
                            }
                        }
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
    found = find_codex_session(
        "/root/candidate_mcp",
        started_at_utc="2026-08-14T00:01:55Z",
        roots=(tmp_path / "sessions",),
    )
    assert found == newer


def test_snapshot_hash_matches_persisted_pretty_json() -> None:
    snapshot = {"captured_at_utc": "2026-08-14T00:00:00Z", "pull_requests": []}
    persisted = json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
    assert snapshot_sha256(snapshot) == hashlib.sha256(persisted.encode()).hexdigest()


def test_model_named_role_cannot_silently_route_to_another_model() -> None:
    with pytest.raises(ValueError, match="requires requested_model=gpt-5.6-luna"):
        RoleSpec(name="luna_verifier", requested_model="gpt-5.6-terra")


def test_find_codex_session_by_thread_uuid(tmp_path: Path) -> None:
    session = tmp_path / "sessions" / "thread.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text(
        json.dumps(
            {
                "timestamp": "2026-08-14T00:02:00Z",
                "type": "session_meta",
                "payload": {"id": "thread-uuid", "source": "vscode"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert find_codex_session("thread-uuid", roots=(session.parent,)) == session


def test_extended_bundle_adds_stale_sweep_and_deferred_recheck(tmp_path: Path) -> None:
    snapshot = tmp_path / "open_pr_snapshot.json"
    snapshot.write_text("{}\n", encoding="utf-8")
    queue = tmp_path / "deferred_candidates.json"
    bundle = build_bundle(
        run_id="chainlove-extended-test",
        repo="Chain-Love/chain-love",
        repo_path=tmp_path,
        base_sha="a" * 40,
        snapshot_path=snapshot,
        snapshot_sha256="b" * 64,
        snapshot_captured_at_utc="2026-08-21T00:00:00Z",
        reward_address="0x4B689c62992FCcC63525d32D70696E45190d260A",
        extended=True,
        category_modifiers={"security": 1.5, "mcpservers": 0.73},
        deferred_queue_path=queue,
    )
    orders = {item.work_order_id: item for item in bundle["work_orders"]}
    assert set(orders) == {
        "candidate_mcp",
        "candidate_services",
        "evidence_review",
        "implementation_review",
        "publish_verification",
        "stale_sweep",
        "deferred_recheck",
    }
    assert orders["stale_sweep"].role == "luna_scanner"
    assert orders["deferred_recheck"].role == "luna_checker"
    assert orders["candidate_services"].scope["category_modifier_hint"] == {
        "security": 1.5,
        "mcpservers": 0.73,
    }
    assert orders["evidence_review"].scope["deferred_queue_write_path"] == str(queue)
    assert orders["deferred_recheck"].scope["deferred_queue_read_path"] == str(queue)
    assert "stale_inventory.json" in orders["evidence_review"].scope["review_input_refs"]
    # default bundle must stay unchanged (5 work orders, no extended keys)
    plain = build_bundle(
        run_id="chainlove-plain-test",
        repo="Chain-Love/chain-love",
        repo_path=tmp_path,
        base_sha="a" * 40,
        snapshot_path=snapshot,
        snapshot_sha256="b" * 64,
        snapshot_captured_at_utc="2026-08-21T00:00:00Z",
    )
    assert len(plain["work_orders"]) == 5
    plain_orders = {item.work_order_id: item for item in plain["work_orders"]}
    assert "category_modifier_hint" not in plain_orders["candidate_mcp"].scope
