"""Tests for weather_dashboard.blend.blender — pure function, no DB."""

from __future__ import annotations

import math

import pytest

from weather_dashboard.blend import (
    BlendConfig,
    blend_probability,
    load_default_config,
)


def _cfg(**overrides) -> BlendConfig:
    base = dict(
        global_alpha=0.30,
        global_beta=0.70,
        blacklist_alpha=0.10,
        clip_lo=0.001,
        clip_hi=0.999,
        raw_model_blacklist=frozenset({"Milan", "Lucknow", "Austin", "Beijing"}),
    )
    base.update(overrides)
    return BlendConfig(**base)


def test_default_blend_normal_city():
    cfg = _cfg()
    r = blend_probability("Tokyo", model_p_yes_raw=0.60, market_implied_p_yes=0.40, config=cfg)
    assert r.blend_mode == "global"
    assert r.blend_alpha == 0.30
    assert r.blend_beta == 0.70
    assert math.isclose(r.p_yes_used, 0.30 * 0.60 + 0.70 * 0.40, abs_tol=1e-12)


def test_blacklist_city_uses_blacklist_alpha():
    cfg = _cfg()
    r = blend_probability("Milan", model_p_yes_raw=0.80, market_implied_p_yes=0.20, config=cfg)
    assert r.blend_mode == "blacklist"
    assert r.blend_alpha == 0.10
    assert r.blend_beta == 0.90
    assert math.isclose(r.p_yes_used, 0.10 * 0.80 + 0.90 * 0.20, abs_tol=1e-12)
    # All four blacklist cities should be treated identically.
    for city in ("Lucknow", "Austin", "Beijing"):
        rr = blend_probability(city, 0.80, 0.20, cfg)
        assert rr.blend_mode == "blacklist"


def test_clip_low():
    cfg = _cfg()
    r = blend_probability("Tokyo", model_p_yes_raw=0.0, market_implied_p_yes=0.0, config=cfg)
    assert r.p_yes_used == cfg.clip_lo


def test_clip_high():
    cfg = _cfg()
    r = blend_probability("Tokyo", model_p_yes_raw=1.0, market_implied_p_yes=1.0, config=cfg)
    assert r.p_yes_used == cfg.clip_hi


def test_missing_market_falls_back_to_raw():
    cfg = _cfg()
    r = blend_probability("Tokyo", model_p_yes_raw=0.42, market_implied_p_yes=None, config=cfg)
    assert r.blend_mode == "raw_only"
    assert r.blend_alpha == 1.0
    assert r.blend_beta == 0.0
    assert math.isclose(r.p_yes_used, 0.42, abs_tol=1e-12)


def test_missing_raw_falls_back_to_market():
    cfg = _cfg()
    r = blend_probability("Tokyo", model_p_yes_raw=None, market_implied_p_yes=0.42, config=cfg)
    assert r.blend_mode == "market_only"
    assert r.blend_alpha == 0.0
    assert r.blend_beta == 1.0
    assert math.isclose(r.p_yes_used, 0.42, abs_tol=1e-12)


def test_missing_both_raises():
    cfg = _cfg()
    with pytest.raises(ValueError):
        blend_probability("Tokyo", None, None, cfg)


def test_market_only_clips():
    cfg = _cfg()
    r = blend_probability("Tokyo", None, 1.5, cfg)
    assert r.p_yes_used == cfg.clip_hi
    r2 = blend_probability("Tokyo", None, -0.1, cfg)
    assert r2.p_yes_used == cfg.clip_lo


def test_blacklist_city_with_missing_market_uses_raw_only():
    """A blacklist city without market price should still surface raw_only —
    blacklist only kicks in when both inputs exist."""
    cfg = _cfg()
    r = blend_probability("Milan", model_p_yes_raw=0.55, market_implied_p_yes=None, config=cfg)
    assert r.blend_mode == "raw_only"
    assert math.isclose(r.p_yes_used, 0.55, abs_tol=1e-12)


def test_config_validation_alpha_beta_sum():
    with pytest.raises(ValueError):
        BlendConfig(
            global_alpha=0.40,
            global_beta=0.70,
            blacklist_alpha=0.10,
            clip_lo=0.001,
            clip_hi=0.999,
            raw_model_blacklist=frozenset(),
        )


def test_config_validation_clip_bounds():
    with pytest.raises(ValueError):
        BlendConfig(
            global_alpha=0.30,
            global_beta=0.70,
            blacklist_alpha=0.10,
            clip_lo=0.5,
            clip_hi=0.4,
            raw_model_blacklist=frozenset(),
        )


def test_load_default_config_from_repo_file():
    cfg = load_default_config()
    assert cfg.global_alpha == 0.30
    assert cfg.global_beta == 0.70
    assert cfg.blacklist_alpha == 0.10
    assert cfg.clip_lo == 0.001
    assert cfg.clip_hi == 0.999
    assert "Milan" in cfg.raw_model_blacklist
    assert "Lucknow" in cfg.raw_model_blacklist
    assert "Austin" in cfg.raw_model_blacklist
    assert "Beijing" in cfg.raw_model_blacklist
