from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class CityScore:
    city: str
    target_date: str
    decision_ts_utc: str
    source_obs_ts_utc: str
    current_bracket: int
    market_side: str
    market_probability: float
    market_entry_price: float
    model_probability: float
    model_id: str
    feature_coverage: float
    missing_features: list[str]
    features: dict[str, float | None]
    market: dict[str, Any]
    lineage: dict[str, Any]


class CityAdapter(Protocol):
    def score(self, profile: dict[str, Any], now: datetime) -> list[CityScore]: ...


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")


def load_jsonl_keys(path: Path, field: str) -> set[str]:
    if not path.exists():
        return set()
    values: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                value = json.loads(line).get(field)
            except json.JSONDecodeError:
                continue
            if value:
                values.add(str(value))
    return values


class ShadowRuntime:
    """Model-agnostic journal runtime. It has deliberately no execution client."""

    schema_version = "weather_city_probability_shadow_v1"

    def __init__(self, config: dict[str, Any], adapters: dict[str, CityAdapter]):
        if config.get("execution_mode") != "zero_notional_shadow":
            raise ValueError("execution_mode must be zero_notional_shadow")
        if config.get("orders_submitted") != 0:
            raise ValueError("orders_submitted must be exactly zero")
        self.config = config
        self.adapters = adapters
        self.output_dir = Path(config["output_dir"])
        self.evaluations = self.output_dir / "evaluations.jsonl"
        self.intents = self.output_dir / "paper_intents.jsonl"
        self.errors = self.output_dir / "errors.jsonl"

    @staticmethod
    def fee_per_share(price: float) -> float:
        return 0.05 * price * (1.0 - price)

    def run_once(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        seen = load_jsonl_keys(self.evaluations, "evaluation_id")
        first_intents = load_jsonl_keys(self.intents, "position_key")
        evaluated = written = intents = errors = 0
        for profile in self.config["profiles"]:
            if not profile.get("enabled", True):
                continue
            adapter_id = profile["adapter"]
            try:
                scores = self.adapters[adapter_id].score(profile, now)
            except Exception as exc:  # persistent telemetry, never silent fallback
                errors += 1
                append_jsonl(self.errors, {
                    "schema_version": self.schema_version,
                    "ts_utc": now.isoformat(),
                    "city": profile.get("city"),
                    "adapter": adapter_id,
                    "error": f"{type(exc).__name__}: {exc}",
                })
                continue
            for score in scores:
                evaluated += 1
                key = "|".join((score.city, score.target_date, score.source_obs_ts_utc,
                                str(score.current_bracket), score.market_side, score.model_id))
                evaluation_id = hashlib.sha256(key.encode()).hexdigest()
                if evaluation_id in seen:
                    continue
                fee = self.fee_per_share(score.market_entry_price)
                effective_cost = score.market_entry_price + fee
                edge = score.model_probability - effective_cost
                row = {
                    "schema_version": self.schema_version,
                    "execution_mode": "zero_notional_shadow",
                    "orders_submitted": 0,
                    "evaluation_id": evaluation_id,
                    **asdict(score),
                    "fee_per_share": fee,
                    "effective_cost_per_share": effective_cost,
                    "edge_after_fee": edge,
                    "would_enter": edge > 0,
                }
                append_jsonl(self.evaluations, row)
                written += 1
                seen.add(evaluation_id)
                position_key = "|".join((score.city, score.target_date,
                                         str(score.current_bracket), score.market_side,
                                         score.model_id))
                if edge > 0 and position_key not in first_intents:
                    append_jsonl(self.intents, {
                        **row,
                        "position_key": position_key,
                        "intent_kind": "first_positive_edge",
                        "notional_usd": 0.0,
                        "shares": 0.0,
                    })
                    intents += 1
                    first_intents.add(position_key)
        summary = {
            "schema_version": self.schema_version,
            "execution_mode": "zero_notional_shadow",
            "orders_submitted": 0,
            "generated_at_utc": now.isoformat(),
            "evaluated": evaluated,
            "new_evaluations": written,
            "new_paper_intents": intents,
            "errors": errors,
        }
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "latest_summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return summary
