from weather_dashboard.cli import ingest_strategy_runtime_orders as cli


def test_order_files_supplement_default_runtime_roots(tmp_path, monkeypatch) -> None:
    root = tmp_path / "runtime"
    discovered = root / "d1" / "live_orders.jsonl"
    discovered.parent.mkdir(parents=True)
    discovered.write_text("")
    explicit = tmp_path / "external" / "orders.jsonl"
    explicit.parent.mkdir()
    explicit.write_text("")
    monkeypatch.setattr(cli, "DEFAULT_ROOTS", (str(root),))

    assert cli.resolve_order_paths(None, [str(explicit)]) == [discovered, explicit]
