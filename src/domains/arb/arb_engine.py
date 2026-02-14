import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List

from src.platform.market_data.http_client import ToolServiceClient

from src.domains.arb.config import ArbConfig, ArbPairConfig
from src.domains.arb.execution import ArbExecution, ExecutionError
from src.domains.arb.market_data import MarketDataManager
from src.domains.arb.utils import (
    best_bid_ask_with_size,
    expected_profit_usdc,
    merge_arb_edge,
    min_fill_size_for_notional,
    split_arb_edge,
)


@dataclass
class PairDecision:
    pair: ArbPairConfig
    action: str  # merge_arb | split_arb | none
    edge: float
    expected_profit: float
    size: float
    detail: Dict[str, Any]


class ArbEngine:
    def __init__(self, config: ArbConfig) -> None:
        self.config = config
        token_ids: List[str] = []
        for p in config.pairs:
            token_ids.extend([p.yes_token_id, p.no_token_id])
        # 去重并保持顺序，避免重复订阅/拉取盘口。
        seen = set()
        unique_token_ids = []
        for tid in token_ids:
            if tid in seen:
                continue
            seen.add(tid)
            unique_token_ids.append(tid)
        self.market_data = MarketDataManager(config, unique_token_ids)

    async def run(self) -> None:
        if not self.config.pairs:
            print("[PM-ARB] no pairs configured. set PM_ARB_PAIRS_JSON first.")
            return

        async with ToolServiceClient(self.config.api_base_url, self.config.api_key) as client:
            execution = ArbExecution(self.config, client)
            await self.market_data.start()
            try:
                tick = 0
                while True:
                    await self._run_tick(client, execution, tick)
                    tick += 1
                    if self.config.max_ticks > 0 and tick >= self.config.max_ticks:
                        return
                    await asyncio.sleep(self.config.tick_interval_sec)
            finally:
                await self.market_data.stop()

    async def _run_tick(self, client: ToolServiceClient, execution: ArbExecution, tick: int) -> None:
        # 1) 获取全部 pair 需要的最新盘口。
        orderbooks = await self.market_data.get_orderbooks(client)
        decisions: List[PairDecision] = []

        for pair in self.config.pairs:
            yes_ob = orderbooks.get(pair.yes_token_id, {})
            no_ob = orderbooks.get(pair.no_token_id, {})
            yes_top = best_bid_ask_with_size(yes_ob)
            no_top = best_bid_ask_with_size(no_ob)

            decision = self._decide(pair, yes_top, no_top)
            decisions.append(decision)

        # 2) 按预期利润排序，优先执行高价值机会。
        decisions.sort(key=lambda x: x.expected_profit, reverse=True)
        actions: List[Dict[str, Any]] = []

        for d in decisions:
            if d.action == "none":
                continue
            try:
                if d.action == "merge_arb":
                    # merge 套利：买入 YES+NO，再 merge 回收 USDC。
                    buy_resp = await execution.place_pair_buy(
                        d.pair,
                        d.detail["ask_yes"],
                        d.detail["ask_no"],
                        d.size,
                    )
                    merge_resp = await execution.merge(d.pair, d.size)
                    actions.append(
                        {
                            "pair": d.pair.name,
                            "action": d.action,
                            "size": d.size,
                            "buy_resp": buy_resp,
                            "merge_resp": merge_resp,
                            "expected_profit": d.expected_profit,
                        }
                    )
                elif d.action == "split_arb":
                    # split 套利：先 split 出 YES+NO，再分别卖出。
                    split_resp = await execution.split(d.pair, d.size)
                    sell_resp = await execution.place_pair_sell(
                        d.pair,
                        d.detail["bid_yes"],
                        d.detail["bid_no"],
                        d.size,
                    )
                    actions.append(
                        {
                            "pair": d.pair.name,
                            "action": d.action,
                            "size": d.size,
                            "split_resp": split_resp,
                            "sell_resp": sell_resp,
                            "expected_profit": d.expected_profit,
                        }
                    )
            except ExecutionError as exc:
                actions.append(
                    {
                        "pair": d.pair.name,
                        "action": d.action,
                        "status": "blocked",
                        "reason": str(exc),
                    }
                )
            except Exception as exc:
                actions.append(
                    {
                        "pair": d.pair.name,
                        "action": d.action,
                        "status": "error",
                        "reason": str(exc),
                    }
                )

        # 3) 周期性 merge：即使当前没套利，也回收成对库存提升资金利用率。
        if self.config.auto_merge_every_ticks > 0 and (tick + 1) % self.config.auto_merge_every_ticks == 0:
            for pair in self.config.pairs:
                try:
                    resp = await execution.periodic_merge_if_possible(pair)
                    actions.append({"pair": pair.name, "action": "periodic_merge", "result": resp})
                except Exception as exc:
                    actions.append(
                        {
                            "pair": pair.name,
                            "action": "periodic_merge",
                            "status": "error",
                            "reason": str(exc),
                        }
                    )

        if decisions:
            top = decisions[0]
            print(
                f"[PM-ARB][tick={tick}] pairs={len(decisions)} actions={len(actions)} "
                f"top_pair={top.pair.name} top_action={top.action} "
                f"top_edge={top.edge:.6f} top_expected_profit={top.expected_profit:.6f} "
                f"top_size={top.size:.6f}"
            )
        else:
            print(f"[PM-ARB][tick={tick}] no decisions")
        for action in actions:
            print(f"[PM-ARB][action] {action}")

    def _decide(self, pair: ArbPairConfig, yes_top: Dict[str, float], no_top: Dict[str, float]) -> PairDecision:
        # 输入仅使用 top-of-book，保证决策路径稳定且低延迟。
        ask_yes = yes_top.get("best_ask", 0.0)
        ask_no = no_top.get("best_ask", 0.0)
        bid_yes = yes_top.get("best_bid", 0.0)
        bid_no = no_top.get("best_bid", 0.0)

        ask_yes_size = yes_top.get("best_ask_size", 0.0)
        ask_no_size = no_top.get("best_ask_size", 0.0)
        bid_yes_size = yes_top.get("best_bid_size", 0.0)
        bid_no_size = no_top.get("best_bid_size", 0.0)

        # merge 候选：按 ask 买双腿，再 merge。
        merge_edge = 0.0
        merge_profit = 0.0
        merge_size = 0.0
        if ask_yes > 0 and ask_no > 0:
            merge_edge = merge_arb_edge(ask_yes, ask_no, self.config.fee_buffer)
            size_cap_by_book = min(ask_yes_size, ask_no_size)
            size_cap_by_notional = min_fill_size_for_notional(
                ask_yes,
                ask_no,
                self.config.max_notional_usdc_per_trade,
            )
            merge_size = max(0.0, min(size_cap_by_book, size_cap_by_notional))
            merge_notional = merge_size * (ask_yes + ask_no)
            merge_profit = expected_profit_usdc(merge_edge, merge_notional, self.config.gas_estimate_usdc)

        # split 候选：先 split，再按 bid 卖双腿。
        split_edge = 0.0
        split_profit = 0.0
        split_size = 0.0
        if bid_yes > 0 and bid_no > 0:
            split_edge = split_arb_edge(bid_yes, bid_no, self.config.fee_buffer)
            size_cap_by_book = min(bid_yes_size, bid_no_size)
            size_cap_by_notional = min_fill_size_for_notional(
                bid_yes,
                bid_no,
                self.config.max_notional_usdc_per_trade,
            )
            split_size = max(0.0, min(size_cap_by_book, size_cap_by_notional))
            split_notional = split_size * 1.0
            split_profit = expected_profit_usdc(split_edge, split_notional, self.config.gas_estimate_usdc)

        # 双重门槛：最小预期利润 + gas 保护阈值。
        gas_guard = self.config.gas_multiplier_guard * self.config.gas_estimate_usdc
        merge_ok = (
            merge_edge > 0
            and merge_profit >= self.config.min_expected_profit_usdc
            and merge_profit >= gas_guard
            and merge_size > 0
        )
        split_ok = (
            split_edge > 0
            and split_profit >= self.config.min_expected_profit_usdc
            and split_profit >= gas_guard
            and split_size > 0
        )

        if merge_ok and (merge_profit >= split_profit or not split_ok):
            return PairDecision(
                pair=pair,
                action="merge_arb",
                edge=merge_edge,
                expected_profit=merge_profit,
                size=merge_size,
                detail={"ask_yes": ask_yes, "ask_no": ask_no},
            )
        if split_ok:
            return PairDecision(
                pair=pair,
                action="split_arb",
                edge=split_edge,
                expected_profit=split_profit,
                size=split_size,
                detail={"bid_yes": bid_yes, "bid_no": bid_no},
            )

        return PairDecision(
            pair=pair,
            action="none",
            edge=max(merge_edge, split_edge),
            expected_profit=max(merge_profit, split_profit),
            size=0.0,
            detail={
                "ask_yes": ask_yes,
                "ask_no": ask_no,
                "bid_yes": bid_yes,
                "bid_no": bid_no,
            },
        )
