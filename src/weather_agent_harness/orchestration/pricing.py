"""Versioned ChatGPT Codex credit accounting for measured worker usage."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import UsageRecord


RATE_CARD_ID = "chatgpt-codex-credits-2026-08-13"
RATE_CARD_SOURCE = "https://developers.openai.com/codex/pricing/"


@dataclass(frozen=True)
class CreditRates:
    input_per_million: float
    cached_input_per_million: float
    output_per_million: float


_ALIASES = {
    "gpt-5.6": "gpt-5.6-sol",
    "gpt-5.6-sol": "gpt-5.6-sol",
    "gpt-5.6-terra": "gpt-5.6-terra",
    "gpt-5.6-luna": "gpt-5.6-luna",
}

_RATES = {
    "gpt-5.6-sol": CreditRates(125.0, 12.5, 750.0),
    "gpt-5.6-terra": CreditRates(50.0, 5.0, 300.0),
    "gpt-5.6-luna": CreditRates(5.0, 0.5, 30.0),
}


def canonical_model(model: str) -> str:
    try:
        return _ALIASES[model.strip().lower()]
    except KeyError as exc:
        raise ValueError(f"unsupported Codex credit model: {model}") from exc


def models_match(requested: str, observed: str) -> bool:
    try:
        return canonical_model(requested) == canonical_model(observed)
    except ValueError:
        return requested.strip().lower() == observed.strip().lower()


def _credits(usage: UsageRecord, model: str, *, fast_mode: bool) -> float:
    if any(
        value is None
        for value in (usage.input_tokens, usage.output_tokens, usage.cached_tokens)
    ):
        raise ValueError("pricing requires complete token usage")
    rates = _RATES[canonical_model(model)]
    input_tokens = usage.input_tokens or 0
    cached_tokens = usage.cached_tokens or 0
    output_tokens = usage.output_tokens or 0
    uncached_tokens = input_tokens - cached_tokens
    total = (
        uncached_tokens * rates.input_per_million
        + cached_tokens * rates.cached_input_per_million
        + output_tokens * rates.output_per_million
    ) / 1_000_000
    return total * (2.5 if fast_mode else 1.0)


def price_usage(
    usage: UsageRecord,
    *,
    model: str,
    baseline_model: str = "gpt-5.6-sol",
    fast_mode: bool = False,
) -> UsageRecord:
    """Attach auditable actual and same-token baseline credit estimates."""

    actual_model = canonical_model(model)
    baseline = canonical_model(baseline_model)
    actual_cost = _credits(usage, actual_model, fast_mode=fast_mode)
    baseline_cost = _credits(usage, baseline, fast_mode=fast_mode)
    savings = baseline_cost - actual_cost
    return usage.model_copy(
        update={
            "estimated_cost_usd": None,
            "billing_model": actual_model,
            "service_tier": "fast" if fast_mode else "standard",
            "rate_card_id": RATE_CARD_ID,
            "estimated_cost_credits": actual_cost,
            "baseline_model": baseline,
            "baseline_cost_credits": baseline_cost,
            "savings_credits": savings,
            "savings_ratio": savings / baseline_cost if baseline_cost else 0.0,
        }
    )


__all__ = [
    "RATE_CARD_ID",
    "RATE_CARD_SOURCE",
    "canonical_model",
    "models_match",
    "price_usage",
]
