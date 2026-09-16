PRAGMA journal_mode=WAL;
PRAGMA synchronous=FULL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS collector_run (
    run_id TEXT PRIMARY KEY,
    collector_commit TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    vantage_id TEXT NOT NULL,
    host_fqdn TEXT NOT NULL,
    started_at_ns INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS collector_run_end (
    run_end_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    ended_at_ns INTEGER NOT NULL,
    termination_reason TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES collector_run(run_id)
);

CREATE TABLE IF NOT EXISTS clock_health (
    clock_sample_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    sampled_at_ns INTEGER NOT NULL,
    sampled_monotonic_ns INTEGER NOT NULL,
    probe_kind TEXT NOT NULL,
    offset_ms REAL,
    uncertainty_ms REAL,
    stratum INTEGER,
    leap_status TEXT,
    clock_valid INTEGER NOT NULL,
    raw_probe TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES collector_run(run_id)
);

CREATE TABLE IF NOT EXISTS access_attempt (
    access_attempt_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    source_family TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    attempted_at_ns INTEGER NOT NULL,
    phase TEXT NOT NULL,
    status TEXT NOT NULL,
    request_or_subscription TEXT NOT NULL,
    response_or_error TEXT,
    evidence_path TEXT,
    FOREIGN KEY (run_id) REFERENCES collector_run(run_id)
);

CREATE TABLE IF NOT EXISTS transport_message (
    transport_message_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    broker_or_endpoint TEXT NOT NULL,
    channel_or_topic TEXT,
    source_message_id TEXT,
    source_data_id TEXT,
    source_pubtime TEXT,
    received_wall_ns INTEGER NOT NULL,
    received_monotonic_ns INTEGER NOT NULL,
    clock_offset_ms REAL,
    clock_valid INTEGER NOT NULL,
    raw_payload_sha256 TEXT NOT NULL,
    raw_payload_path TEXT NOT NULL,
    payload_size_bytes INTEGER NOT NULL,
    FOREIGN KEY (run_id) REFERENCES collector_run(run_id)
);
CREATE INDEX IF NOT EXISTS idx_transport_source_received ON transport_message(source_id, received_wall_ns);
CREATE INDEX IF NOT EXISTS idx_transport_data_id ON transport_message(source_data_id, source_pubtime);

CREATE TABLE IF NOT EXISTS wis2_notification (
    notification_record_id TEXT PRIMARY KEY,
    transport_message_id TEXT NOT NULL,
    wnm_id TEXT,
    pubtime TEXT,
    data_id TEXT,
    metadata_id TEXT,
    cache_requested INTEGER,
    global_cache_json TEXT,
    temporal_json TEXT NOT NULL,
    links_json TEXT NOT NULL,
    content_json TEXT,
    integrity_json TEXT,
    parse_status TEXT NOT NULL,
    error_code TEXT,
    FOREIGN KEY (transport_message_id) REFERENCES transport_message(transport_message_id)
);

-- The raw callback cannot parse WNM metadata before its append-only transport
-- write. This view exposes the unified transport contract without mutating the
-- original receipt row after asynchronous notification parsing.
CREATE VIEW IF NOT EXISTS transport_message_enriched AS
SELECT
    t.transport_message_id,
    t.run_id,
    t.source_id,
    t.broker_or_endpoint,
    t.channel_or_topic,
    COALESCE(t.source_message_id, w.wnm_id) AS source_message_id,
    COALESCE(t.source_data_id, w.data_id) AS source_data_id,
    COALESCE(t.source_pubtime, w.pubtime) AS source_pubtime,
    t.received_wall_ns,
    t.received_monotonic_ns,
    t.clock_offset_ms,
    t.clock_valid,
    t.raw_payload_sha256,
    t.raw_payload_path,
    t.payload_size_bytes
FROM transport_message t
LEFT JOIN wis2_notification w USING(transport_message_id);

CREATE TABLE IF NOT EXISTS payload_fetch (
    fetch_id TEXT PRIMARY KEY,
    transport_message_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    url_hash TEXT NOT NULL,
    started_at_ns INTEGER NOT NULL,
    first_byte_at_ns INTEGER,
    finished_at_ns INTEGER,
    http_status INTEGER,
    content_type TEXT,
    content_encoding TEXT,
    sniffed_format TEXT,
    payload_sha256 TEXT,
    payload_path TEXT,
    integrity_status TEXT,
    error_code TEXT,
    FOREIGN KEY (transport_message_id) REFERENCES transport_message(transport_message_id)
);

CREATE TABLE IF NOT EXISTS observation_event (
    observation_version_id TEXT PRIMARY KEY,
    event_family_id TEXT NOT NULL,
    raw_report_id TEXT,
    semantic_version_id TEXT NOT NULL,
    station_id TEXT NOT NULL,
    report_kind TEXT NOT NULL,
    observation_time TEXT NOT NULL,
    is_correction INTEGER NOT NULL,
    correction_marker TEXT,
    air_temperature_c REAL,
    dewpoint_c REAL,
    wind_direction_deg REAL,
    wind_speed_kt REAL,
    visibility_m REAL,
    altimeter_hpa REAL,
    normalized_raw_text TEXT,
    normalized_fields_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_event_family ON observation_event(event_family_id);

CREATE TABLE IF NOT EXISTS source_observation_seen (
    source_seen_id TEXT PRIMARY KEY,
    observation_version_id TEXT NOT NULL,
    transport_message_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    vantage_id TEXT NOT NULL,
    notification_seen_at_ns INTEGER,
    payload_fetch_started_at_ns INTEGER,
    payload_fetch_finished_at_ns INTEGER,
    station_decoded_at_ns INTEGER NOT NULL,
    first_actionable_seen_at_ns INTEGER NOT NULL,
    clock_offset_ms REAL,
    clock_valid INTEGER NOT NULL,
    raw_payload_sha256 TEXT NOT NULL,
    evidence_status TEXT NOT NULL,
    FOREIGN KEY (observation_version_id) REFERENCES observation_event(observation_version_id),
    FOREIGN KEY (transport_message_id) REFERENCES transport_message(transport_message_id)
);
CREATE INDEX IF NOT EXISTS idx_seen_event_actionable ON source_observation_seen(observation_version_id, first_actionable_seen_at_ns);

CREATE TABLE IF NOT EXISTS replay_audit (
    replay_audit_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    replayed_at_ns INTEGER NOT NULL,
    raw_records INTEGER NOT NULL,
    observations INTEGER NOT NULL,
    stable_identity INTEGER NOT NULL,
    details_json TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES collector_run(run_id)
);

CREATE TRIGGER IF NOT EXISTS collector_run_no_update BEFORE UPDATE ON collector_run BEGIN SELECT RAISE(ABORT, 'collector_run is append-only'); END;
CREATE TRIGGER IF NOT EXISTS collector_run_no_delete BEFORE DELETE ON collector_run BEGIN SELECT RAISE(ABORT, 'collector_run is append-only'); END;
CREATE TRIGGER IF NOT EXISTS clock_health_no_update BEFORE UPDATE ON clock_health BEGIN SELECT RAISE(ABORT, 'clock_health is append-only'); END;
CREATE TRIGGER IF NOT EXISTS clock_health_no_delete BEFORE DELETE ON clock_health BEGIN SELECT RAISE(ABORT, 'clock_health is append-only'); END;
CREATE TRIGGER IF NOT EXISTS transport_message_no_update BEFORE UPDATE ON transport_message BEGIN SELECT RAISE(ABORT, 'transport_message is append-only'); END;
CREATE TRIGGER IF NOT EXISTS transport_message_no_delete BEFORE DELETE ON transport_message BEGIN SELECT RAISE(ABORT, 'transport_message is append-only'); END;
CREATE TRIGGER IF NOT EXISTS observation_event_no_update BEFORE UPDATE ON observation_event BEGIN SELECT RAISE(ABORT, 'observation_event is append-only'); END;
CREATE TRIGGER IF NOT EXISTS observation_event_no_delete BEFORE DELETE ON observation_event BEGIN SELECT RAISE(ABORT, 'observation_event is append-only'); END;
CREATE TRIGGER IF NOT EXISTS source_seen_no_update BEFORE UPDATE ON source_observation_seen BEGIN SELECT RAISE(ABORT, 'source_observation_seen is append-only'); END;
CREATE TRIGGER IF NOT EXISTS source_seen_no_delete BEFORE DELETE ON source_observation_seen BEGIN SELECT RAISE(ABORT, 'source_observation_seen is append-only'); END;
