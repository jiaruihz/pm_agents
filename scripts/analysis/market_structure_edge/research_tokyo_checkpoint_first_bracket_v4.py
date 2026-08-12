#!/usr/bin/env python3
"""Tokyo checkpoint strategy with one first entry per exact bracket.

The weather model is still the pre-2026-07-16 frozen model.  The corrected
position selector was specified after inspecting the original 15-day result,
so its 2026-07-16..30 trade metrics are diagnostic rather than a new frozen
forward claim.  No order is submitted.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.market_structure_edge import (
    research_tokyo_continuous_ladder_forward_v3 as v3,
)


def main(argv: list[str] | None = None) -> int:
    supplied = list(argv if argv is not None else sys.argv[1:])
    return v3.main(
        [
            "--selection-policy",
            "first_signal_per_model_target_date_bracket",
            "--analysis-version",
            "tokyo_checkpoint_first_bracket_v4",
            "--strategy-evaluation-status",
            "post_forward_user_corrected_selector_diagnostic",
            *supplied,
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
