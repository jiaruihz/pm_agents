from typing import Any, Dict, List

from src.platform.market_data.http_client import ToolServiceClient

from src.strategies.arb.config import ArbConfig, ArbPairConfig
from src.strategies.arb.utils import to_ctf_units


class ExecutionError(RuntimeError):
    pass


class ArbExecution:
    def __init__(self, config: ArbConfig, client: ToolServiceClient) -> None:
        self.config = config
        self.client = client

    async def place_pair_buy(self, pair: ArbPairConfig, yes_price: float, no_price: float, size: float) -> List[Dict[str, Any]]:
        # dry_run 下只返回计划动作，不触发真实下单。
        if self.config.dry_run:
            return [
                {"mode": "dry_run", "action": "BUY", "token_id": pair.yes_token_id, "price": yes_price, "size": size},
                {"mode": "dry_run", "action": "BUY", "token_id": pair.no_token_id, "price": no_price, "size": size},
            ]

        # 当前服务不支持严格原子 FOK 双腿执行：若策略强制要求则直接阻断。
        if self.config.strict_fok_required and not self.config.allow_degraded_execution:
            raise ExecutionError("strict FOK pair execution required but unsupported by current API")

        # 降级执行路径：分别发两笔限价单，存在腿风险。
        r1 = await self.client.retry(self.client.place_limit_order, pair.yes_token_id, yes_price, size, "BUY")
        r2 = await self.client.retry(self.client.place_limit_order, pair.no_token_id, no_price, size, "BUY")
        return [r1, r2]

    async def place_pair_sell(self, pair: ArbPairConfig, yes_price: float, no_price: float, size: float) -> List[Dict[str, Any]]:
        if self.config.dry_run:
            return [
                {"mode": "dry_run", "action": "SELL", "token_id": pair.yes_token_id, "price": yes_price, "size": size},
                {"mode": "dry_run", "action": "SELL", "token_id": pair.no_token_id, "price": no_price, "size": size},
            ]

        # 卖出双腿同样受制于当前 API 的原子性限制。
        if self.config.strict_fok_required and not self.config.allow_degraded_execution:
            raise ExecutionError("strict FOK pair execution required but unsupported by current API")

        r1 = await self.client.retry(self.client.place_limit_order, pair.yes_token_id, yes_price, size, "SELL")
        r2 = await self.client.retry(self.client.place_limit_order, pair.no_token_id, no_price, size, "SELL")
        return [r1, r2]

    async def split(self, pair: ArbPairConfig, collateral_usdc: float) -> Dict[str, Any]:
        # 链上接口使用整数单位，先做单位换算。
        amount_units = to_ctf_units(collateral_usdc, self.config.ctf_amount_scale)
        if self.config.dry_run:
            return {
                "mode": "dry_run",
                "action": "split",
                "pair": pair.name,
                "collateral_usdc": collateral_usdc,
                "amount_units": amount_units,
            }
        return await self.client.retry(
            self.client._post,
            "/ctf/split",
            {
                "condition_id": pair.condition_id,
                "partition": pair.partition,
                "amount": amount_units,
                "collateral_token": pair.collateral_token,
                "parent_collection_id": pair.parent_collection_id,
            },
        )

    async def merge(self, pair: ArbPairConfig, collateral_usdc: float) -> Dict[str, Any]:
        # merge 与 split 的单位换算保持一致，避免资金口径偏差。
        amount_units = to_ctf_units(collateral_usdc, self.config.ctf_amount_scale)
        if self.config.dry_run:
            return {
                "mode": "dry_run",
                "action": "merge",
                "pair": pair.name,
                "collateral_usdc": collateral_usdc,
                "amount_units": amount_units,
            }
        return await self.client.retry(
            self.client.merge_positions,
            pair.condition_id,
            pair.partition,
            amount_units,
            pair.collateral_token,
            pair.parent_collection_id,
        )

    async def periodic_merge_if_possible(self, pair: ArbPairConfig) -> Dict[str, Any]:
        # 仅当 YES/NO 均有仓位时才可 merge，取可配对最小值。
        pos = await self.client.retry(self.client.get_positions, [pair.yes_token_id, pair.no_token_id])
        yes_qty = float(pos.get(pair.yes_token_id, 0.0))
        no_qty = float(pos.get(pair.no_token_id, 0.0))
        mergeable_shares = min(yes_qty, no_qty)
        if mergeable_shares < self.config.auto_merge_min_shares:
            return {
                "status": "skip",
                "reason": "insufficient_paired_inventory",
                "pair": pair.name,
                "mergeable_shares": mergeable_shares,
            }

        # 约定：1 对 YES+NO 份额近似对应 1 USDC 抵押。
        collateral_usdc = mergeable_shares
        resp = await self.merge(pair, collateral_usdc)
        return {
            "status": "ok",
            "pair": pair.name,
            "mergeable_shares": mergeable_shares,
            "merge_response": resp,
        }
