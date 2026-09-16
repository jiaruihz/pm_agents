"""P0-06D specialist wallet recall provider tests.

Covers the work-order acceptance list: freshness just-before/at/after the
boundary, the stale historical store fixture, Address != Entity semantics,
ambiguous aliases, incomplete pagination/receipts, direction redaction across
nested features/reasons, retry/cross-run identity, input ordering and provider
isolation.  Every request is fixture-frozen or code-built against a fixed
observation clock; the provider is pure and offline.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.polymarket_alpha.contracts import RecallerType, canonical_json
from src.polymarket_alpha.recall import (
    ProviderBatch,
    ProviderDescriptor,
    ProviderRegistry,
    RecallAggregationRequest,
    RecallAggregator,
    RejectedRecall,
    recall_dedupe_key,
)
from src.polymarket_alpha.recall.wallet import (
    PROVIDER_VERSION,
    PUBLIC_FACT_EXCLUDED_FIELDS,
    WALLET_REASON_CODES,
    WalletAddressAlias,
    WalletRecallConfig,
    WalletRecallProvider,
    WalletRecallRejectionReason,
    WalletRecallRequest,
    wallet_public_leak_reasons,
)
from src.polymarket_alpha.security import audit_source_tree


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "wallet_recall"
AS_OF = datetime(2026, 8, 26, 7, 0, tzinfo=timezone.utc)
ADDR_A = "0x1111111111111111111111111111111111111111"
ADDR_B = "0x2222222222222222222222222222222222222222"
ADDR_C = "0x3333333333333333333333333333333333333333"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"), parse_float=Decimal)


def _fresh_payload() -> dict:
    return _load("fresh_snapshot_request.json")["request"]


def _request(payload: dict) -> WalletRecallRequest:
    return WalletRecallRequest(**payload)


def _provider(**config_kwargs) -> WalletRecallProvider:
    return WalletRecallProvider(WalletRecallConfig(**config_kwargs) if config_kwargs else None)


def _hit_by_market(hits, market_id: str):
    matching = [hit for hit in hits if hit.market_id == market_id]
    assert len(matching) == 1
    return matching[0]


def _set_observed(payload: dict, fact_id: str, when: datetime) -> None:
    for fact in payload["facts"]:
        if fact["fact_id"] == fact_id:
            fact["observed_at"] = when.isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Requirement 1: configurable freshness at the observation/as-of boundary.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("age", "expect_current"),
    [
        pytest.param(timedelta(seconds=3600, microseconds=1), False, id="just-before-boundary"),
        pytest.param(timedelta(seconds=3600), True, id="exactly-at-boundary"),
        pytest.param(timedelta(seconds=3599, microseconds=999999), True, id="just-after-boundary"),
    ],
)
def test_freshness_boundary_is_inclusive_at_max_age(age: timedelta, expect_current: bool) -> None:
    payload = _fresh_payload()
    payload["facts"] = [fact for fact in payload["facts"] if fact["fact_id"] == "fact-alpha-1"]
    payload["token_markets"] = [
        mapping for mapping in payload["token_markets"] if mapping["token_id"] == "tok-alpha-leg-a"
    ]
    _set_observed(payload, "fact-alpha-1", AS_OF - age)
    payload["pagination"]["window_start"] = (AS_OF - timedelta(hours=8)).isoformat().replace(
        "+00:00", "Z"
    )

    outcome = _provider(max_source_age_seconds=3600).recall(_request(payload))

    assert [hit.market_id for hit in outcome.hits] == (["market-alpha"] if expect_current else [])
    assert [hit.market_id for hit in outcome.historical_hits] == (
        [] if expect_current else ["market-alpha"]
    )
    for hit in outcome.hits:
        assert hit.historical_only is False
    for hit in outcome.historical_hits:
        assert hit.historical_only is True
    assert outcome.rejections == ()


def test_stale_historical_store_fixture_produces_no_current_hit() -> None:
    fixture = _load("stale_historical_store_snapshot.json")
    expect = fixture["expect"]

    outcome = _provider().recall(_request(fixture["request"]))

    assert outcome.hits == ()
    assert outcome.historical_hits == ()
    assert {item.fact_id: item.reason for item in outcome.rejections} == {
        fact_id: WalletRecallRejectionReason(reason)
        for fact_id, reason in expect["fact_rejections"].items()
    }


def test_stale_facts_with_covering_receipt_emit_historical_only_hits() -> None:
    payload = _fresh_payload()
    stale_at = AS_OF - timedelta(days=59)
    for fact in payload["facts"]:
        fact["observed_at"] = stale_at.isoformat().replace("+00:00", "Z")
    payload["pagination"]["window_start"] = (AS_OF - timedelta(days=60)).isoformat().replace(
        "+00:00", "Z"
    )

    outcome = _provider().recall(_request(payload))

    assert outcome.hits == ()
    assert sorted(hit.market_id for hit in outcome.historical_hits) == ["market-alpha", "market-beta"]
    assert all(hit.historical_only for hit in outcome.historical_hits)
    assert all(hit.valid_until > hit.observed_at for hit in outcome.historical_hits)
    assert outcome.rejections == ()


def test_aggregator_rejects_historical_wallet_hits_and_accepts_current() -> None:
    provider = _provider()
    payload = _fresh_payload()
    payload["facts"] = [
        fact for fact in payload["facts"] if fact["fact_id"] in {"fact-alpha-1", "fact-beta-1"}
    ]
    _set_observed(payload, "fact-beta-1", AS_OF - timedelta(days=59))
    payload["pagination"]["window_start"] = (AS_OF - timedelta(days=60)).isoformat().replace(
        "+00:00", "Z"
    )
    outcome = provider.recall(_request(payload))
    assert [hit.market_id for hit in outcome.hits] == ["market-alpha"]
    assert [hit.market_id for hit in outcome.historical_hits] == ["market-beta"]

    registry = ProviderRegistry((provider.descriptor,))
    aggregator = RecallAggregator(registry)
    batch = ProviderBatch(
        provider_id=provider.descriptor.provider_id,
        hits=outcome.hits + outcome.historical_hits,
    )
    aggregation = aggregator.aggregate(
        RecallAggregationRequest(
            run_id="agg-run-1",
            created_at=AS_OF,
            as_of=AS_OF,
            batches=(batch,),
        )
    )

    assert aggregation.rejected == (
        RejectedRecall(
            provider_id=provider.descriptor.provider_id,
            recall_hit_id=outcome.historical_hits[0].record_id,
            reason="HISTORICAL_ONLY",
        ),
    )
    assert [result.candidate.market_id for result in aggregation.results] == ["market-alpha"]
    rationale = aggregation.results[0].candidate.selection_rationale
    assert "SPECIALIST_WALLET:SPECIALIST_WALLET_ACTIVITY" in rationale


# ---------------------------------------------------------------------------
# Requirement 2: Address != Entity.
# ---------------------------------------------------------------------------


def test_address_entity_distinction_never_merges_addresses() -> None:
    fixture = _load("fresh_snapshot_request.json")
    expect = fixture["expect"]

    outcome = _provider().recall(_request(fixture["request"]))

    alpha = _hit_by_market(outcome.hits, "market-alpha")
    alpha_expect = expect["market_alpha"]
    assert list(alpha.features["specialist_wallet_addresses"]) == alpha_expect["addresses"]
    assert len(set(alpha_expect["addresses"])) == 2  # two addresses stay two addresses
    attributions = alpha.features["specialist_entity_attributions"]
    assert [item.entity_id for item in attributions] == alpha_expect["entity_ids"]
    assert [item.address for item in attributions] == alpha_expect["addresses"]
    assert len({item.provenance for item in attributions}) == 2  # explicit provenance per address
    for item in attributions:
        assert item.entity_id != item.address  # entity namespace is disjoint
    assert list(alpha.reason_codes) == alpha_expect["reason_codes"]

    beta = _hit_by_market(outcome.hits, "market-beta")
    beta_expect = expect["market_beta"]
    assert list(beta.features["specialist_wallet_addresses"]) == beta_expect["addresses"]
    assert beta.features["specialist_entity_attributions"] == ()
    assert list(beta.reason_codes) == beta_expect["reason_codes"]


def test_entity_id_cannot_be_an_address() -> None:
    with pytest.raises(ValidationError, match="Address != Entity"):
        WalletAddressAlias(
            address=ADDR_A,
            entity_id="0x4444444444444444444444444444444444444444",
            provenance="curated analyst mapping",
            confidence=Decimal("0.9"),
        )


def test_duplicate_identical_alias_is_not_ambiguous() -> None:
    payload = _fresh_payload()
    alias = payload["aliases"][0]
    payload["aliases"] = [alias, dict(alias)]

    outcome = _provider().recall(_request(payload))

    assert outcome.rejections == ()
    alpha = _hit_by_market(outcome.hits, "market-alpha")
    # address B lost its alias entry in this payload, so only A attributes
    assert [item.entity_id for item in alpha.features["specialist_entity_attributions"]] == [
        "ent-alpha"
    ]


# ---------------------------------------------------------------------------
# Requirement 3: receipts, mapping and aliases fail closed.
# ---------------------------------------------------------------------------


_FAIL_CLOSED_CASES = _load("fail_closed_cases.json")["cases"]


@pytest.mark.parametrize(
    "case", _FAIL_CLOSED_CASES, ids=[case["name"] for case in _FAIL_CLOSED_CASES]
)
def test_receipt_mapping_and_alias_cases_fail_closed(case: dict) -> None:
    expect = case["expect"]

    outcome = _provider().recall(_request(case["request"]))

    assert {item.fact_id: item.reason for item in outcome.rejections} == {
        fact_id: WalletRecallRejectionReason(reason)
        for fact_id, reason in expect["fact_rejections"].items()
    }
    assert sorted(hit.market_id for hit in outcome.hits) == expect["current_market_ids"]
    assert sorted(hit.market_id for hit in outcome.historical_hits) == expect["historical_market_ids"]


def test_window_exact_boundary_coverage_is_allowed() -> None:
    payload = _fresh_payload()
    payload["facts"] = [fact for fact in payload["facts"] if fact["fact_id"] == "fact-alpha-1"]
    payload["token_markets"] = [
        mapping for mapping in payload["token_markets"] if mapping["token_id"] == "tok-alpha-leg-a"
    ]
    payload["pagination"]["window_start"] = (AS_OF - timedelta(hours=1)).isoformat().replace(
        "+00:00", "Z"
    )
    payload["pagination"]["window_end"] = AS_OF.isoformat().replace("+00:00", "Z")
    _set_observed(payload, "fact-alpha-1", AS_OF - timedelta(hours=1))

    outcome = _provider(max_source_age_seconds=3600).recall(_request(payload))

    assert outcome.rejections == ()
    assert [hit.market_id for hit in outcome.hits] == ["market-alpha"]


# ---------------------------------------------------------------------------
# Requirement 4: direction stays private; public payloads are redacted.
# ---------------------------------------------------------------------------


def test_public_hits_carry_no_direction_or_token_bytes() -> None:
    fixture = _load("fresh_snapshot_request.json")
    payload = fixture["request"]
    _set_observed(payload, "fact-beta-1", AS_OF - timedelta(days=59))
    payload["pagination"]["window_start"] = (AS_OF - timedelta(days=60)).isoformat().replace(
        "+00:00", "Z"
    )
    outcome = _provider().recall(_request(payload))
    assert outcome.hits and outcome.historical_hits

    token_ids = [mapping["token_id"] for mapping in payload["token_markets"]]
    # distinctive private decimals only: short strings like "3" occur inside
    # hex record ids and timestamps by coincidence
    private_values = {"150.5", "42.25"}
    for hit in outcome.hits + outcome.historical_hits:
        public_json = canonical_json(hit)
        assert wallet_public_leak_reasons(
            {
                "features": hit.features,
                "reason_codes": hit.reason_codes,
                "extensions": hit.extensions,
            }
        ) == ()
        for word in ("BUY", "SELL", "LONG", "SHORT", "buy", "sell", "long", "short"):
            assert word not in public_json
        for token_id in token_ids:
            assert token_id not in public_json  # token legs encode direction
        for value in private_values:
            assert value not in public_json  # notionals/sizes stay private
        for key in _nested_keys(hit.model_dump(mode="python")):
            assert key not in PUBLIC_FACT_EXCLUDED_FIELDS


def _nested_keys(value) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            keys.add(key)
            keys |= _nested_keys(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            keys |= _nested_keys(item)
    return keys


@pytest.mark.parametrize(
    ("payload", "expected_leak"),
    [
        ({"wallet_notes": {"side": "HOLD"}}, "key_fragment:side"),
        ({"meta": [{"position_direction": "LONG"}]}, "key_fragment:position"),
        ({"agg": {"notional_usdc": "12.5"}}, "key_fragment:notional"),
        ({"traded": {"token_id": "tok-1"}}, "key_fragment:token"),
        ({"sizing": {"order_size": 3}}, "key_fragment:size"),
        ({"answer": "YES"}, "direction_token"),
        ({"answer": "NO"}, "direction_token"),
        ({"comment": "wallet buys YES tokens"}, "fragment:buy"),
        ({"comment": "heavily sold at launch"}, "fragment:sold"),
        ({"fair_value_note": "see model"}, "key_fragment:fair"),
    ],
)
def test_leak_scanner_catches_nested_adversarial_payloads(payload: dict, expected_leak: str) -> None:
    reasons = wallet_public_leak_reasons(payload)
    assert expected_leak in " ".join(reasons)


def test_leak_scanner_accepts_public_wallet_features() -> None:
    outcome = _provider().recall(_request(_fresh_payload()))
    for hit in outcome.hits:
        assert wallet_public_leak_reasons(
            {"features": hit.features, "reason_codes": hit.reason_codes, "extensions": hit.extensions}
        ) == ()


def test_direction_flip_keeps_public_fingerprint_and_changes_private() -> None:
    def _payload(side: str, position: str, notional: str, size: str) -> dict:
        payload = _fresh_payload()
        payload["facts"] = [fact for fact in payload["facts"] if fact["fact_id"] == "fact-alpha-1"]
        payload["token_markets"] = [
            mapping
            for mapping in payload["token_markets"]
            if mapping["token_id"] == "tok-alpha-leg-a"
        ]
        payload["aliases"] = []
        fact = payload["facts"][0]
        fact["side"] = side
        fact["position_direction"] = position
        fact["notional_usdc"] = notional
        fact["size"] = size
        return payload

    buy = _provider().recall(_request(_payload("BUY", "LONG", "150.5", "10")))
    sell = _provider().recall(_request(_payload("SELL", "SHORT", "88", "7")))

    assert buy.public_input_sha256 == sell.public_input_sha256
    assert buy.private_input_sha256 != sell.private_input_sha256
    assert buy.hits[0].record_id == sell.hits[0].record_id
    assert recall_dedupe_key(buy.hits[0]) == recall_dedupe_key(sell.hits[0])


# ---------------------------------------------------------------------------
# Requirement 5: deterministic SPECIALIST_WALLET-only output.
# ---------------------------------------------------------------------------


def test_hits_use_wallet_reason_codes_only_and_no_valuation() -> None:
    fresh = _provider().recall(_request(_fresh_payload()))

    payload = _fresh_payload()
    for fact in payload["facts"]:
        _set_observed(payload, fact["fact_id"], AS_OF - timedelta(days=59))
    payload["pagination"]["window_start"] = (AS_OF - timedelta(days=60)).isoformat().replace(
        "+00:00", "Z"
    )
    stale = _provider().recall(_request(payload))

    config = WalletRecallConfig()
    for hit in fresh.hits + fresh.historical_hits + stale.hits + stale.historical_hits:
        assert hit.recaller is RecallerType.SPECIALIST_WALLET
        assert hit.recaller_version == PROVIDER_VERSION == config.provider_version
        assert hit.source == config.provider_id
        assert set(hit.reason_codes) <= WALLET_REASON_CODES
        assert hit.raw_score == config.hit_raw_score  # fixed weight, not a probability
        assert hit.extensions == {}
        for key in hit.features:
            assert "price" not in key and "prob" not in key and "fair" not in key


# ---------------------------------------------------------------------------
# Retry/cross-run identity, input ordering, provider isolation.
# ---------------------------------------------------------------------------


def test_exact_retry_is_stable_and_cross_run_is_semantically_dedupable() -> None:
    provider = _provider()
    first = provider.recall(_request(_fresh_payload()))
    exact_retry = provider.recall(_request(_fresh_payload()))
    second_payload = _fresh_payload()
    second_payload["run_id"] = "wallet-recall-run-2"
    second = provider.recall(_request(second_payload))

    assert exact_retry == first
    assert [hit.record_id for hit in first.hits] != [hit.record_id for hit in second.hits]
    assert [recall_dedupe_key(hit) for hit in first.hits] == [
        recall_dedupe_key(hit) for hit in second.hits
    ]
    assert first.public_input_sha256 == second.public_input_sha256
    assert first.private_input_sha256 == second.private_input_sha256
    for hit_a, hit_b in zip(first.hits, second.hits):
        assert hit_a.model_dump(exclude={"record_id", "run_id"}) == hit_b.model_dump(
            exclude={"record_id", "run_id"}
        )
        assert hit_a.run_id != hit_b.run_id


def test_input_ordering_does_not_change_outcome() -> None:
    provider = _provider()
    baseline = provider.recall(_request(_fresh_payload()))

    shuffled = _fresh_payload()
    shuffled["facts"] = list(reversed(shuffled["facts"]))
    shuffled["aliases"] = list(reversed(shuffled["aliases"]))
    shuffled["token_markets"] = [
        shuffled["token_markets"][2],
        shuffled["token_markets"][0],
        shuffled["token_markets"][1],
    ]
    reordered = provider.recall(_request(shuffled))

    assert reordered.hits == baseline.hits
    assert reordered.historical_hits == baseline.historical_hits
    assert reordered.rejections == baseline.rejections
    assert reordered.public_input_sha256 == baseline.public_input_sha256
    assert reordered.private_input_sha256 == baseline.private_input_sha256


def test_provider_isolation_between_instances_and_providers() -> None:
    payload = _fresh_payload()
    outcome_a = _provider().recall(_request(payload))
    outcome_b = _provider().recall(_request(payload))
    assert outcome_a == outcome_b

    mismatched = WalletRecallProvider(WalletRecallConfig(expected_source_id="other_store"))
    rejected = mismatched.recall(_request(payload))
    assert rejected.hits == () and rejected.historical_hits == ()
    assert all(
        item.reason == WalletRecallRejectionReason.SOURCE_IDENTITY_MISMATCH
        for item in rejected.rejections
    )
    # the misconfigured provider leaves no state behind for the default one
    assert _provider().recall(_request(payload)) == outcome_a

    hit = outcome_a.hits[0]
    registry = ProviderRegistry(
        (
            _provider().descriptor,
            ProviderDescriptor(
                provider_id="new_changed",
                recaller=RecallerType.NEW_CHANGED,
                recaller_version="new-v1",
            ),
        )
    )
    assert registry.validate_hit("specialist_wallet", hit, include_book=False) is None
    assert (
        registry.validate_hit("new_changed", hit, include_book=False)
        == "SOURCE_PROVIDER_MISMATCH"
    )
    mismatched_recaller = ProviderRegistry(
        (
            ProviderDescriptor(
                provider_id="specialist_wallet",
                recaller=RecallerType.NEW_CHANGED,
                recaller_version="new-v1",
            ),
        )
    )
    assert (
        mismatched_recaller.validate_hit("specialist_wallet", hit, include_book=False)
        == "RECALLER_TYPE_MISMATCH"
    )


def test_wallet_module_passes_offline_source_audit() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "polymarket_alpha" / "recall"
    result = audit_source_tree(root)
    assert result.passed, result.violations


# ---------------------------------------------------------------------------
# Golden fingerprints (sealed determinism anchor).
# ---------------------------------------------------------------------------


def test_golden_fingerprints_match_sealed_fixture() -> None:
    golden = _load("golden_request_fingerprints.json")
    outcome = _provider(**golden["config"]).recall(_request(golden["request"]))

    assert outcome.public_input_sha256 == golden["expected"]["public_input_sha256"]
    assert outcome.private_input_sha256 == golden["expected"]["private_input_sha256"]
    assert [hit.record_id for hit in outcome.hits] == golden["expected"]["hit_record_ids"]
    assert [hit.market_id for hit in outcome.hits] == golden["expected"]["current_market_ids"]
    assert [hit.market_id for hit in outcome.historical_hits] == golden["expected"][
        "historical_market_ids"
    ]
