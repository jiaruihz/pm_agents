from scripts.analysis.market_structure_edge import (
    research_tokyo_checkpoint_first_bracket_v4 as subject,
)


def test_wrapper_preserves_caller_artifact_route(monkeypatch) -> None:
    captured: list[str] = []

    def fake_main(argv: list[str]) -> int:
        captured.extend(argv)
        return 0

    monkeypatch.setattr(subject.v3, "main", fake_main)

    assert subject.main(["--run-id", "tokyo-v4-replay-1"]) == 0
    assert captured[-2:] == ["--run-id", "tokyo-v4-replay-1"]
    assert "--out" not in captured
