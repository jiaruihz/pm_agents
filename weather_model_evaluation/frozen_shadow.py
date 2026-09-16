"""Evidence-only frozen shadow epochs; never a real execution lifecycle."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from .contracts import stable_sha256
from .probability import date_block_bootstrap_delta


_ZERO_ACCOUNTING_FIELDS = (
    "orders",
    "fills",
    "notional",
    "runtime_orders",
    "runtime_fills",
    "runtime_notional",
)
_PROBABILITY_FIELDS = ("p_model", "market_p")


def _require_sha256(value: str, *, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value.lower()
    ):
        raise ValueError(f"{field} must be a real SHA-256")
    return value.lower()


def _finite_decimal(value: Any, *, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite numeric value")
    try:
        parsed = Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"{field} must be a finite numeric value") from exc
    if not parsed.is_finite():
        raise ValueError(f"{field} must be a finite numeric value")
    return parsed


def _require_model_version(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or value == "legacy_unbound":
        raise ValueError("frozen shadow requires an explicit bound model_version")
    return value.strip()


@dataclass(frozen=True)
class FrozenShadowEpoch:
    epoch_hash: str
    target_dates: tuple[str, ...]
    rows_hash: str
    dataset_hash: str
    split_manifest_hash: str
    model_version: str
    execution_assumption_hash: str
    zero_notional: bool = True
    promotion_authority: bool = False
    evidence_class: str = "shadow_evidence_only"

    def __post_init__(self) -> None:
        if not self.zero_notional:
            raise ValueError("frozen shadow epochs must remain zero-notional")
        if self.promotion_authority:
            raise ValueError("frozen shadow epochs never have promotion authority")
        legacy = self.dataset_hash == "legacy_unbound" or self.split_manifest_hash == "legacy_unbound"
        if legacy:
            if (
                self.dataset_hash != "legacy_unbound"
                or self.split_manifest_hash != "legacy_unbound"
                or self.evidence_class != "shadow_evidence_only_legacy_unbound"
            ):
                raise ValueError("legacy_unbound requires the explicit legacy evidence class")
            return
        _require_sha256(self.dataset_hash, field="dataset_hash")
        _require_sha256(self.split_manifest_hash, field="split_manifest_hash")
        _require_sha256(
            self.execution_assumption_hash, field="execution_assumption_hash"
        )
        _require_model_version(self.model_version)

    @classmethod
    def build(
        cls,
        rows: Iterable[Mapping[str, Any]],
        *,
        target_dates: Sequence[str],
        dataset_hash: str,
        split_manifest_hash: str,
        model_version: str,
        execution_assumptions: Mapping[str, Any] | None = None,
    ) -> "FrozenShadowEpoch":
        data = tuple(dict(row) for row in rows)
        dates = tuple(sorted(set(target_dates)))
        observed = {str(row.get("target_date")) for row in data}
        if not dates or observed != set(dates):
            raise ValueError("epoch rows must cover exactly every frozen target_date")
        dataset_hash = _require_sha256(dataset_hash, field="dataset_hash")
        split_manifest_hash = _require_sha256(
            split_manifest_hash, field="split_manifest_hash"
        )
        model_version = _require_model_version(model_version)
        validate_shadow_runtime(data)
        rows_hash = stable_sha256(sorted(data, key=lambda row: stable_sha256(row)))
        assumption_hash = stable_sha256(dict(execution_assumptions or {}))
        body = {
            "target_dates": dates,
            "rows_hash": rows_hash,
            "dataset_hash": dataset_hash,
            "split_manifest_hash": split_manifest_hash,
            "model_version": model_version,
            "execution_assumption_hash": assumption_hash,
            "zero_notional": True,
            "promotion_authority": False,
            "evidence_class": "shadow_evidence_only",
        }
        return cls(epoch_hash=stable_sha256(body), **body)

    @classmethod
    def build_legacy_unbound(
        cls,
        rows: Iterable[Mapping[str, Any]],
        *,
        target_dates: Sequence[str],
        allow_legacy_unbound: bool = False,
        model_version: str = "legacy_unbound",
        execution_assumptions: Mapping[str, Any] | None = None,
    ) -> "FrozenShadowEpoch":
        """Read old shadow evidence without making it eligible for promotion.

        The explicit opt-in prevents normal callers from silently creating an
        unbound epoch after the dataset/split contract became mandatory.
        """
        if not allow_legacy_unbound:
            raise ValueError("legacy_unbound epochs require allow_legacy_unbound=True")
        data = tuple(dict(row) for row in rows)
        dates = tuple(sorted(set(target_dates)))
        observed = {str(row.get("target_date")) for row in data}
        if not dates or observed != set(dates):
            raise ValueError("epoch rows must cover exactly every frozen target_date")
        validate_shadow_runtime(data)
        rows_hash = stable_sha256(sorted(data, key=lambda row: stable_sha256(row)))
        assumption_hash = stable_sha256(dict(execution_assumptions or {}))
        body = {
            "target_dates": dates,
            "rows_hash": rows_hash,
            "dataset_hash": "legacy_unbound",
            "split_manifest_hash": "legacy_unbound",
            "model_version": model_version,
            "execution_assumption_hash": assumption_hash,
            "zero_notional": True,
            "promotion_authority": False,
            "evidence_class": "shadow_evidence_only_legacy_unbound",
        }
        return cls(epoch_hash=stable_sha256(body), **body)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_shadow_runtime(rows: Iterable[Mapping[str, Any]]) -> None:
    for row in rows:
        missing_accounting = [
            field
            for field in _ZERO_ACCOUNTING_FIELDS
            if field not in row or row[field] is None
        ]
        if missing_accounting:
            raise ValueError(
                f"frozen shadow lacks zero-accounting evidence: {missing_accounting}"
            )
        for field in _ZERO_ACCOUNTING_FIELDS:
            value = _finite_decimal(row[field], field=field)
            if value != 0:
                raise ValueError(f"frozen shadow must be zero-notional: {field}")
        for field in _PROBABILITY_FIELDS:
            if field not in row or row[field] is None:
                raise ValueError(f"frozen shadow lacks probability evidence: {field}")
            value = _finite_decimal(row[field], field=field)
            if not Decimal("0") <= value <= Decimal("1"):
                raise ValueError(f"{field} must be in [0, 1]")
        score_fields = [field for field in ("settled_score", "label") if field in row]
        if not score_fields:
            raise ValueError("frozen shadow lacks explicit settlement-label evidence")
        scores = [row[field] for field in score_fields if row[field] is not None]
        if any(_finite_decimal(score, field="settled score") not in (Decimal("0"), Decimal("1")) for score in scores):
            raise ValueError("settled score must be binary")
        if len(scores) == 2 and scores[0] != scores[1]:
            raise ValueError("settled_score and label conflict")


def maker_proxy_fill(quote: Mapping[str, Any]) -> bool:
    """A quote touch is not a fill: require sell volume to clear queue + own size."""
    if not quote.get("quote_touched", False):
        for field in ("sell_volume", "visible_queue", "own_size"):
            if quote.get(field) is not None:
                _finite_decimal(quote[field], field=field)
        return False
    required = ("sell_volume", "visible_queue", "own_size")
    missing = [field for field in required if quote.get(field) is None]
    if missing:
        raise ValueError(f"maker proxy lacks queue/volume evidence: {missing}")
    sell_volume = _finite_decimal(quote["sell_volume"], field="sell_volume")
    visible_queue = _finite_decimal(quote["visible_queue"], field="visible_queue")
    own_size = _finite_decimal(quote["own_size"], field="own_size")
    if sell_volume < 0 or visible_queue < 0 or own_size <= 0:
        raise ValueError("maker proxy queue/volume must be non-negative and own_size positive")
    return sell_volume >= visible_queue + own_size


def _maker_economics(
    row: Mapping[str, Any],
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal, Decimal]:
    size = _finite_decimal(row["own_size"], field="own_size")
    maker_price = _finite_decimal(row["maker_price"], field="maker_price")
    taker_price = _finite_decimal(row["taker_price"], field="taker_price")
    markout_price = _finite_decimal(row["markout_price"], field="markout_price")
    maker_fee = _finite_decimal(row["maker_fee_usd"], field="maker_fee_usd")
    taker_fee = _finite_decimal(row["taker_fee_usd"], field="taker_fee_usd")
    if size <= 0:
        raise ValueError("maker proxy own_size must be positive")
    for name, value in (
        ("maker_price", maker_price),
        ("taker_price", taker_price),
        ("markout_price", markout_price),
    ):
        if not Decimal("0") <= value <= Decimal("1"):
            raise ValueError(f"{name} must be in [0, 1]")
    if maker_fee < 0 or taker_fee < 0:
        raise ValueError("maker/taker fee evidence must be non-negative")
    return size, maker_price, taker_price, markout_price, maker_fee, taker_fee


def _paired_roles(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Mapping[str, Any]]]:
    paired: dict[str, dict[str, Mapping[str, Any]]] = {}
    for row in rows:
        row_id = str(row.get("row_id") or "")
        role = str(row.get("liquidity_role") or "")
        if not row_id or role not in {"maker", "taker"}:
            raise ValueError("shadow comparison requires row_id and maker/taker liquidity_role")
        if role in paired.setdefault(row_id, {}):
            raise ValueError(f"duplicate {role} shadow row: {row_id}")
        paired[row_id][role] = row
    incomplete = sorted(
        row_id for row_id, roles in paired.items() if set(roles) != {"maker", "taker"}
    )
    if incomplete:
        raise ValueError(f"maker and taker evidence must use the same rows: {incomplete}")
    for row_id, roles in paired.items():
        maker = roles["maker"]
        taker = roles["taker"]
        for field in ("target_date", "p_model", "market_p", "label", "settled_score"):
            maker_value = maker.get(field)
            taker_value = taker.get(field)
            if maker_value is not None or taker_value is not None:
                if maker_value != taker_value:
                    raise ValueError(
                        f"maker/taker paired denominator mismatch: {row_id}: {field}"
                    )
    return paired


def score_frozen_shadow(rows: Iterable[Mapping[str, Any]], *, draws: int = 2000, seed: int = 0) -> dict[str, Any]:
    data = [dict(row) for row in rows]
    validate_shadow_runtime(data)
    if draws <= 0:
        raise ValueError("bootstrap draws must be positive")
    pairs = _paired_roles(data)
    makers = [roles["maker"] for roles in pairs.values()]
    # Score model probability once per logical opportunity, not once per
    # execution role.  The taker row is the denominator anchor by convention.
    settled = [
        roles["taker"]
        for roles in pairs.values()
        if roles["taker"].get("settled_score", roles["taker"].get("label"))
        is not None
    ]
    if not settled:
        raise ValueError("no settled shadow scores")
    candidate = [float(row["p_model"]) for row in settled]
    baseline = [float(row["market_p"]) for row in settled]
    labels = [int(row.get("settled_score", row.get("label"))) for row in settled]
    candidate_loss = [(probability - label) ** 2 for probability, label in zip(candidate, labels)]
    baseline_loss = [(probability - label) ** 2 for probability, label in zip(baseline, labels)]
    delta = date_block_bootstrap_delta(
        pd.DataFrame({"target_date": [str(row["target_date"]) for row in settled]}),
        candidate_loss, baseline_loss, draws=draws, seed=seed,
    )
    maker_evidence_fields = (
        "quote_touched",
        "sell_volume",
        "visible_queue",
        "own_size",
        "maker_price",
        "taker_price",
        "maker_fee_usd",
        "taker_fee_usd",
        "markout_price",
    )
    maker_evidence = [
        row
        for row in makers
        if all(row.get(field) is not None for field in maker_evidence_fields)
    ]
    maker_economics = {
        str(row["row_id"]): _maker_economics(row) for row in maker_evidence
    }
    maker_fills = {
        str(row["row_id"]): maker_proxy_fill(row) for row in maker_evidence
    }
    result = {
        "evidence_class": "shadow_evidence_only",
        "promotion_authority": False,
        "queue_lifecycle": "not_real; quote touch is not a fill",
        "settled_rows": len(settled),
        "maker_proxy_rows": len(maker_evidence),
        "maker_proxy_missing_evidence_rows": len(makers) - len(maker_evidence),
        "maker_proxy_fills": sum(maker_fills.values()),
        "maker_increment_status": (
            "proxy_only"
            if len(maker_evidence) == len(makers)
            else "blocked_missing_execution_evidence"
        ),
        "target_date_block_bootstrap": delta,
    }
    if len(maker_evidence) != len(makers):
        result["maker_increment_blocker"] = {
            str(row["row_id"]): [
                field for field in maker_evidence_fields if row.get(field) is None
            ]
            for row in makers
            if any(row.get(field) is None for field in maker_evidence_fields)
        }
        return result

    maker_pnl: list[Decimal] = []
    taker_pnl: list[Decimal] = []
    maker_dates: list[str] = []
    maker_markout: list[Decimal] = []
    for row in maker_evidence:
        row_id = str(row["row_id"])
        if not maker_fills[row_id]:
            continue
        label = row.get("settled_score", row.get("label"))
        if label is None:
            continue
        (
            size,
            maker_price,
            taker_price,
            markout_price,
            maker_fee,
            taker_fee,
        ) = maker_economics[row_id]
        outcome = _finite_decimal(label, field="settled score")
        maker_pnl.append(size * (outcome - maker_price) - maker_fee)
        taker_pnl.append(size * (outcome - taker_price) - taker_fee)
        maker_markout.append(size * (markout_price - maker_price))
        maker_dates.append(str(row["target_date"]))
    maker_total = sum(maker_pnl, Decimal("0"))
    taker_total = sum(taker_pnl, Decimal("0"))
    markout_total = sum(maker_markout, Decimal("0"))
    result["maker_proxy_net_pnl_usd"] = str(maker_total)
    result["same_rows_taker_net_pnl_usd"] = str(taker_total)
    result["maker_proxy_increment_usd"] = str(maker_total - taker_total)
    result["maker_proxy_markout_usd"] = str(markout_total)
    if maker_pnl:
        result["maker_vs_taker_target_date_block_bootstrap"] = date_block_bootstrap_delta(
            pd.DataFrame({"target_date": maker_dates}),
            [-float(value) for value in maker_pnl],
            [-float(value) for value in taker_pnl],
            draws=draws,
            seed=seed,
        )
    else:
        result["maker_increment_status"] = "blocked_no_proxy_fills"
    return result
