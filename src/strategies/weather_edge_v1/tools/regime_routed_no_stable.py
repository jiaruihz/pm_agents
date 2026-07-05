from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[4]
ANALYSIS_DIR = ROOT / "scripts/analysis/reheat_risk"
if str(ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(ANALYSIS_DIR))

import research_intraday_weather_regime_atlas_v1 as _atlas  # noqa: E402
import research_regime_routed_no_expression_v1 as _research  # noqa: E402
from research_reheat_feature_factory_v1 import bracket_contains, parse_bracket  # noqa: E402


ASK_MIN = _research.ASK_MIN
ASK_CAPS = _research.ASK_CAPS
DECISION_HOURS = _research.DECISION_HOURS


def add_regime_labels(rows: pd.DataFrame) -> pd.DataFrame:
    return _atlas.add_regime_labels(rows)


def add_soft_weights(rows: pd.DataFrame) -> pd.DataFrame:
    return _research.add_soft_weights(rows)
