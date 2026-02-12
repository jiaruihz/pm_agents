from typing import Any, Dict, List

from pmm.data.http_client import ToolServiceClient

from pm_arb_bot.config import ArbConfig, ArbPairConfig
from pm_arb_bot.utils import to_ctf_units


class ExecutionError(RuntimeError):
    pass


class ArbExecution:
    def __init__(self, config: ArbConfig, client: ToolServiceClient) -> None:
        self.config = config
        self.client = client

    async def place_pair_buy(self, pair: ArbPairConfig, yes_price: float, no_price: float, size: float) -> List[Dict[str, Any]]:
        if self.config.dry_run:
            return [
                {"mode": "dry_run", "action": "BUY", "token_id": pair.yes_token_id, "price": yes_price, "size": size},
                {"mode": "dry_run", "action": "BUY", "token_id": pair.no_token_id, "price": no_price, "size": size},
            ]

        # NOTE: strict atomic FOK pair execution is not available in current service API.
        if self.config.strict_fok_required and not self.config.allow_degraded_execution:
            raise ExecutionError("strict FOK pair execution required but unsupported by current API")

        # Degraded path: send two limit BUY orders at current best ask.
        r1 = await self.client.retry(self.client.place_limit_order, pair.yes_token_id, yes_price, size, "BUY")
        r2 = await self.client.retry(self.client.place_limit_order, pair.no_token_id, no_price, size, "BUY")
        return [r1, r2]

    async def place_pair_sell(self, pair: ArbPairConfig, yes_price: float, no_price: float, size: float) -> List[Dict[str, Any]]:
        if self.config.dry_run:
            return [
                {"mode": "dry_run", "action": "SELL", "token_id": pair.yes_token_id, "price": yes_price, "size": size},
                {"mode": "dry_run", "action": "SELL", "token_id": pair.no_token_id, "price": no_price, "size": size},
            ]

        # NOTE: strict atomic FOK pair execution is not available in current service API.
        if self.config.strict_fok_required and not self.config.allow_degraded_execution:
            raise ExecutionError("strict FOK pair execution required but unsupported by current API")

        r1 = await self.client.retry(self.client.place_limit_order, pair.yes_token_id, yes_price, size, "SELL")
        r2 = await self.client.retry(self.client.place_limit_order, pair.no_token_id, no_price, size, "SELL")
        return [r1, r2]

    async def split(self, pair: ArbPairConfig, collateral_usdc: float) -> Dict[str, Any]:
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

        # Assumption: one paired YES+NO share corresponds to ~1 USDC collateral.
        collateral_usdc = mergeable_shares
        resp = await self.merge(pair, collateral_usdc)
        return {
            "status": "ok",
            "pair": pair.name,
            "mergeable_shares": mergeable_shares,
            "merge_response": resp,
        }
