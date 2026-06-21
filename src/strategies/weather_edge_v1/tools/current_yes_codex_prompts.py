"""Versioned Codex prompts for current-YES weather preflight."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CurrentYesCodexPrompt:
    version: str
    title: str
    description: str
    instructions: str


PROMPT_VERSIONS: dict[str, CurrentYesCodexPrompt] = {
    "current_yes_codex_v1_reheat_guard": CurrentYesCodexPrompt(
        version="current_yes_codex_v1_reheat_guard",
        title="v1 reheat guard",
        description="Original conservative reviewer; often labels uncertain setups as veto or shadow_only.",
        instructions=(
            "You are a weather derivatives pre-trade risk reviewer. "
            "Review whether a BUY_YES trade on the current running-max temperature bracket is sensible. "
            "Think like a human weather trader: inspect the full hourly temperature path, recent observed trend, "
            "observation cadence/freshness, humidity/cloud/wind context, and whether the model edge is credible. "
            "Do not invent data. Prefer veto or shadow_only when the setup depends on stale observations, an abnormal "
            "forecast curve, remaining afternoon reheat risk, or a fragile one-hour dip. "
            "Return JSON only with keys: decision (allow|veto|shadow_only), confidence (0..1), "
            "risk_tags (array of short strings), temperature_pattern_summary (short string), "
            "reasons (array of short strings), action (short string)."
        ),
    ),
    "current_yes_codex_v2_veto_loss_detector": CurrentYesCodexPrompt(
        version="current_yes_codex_v2_veto_loss_detector",
        title="v2 veto loss detector",
        description="Treats shadow_only as commentary; veto is reserved for clear likely loser setups.",
        instructions=(
            "You are a weather derivatives pre-trade risk reviewer for a BUY_YES trade on the current running-max "
            "temperature bracket. Your job is not to be generally cautious; your job is to identify cases that are "
            "likely to be losing trades. Inspect the observed temperature trend, freshness/cadence, full forecast "
            "hourly path, remaining heating window, humidity/cloud/wind context, and market/model edge. "
            "Use decision=veto only when the evidence clearly says the current high is unlikely to survive or the "
            "setup is based on stale/misleading state. Use decision=shadow_only for uncertain or thin-context cases "
            "that should be logged but not filtered in backtests. Use decision=allow when the current high has a "
            "credible survival path and the market/model edge is not obviously stale. Missing context by itself is "
            "not a veto unless the missing field is essential to the trade thesis. "
            "Return JSON only with keys: decision (allow|veto|shadow_only), confidence (0..1), "
            "risk_tags (array of short strings), temperature_pattern_summary (short string), "
            "reasons (array of short strings), action (short string)."
        ),
    ),
    "current_yes_codex_v3_price_aware_veto": CurrentYesCodexPrompt(
        version="current_yes_codex_v3_price_aware_veto",
        title="v3 price-aware veto",
        description="Requires weather risk to overwhelm the quoted edge before vetoing.",
        instructions=(
            "You are a weather derivatives pre-trade risk reviewer for a BUY_YES trade on the current running-max "
            "temperature bracket. First decide the weather pattern: mature peak/fade, still-heating day, stale "
            "observation race, fragile one-hour dip, or unclear. Then compare that risk to the market/model terms "
            "in market_and_model: ask, p_yes_win, and edge_at_limit. Use decision=veto only when the weather pattern "
            "would make the YES materially overpriced at the quoted ask, or when stale state makes the apparent edge "
            "untrustworthy. Use decision=shadow_only for ambiguous weather or missing-context cases; shadow_only is "
            "an advisory label, not a trade filter. Use decision=allow when the price/edge appears to compensate for "
            "the remaining weather risk. "
            "Return JSON only with keys: decision (allow|veto|shadow_only), confidence (0..1), "
            "risk_tags (array of short strings), temperature_pattern_summary (short string), "
            "reasons (array of short strings), action (short string)."
        ),
    ),
}

PROMPT_VERSION_ALIASES = {
    "v1": "current_yes_codex_v1_reheat_guard",
    "reheat_guard": "current_yes_codex_v1_reheat_guard",
    "v2": "current_yes_codex_v2_veto_loss_detector",
    "veto_loss_detector": "current_yes_codex_v2_veto_loss_detector",
    "v3": "current_yes_codex_v3_price_aware_veto",
    "price_aware": "current_yes_codex_v3_price_aware_veto",
}

DEFAULT_PROMPT_VERSION = "current_yes_codex_v2_veto_loss_detector"


def normalize_prompt_version(version: str | None) -> str:
    raw = (version or DEFAULT_PROMPT_VERSION).strip()
    normalized = PROMPT_VERSION_ALIASES.get(raw, raw)
    if normalized not in PROMPT_VERSIONS:
        allowed = ", ".join(sorted(PROMPT_VERSIONS))
        raise ValueError(f"unknown current-YES Codex prompt version {raw!r}; allowed: {allowed}")
    return normalized


def build_current_yes_codex_prompt(payload: dict[str, Any], version: str | None = None) -> str:
    prompt_version = normalize_prompt_version(version)
    spec = PROMPT_VERSIONS[prompt_version]
    return (
        f"PROMPT_VERSION: {spec.version}\n"
        f"PROMPT_TITLE: {spec.title}\n"
        f"{spec.instructions}"
        f"\n\nINPUT_JSON:\n{json.dumps(payload, ensure_ascii=False, sort_keys=True)}"
    )
