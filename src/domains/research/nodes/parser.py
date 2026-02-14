"""Parser node: LLM parsing and market_rule_parses population."""

import asyncio
import json
from typing import Any, Dict, List

from ..llm_prompts import PROMPT_VERSION
from ..parser import parse_market_with_llm, save_market_rule_parses_records, save_market_rule_parse_failure
from ..storage import get_market_rule_parse, get_markets_by_status, update_market_statuses
from .constants import DEFAULT_READY_SCORE, STATUS_PARSED, STATUS_READY_TO_PARSE, STATUS_READY_TO_SEARCH


class ParserNode:
    def __init__(
        self,
        batch: int = 100,
        ready_score: int = DEFAULT_READY_SCORE,
        concurrency: int = 3,
        show_progress: bool = True,
    ) -> None:
        self.batch = batch
        self.ready_score = ready_score
        self.concurrency = max(1, concurrency)
        self.show_progress = show_progress

    async def _parse_one(self, market: Dict[str, Any]) -> Dict[str, Any]:
        try:
            parsed = await parse_market_with_llm(market)
        except ValueError:
            return {"market_id": market["market_id"], "parsed": False, "error": "LLM config missing"}
        except Exception as exc:
            return {"market_id": market["market_id"], "parsed": False, "error": str(exc)}
        if not parsed:
            return {"market_id": market["market_id"], "parsed": False}
        alpha_score = int(round((parsed.clarity_score or 0) * 100))
        save_market_rule_parses_records(
            market["market_id"],
            parsed,
            parsed.dict(),
            strategy_tag="NOISE",
            alpha_score=alpha_score,
            hard_constraints=[],
            search_keywords=[],
        )
        return {"market_id": market["market_id"], "parsed": True, "alpha_score": alpha_score}

    async def execute(self) -> Dict[str, Any]:
        markets = get_markets_by_status([STATUS_READY_TO_PARSE], limit=self.batch)
        if not markets:
            return {"parsed": 0, "ready_to_search": 0, "kept": 0, "skipped_existing": 0}

        async def _process_market(market: Dict[str, Any]) -> Dict[str, int]:
            market_id = market["market_id"]
            existing = get_market_rule_parse(market_id, PROMPT_VERSION)
            if existing:
                alpha_score = existing.get("alpha_score")
                if alpha_score is None:
                    alpha_score = int(round((existing.get("clarity_score") or 0) * 100))
                if alpha_score >= self.ready_score:
                    update_market_statuses([market_id], STATUS_READY_TO_SEARCH)
                    return {"ready": 1, "kept": 0, "parsed": 0, "skipped": 1}
                update_market_statuses([market_id], STATUS_PARSED)
                return {"ready": 0, "kept": 1, "parsed": 0, "skipped": 1}

            res = await self._parse_one(market)
            if not res.get("parsed"):
                if res.get("error"):
                    save_market_rule_parse_failure(market_id, res["error"])
                    update_market_statuses([market_id], STATUS_PARSED)
                    return {"ready": 0, "kept": 0, "parsed": 0, "skipped": 0, "failed": 1}
                return {"ready": 0, "kept": 0, "parsed": 0, "skipped": 0, "failed": 0}
            if (res.get("alpha_score") or 0) >= self.ready_score:
                update_market_statuses([market_id], STATUS_READY_TO_SEARCH)
                return {"ready": 1, "kept": 0, "parsed": 1, "skipped": 0, "failed": 0}
            update_market_statuses([market_id], STATUS_PARSED)
            return {"ready": 0, "kept": 1, "parsed": 1, "skipped": 0, "failed": 0}

        sem = asyncio.Semaphore(self.concurrency)

        async def _guarded(market: Dict[str, Any]) -> Dict[str, int]:
            async with sem:
                try:
                    return await _process_market(market)
                except Exception:
                    return {"ready": 0, "kept": 0, "parsed": 0, "skipped": 0}

        tasks = [asyncio.create_task(_guarded(m)) for m in markets]
        if self.show_progress:
            from rich.progress import (
                BarColumn,
                Progress,
                TaskProgressColumn,
                TextColumn,
                TimeElapsedColumn,
                TimeRemainingColumn,
            )

            results = []
            with Progress(
                TextColumn("{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TimeElapsedColumn(),
                TimeRemainingColumn(),
            ) as progress:
                task_id = progress.add_task("LLM parsing", total=len(tasks))
                for coro in asyncio.as_completed(tasks):
                    results.append(await coro)
                    progress.update(task_id, advance=1)
        else:
            results = await asyncio.gather(*tasks)
        parsed_count = sum(r["parsed"] for r in results)
        ready_count = sum(r["ready"] for r in results)
        kept_count = sum(r["kept"] for r in results)
        skipped_existing = sum(r["skipped"] for r in results)
        failed_count = sum(r.get("failed", 0) for r in results)

        return {
            "parsed": parsed_count,
            "ready_to_search": ready_count,
            "kept": kept_count,
            "skipped_existing": skipped_existing,
            "failed": failed_count,
        }
