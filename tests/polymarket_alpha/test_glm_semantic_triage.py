from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.polymarket_alpha.contracts import canonical_json
from src.polymarket_alpha.storage import AlphaRepository
from src.polymarket_alpha.triage import (
    ProviderReturnMetadata,
    SemanticTriageDisposition,
    TriageIsolationError,
    TriageResultError,
    build_semantic_triage_projection,
    import_semantic_triage_result,
)


SCRIPT = ROOT / "scripts" / "ops" / "polymarket_alpha_glm_semantic_triage.py"
SPEC = importlib.util.spec_from_file_location("alpha_glm_triage_sidecar", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
sidecar = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sidecar)

NOW = datetime(2026, 8, 28, 0, 0, tzinfo=timezone.utc)


def _markets() -> list[dict]:
    return [
        {
            "id": "m-weather",
            "question": "Will the highest temperature in Alpha City be 27°C on August 28?",
            "description": "Resolves Yes if the official station records a daily maximum of 27°C. Official observations are primary.",
            "endDate": "2026-08-29T00:00:00Z",
            "slug": "secret-market-slug",
            "bestBid": 0.12,
            "bestAsk": 0.13,
            "outcomePrices": '["0.12", "0.88"]',
            "events": [{"title": "Alpha City daily temperature", "slug": "private-event"}],
        },
        {
            "id": "m-ipo",
            "question": "Will ExampleCo complete an IPO by December 31, 2027?",
            "description": "Resolves Yes if ExampleCo completes its first public share offering by the deadline. Official exchange records are primary.",
            "endDate": "2028-01-01T05:00:00Z",
            "lastTradePrice": 0.42,
            "events": [{"title": "ExampleCo IPO deadlines"}],
        },
    ]


def _payload(projection) -> dict:
    return {
        "projection_id": projection.projection_id,
        "items": [
            {
                "item_id": item.item_id,
                "topic_family": "WEATHER" if "temperature" in item.neutral_proposition else "CORPORATE_EVENT",
                "subject_entities": ["Alpha City"] if "temperature" in item.neutral_proposition else ["ExampleCo"],
                "deadline_interpretation": "Inclusive through the stated deadline.",
                "resolution_source_type": "OFFICIAL",
                "researchability": "HIGH",
                "ambiguity_codes": [],
                "duplicate_group_hint": None,
                "disposition": "ADVANCE",
                "confidence_milli": 900,
                "reason_codes": ["CLEAR_RULE", "PRIMARY_SOURCE_AVAILABLE"],
            }
            for item in projection.items
        ],
    }


def _metadata():
    return ProviderReturnMetadata(
        provider="fake",
        requested_model="haiku",
        reported_model="GLM-test",
        duration_ms=12,
        total_cost_usd_micros=0,
        usage={"input_tokens": 100, "output_tokens": 50},
    )


def test_projection_is_allowlist_only_and_blinds_market_identity() -> None:
    projection, bindings = build_semantic_triage_projection(
        _markets(), run_id="triage-test", created_at=NOW
    )
    encoded = projection.canonical_bytes().decode("utf-8")

    assert len(projection.items) == 2
    assert {binding.market_id for binding in bindings} == {"m-weather", "m-ipo"}
    for forbidden in (
        "m-weather",
        "m-ipo",
        "secret-market-slug",
        "bestBid",
        "bestAsk",
        "outcomePrices",
        "lastTradePrice",
        "polymarket.com",
    ):
        assert forbidden not in encoded


def test_projection_rejects_nested_polymarket_url() -> None:
    markets = _markets()
    markets[0]["events"][0]["title"] = "See https://polymarket.com/event/leak"

    with pytest.raises((TriageIsolationError, ValueError)):
        build_semantic_triage_projection(markets, run_id="triage-test", created_at=NOW)


def test_projection_rejects_duplicate_market_identity() -> None:
    markets = _markets()
    markets[1]["id"] = markets[0]["id"]

    with pytest.raises(TriageIsolationError, match="duplicate market_id"):
        build_semantic_triage_projection(markets, run_id="triage-test", created_at=NOW)


def test_import_seals_decisions_receipt_and_database_idempotently(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    projection, bindings = build_semantic_triage_projection(
        _markets(), run_id="triage-test", created_at=NOW
    )
    payload = _payload(projection)
    wrapper = canonical_json({"structured_output": payload}).encode()
    repository = AlphaRepository(tmp_path / "alpha.db")

    first = import_semantic_triage_result(
        projection=projection,
        bindings=bindings,
        provider_payload=payload,
        prompt_bytes=b"prompt",
        provider_wrapper_bytes=wrapper,
        provider=_metadata(),
        artifact_root=artifact_root,
        imported_at=NOW,
        repository=repository,
    )
    second = import_semantic_triage_result(
        projection=projection,
        bindings=bindings,
        provider_payload=payload,
        prompt_bytes=b"prompt",
        provider_wrapper_bytes=wrapper,
        provider=_metadata(),
        artifact_root=artifact_root,
        imported_at=NOW,
        repository=repository,
    )

    assert first == second
    assert first.receipt.execution == "NO_ORDER"
    assert first.receipt.provider_dispositions == {"ADVANCE": 2, "REVIEW": 0, "DEFER": 0}
    assert first.receipt.dispositions == {"ADVANCE": 2, "REVIEW": 0, "DEFER": 0}
    assert all(not decision.model_only_terminal_rejection for decision in first.decisions)
    assert all(repository.get_contract(decision.record_id) for decision in first.decisions)
    sealed = artifact_root / "semantic_triage" / first.receipt.receipt_id
    assert {path.name for path in sealed.iterdir()} == {
        "projection.json",
        "bindings.json",
        "prompt.txt",
        "provider-wrapper.json",
        "provider-result.json",
        "decisions.json",
        "receipt.json",
    }


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "wrong_projection"])
def test_import_rejects_incomplete_or_mismatched_provider_result(
    tmp_path: Path, mutation: str
) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    projection, bindings = build_semantic_triage_projection(
        _markets(), run_id="triage-test", created_at=NOW
    )
    payload = _payload(projection)
    if mutation == "missing":
        payload["items"].pop()
    elif mutation == "duplicate":
        payload["items"][1]["item_id"] = payload["items"][0]["item_id"]
    else:
        payload["projection_id"] = "semantic_projection:" + "0" * 64

    with pytest.raises(TriageResultError):
        import_semantic_triage_result(
            projection=projection,
            bindings=bindings,
            provider_payload=payload,
            prompt_bytes=b"prompt",
            provider_wrapper_bytes=b"{}",
            provider=_metadata(),
            artifact_root=artifact_root,
            imported_at=NOW,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("fair_value", "unknown"),
        ("trade_direction", "BUY"),
        ("market_url", "https://polymarket.com/event/leak"),
    ],
)
def test_provider_result_rejects_estimation_and_market_semantics(
    tmp_path: Path, field: str, value: str
) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    projection, bindings = build_semantic_triage_projection(
        _markets(), run_id="triage-test", created_at=NOW
    )
    payload = _payload(projection)
    payload["items"][0][field] = value

    with pytest.raises(TriageResultError):
        import_semantic_triage_result(
            projection=projection,
            bindings=bindings,
            provider_payload=payload,
            prompt_bytes=b"prompt",
            provider_wrapper_bytes=b"{}",
            provider=_metadata(),
            artifact_root=artifact_root,
            imported_at=NOW,
        )


def test_provider_result_rejects_probability_in_allowlisted_free_text(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    projection, bindings = build_semantic_triage_projection(
        _markets(), run_id="triage-test", created_at=NOW
    )
    payload = _payload(projection)
    payload["items"][0]["deadline_interpretation"] = "Market probability is 10%"

    with pytest.raises(TriageResultError):
        import_semantic_triage_result(
            projection=projection,
            bindings=bindings,
            provider_payload=payload,
            prompt_bytes=b"prompt",
            provider_wrapper_bytes=b"{}",
            provider=_metadata(),
            artifact_root=artifact_root,
            imported_at=NOW,
        )


def test_sidecar_command_disables_tools_sessions_and_mcp() -> None:
    command = sidecar._command(
        claude_bin=Path("/absolute/claude"),
        model="haiku",
        schema={"type": "object"},
    )

    assert command[:2] == ["/absolute/claude", "-p"]
    assert command[command.index("--tools") + 1] == ""
    assert "--safe-mode" in command
    assert "--strict-mcp-config" in command
    assert command[command.index("--mcp-config") + 1] == '{"mcpServers":{}}'
    assert "--disable-slash-commands" in command
    assert "--no-session-persistence" in command
    assert command[command.index("--permission-mode") + 1] == "dontAsk"


def test_provider_model_is_recovered_from_model_usage() -> None:
    assert sidecar._reported_model({"modelUsage": {"glm-4.7": {"inputTokens": 1}}}) == "glm-4.7"
    with pytest.raises(sidecar.SidecarError, match="exactly one"):
        sidecar._reported_model({"modelUsage": {"glm-a": {}, "glm-b": {}}})


def test_sidecar_fake_execution_uses_stdin_and_returns_bound_decisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projection, _ = build_semantic_triage_projection(
        _markets(), run_id="triage-sidecar", created_at=NOW
    )
    wrapper = {
        "is_error": False,
        "model": "GLM-test",
        "duration_ms": 25,
        "total_cost_usd": "0.0002",
        "usage": {"input_tokens": 200, "output_tokens": 80},
        "structured_output": _payload(projection),
    }
    observed: dict = {}

    def fake_runner(command, **kwargs):
        observed["command"] = command
        observed.update(kwargs)
        return subprocess.CompletedProcess(command, 0, json.dumps(wrapper), "")

    monkeypatch.setenv("HTTP_PROXY", "http://untrusted.proxy")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "never-serialize-this")
    output = sidecar.run_sidecar(
        markets=_markets(),
        output_root=tmp_path / "artifacts",
        repository_path=tmp_path / "alpha.db",
        run_id="triage-sidecar",
        model="haiku",
        claude_bin=Path("/absolute/claude"),
        now=NOW,
        runner=fake_runner,
    )

    assert output["item_count"] == 2
    assert output["execution"] == "NO_ORDER"
    assert "m-weather" not in observed["input"]
    assert "never-serialize-this" not in " ".join(observed["command"])
    assert "HTTP_PROXY" not in observed["env"]
    assert observed["env"]["ANTHROPIC_AUTH_TOKEN"] == "never-serialize-this"
    sealed_text = "".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "artifacts").rglob("*")
        if path.is_file()
    )
    assert "never-serialize-this" not in sealed_text


def test_defer_is_explicitly_nonterminal(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    projection, bindings = build_semantic_triage_projection(
        _markets(), run_id="triage-test", created_at=NOW
    )
    payload = _payload(projection)
    payload["items"][0]["disposition"] = SemanticTriageDisposition.DEFER.value
    imported = import_semantic_triage_result(
        projection=projection,
        bindings=bindings,
        provider_payload=payload,
        prompt_bytes=b"prompt",
        provider_wrapper_bytes=b"{}",
        provider=_metadata(),
        artifact_root=artifact_root,
        imported_at=NOW,
    )

    deferred = imported.decisions[0]
    assert deferred.result.disposition == SemanticTriageDisposition.DEFER
    assert deferred.effective_disposition == SemanticTriageDisposition.DEFER
    assert deferred.model_only_terminal_rejection is False


def test_elapsed_deadline_deterministically_defers_model_advance(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    markets = _markets()
    markets[0]["endDate"] = "2026-08-27T00:00:00Z"
    projection, bindings = build_semantic_triage_projection(
        markets, run_id="triage-expired", created_at=NOW
    )
    imported = import_semantic_triage_result(
        projection=projection,
        bindings=bindings,
        provider_payload=_payload(projection),
        prompt_bytes=b"prompt",
        provider_wrapper_bytes=b"{}",
        provider=_metadata(),
        artifact_root=artifact_root,
        imported_at=NOW,
    )

    expired = imported.decisions[0]
    assert expired.result.disposition == SemanticTriageDisposition.ADVANCE
    assert expired.eligibility == "DEADLINE_ELAPSED"
    assert expired.effective_disposition == SemanticTriageDisposition.DEFER
    assert imported.receipt.provider_dispositions == {"ADVANCE": 2, "REVIEW": 0, "DEFER": 0}
    assert imported.receipt.dispositions == {"ADVANCE": 1, "REVIEW": 0, "DEFER": 1}
