"""Prompt builder: generate decision markdown."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from ..storage import update_market_statuses
from .constants import STATUS_READY_FOR_DECISION


class PromptBuilder:
    def __init__(self, output_path: str = "output/pap_candidates.md") -> None:
        self.output_path = Path(output_path)

    def generate(self, candidates: List[Dict[str, Any]]) -> str:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        lines = [
            "# Polymarket Alpha Candidates",
            f"Date (UTC): {now}",
            "",
        ]
        if not candidates:
            lines.append("No candidates found.")
        else:
            for c in candidates:
                lines.extend(
                    [
                        f"## {c.get('slug') or c['market_id']}",
                        f"- market_id: {c['market_id']}",
                        f"- edge: {c.get('edge'):.4f}",
                        f"- alpha_score: {c.get('alpha_score')}",
                        f"- mid: {c.get('mid')}",
                        f"- spread: {c.get('spread')}",
                        f"- liquidity: {c.get('liquidity')}",
                        f"- volume: {c.get('volume')}",
                        "",
                    ]
                )

        self.output_path.write_text("\n".join(lines), encoding="utf-8")
        update_market_statuses([c["market_id"] for c in candidates], STATUS_READY_FOR_DECISION)
        return str(self.output_path)
