from scripts.ops.check_weather_runtime_resources import hazards, introduced


def test_bulk_history_cannot_hide_behind_output_or_disk_wrapper():
    assert hazards('rows = path.read_text().splitlines()')
    assert hazards('rows = list(iter_jsonl(path))')
    assert hazards('rows = DiskRows([row for row in source])')
    assert not hazards('rows = DiskRows(row for row in source)')


def test_gate_distinguishes_new_regression_from_reviewed_baseline():
    old = 'rows = path.read_text().splitlines()'
    assert not introduced(old, old)
    assert introduced(old, old + '\nother = set(read_jsonl(journal))')
