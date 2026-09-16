"""Scoring utilities for markets."""

from typing import Dict

from .config import get_settings


def clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


def trigger_bonus(trigger_type: str) -> float:
    mapping = {
        "definition_driven": 1.0,
        "procedural_vote": 1.0,
        "data_print": 1.0,
        "military_action": 0.7,
        "financial_price_level": 0.7,
        "mixed": 0.5,
        "other": 0.5,
        "unknown": 0.0,
    }
    return mapping.get(trigger_type, 0.0)


def liquidity_score(volume: float, depth_total: float, spread_pct_mid: float) -> float:
    settings = get_settings()
    vol_score = clamp(volume / settings.vol_score_threshold, 0, 1) * 100 if volume is not None else 0
    depth_score = clamp(depth_total / settings.depth_score_threshold, 0, 1) * 100 if depth_total is not None else 0
    spread_score = 100 * max(0, 1 - (spread_pct_mid or 0) / settings.spread_pct_cap) if spread_pct_mid is not None else 0
    return 0.4 * vol_score + 0.4 * depth_score + 0.2 * spread_score


def friction_score(liquidity_score_val: float, spread_pct_mid: float) -> float:
    settings = get_settings()
    spread_component = 100 * max(0, 1 - (spread_pct_mid or 0) / settings.spread_pct_cap) if spread_pct_mid is not None else 0
    return clamp(0.7 * liquidity_score_val + 0.3 * spread_component, 0, 100)


def rule_score(clarity_score: float, dispute_risk_score: float, trigger_type: str) -> float:
    clarity = clarity_score or 0
    dispute = dispute_risk_score or 0
    trig = trigger_bonus(trigger_type)
    return (0.5 * clarity + 0.3 * (1 - dispute) + 0.2 * trig) * 100


def total_score(rule_score_val: float, friction_score_val: float) -> float:
    settings = get_settings()
    return settings.w_rule * rule_score_val + settings.w_friction * friction_score_val


def build_feature_summary(metrics: Dict[str, float], rule_fields: Dict[str, float]) -> Dict[str, float]:
    depth_total = (metrics.get("depth_1pct_bid", 0) or 0) + (metrics.get("depth_1pct_ask", 0) or 0)
    lq = liquidity_score(rule_fields.get("volume", 0) or 0, depth_total, metrics.get("spread_pct_mid"))
    fr = friction_score(lq, metrics.get("spread_pct_mid"))
    rs = rule_score(rule_fields.get("clarity_score"), rule_fields.get("dispute_risk_score"), rule_fields.get("trigger_type", "unknown"))
    ts = total_score(rs, fr)
    return {
        "liquidity_score": lq,
        "friction_score": fr,
        "rule_score": rs,
        "total_score": ts,
    }

