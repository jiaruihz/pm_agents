from src.strategies.rule_lawyer.telegram_research_bot import parse_user_request


def test_parse_user_request_full_command() -> None:
    mode, target = parse_user_request("/full https://polymarket.com/event/foo/bar")
    assert mode == "full"
    assert target == "https://polymarket.com/event/foo/bar"


def test_parse_user_request_plain_url_uses_default_mode() -> None:
    mode, target = parse_user_request("https://polymarket.com/event/foo", default_mode="prompt")
    assert mode == "prompt"
    assert target == "https://polymarket.com/event/foo"


def test_parse_user_request_help_on_unknown_text() -> None:
    mode, target = parse_user_request("hello world", default_mode="full")
    assert mode == "help"
    assert target == ""
