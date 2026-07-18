from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OPS = ROOT / "scripts/ops"


def test_jrs_managed_start_entries_use_canonical_tmux_helper():
    offenders: list[str] = []
    for path in sorted(OPS.glob("start_*.sh")):
        text = path.read_text(encoding="utf-8")
        touches_jrs = "/Volumes/jrs" in text or "weather_data_feed_service_runtime" in text
        manages_process = any(token in text for token in ("tmux -L", '"$TMUX_BIN" -L', "screen -dmS", "nohup"))
        if not (touches_jrs and manages_process):
            continue
        if "weather_jrs_tmux_env.sh" not in text:
            offenders.append(f"{path.name}:missing_canonical_helper")
        if "weather_jrs_tmux_start_socket" not in text:
            offenders.append(f"{path.name}:missing_internal_write_probe")
        if "weather-jrs" in text or "weather-full-ladder" in text:
            offenders.append(f"{path.name}:legacy_socket_literal")
        if "screen -dmS" in text:
            offenders.append(f"{path.name}:screen_start")
    assert offenders == []


def test_no_ops_entrypoint_keeps_legacy_jrs_socket_literal():
    offenders = []
    for path in sorted(OPS.iterdir()):
        if path.suffix not in {".sh", ".py"}:
            continue
        if "weather-jrs" in path.read_text(encoding="utf-8"):
            offenders.append(path.name)
    assert offenders == []
