from scripts.analysis.reheat_risk import (
    research_current_yes_core_carry_physical_semantic_audit_v2 as subject,
)


def test_precip_parser_recognizes_common_metar_codes() -> None:
    assert subject.re_search_precip("RA")
    assert subject.re_search_precip("-SHRA BR")
    assert subject.re_search_precip("TSRA")
    assert not subject.re_search_precip("BR HZ")
