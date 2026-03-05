from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

from src.strategies.pmm.config import PMMConfig
from src.strategies.pmm.core.pricing import compute_quotes_pro
from src.strategies.pmm.core.strategy_base import QuoteTarget, StrategyQuoteInput

AnchorFn = Callable[[float, float, float, float, float, float, float], Tuple[float, float]]
QuantizeFn = Callable[[float, float, float, str], Tuple[float, float]]
TargetSizesFn = Callable[[PMMConfig, float, float, float], Tuple[float, float]]


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


class SmartMoneyFollowV1Strategy:
    """
    Directional quote-tilt strategy based on "smart money" signals.

    Signal sources (priority: file > params, weighted blend):
    - strategy_params.smart_money_token_signals / token_signals: {token_id: [-1, 1]}
    - strategy_params.smart_money_wallets: wallet stats + token convictions
    - strategy_params.smart_money_signal_file: optional json path for live-updated signals
    """

    key = "smart_money_follow_v1"

    def __init__(
        self,
        anchor_quotes_fn: AnchorFn,
        quantize_pair_fn: QuantizeFn,
        target_sizes_fn: TargetSizesFn,
        time_fn: Callable[[], float] | None = None,
    ) -> None:
        self._anchor_quotes = anchor_quotes_fn
        self._quantize_pair = quantize_pair_fn
        self._target_sizes = target_sizes_fn
        self._time_fn = time_fn or time.time
        self._signal_file_path: str = ""
        self._signal_file_next_reload_ts: float = 0.0
        self._signal_file_payload: Dict[str, Any] = {}

    def generate_quotes(
        self,
        quote_input: StrategyQuoteInput,
        config: PMMConfig,
    ) -> List[QuoteTarget]:
        tick = max(1e-6, float(config.price_tick))
        spread_ticks = max(1, int(round(quote_input.adaptive_spread / tick)))
        size_decay_power = float(config.strategy_params.get("size_decay_power", 2.0))
        quote = compute_quotes_pro(
            mid=quote_input.mid,
            spread_ticks=spread_ticks,
            tick_size=tick,
            position=quote_input.position,
            open_buy_qty=quote_input.open_buy_qty,
            open_sell_qty=quote_input.open_sell_qty,
            max_position=max(1.0, float(config.max_position)),
            size_decay_power=size_decay_power,
        )
        target_bid = quote.bid
        target_ask = quote.ask
        bid_price, ask_price = self._anchor_quotes(
            target_bid=target_bid,
            target_ask=target_ask,
            best_bid=quote_input.best_bid,
            best_ask=quote_input.best_ask,
            join_epsilon=config.join_epsilon,
            fair_value=quote_input.mid,
            min_edge=config.min_edge,
        )

        sm_signal = self._resolve_smart_money_signal(
            token_id=quote_input.token_id,
            strategy_params=config.strategy_params or {},
        )
        sm_min_abs_signal = max(
            0.0,
            min(1.0, _to_float(config.strategy_params.get("smart_money_min_abs_signal"), 0.05)),
        )
        if abs(sm_signal) < sm_min_abs_signal:
            sm_signal = 0.0

        if sm_signal != 0.0:
            price_tilt_factor = _to_float(
                config.strategy_params.get("smart_money_price_tilt_factor"),
                0.35,
            )
            max_price_tilt = max(
                0.0,
                _to_float(config.strategy_params.get("smart_money_max_price_tilt"), 0.02),
            )
            raw_price_tilt = quote_input.adaptive_spread * price_tilt_factor * sm_signal
            price_tilt = _clamp(raw_price_tilt, -max_price_tilt, max_price_tilt)
            bid_price = _clamp(bid_price + price_tilt, 0.0001, 0.9999)
            ask_price = _clamp(ask_price + price_tilt, 0.0001, 0.9999)

        bid_price, ask_price = self._quantize_pair(
            bid_price,
            ask_price,
            tick=config.price_tick,
            mode=config.price_tick_mode,
        )

        base_buy_size, base_sell_size = self._target_sizes(
            config=config,
            position=quote_input.position,
            usdc_balance=quote_input.effective_usdc_balance,
            bid_price=bid_price,
        )
        buy_size = base_buy_size * max(0.0, quote.bid_size_adj) if quote.allow_buy else 0.0
        sell_size = base_sell_size * max(0.0, quote.ask_size_adj) if quote.allow_sell else 0.0

        if sm_signal != 0.0:
            size_tilt_factor = max(
                0.0,
                _to_float(config.strategy_params.get("smart_money_size_tilt_factor"), 0.75),
            )
            min_size_scale = max(
                0.0,
                _to_float(config.strategy_params.get("smart_money_min_size_scale"), 0.05),
            )
            max_size_scale = max(
                min_size_scale,
                _to_float(config.strategy_params.get("smart_money_max_size_scale"), 3.0),
            )

            buy_scale = _clamp(1.0 + (sm_signal * size_tilt_factor), min_size_scale, max_size_scale)
            sell_scale = _clamp(1.0 - (sm_signal * size_tilt_factor), min_size_scale, max_size_scale)
            buy_size *= buy_scale
            sell_size *= sell_scale

            one_side_only_threshold = max(
                0.0,
                min(1.0, _to_float(config.strategy_params.get("smart_money_one_side_only_threshold"), 0.90)),
            )
            if abs(sm_signal) >= one_side_only_threshold:
                if sm_signal > 0:
                    sell_size = 0.0
                else:
                    buy_size = 0.0

        quotes: List[QuoteTarget] = []
        if buy_size > 0:
            quotes.append(
                QuoteTarget(
                    token_id=quote_input.token_id,
                    side="BUY",
                    price=bid_price,
                    size=buy_size,
                    level=0,
                    target_price=target_bid,
                )
            )
        if sell_size > 0:
            quotes.append(
                QuoteTarget(
                    token_id=quote_input.token_id,
                    side="SELL",
                    price=ask_price,
                    size=sell_size,
                    level=0,
                    target_price=target_ask,
                )
            )
        return quotes

    def _resolve_smart_money_signal(
        self,
        token_id: str,
        strategy_params: Dict[str, Any],
    ) -> float:
        reverse = bool(strategy_params.get("smart_money_reverse", False))
        param_signal = self._extract_signal_from_payload(
            payload=strategy_params,
            token_id=token_id,
            use_wallets=True,
        )
        file_payload = self._load_signal_file_payload(strategy_params)
        file_signal = self._extract_signal_from_payload(
            payload=file_payload,
            token_id=token_id,
            use_wallets=True,
        )
        file_weight = _clamp(
            _to_float(strategy_params.get("smart_money_file_signal_weight"), 0.7),
            0.0,
            1.0,
        )
        if file_signal == 0.0:
            combined = param_signal
        else:
            combined = (1.0 - file_weight) * param_signal + file_weight * file_signal
        if reverse:
            combined = -combined
        return _clamp(combined, -1.0, 1.0)

    def _extract_signal_from_payload(
        self,
        payload: Dict[str, Any],
        token_id: str,
        use_wallets: bool,
    ) -> float:
        if not isinstance(payload, dict) or not payload:
            return 0.0

        if "smart_money_signal" in payload:
            direct_signal = _clamp(_to_float(payload.get("smart_money_signal"), 0.0), -1.0, 1.0)
        else:
            direct_signal = 0.0

        signal_map = payload.get("smart_money_token_signals")
        if not isinstance(signal_map, dict):
            signal_map = payload.get("token_signals")
        map_signal = _clamp(_to_float((signal_map or {}).get(token_id), 0.0), -1.0, 1.0)

        wallets_signal = 0.0
        if use_wallets:
            wallets_signal = self._extract_wallets_signal(payload, token_id)

        has_map = isinstance(signal_map, dict) and token_id in signal_map
        if has_map:
            if wallets_signal == 0.0:
                return map_signal
            mix = _clamp(_to_float(payload.get("smart_money_token_wallet_mix"), 0.75), 0.0, 1.0)
            return _clamp((mix * map_signal) + ((1.0 - mix) * wallets_signal), -1.0, 1.0)
        if wallets_signal != 0.0:
            return wallets_signal
        if direct_signal != 0.0:
            return direct_signal
        return 0.0

    def _extract_wallets_signal(self, payload: Dict[str, Any], token_id: str) -> float:
        wallets = payload.get("smart_money_wallets")
        if not isinstance(wallets, list):
            wallets = payload.get("wallets")
        if not isinstance(wallets, list) or not wallets:
            return 0.0

        min_win_rate = _clamp(_to_float(payload.get("smart_money_min_wallet_win_rate"), 0.70), 0.0, 1.0)
        min_resolved = max(0.0, _to_float(payload.get("smart_money_min_resolved_markets"), 40))
        max_wallets = int(max(1, _to_float(payload.get("smart_money_max_wallets"), 50)))

        wallet_scores: List[Tuple[float, float]] = []
        for raw in wallets[:max_wallets]:
            if not isinstance(raw, dict):
                continue
            win_rate = _to_float(raw.get("win_rate"), 0.0)
            resolved = _to_float(raw.get("resolved_markets"), _to_float(raw.get("resolved_trades"), 0.0))
            if win_rate < min_win_rate or resolved < min_resolved:
                continue
            convictions = raw.get("token_convictions")
            if not isinstance(convictions, dict) or token_id not in convictions:
                continue
            conviction = _clamp(_to_float(convictions.get(token_id), 0.0), -1.0, 1.0)
            if conviction == 0.0:
                continue
            confidence = max(0.0, _to_float(raw.get("confidence"), 1.0))
            base_weight = max(0.0, _to_float(raw.get("weight"), 1.0))
            win_bonus = max(0.0, (win_rate - min_win_rate) / max(1e-6, 1.0 - min_win_rate))
            trade_bonus = max(0.1, min(2.0, math.log1p(resolved) / 3.0))
            score = conviction * base_weight * confidence * (0.5 + win_bonus) * trade_bonus
            wallet_scores.append((score, abs(score)))

        if not wallet_scores:
            return 0.0
        top_k = int(max(1, _to_float(payload.get("smart_money_top_k_wallets"), 8)))
        wallet_scores.sort(key=lambda x: abs(x[0]), reverse=True)
        selected = wallet_scores[:top_k]
        denom = sum(abs_weight for _, abs_weight in selected)
        if denom <= 0:
            return 0.0
        return _clamp(sum(score for score, _ in selected) / denom, -1.0, 1.0)

    def _load_signal_file_payload(self, strategy_params: Dict[str, Any]) -> Dict[str, Any]:
        path = str(strategy_params.get("smart_money_signal_file", "")).strip()
        if not path:
            return {}
        now = self._time_fn()
        reload_sec = max(
            0.1,
            _to_float(strategy_params.get("smart_money_signal_reload_sec"), 5.0),
        )
        if (
            path == self._signal_file_path
            and now < self._signal_file_next_reload_ts
            and isinstance(self._signal_file_payload, dict)
        ):
            return self._signal_file_payload
        self._signal_file_next_reload_ts = now + reload_sec
        try:
            fpath = Path(path)
            if not fpath.exists():
                return self._signal_file_payload if path == self._signal_file_path else {}
            payload = json.loads(fpath.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                self._signal_file_path = path
                self._signal_file_payload = payload
                return payload
        except Exception:
            return self._signal_file_payload if path == self._signal_file_path else {}
        return self._signal_file_payload if path == self._signal_file_path else {}
