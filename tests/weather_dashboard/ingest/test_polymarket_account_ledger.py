from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

import pytest

from weather_dashboard.ingest.polymarket_account_ledger import (
    AccountConfig,
    SCHEMA_VERSION,
    connect_ledger,
    register_accounts,
    sync_account,
)


PERSONAL = AccountConfig.from_mapping(
    {
        "account_id": "jiaruihz",
        "account_label": "jiaruihz",
        "account_kind": "personal",
        "proxy_wallet": "0x83946fd2a83b4c0045a17a938ae0802dfaf58d66",
        "relayer_api_key_address": "0x06D760A9e0D520d748537aA1f23cc7356c9DC656",
        "keychain_account": "jiaruihz",
        "relayer_api_key_service": "pm_agents.polymarket.relayer_api_key",
        "sync_relayer": True,
        "tags": ["personal", "capital_efficiency"],
    }
)
WEATHER = AccountConfig.from_mapping(
    {
        "account_id": "weather_live",
        "account_label": "weather_live",
        "account_kind": "weather_bot",
        "proxy_wallet": "0x76c7ad96e789e3995046af3bb13218f5f1a2860e",
        "sync_relayer": False,
        "tags": ["weather", "bot"],
    }
)
NOW = datetime(2026, 8, 28, 1, 2, 3, tzinfo=timezone.utc)


def activity(wallet: str = PERSONAL.proxy_wallet):
    return {
        "proxyWallet": wallet,
        "timestamp": 1_777_500_000,
        "conditionId": "0x" + "1" * 64,
        "type": "TRADE",
        "size": 10,
        "usdcSize": 8.1,
        "price": 0.8,
        "asset": "token-1",
        "side": "BUY",
        "outcomeIndex": 0,
        "title": "Example market",
        "slug": "example-market",
        "eventSlug": "example-event",
        "outcome": "Yes",
        "transactionHash": "0xabc",
    }


def position(wallet: str = PERSONAL.proxy_wallet):
    return {
        "proxyWallet": wallet,
        "asset": "token-1",
        "conditionId": "0x" + "1" * 64,
        "size": 10,
        "avgPrice": 0.81,
        "initialValue": 8.1,
        "currentValue": 9.0,
        "cashPnl": 0.9,
        "realizedPnl": 0,
        "curPrice": 0.9,
        "totalBought": 10,
        "redeemable": False,
        "outcome": "Yes",
        "title": "Example market",
    }


def relayer(state: str = "STATE_CONFIRMED", updated: str = "2026-08-28T00:00:00Z"):
    return {
        "transactionID": "rel-1",
        "transactionHash": "0xdef",
        "from": PERSONAL.relayer_api_key_address,
        "to": "0x" + "2" * 40,
        "proxyAddress": PERSONAL.proxy_wallet,
        "nonce": "1",
        "state": state,
        "type": "PROXY",
        "createdAt": "2026-08-27T23:59:00Z",
        "updatedAt": updated,
    }


class FakeSource:
    def __init__(self, *, activities=None, positions=None, relayer_rows=None):
        self.activities = list(activities if activities is not None else [activity()])
        self.positions = list(positions if positions is not None else [position()])
        self.relayer_rows = list(relayer_rows if relayer_rows is not None else [relayer()])

    def earliest_activity_timestamp(self, proxy_wallet):
        return min((row["timestamp"] for row in self.activities), default=None)

    def activity_rows(self, proxy_wallet, start_ts, end_ts):
        rows = [row for row in self.activities if start_ts <= row["timestamp"] <= end_ts]
        return rows, {"requests": 1, "split_nodes": 0, "leaf_windows": 1, "max_split_depth": 0}

    def current_positions(self, proxy_wallet):
        return self.positions, False

    def public_trades(self, proxy_wallet):
        rows = [
            {
                key: row.get(key)
                for key in (
                    "proxyWallet",
                    "timestamp",
                    "conditionId",
                    "size",
                    "price",
                    "asset",
                    "side",
                    "outcomeIndex",
                    "title",
                    "slug",
                    "eventSlug",
                    "outcome",
                    "transactionHash",
                )
            }
            for row in self.activities
            if row.get("type") == "TRADE"
        ]
        return rows, False

    def relayer_transactions(self, api_key, api_key_address):
        assert api_key == "secret-from-keychain"
        assert api_key_address == PERSONAL.relayer_api_key_address
        return self.relayer_rows


def key_loader(account, service):
    assert account == "jiaruihz"
    assert service == "pm_agents.polymarket.relayer_api_key"
    return "secret-from-keychain"


def test_account_registry_separates_personal_and_weather_wallets(tmp_path):
    conn = connect_ledger(tmp_path / "ledger.db")
    register_accounts(conn, [PERSONAL, WEATHER], observed_at=NOW)
    rows = conn.execute(
        "SELECT account_id, account_kind, proxy_wallet FROM pm_accounts ORDER BY account_id"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("jiaruihz", "personal", PERSONAL.proxy_wallet),
        ("weather_live", "weather_bot", WEATHER.proxy_wallet),
    ]


def test_sync_is_idempotent_for_events_and_append_only_for_snapshots(tmp_path):
    conn = connect_ledger(tmp_path / "ledger.db")
    first = sync_account(
        conn=conn,
        account=PERSONAL,
        source=FakeSource(),
        relayer_key_loader=key_loader,
        full_backfill=True,
        observed_at=NOW,
    )
    second = sync_account(
        conn=conn,
        account=PERSONAL,
        source=FakeSource(),
        relayer_key_loader=key_loader,
        observed_at=datetime(2026, 8, 28, 2, 2, 3, tzinfo=timezone.utc),
    )
    assert first["sync"]["activity_inserted"] == 1
    assert second["sync"]["activity_inserted"] == 0
    assert first["sync"]["relayer_states_inserted"] == 1
    assert second["sync"]["relayer_states_inserted"] == 0
    assert conn.execute("SELECT COUNT(*) FROM pm_activity_events").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM pm_position_snapshots").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM pm_sync_runs").fetchone()[0] == 2

    fill = conn.execute("SELECT * FROM v_pm_public_fills").fetchone()
    assert fill["account_id"] == "jiaruihz"
    assert fill["gross_notional_usd"] == pytest.approx(8.0)
    assert fill["observed_cash_amount_usd"] == pytest.approx(8.1)
    assert fill["signed_trade_cashflow_usd"] == pytest.approx(-8.1)
    assert fill["activity_gross_shares"] == pytest.approx(10)
    assert fill["trade_net_shares"] == pytest.approx(10)
    assert fill["evidence_class"] == "public_activity_plus_public_trade_crosscheck"


def test_relayer_state_transitions_are_preserved(tmp_path):
    conn = connect_ledger(tmp_path / "ledger.db")
    sync_account(
        conn=conn,
        account=PERSONAL,
        source=FakeSource(relayer_rows=[relayer("STATE_MINED", "2026-08-28T00:00:00Z")]),
        relayer_key_loader=key_loader,
        full_backfill=True,
        observed_at=NOW,
    )
    sync_account(
        conn=conn,
        account=PERSONAL,
        source=FakeSource(relayer_rows=[relayer("STATE_CONFIRMED", "2026-08-28T00:01:00Z")]),
        relayer_key_loader=key_loader,
        observed_at=datetime(2026, 8, 28, 2, tzinfo=timezone.utc),
    )
    states = conn.execute(
        "SELECT state FROM pm_relayer_transaction_states ORDER BY source_updated_at_utc"
    ).fetchall()
    assert [row[0] for row in states] == ["STATE_MINED", "STATE_CONFIRMED"]


def test_cross_account_source_rows_fail_closed(tmp_path):
    conn = connect_ledger(tmp_path / "ledger.db")
    with pytest.raises(RuntimeError, match="outside configured account"):
        sync_account(
            conn=conn,
            account=PERSONAL,
            source=FakeSource(activities=[activity(WEATHER.proxy_wallet)]),
            relayer_key_loader=key_loader,
            full_backfill=True,
            observed_at=NOW,
        )
    assert conn.execute("SELECT COUNT(*) FROM pm_sync_runs").fetchone()[0] == 0


def test_public_trade_crosscheck_fails_on_missing_activity_fill(tmp_path):
    class MismatchSource(FakeSource):
        def public_trades(self, proxy_wallet):
            extra = activity()
            extra["transactionHash"] = "0xmissing"
            extra["timestamp"] += 1
            return super().public_trades(proxy_wallet)[0] + [extra], False

    conn = connect_ledger(tmp_path / "ledger.db")
    with pytest.raises(RuntimeError, match="public trade coverage mismatch"):
        sync_account(
            conn=conn,
            account=PERSONAL,
            source=MismatchSource(),
            relayer_key_loader=key_loader,
            full_backfill=True,
            observed_at=NOW,
        )
    assert conn.execute("SELECT COUNT(*) FROM pm_sync_runs").fetchone()[0] == 0


def test_missing_source_wallet_identity_fails_closed(tmp_path):
    row = activity()
    row.pop("proxyWallet")
    conn = connect_ledger(tmp_path / "ledger.db")
    with pytest.raises(RuntimeError, match="without required account identity"):
        sync_account(
            conn=conn,
            account=PERSONAL,
            source=FakeSource(activities=[row]),
            relayer_key_loader=key_loader,
            full_backfill=True,
            observed_at=NOW,
        )
    assert conn.execute("SELECT COUNT(*) FROM pm_sync_runs").fetchone()[0] == 0


def test_truncated_positions_fail_before_snapshot_write(tmp_path):
    class TruncatedPositionSource(FakeSource):
        def current_positions(self, proxy_wallet):
            return self.positions, True

    conn = connect_ledger(tmp_path / "ledger.db")
    with pytest.raises(RuntimeError, match="incomplete snapshot"):
        sync_account(
            conn=conn,
            account=PERSONAL,
            source=TruncatedPositionSource(),
            relayer_key_loader=key_loader,
            full_backfill=True,
            observed_at=NOW,
        )
    assert conn.execute("SELECT COUNT(*) FROM pm_position_snapshots").fetchone()[0] == 0


def test_truncated_public_trades_do_not_write_partial_enrichment(tmp_path):
    class TruncatedTradeSource(FakeSource):
        def public_trades(self, proxy_wallet):
            rows, _ = super().public_trades(proxy_wallet)
            return rows, True

    conn = connect_ledger(tmp_path / "ledger.db")
    result = sync_account(
        conn=conn,
        account=PERSONAL,
        source=TruncatedTradeSource(),
        relayer_key_loader=key_loader,
        full_backfill=True,
        observed_at=NOW,
    )
    assert result["sync"]["public_trade_crosscheck"]["gate_pass"] is False
    assert result["sync"]["public_trade_records_inserted"] == 0
    assert conn.execute("SELECT COUNT(*) FROM pm_public_trade_records").fetchone()[0] == 0
    fill = conn.execute("SELECT * FROM v_pm_public_fills").fetchone()
    assert fill["trade_record_key"] is None
    assert fill["evidence_class"] == "public_activity_account_level"


def test_exact_public_trade_duplicates_are_reported(tmp_path):
    class DuplicateTradeSource(FakeSource):
        def public_trades(self, proxy_wallet):
            rows, truncated = super().public_trades(proxy_wallet)
            return rows + [dict(rows[0])], truncated

    conn = connect_ledger(tmp_path / "ledger.db")
    result = sync_account(
        conn=conn,
        account=PERSONAL,
        source=DuplicateTradeSource(),
        relayer_key_loader=key_loader,
        full_backfill=True,
        observed_at=NOW,
    )
    gate = result["sync"]["public_trade_crosscheck"]
    assert gate["gate_pass"] is True
    assert gate["exact_duplicate_rows_collapsed"] == 1
    assert conn.execute("SELECT COUNT(*) FROM pm_public_trade_records").fetchone()[0] == 1


def test_v1_database_migrates_explicitly_to_current_schema(tmp_path):
    path = tmp_path / "ledger.db"
    conn = connect_ledger(path)
    conn.execute(
        "UPDATE ledger_meta SET value='polymarket_account_ledger_v1' WHERE key='schema_version'"
    )
    conn.commit()
    conn.close()

    migrated = connect_ledger(path)
    assert migrated.execute(
        "SELECT value FROM ledger_meta WHERE key='schema_version'"
    ).fetchone()[0] == SCHEMA_VERSION
    assert migrated.execute(
        """
        SELECT COUNT(*) FROM ledger_migrations
        WHERE from_version='polymarket_account_ledger_v1' AND to_version=?
        """,
        (SCHEMA_VERSION,),
    ).fetchone()[0] == 1


def test_unknown_schema_is_rejected_before_migration(tmp_path):
    path = tmp_path / "ledger.db"
    conn = connect_ledger(path)
    conn.execute(
        "UPDATE ledger_meta SET value='polymarket_account_ledger_v999' WHERE key='schema_version'"
    )
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="no migration"):
        connect_ledger(path)


def test_trade_endpoint_net_shares_can_differ_from_activity_gross_shares(tmp_path):
    class FeeAdjustedSource(FakeSource):
        def public_trades(self, proxy_wallet):
            rows, truncated = super().public_trades(proxy_wallet)
            rows[0]["size"] = 9.75
            return rows, truncated

    conn = connect_ledger(tmp_path / "ledger.db")
    result = sync_account(
        conn=conn,
        account=PERSONAL,
        source=FeeAdjustedSource(),
        relayer_key_loader=key_loader,
        full_backfill=True,
        observed_at=NOW,
    )
    assert result["sync"]["public_trade_crosscheck"]["gate_pass"] is True
    assert result["sync"]["public_trade_crosscheck"]["share_quantity_mismatches"] == 1
    fill = conn.execute("SELECT * FROM v_pm_public_fills").fetchone()
    assert fill["activity_gross_shares"] == pytest.approx(10)
    assert fill["trade_net_shares"] == pytest.approx(9.75)
    assert fill["fill_shares"] == pytest.approx(9.75)
    assert fill["share_quantity_delta"] == pytest.approx(0.25)


def test_proxy_wallet_cannot_be_reassigned_to_another_label(tmp_path):
    conn = connect_ledger(tmp_path / "ledger.db")
    register_accounts(conn, [PERSONAL], observed_at=NOW)
    duplicate = AccountConfig.from_mapping(
        {
            "account_id": "other",
            "account_label": "other",
            "account_kind": "external",
            "proxy_wallet": PERSONAL.proxy_wallet,
            "sync_relayer": False,
        }
    )
    with pytest.raises(RuntimeError, match="already belongs"):
        register_accounts(conn, [duplicate], observed_at=NOW)


def test_schema_rejects_foreign_keyless_writes(tmp_path):
    conn = connect_ledger(tmp_path / "ledger.db")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO pm_sync_runs(
                run_id, account_id, started_at_utc, completed_at_utc,
                requested_start_ts, requested_end_ts, full_backfill,
                public_activity_rows_fetched, public_activity_rows_inserted,
                position_rows_fetched, position_rows_inserted,
                relayer_rows_fetched, relayer_states_inserted, coverage_json
            ) VALUES('bad', 'missing', 'x', 'x', NULL, 1, 0, 0, 0, 0, 0, 0, 0, '{}')
            """
        )
