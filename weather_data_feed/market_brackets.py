from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


_NUMBER_RE = re.compile(r"(?<![\d.])-?\d+(?:\.\d+)?")


@dataclass(frozen=True)
class MarketBracket:
    label: str
    low: float | None
    high: float | None
    bottom: bool = False
    top: bool = False

    def contains(self, value: float) -> bool:
        if self.bottom:
            return self.high is not None and value <= self.high
        if self.top:
            return self.low is not None and value >= self.low
        return self.low is not None and self.high is not None and self.low <= value <= self.high

    def as_dict(self, *, include_label: bool = True) -> dict[str, Any]:
        out = {
            "low": self.low,
            "high": self.high,
            "bottom": self.bottom,
            "top": self.top,
        }
        if include_label:
            out["label"] = self.label
        return out


def normalize_label(label: str) -> str:
    return str(label).replace("°C", "").replace("°F", "").replace("°", "").strip()


def parse_market_bracket(label: str, question: str = "") -> MarketBracket | None:
    lab = normalize_label(label)
    q = str(question or "").lower()
    nums = _NUMBER_RE.findall(lab)
    if not nums:
        return None
    is_bottom = "or below" in q or "or lower" in q
    is_top = lab.endswith("+") or "or higher" in q or "or above" in q
    if is_bottom:
        return MarketBracket(label=lab, low=None, high=float(nums[0]), bottom=True, top=False)
    if is_top:
        return MarketBracket(label=lab, low=float(nums[0]), high=None, bottom=False, top=True)
    if "-" in lab and len(nums) >= 2:
        return MarketBracket(label=lab, low=float(nums[0]), high=float(nums[1]), bottom=False, top=False)
    value = float(nums[0])
    return MarketBracket(label=lab, low=value, high=value, bottom=False, top=False)


def parse_label_dict(label: str, question: str = "", *, include_label: bool = True) -> dict[str, Any] | None:
    bracket = parse_market_bracket(label, question)
    return None if bracket is None else bracket.as_dict(include_label=include_label)


def bracket_contains(parsed: dict[str, Any] | MarketBracket, value: float) -> bool:
    if isinstance(parsed, MarketBracket):
        return parsed.contains(value)
    low = parsed.get("low")
    high = parsed.get("high")
    if parsed.get("bottom"):
        return high is not None and value <= float(high)
    if parsed.get("top"):
        return low is not None and value >= float(low)
    return low is not None and high is not None and float(low) <= value <= float(high)


def bracket_center(value: Any) -> float:
    """Return the numeric center used to order exact/range/open-tail rungs."""

    parsed = parse_market_bracket(str(value))
    if parsed is None:
        return float("nan")
    if parsed.low is None:
        return float(parsed.high) if parsed.high is not None else float("nan")
    if parsed.high is None:
        return float(parsed.low)
    return (float(parsed.low) + float(parsed.high)) / 2.0
