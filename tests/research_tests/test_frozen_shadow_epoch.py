import pytest
from decimal import Decimal

from weather_model_evaluation import FrozenShadowEpoch, maker_proxy_fill, score_frozen_shadow


def _rows():
    return [
        {
            "row_id": "one",
            "target_date": "2026-01-01",
            "liquidity_role": role,
            "p_model": .8,
            "market_p": .6,
            "label": 1,
            "orders": 0,
            "fills": 0,
            "notional": 0,
            "runtime_orders": 0,
            "runtime_fills": 0,
            "runtime_notional": 0,
        }
        for role in ("maker", "taker")
    ]


_DATASET_HASH = "a" * 64
_SPLIT_HASH = "b" * 64
_BUILD_KWARGS = {
    "dataset_hash": _DATASET_HASH,
    "split_manifest_hash": _SPLIT_HASH,
    "model_version": "maker-shadow-v1",
}


def test_shadow_epoch_is_zero_notional_and_has_no_promotion_authority():
    epoch = FrozenShadowEpoch.build(_rows(), target_dates=["2026-01-01"], **_BUILD_KWARGS)
    assert epoch.zero_notional and not epoch.promotion_authority
    assert maker_proxy_fill({"quote_touched": True, "sell_volume": 9, "visible_queue": 5, "own_size": 5}) is False
    assert maker_proxy_fill({"quote_touched": True, "sell_volume": 10, "visible_queue": 5, "own_size": 5}) is True
    score = score_frozen_shadow(_rows(), draws=10)
    assert score["queue_lifecycle"].startswith("not_real")
    assert score["settled_rows"] == 1
    assert score["maker_increment_status"] == "blocked_missing_execution_evidence"


def test_shadow_rejects_runtime_execution_or_mismatched_maker_rows():
    with pytest.raises(ValueError, match="zero-notional"):
        FrozenShadowEpoch.build([{**_rows()[0], "orders": 1}], target_dates=["2026-01-01"], **_BUILD_KWARGS)
    rows = _rows(); rows[1]["row_id"] = "different"
    with pytest.raises(ValueError, match="same rows"):
        score_frozen_shadow(rows, draws=10)
    with pytest.raises(ValueError, match="lacks queue/volume"):
        maker_proxy_fill({"quote_touched": True})


def test_shadow_requires_explicit_accounting_probability_and_label_evidence():
    row = _rows()[0]
    for missing, message in (
        ("orders", "zero-accounting evidence"),
        ("p_model", "probability evidence"),
        ("label", "settlement-label evidence"),
    ):
        incomplete = dict(row)
        incomplete.pop(missing)
        with pytest.raises(ValueError, match=message):
            FrozenShadowEpoch.build(
                [incomplete], target_dates=["2026-01-01"], **_BUILD_KWARGS
            )


def test_complete_maker_proxy_reports_same_rows_fee_adjusted_increment() -> None:
    rows = _rows()
    maker = rows[0]
    maker.update(
        {
            "quote_touched": True,
            "sell_volume": 10,
            "visible_queue": 5,
            "own_size": 5,
            "maker_price": 0.55,
            "taker_price": 0.60,
            "maker_fee_usd": 0.0,
            "taker_fee_usd": 0.05,
            "markout_price": 0.54,
        }
    )
    score = score_frozen_shadow(rows, draws=10)
    assert score["maker_increment_status"] == "proxy_only"
    assert score["maker_proxy_fills"] == 1
    assert Decimal(score["maker_proxy_increment_usd"]) > 0
    assert "maker_vs_taker_target_date_block_bootstrap" in score


def test_shadow_binding_is_required_and_legacy_cannot_be_silently_promoted() -> None:
    with pytest.raises(TypeError):
        FrozenShadowEpoch.build(_rows(), target_dates=["2026-01-01"], model_version="v1")
    with pytest.raises(ValueError, match="real SHA-256"):
        FrozenShadowEpoch.build(
            _rows(), target_dates=["2026-01-01"], dataset_hash="legacy_unbound",
            split_manifest_hash=_SPLIT_HASH, model_version="v1",
        )
    with pytest.raises(ValueError, match="allow_legacy_unbound"):
        FrozenShadowEpoch.build_legacy_unbound(_rows(), target_dates=["2026-01-01"])
    legacy = FrozenShadowEpoch.build_legacy_unbound(
        _rows(), target_dates=["2026-01-01"], allow_legacy_unbound=True
    )
    assert legacy.dataset_hash == "legacy_unbound"
    assert not legacy.promotion_authority


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), "NaN", "Infinity"])
def test_maker_proxy_rejects_nonfinite_quantities_and_economics(bad) -> None:
    with pytest.raises(ValueError, match="finite"):
        maker_proxy_fill({"quote_touched": True, "sell_volume": bad, "visible_queue": 1, "own_size": 1})
    rows = _rows()
    rows[0].update({"quote_touched": True, "sell_volume": 2, "visible_queue": 1, "own_size": 1, "maker_price": .5, "taker_price": .6, "maker_fee_usd": bad, "taker_fee_usd": 0, "markout_price": .5})
    with pytest.raises(ValueError, match="finite"):
        score_frozen_shadow(rows, draws=10)


def test_unfilled_maker_proxy_still_rejects_nonfinite_evidence() -> None:
    with pytest.raises(ValueError, match="finite"):
        maker_proxy_fill(
            {
                "quote_touched": False,
                "sell_volume": float("nan"),
                "visible_queue": 1,
                "own_size": 1,
            }
        )
    rows = _rows()
    rows[0].update(
        {
            "quote_touched": False,
            "sell_volume": 0,
            "visible_queue": 1,
            "own_size": 1,
            "maker_price": 0.5,
            "taker_price": 0.6,
            "maker_fee_usd": float("inf"),
            "taker_fee_usd": 0,
            "markout_price": 0.5,
        }
    )
    with pytest.raises(ValueError, match="finite"):
        score_frozen_shadow(rows, draws=10)
