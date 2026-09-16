"""P0-12: closed-world fixture integration proof."""

from src.polymarket_alpha.contracts import CandidateState
from src.polymarket_alpha.pilot import run_offline_fixture_pilot
from src.polymarket_alpha.recall.book_anomaly import BookAnomalyRecallProvider
from src.polymarket_alpha.recall.controversy import ControversyRecaller
from src.polymarket_alpha.recall.new_changed import NewChangedRecaller
from src.polymarket_alpha.recall.registry import ProviderRegistry
from src.polymarket_alpha.recall.structural_metadata import StructuralMetadataRecaller
from src.polymarket_alpha.recall.wallet import WalletRecallProvider
from src.polymarket_alpha.security import audit_source_tree
from src.polymarket_alpha.storage import AlphaRepository


def test_offline_fixture_pilot_runs_full_protocol_and_persists_atomic_no_order_ledger(tmp_path) -> None:
    repo = AlphaRepository(tmp_path / "alpha-pilot.db")
    result = run_offline_fixture_pilot(repo)
    assert result.lifecycle_state == CandidateState.SIMULATION_RECORDED
    assert result.ranked.decision.execution == "NO_ORDER"
    assert result.ranked.prediction.position_state.value == "SIMULATED"
    assert repo.get_contract(result.ranked.decision.record_id) is not None
    assert repo.get_contract(result.ranked.prediction.record_id) is not None
    assert all(repo.get_contract(record_id) is not None for record_id in result.persisted_record_ids)
    replay = run_offline_fixture_pilot(repo)
    assert replay == result


def test_p0_12_source_is_offline_security_auditable() -> None:
    assert audit_source_tree("src/polymarket_alpha/pilot").violations == ()


def test_all_recall_provider_branches_share_one_registry_and_book_is_optional() -> None:
    descriptors = (
        NewChangedRecaller().descriptor(),
        StructuralMetadataRecaller().descriptor(),
        ControversyRecaller().descriptor(),
        WalletRecallProvider().descriptor,
        BookAnomalyRecallProvider().descriptor,
    )
    registry = ProviderRegistry(descriptors)
    assert {item.provider_id for item in registry.active()} == {
        "new_changed",
        "structural_metadata",
        "controversy",
        "specialist_wallet",
    }
    assert {item.provider_id for item in registry.active(include_book=True)} == {
        "new_changed",
        "structural_metadata",
        "controversy",
        "specialist_wallet",
        "book_anomaly",
    }

    for disabled in descriptors:
        isolated = ProviderRegistry(
            item.model_copy(update={"enabled": item.provider_id != disabled.provider_id})
            for item in descriptors
        )
        assert disabled.provider_id not in {
            item.provider_id for item in isolated.active(include_book=True)
        }
