from datetime import datetime, timezone

from scripts.ops.station_basis_live_prep_gate_v1 import candidate_price_telemetry


def test_candidate_price_telemetry_marks_high_ask_as_unliveable():
    now = datetime(2026, 6, 14, 7, 0, tzinfo=timezone.utc)
    rows = [
        {
            "ts_utc": "2026-06-14T06:58:40+00:00",
            "city": "KualaLumpur",
            "rule": "no_d1_exh",
            "side": "BUY_NO",
            "bracket": "33C",
            "status": "ask_out_of_band",
            "ask": 0.998,
        },
        {
            "ts_utc": "2026-06-14T06:58:40+00:00",
            "city": "KualaLumpur",
            "rule": "no_d2_exh",
            "side": "BUY_NO",
            "bracket": "34C",
            "status": "no_asks",
        },
    ]

    out = candidate_price_telemetry(rows, now=now)

    assert out["recent_rows"] == 2
    assert out["price_eligible_rows"] == 0
    assert out["blocked_by_price_rows"] == 1
    assert out["near_miss_ask_90_97_rows"] == 0
    assert out["high_ask_gt_97_rows"] == 1
    assert out["no_ask_rows"] == 1
    assert out["min_ask_row"]["win_roi_if_fills"] == 0.002004


def test_candidate_price_telemetry_separates_near_miss_from_price_eligible():
    now = datetime(2026, 6, 14, 7, 0, tzinfo=timezone.utc)
    rows = [
        {
            "ts_utc": "2026-06-14T06:00:00+00:00",
            "city": "Chicago",
            "rule": "no_d1_exh",
            "side": "BUY_NO",
            "status": "ask_out_of_band",
            "ask": 0.93,
        },
        {
            "ts_utc": "2026-06-14T06:01:00+00:00",
            "city": "PanamaCity",
            "rule": "no_d1_exh",
            "side": "BUY_NO",
            "status": "entry_logged",
            "ask": 0.82,
        },
    ]

    out = candidate_price_telemetry(rows, now=now)

    assert out["near_miss_ask_90_97_rows"] == 1
    assert out["high_ask_gt_97_rows"] == 0
    assert out["price_eligible_rows"] == 1
    assert out["ask_band_counts"] == {"0.90-0.97": 1, "<=0.90": 1}
