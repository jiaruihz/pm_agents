"""
blender.py

Pure function that turns (raw model probability, market-implied probability)
into the probability used by the trading engine, with config-driven weights and
a per-city blacklist for cities whose raw forecast model has been shown to be
materially worse than market.

Reference design: docs/WEATHER_CITY_BLEND_MODEL_IMPLEMENTATION_PLAN_2026-06-05.md §2.1
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


DEFAULT_CONFIG_PATH = Path(__file__).with_name("city_blend_config.json")


@dataclass(frozen=True)
class BlendConfig:
    global_alpha: float = 0.30
    global_beta: float = 0.70
    blacklist_alpha: float = 0.10
    clip_lo: float = 0.001
    clip_hi: float = 0.999
    raw_model_blacklist: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not 0.0 <= self.global_alpha <= 1.0:
            raise ValueError(f"global_alpha out of [0,1]: {self.global_alpha}")
        if not 0.0 <= self.blacklist_alpha <= 1.0:
            raise ValueError(f"blacklist_alpha out of [0,1]: {self.blacklist_alpha}")
        if abs((self.global_alpha + self.global_beta) - 1.0) > 1e-9:
            raise ValueError(
                f"global_alpha + global_beta must equal 1.0 "
                f"(got {self.global_alpha} + {self.global_beta})"
            )
        if not 0.0 < self.clip_lo < self.clip_hi < 1.0:
            raise ValueError(
                f"clip bounds invalid: lo={self.clip_lo} hi={self.clip_hi}"
            )


@dataclass(frozen=True)
class BlendResult:
    p_yes_used: float
    blend_alpha: float
    blend_beta: float
    blend_mode: str  # "global" | "blacklist" | "market_only" | "raw_only"
    reason: str


def load_default_config(path: Optional[Path] = None) -> BlendConfig:
    src = Path(path) if path else DEFAULT_CONFIG_PATH
    data = json.loads(src.read_text(encoding="utf-8"))
    return BlendConfig(
        global_alpha=float(data["global_alpha"]),
        global_beta=float(data["global_beta"]),
        blacklist_alpha=float(data["blacklist_alpha"]),
        clip_lo=float(data["clip_lo"]),
        clip_hi=float(data["clip_hi"]),
        raw_model_blacklist=frozenset(data.get("raw_model_blacklist", [])),
    )


def _clip(p: float, lo: float, hi: float) -> float:
    if p < lo:
        return lo
    if p > hi:
        return hi
    return p


def blend_probability(
    city: str,
    model_p_yes_raw: Optional[float],
    market_implied_p_yes: Optional[float],
    config: BlendConfig,
) -> BlendResult:
    """
    Combine raw model probability with market-implied probability.

    Decision table:
        raw     market   mode          formula
        -----   ------   -----------   -----------------------------------
        set     set      global        alpha*raw + beta*market
        set     set      blacklist     blacklist_alpha*raw + (1-bl_a)*market
        set     None     raw_only      raw
        None    set      market_only   market
        None    None     —             ValueError
    """
    if model_p_yes_raw is None and market_implied_p_yes is None:
        raise ValueError(
            f"blend_probability({city}): both model_p_yes_raw and "
            f"market_implied_p_yes are None"
        )

    if model_p_yes_raw is None:
        return BlendResult(
            p_yes_used=_clip(float(market_implied_p_yes), config.clip_lo, config.clip_hi),
            blend_alpha=0.0,
            blend_beta=1.0,
            blend_mode="market_only",
            reason="missing model_p_yes_raw",
        )

    if market_implied_p_yes is None:
        return BlendResult(
            p_yes_used=_clip(float(model_p_yes_raw), config.clip_lo, config.clip_hi),
            blend_alpha=1.0,
            blend_beta=0.0,
            blend_mode="raw_only",
            reason="missing market_implied_p_yes",
        )

    if city in config.raw_model_blacklist:
        alpha = config.blacklist_alpha
        mode = "blacklist"
        reason = f"city {city} is on raw_model_blacklist"
    else:
        alpha = config.global_alpha
        mode = "global"
        reason = "global default blend"

    beta = 1.0 - alpha
    p = alpha * float(model_p_yes_raw) + beta * float(market_implied_p_yes)
    return BlendResult(
        p_yes_used=_clip(p, config.clip_lo, config.clip_hi),
        blend_alpha=alpha,
        blend_beta=beta,
        blend_mode=mode,
        reason=reason,
    )
