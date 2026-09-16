from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path

import pytest

from src.polymarket_alpha.contracts import (
    EstimateStage,
    PacketStage,
    ResearchImportStatus,
    ResearchResultEnvelope,
    canonical_json,
    stable_record_id,
)
from src.polymarket_alpha.research.handoff import (
    HandoffConflictError,
    HandoffPathError,
    HandoffState,
    export_research_packet,
    ingest_research_result,
    seal_result_handoff,
)
from src.polymarket_alpha.security import audit_source_tree
from tests.polymarket_alpha.test_decision_ledger_p0_09b import _inputs
from tests.polymarket_alpha.test_research_importer_p0_08b import NOW, _packet, _result


def _blind_submission(packet):
    result, contents = _result(packet)
    return canonical_json(result).encode("utf-8"), contents


def _market_submission(packet):
    blind_packet = _packet()
    blind_result, contents = _result(blind_packet)
    raw = blind_result.model_dump(mode="python")
    estimate = raw["probability_estimate"]
    estimate.update(
        {
            "record_id": stable_record_id("probability_estimate", "market-handoff"),
            "market_id": packet.market_id,
            "estimate_stage": EstimateStage.FINAL,
            "p_market_yes_low": "0.2",
            "p_market_yes_mid": "0.3",
            "p_market_yes_high": "0.4",
        }
    )
    raw.update(
        {
            "record_id": stable_record_id("research_result", packet.record_id, "handoff"),
            "result_id": stable_record_id("research_result", packet.record_id, "handoff"),
            "packet_stage": PacketStage.MARKET_AWARE,
            "packet_id": packet.record_id,
            "packet_sha256": packet.canonical_sha256,
        }
    )
    result = ResearchResultEnvelope.model_validate(raw)
    return canonical_json(result).encode("utf-8"), contents


def test_blind_export_ingest_is_idempotent_and_sealable(tmp_path: Path) -> None:
    packet = _packet()
    exported = export_research_packet(
        artifact_root=tmp_path,
        packet=packet,
        packet_locator="outbox/blind.packet.json",
        manifest_locator="outbox/blind.manifest.json",
        created_at=NOW,
    )
    retry = export_research_packet(
        artifact_root=tmp_path,
        packet=packet,
        packet_locator="outbox/blind.packet.json",
        manifest_locator="outbox/blind.manifest.json",
        created_at=NOW,
    )
    assert retry == exported
    assert json.loads((tmp_path / exported.packet_locator).read_bytes())["packet_stage"] == "BLIND"
    submitted, sources = _blind_submission(packet)
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox/result.json").write_bytes(submitted)
    outcome, receipt = ingest_research_result(
        artifact_root=tmp_path,
        packet=packet,
        packet_manifest=exported,
        allowed_result_locator="inbox/result.json",
        source_contents=sources,
        imported_at=NOW + timedelta(minutes=1),
        run_id="handoff-import-blind",
    )
    assert outcome.receipt.status == ResearchImportStatus.ACCEPTED
    assert receipt.state == HandoffState.ACCEPTED
    assert (tmp_path / receipt.sealed_submission_locator).read_bytes() == submitted
    seal_result_handoff(artifact_root=tmp_path, receipt=receipt, receipt_locator="receipts/blind.json")
    assert (tmp_path / "receipts/blind.json").read_bytes() == receipt.canonical_bytes()
    (tmp_path / "inbox/result.json").write_bytes(b"\n" + submitted)
    with pytest.raises(HandoffConflictError, match="immutable locator conflict"):
        ingest_research_result(
            artifact_root=tmp_path,
            packet=packet,
            packet_manifest=exported,
            allowed_result_locator="inbox/result.json",
            source_contents=sources,
            imported_at=NOW + timedelta(minutes=2),
            run_id="handoff-import-blind-replaced",
        )


def test_market_export_and_ingest_and_result_mismatch_quarantines(tmp_path: Path) -> None:
    packet = _inputs()["market_packet"]
    exported = export_research_packet(
        artifact_root=tmp_path,
        packet=packet,
        packet_locator="outbox/market.packet.json",
        manifest_locator="outbox/market.manifest.json",
        created_at=NOW,
    )
    submitted, sources = _market_submission(packet)
    (tmp_path / "inbox").mkdir()
    target = tmp_path / "inbox/market-result.json"
    target.write_bytes(submitted)
    accepted, receipt = ingest_research_result(
        artifact_root=tmp_path, packet=packet, packet_manifest=exported,
        allowed_result_locator="inbox/market-result.json", source_contents=sources,
        imported_at=NOW + timedelta(minutes=1), run_id="handoff-import-market",
    )
    assert accepted.result is not None
    assert receipt.state == HandoffState.ACCEPTED
    raw = json.loads(submitted)
    raw["packet_stage"] = "BLIND"
    target.write_bytes(canonical_json(raw).encode("utf-8"))
    bad, bad_receipt = ingest_research_result(
        artifact_root=tmp_path, packet=packet, packet_manifest=exported,
        allowed_result_locator="inbox/market-result.json", source_contents=sources,
        imported_at=NOW + timedelta(minutes=1), run_id="handoff-import-market-bad",
    )
    assert bad.result is None
    assert bad_receipt.state == HandoffState.QUARANTINED


def test_conflict_tamper_missing_sources_and_path_attacks_fail_closed(tmp_path: Path) -> None:
    packet = _packet()
    exported = export_research_packet(
        artifact_root=tmp_path, packet=packet, packet_locator="outbox/packet.json",
        manifest_locator="outbox/manifest.json", created_at=NOW,
    )
    other = packet.model_copy(update={"extensions": {"different": True}})
    with pytest.raises(HandoffConflictError):
        export_research_packet(
            artifact_root=tmp_path, packet=other, packet_locator="outbox/packet.json",
            manifest_locator="outbox/other.manifest.json", created_at=NOW,
        )
    (tmp_path / exported.packet_locator).write_bytes(b"tampered")
    with pytest.raises(HandoffConflictError, match="packet bytes"):
        ingest_research_result(
            artifact_root=tmp_path, packet=packet, packet_manifest=exported,
            allowed_result_locator="inbox/missing.json", source_contents={},
            imported_at=NOW, run_id="run",
        )
    with pytest.raises(HandoffPathError):
        export_research_packet(
            artifact_root=tmp_path, packet=packet, packet_locator="../escape.json",
            manifest_locator="outbox/safe.json", created_at=NOW,
        )
    outside = tmp_path.parent / "handoff-outside.json"
    outside.write_bytes(b"outside")
    link = tmp_path / "linked"
    link.symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(HandoffPathError):
        export_research_packet(
            artifact_root=tmp_path, packet=packet, packet_locator="linked/escape.json",
            manifest_locator="outbox/again.json", created_at=NOW,
        )


def test_wrong_manifest_and_missing_source_do_not_advance(tmp_path: Path) -> None:
    packet = _packet()
    exported = export_research_packet(
        artifact_root=tmp_path, packet=packet, packet_locator="outbox/packet.json",
        manifest_locator="outbox/manifest.json", created_at=NOW,
    )
    submitted, _ = _blind_submission(packet)
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox/result.json").write_bytes(submitted)
    with pytest.raises(ValueError, match="does not bind"):
        ingest_research_result(
            artifact_root=tmp_path, packet=packet,
            packet_manifest=replace(exported, packet_sha256="a" * 64),
            allowed_result_locator="inbox/result.json", source_contents={},
            imported_at=NOW, run_id="run",
        )
    outcome, receipt = ingest_research_result(
        artifact_root=tmp_path, packet=packet, packet_manifest=exported,
        allowed_result_locator="inbox/result.json", source_contents={},
        imported_at=NOW + timedelta(minutes=1), run_id="run-missing",
    )
    assert outcome.result is None
    assert receipt.state == HandoffState.QUARANTINED


def test_handoff_source_has_no_network_capabilities() -> None:
    assert audit_source_tree("src/polymarket_alpha/research").violations == ()
