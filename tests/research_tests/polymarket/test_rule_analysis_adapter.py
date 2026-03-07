import unittest
from unittest.mock import patch

from src.strategies.rule_lawyer.adapters.rule_analysis_adapter import run_rule_analysis
from src.strategies.rule_lawyer.models_research import ResolvedMarket


class RuleAnalysisAdapterTests(unittest.TestCase):
    @patch("src.strategies.rule_lawyer.adapters.rule_analysis_adapter._llm_ready", return_value=False)
    def test_rule_analysis_unavailable_without_llm_when_required(self, _mock_llm_ready) -> None:
        market = ResolvedMarket(
            market_id="1",
            slug="test-market",
            question="Will X happen?",
            description="This market resolves based on credible reporting unless the committee determines otherwise.",
            rules="This market resolves YES if X happens. Variations may be considered at Polymarket's sole discretion.",
            end_date="2026-12-31T00:00:00Z",
        )
        summary = run_rule_analysis(market)
        self.assertEqual(summary.rule_status, "unavailable")
        self.assertEqual(summary.source_trace["mode"], "llm_required")
        self.assertEqual(summary.rule_clarity_score, 0.0)
        self.assertFalse(summary.llm_used)

    @patch("src.strategies.rule_lawyer.adapters.rule_analysis_adapter._llm_ready", return_value=False)
    def test_rule_analysis_heuristic_when_fallback_allowed(self, _mock_llm_ready) -> None:
        market = ResolvedMarket(
            market_id="1",
            slug="test-market",
            question="Will X happen?",
            description="This market resolves based on credible reporting unless the committee determines otherwise.",
            rules="This market resolves YES if X happens. Variations may be considered at Polymarket's sole discretion.",
            end_date="2026-12-31T00:00:00Z",
        )
        summary = run_rule_analysis(market, require_llm=False)
        self.assertEqual(summary.rule_status, "ok")
        self.assertEqual(summary.source_trace["mode"], "heuristic")
        self.assertGreater(summary.rule_clarity_score, 0)
        self.assertGreater(summary.resolution_risk, 0)
        self.assertTrue(summary.ambiguity_flags)


if __name__ == "__main__":
    unittest.main()
