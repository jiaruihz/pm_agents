from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "research_late_window_residual_trade_win_v2",
    ROOT / "scripts/analysis/reheat_risk/research_late_window_residual_trade_win_v2.py",
)
assert SPEC is not None and SPEC.loader is not None
trade_win = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = trade_win
SPEC.loader.exec_module(trade_win)


def test_market_no_baseline_uses_executable_no_ask_without_inversion() -> None:
    frame = pd.DataFrame({"entry_price": [0.89, 0.95, np.nan]})

    actual = trade_win.market_no_baseline(frame)

    assert actual.iloc[0] == 0.89
    assert actual.iloc[1] == 0.95
    assert np.isnan(actual.iloc[2])


def test_coherent_exact_probability_cannot_exceed_touch_probability() -> None:
    p_touch = np.array([0.20, 0.65, 1.00])
    p_stop_given_touch = np.array([0.90, 0.75, 0.30])

    p_exact = trade_win.coherent_exact_probability(p_touch, p_stop_given_touch)

    np.testing.assert_allclose(p_exact, np.array([0.18, 0.4875, 0.30]))
    assert np.all(p_exact <= p_touch)
