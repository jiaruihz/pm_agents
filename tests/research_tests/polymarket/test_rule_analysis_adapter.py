import unittest
from unittest.mock import patch

from src.strategies.rule_lawyer.adapters.rule_analysis_adapter import run_rule_analysis
from src.strategies.rule_lawyer.models_research import ResolvedMarket


class RuleAnalysisAdapterTests(unittest.TestCase):
    @patch("src.strategies.rule_lawyer.adapters.rule_analysis_adapter._codex_cli_ready", return_value=False)
    @patch("src.strategies.rule_lawyer.adapters.rule_analysis_adapter._llm_ready", return_value=False)
    def test_rule_analysis_unavailable_without_llm_when_required(self, _mock_llm_ready, _mock_codex_ready) -> None:
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

    @patch("src.strategies.rule_lawyer.adapters.rule_analysis_adapter._codex_cli_ready", return_value=False)
    @patch("src.strategies.rule_lawyer.adapters.rule_analysis_adapter._llm_ready", return_value=False)
    def test_rule_analysis_heuristic_when_fallback_allowed(self, _mock_llm_ready, _mock_codex_ready) -> None:
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

    @patch("src.strategies.rule_lawyer.adapters.rule_analysis_adapter._codex_cli_ready", return_value=True)
    @patch("src.strategies.rule_lawyer.adapters.rule_analysis_adapter._llm_ready", return_value=False)
    @patch("src.strategies.rule_lawyer.parser.parse_market_with_codex_cli")
    def test_rule_analysis_uses_codex_cli_backend(self, mock_parse_codex, _mock_llm_ready, _mock_codex_ready) -> None:
        from src.strategies.rule_lawyer.parser import RuleParse, TimeWindow

        mock_parse_codex.return_value = RuleParse(
            market_id="1",
            slug="test-market",
            time_window=TimeWindow(end_at_utc="2026-12-31T00:00:00Z", timezone_source="rules"),
            settlement_source_type="official_docs",
            trigger_type="definition_driven",
            trigger_minimum_conditions=["Official acquisition agreement announced"],
            explicit_exclusions=["Rumors without announced agreement"],
            entity_definitions=[],
            ambiguity_flags=[],
            clarity_score=0.82,
            dispute_risk_score=0.18,
            notes_for_humans="Agreement announcement is enough for Yes.",
            llm_confidence=0.76,
        )
        market = ResolvedMarket(
            market_id="1",
            slug="test-market",
            question="Will X happen?",
            description="",
            rules="",
            end_date="2026-12-31T00:00:00Z",
        )
        summary = run_rule_analysis(market)
        self.assertEqual(summary.rule_status, "ok")
        self.assertTrue(summary.llm_used)
        self.assertEqual(summary.source_trace["backend"], "codex_cli")
        self.assertEqual(summary.source_trace["mode"], "codex_cli")


if __name__ == "__main__":
    unittest.main()
