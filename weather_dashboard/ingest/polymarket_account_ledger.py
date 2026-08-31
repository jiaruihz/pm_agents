"""Append-only, account-scoped persistence for external Polymarket wallets.

This ledger is deliberately separate from the weather canonical database.  It
stores public account activity, point-in-time positions, and Relayer transaction
states.  Public activity is account-level evidence: it cannot recover private
signals, unfilled/cancelled orders, or the original order-post timestamp.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Callable, Iterable, Mapping, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

import requests


DATA_API = "https://data-api.polymarket.com"
RELAYER_API = "https://relayer-v2.polymarket.com"
SCHEMA_VERSION = "polymarket_account_ledger_v2"
MIGRATABLE_SCHEMA_VERSIONS = {"polymarket_account_ledger_v1"}
TRANSIENT_HTTP_STATUS = {403, 408, 425, 429, 500, 502, 503, 504}
ACTIVITY_PAGE_SIZE = 500
ACTIVITY_WINDOW_CAP = 5_500
BJ = ZoneInfo("Asia/Shanghai")
ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_address(value: str | None, *, required: bool = True) -> str | None:
    text = str(value or "").strip()
    if not text:
        if required:
            raise ValueError("wallet address is required")
        return None
    if not ADDRESS_RE.fullmatch(text):
        raise ValueError(f"invalid wallet address: {text}")
    return text.lower()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_key(parts: Iterable[Any]) -> str:
    return hashlib.sha256(canonical_json(list(parts)).encode("utf-8")).hexdigest()


def activity_event_key(row: Mapping[str, Any]) -> str:
    """Stable fallback identity for one public account-activity row."""
    return stable_key(
        (
            row.get("transactionHash"),
            row.get("type"),
            row.get("asset"),
            row.get("conditionId"),
            row.get("side"),
            row.get("size"),
            row.get("usdcSize"),
            row.get("price"),
            row.get("timestamp"),
            row.get("outcomeIndex"),
        )
    )


def public_trade_join_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    """Common identity shared by Data API ``activity`` and ``trades`` rows.

    ``size`` is intentionally excluded.  On fee-enabled BUY markets the
    activity endpoint reports gross shares/cash while the trades endpoint can
    report net received shares.
    """
    return (
        row.get("transactionHash"),
        row.get("asset"),
        str(row.get("side") or "").upper(),
        round(float(row.get("price") or 0), 10),
        int(row.get("timestamp") or 0),
    )


def public_trade_record_key(row: Mapping[str, Any]) -> str:
    return stable_key((*public_trade_join_key(row), round(float(row.get("size") or 0), 8)))


def position_key(row: Mapping[str, Any]) -> str:
    asset = str(row.get("asset") or "").strip()
    if asset:
        return asset
    return stable_key((row.get("conditionId"), row.get("outcomeIndex"), row.get("outcome")))


@dataclass(frozen=True)
class AccountConfig:
    account_id: str
    account_label: str
    account_kind: str
    proxy_wallet: str
    relayer_api_key_address: str | None
    keychain_account: str | None
    relayer_api_key_service: str | None
    sync_relayer: bool
    tags: tuple[str, ...]

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "AccountConfig":
        account_id = str(row.get("account_id") or "").strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,63}", account_id):
            raise ValueError(f"invalid account_id: {account_id!r}")
        kind = str(row.get("account_kind") or "").strip()
        if kind not in {"personal", "weather_bot", "external"}:
            raise ValueError(f"invalid account_kind for {account_id}: {kind!r}")
        relayer_address = normalize_address(
            row.get("relayer_api_key_address"), required=False
        )
        sync_relayer = bool(row.get("sync_relayer", False))
        keychain_account = str(row.get("keychain_account") or "").strip() or None
        keychain_service = str(row.get("relayer_api_key_service") or "").strip() or None
        if sync_relayer and not (relayer_address and keychain_account and keychain_service):
            raise ValueError(
                f"account {account_id} enables Relayer sync without address/keychain identity"
            )
        return cls(
            account_id=account_id,
            account_label=str(row.get("account_label") or account_id).strip(),
            account_kind=kind,
            proxy_wallet=str(normalize_address(row.get("proxy_wallet"))),
            relayer_api_key_address=relayer_address,
            keychain_account=keychain_account,
            relayer_api_key_service=keychain_service,
            sync_relayer=sync_relayer,
            tags=tuple(sorted({str(item).strip() for item in row.get("tags", []) if str(item).strip()})),
        )


def load_account_registry(path: Path) -> dict[str, AccountConfig]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "polymarket_account_registry_v1":
        raise ValueError(f"unsupported account registry schema: {payload.get('schema_version')!r}")
    rows = payload.get("accounts")
    if not isinstance(rows, list):
        raise ValueError("account registry must contain an accounts list")
    accounts = [AccountConfig.from_mapping(row) for row in rows]
    by_id = {row.account_id: row for row in accounts}
    if len(by_id) != len(accounts):
        raise ValueError("duplicate account_id in registry")
    wallets = [row.proxy_wallet for row in accounts]
    if len(set(wallets)) != len(wallets):
        raise ValueError("one proxy wallet cannot be assigned to multiple account labels")
    return by_id


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ledger_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ledger_migrations (
    from_version TEXT NOT NULL,
    to_version TEXT NOT NULL,
    migrated_at_utc TEXT NOT NULL,
    PRIMARY KEY (from_version, to_version)
);

CREATE TABLE IF NOT EXISTS pm_accounts (
    account_id TEXT PRIMARY KEY,
    account_label TEXT NOT NULL UNIQUE,
    account_kind TEXT NOT NULL,
    proxy_wallet TEXT NOT NULL UNIQUE,
    relayer_api_key_address TEXT,
    tags_json TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    last_registered_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pm_sync_runs (
    run_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES pm_accounts(account_id),
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT NOT NULL,
    requested_start_ts INTEGER,
    requested_end_ts INTEGER NOT NULL,
    full_backfill INTEGER NOT NULL,
    public_activity_rows_fetched INTEGER NOT NULL,
    public_activity_rows_inserted INTEGER NOT NULL,
    position_rows_fetched INTEGER NOT NULL,
    position_rows_inserted INTEGER NOT NULL,
    relayer_rows_fetched INTEGER NOT NULL,
    relayer_states_inserted INTEGER NOT NULL,
    coverage_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pm_activity_events (
    account_id TEXT NOT NULL REFERENCES pm_accounts(account_id),
    event_key TEXT NOT NULL,
    event_ts INTEGER NOT NULL,
    event_ts_utc TEXT NOT NULL,
    event_date_bj TEXT NOT NULL,
    activity_type TEXT NOT NULL,
    condition_id TEXT,
    asset_id TEXT,
    side TEXT,
    price REAL,
    size REAL,
    usdc_size REAL,
    transaction_hash TEXT,
    title TEXT,
    slug TEXT,
    event_slug TEXT,
    outcome TEXT,
    raw_json TEXT NOT NULL,
    first_seen_run_id TEXT NOT NULL REFERENCES pm_sync_runs(run_id),
    first_seen_at_utc TEXT NOT NULL,
    PRIMARY KEY (account_id, event_key)
);

CREATE TABLE IF NOT EXISTS pm_position_snapshots (
    run_id TEXT NOT NULL REFERENCES pm_sync_runs(run_id),
    account_id TEXT NOT NULL REFERENCES pm_accounts(account_id),
    observed_at_utc TEXT NOT NULL,
    position_key TEXT NOT NULL,
    condition_id TEXT,
    asset_id TEXT,
    outcome TEXT,
    size REAL,
    avg_price REAL,
    initial_value REAL,
    current_value REAL,
    cash_pnl REAL,
    realized_pnl REAL,
    current_price REAL,
    total_bought REAL,
    redeemable INTEGER NOT NULL,
    end_date TEXT,
    title TEXT,
    slug TEXT,
    event_slug TEXT,
    raw_json TEXT NOT NULL,
    PRIMARY KEY (run_id, position_key)
);

CREATE TABLE IF NOT EXISTS pm_relayer_transaction_states (
    account_id TEXT NOT NULL REFERENCES pm_accounts(account_id),
    transaction_id TEXT NOT NULL,
    state TEXT NOT NULL,
    source_updated_at_utc TEXT NOT NULL,
    transaction_hash TEXT,
    signer_address TEXT,
    proxy_wallet TEXT,
    target_address TEXT,
    nonce TEXT,
    transaction_type TEXT,
    source_created_at_utc TEXT,
    raw_json TEXT NOT NULL,
    first_seen_run_id TEXT NOT NULL REFERENCES pm_sync_runs(run_id),
    first_seen_at_utc TEXT NOT NULL,
    PRIMARY KEY (account_id, transaction_id, state, source_updated_at_utc)
);

CREATE TABLE IF NOT EXISTS pm_public_trade_records (
    account_id TEXT NOT NULL REFERENCES pm_accounts(account_id),
    trade_record_key TEXT NOT NULL,
    matched_activity_event_key TEXT,
    event_ts INTEGER NOT NULL,
    transaction_hash TEXT,
    condition_id TEXT,
    asset_id TEXT,
    side TEXT,
    price REAL,
    net_fill_shares REAL,
    outcome TEXT,
    raw_json TEXT NOT NULL,
    first_seen_run_id TEXT NOT NULL REFERENCES pm_sync_runs(run_id),
    first_seen_at_utc TEXT NOT NULL,
    PRIMARY KEY (account_id, trade_record_key),
    FOREIGN KEY (account_id, matched_activity_event_key)
        REFERENCES pm_activity_events(account_id, event_key)
);

CREATE INDEX IF NOT EXISTS idx_pm_activity_account_ts
    ON pm_activity_events(account_id, event_ts);
CREATE INDEX IF NOT EXISTS idx_pm_activity_account_market
    ON pm_activity_events(account_id, condition_id, asset_id);
CREATE INDEX IF NOT EXISTS idx_pm_positions_account_observed
    ON pm_position_snapshots(account_id, observed_at_utc);
CREATE INDEX IF NOT EXISTS idx_pm_relayer_account_created
    ON pm_relayer_transaction_states(account_id, source_created_at_utc);
CREATE INDEX IF NOT EXISTS idx_pm_public_trades_account_activity
    ON pm_public_trade_records(account_id, matched_activity_event_key);
CREATE UNIQUE INDEX IF NOT EXISTS uq_pm_public_trades_activity_match
    ON pm_public_trade_records(account_id, matched_activity_event_key)
    WHERE matched_activity_event_key IS NOT NULL;

DROP VIEW IF EXISTS v_pm_public_fills;
CREATE VIEW v_pm_public_fills AS
SELECT
    a.account_id,
    a.event_key AS public_fill_key,
    t.trade_record_key,
    a.event_ts,
    a.event_ts_utc AS fill_ts_utc,
    a.event_date_bj AS fill_date_bj,
    a.transaction_hash,
    a.condition_id,
    a.asset_id,
    a.side,
    a.outcome,
    a.price AS fill_price,
    a.size AS activity_gross_shares,
    t.net_fill_shares AS trade_net_shares,
    COALESCE(t.net_fill_shares, a.size) AS fill_shares,
    a.size - t.net_fill_shares AS share_quantity_delta,
    a.price * a.size AS gross_notional_usd,
    a.usdc_size AS observed_cash_amount_usd,
    CASE
        WHEN UPPER(a.side) = 'BUY' THEN -a.usdc_size
        WHEN UPPER(a.side) = 'SELL' THEN a.usdc_size
        ELSE NULL
    END AS signed_trade_cashflow_usd,
    a.title,
    a.slug,
    a.event_slug,
    CASE
        WHEN t.trade_record_key IS NOT NULL
            THEN 'public_activity_plus_public_trade_crosscheck'
        ELSE 'public_activity_account_level'
    END AS evidence_class,
    a.first_seen_at_utc
FROM pm_activity_events AS a
LEFT JOIN pm_public_trade_records AS t
  ON t.account_id = a.account_id
 AND t.matched_activity_event_key = a.event_key
WHERE UPPER(a.activity_type) = 'TRADE';
"""


def connect_ledger(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    meta_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ledger_meta'"
    ).fetchone()
    preexisting_version = None
    if meta_exists:
        row = conn.execute(
            "SELECT value FROM ledger_meta WHERE key='schema_version'"
        ).fetchone()
        preexisting_version = str(row[0]) if row else None
    if (
        preexisting_version
        and preexisting_version != SCHEMA_VERSION
        and preexisting_version not in MIGRATABLE_SCHEMA_VERSIONS
    ):
        conn.close()
        raise RuntimeError(
            f"unsupported ledger schema {preexisting_version!r}; "
            f"no migration to {SCHEMA_VERSION!r}"
        )
    conn.executescript(SCHEMA_SQL)
    existing = conn.execute(
        "SELECT value FROM ledger_meta WHERE key='schema_version'"
    ).fetchone()
    if existing and existing[0] in MIGRATABLE_SCHEMA_VERSIONS:
        previous = str(existing[0])
        conn.execute(
            "UPDATE ledger_meta SET value=? WHERE key='schema_version'",
            (SCHEMA_VERSION,),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO ledger_migrations(
                from_version, to_version, migrated_at_utc
            ) VALUES(?, ?, ?)
            """,
            (previous, SCHEMA_VERSION, utc_iso(utc_now())),
        )
    elif existing and existing[0] != SCHEMA_VERSION:
        raise AssertionError("schema preflight accepted an unsupported version")
    else:
        conn.execute(
            "INSERT OR IGNORE INTO ledger_meta(key, value) VALUES('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
    conn.commit()
    return conn


def register_accounts(
    conn: sqlite3.Connection,
    accounts: Iterable[AccountConfig],
    *,
    observed_at: datetime,
) -> None:
    timestamp = utc_iso(observed_at)
    for account in accounts:
        existing = conn.execute(
            "SELECT proxy_wallet FROM pm_accounts WHERE account_id=?",
            (account.account_id,),
        ).fetchone()
        if existing and existing["proxy_wallet"] != account.proxy_wallet:
            raise RuntimeError(
                f"refusing proxy-wallet reassignment for {account.account_id}: "
                f"{existing['proxy_wallet']} -> {account.proxy_wallet}"
            )
        owner = conn.execute(
            "SELECT account_id FROM pm_accounts WHERE proxy_wallet=?",
            (account.proxy_wallet,),
        ).fetchone()
        if owner and owner["account_id"] != account.account_id:
            raise RuntimeError(
                f"proxy wallet {account.proxy_wallet} already belongs to {owner['account_id']}"
            )
        conn.execute(
            """
            INSERT INTO pm_accounts(
                account_id, account_label, account_kind, proxy_wallet,
                relayer_api_key_address, tags_json, created_at_utc,
                last_registered_at_utc
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
                account_label=excluded.account_label,
                account_kind=excluded.account_kind,
                relayer_api_key_address=excluded.relayer_api_key_address,
                tags_json=excluded.tags_json,
                last_registered_at_utc=excluded.last_registered_at_utc
            """,
            (
                account.account_id,
                account.account_label,
                account.account_kind,
                account.proxy_wallet,
                account.relayer_api_key_address,
                canonical_json(account.tags),
                timestamp,
                timestamp,
            ),
        )
    conn.commit()


class AccountSource(Protocol):
    def earliest_activity_timestamp(self, proxy_wallet: str) -> int | None: ...

    def activity_rows(
        self, proxy_wallet: str, start_ts: int, end_ts: int
    ) -> tuple[list[dict[str, Any]], dict[str, int]]: ...

    def current_positions(
        self, proxy_wallet: str
    ) -> tuple[list[dict[str, Any]], bool]: ...

    def public_trades(
        self, proxy_wallet: str
    ) -> tuple[list[dict[str, Any]], bool]: ...

    def relayer_transactions(
        self, api_key: str, api_key_address: str
    ) -> list[dict[str, Any]]: ...


class PolymarketAccountSource:
    def __init__(
        self,
        *,
        proxy: str | None = None,
        attempts: int = 6,
        session: requests.Session | None = None,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "pm-agents-account-ledger/1.0",
            }
        )
        self.proxies = {"http": proxy, "https": proxy} if proxy else None
        self.attempts = attempts

    def _get(self, url: str, *, params=None, headers=None) -> Any:
        last_error = ""
        for attempt in range(self.attempts):
            try:
                response = self.session.get(
                    url,
                    params=params,
                    headers=headers,
                    proxies=self.proxies,
                    timeout=(10, 45),
                )
                if response.status_code == 200:
                    return response.json()
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                if response.status_code not in TRANSIENT_HTTP_STATUS:
                    response.raise_for_status()
            except (requests.RequestException, ValueError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            if attempt + 1 < self.attempts:
                time.sleep(min(0.5 * (attempt + 1), 4.0))
        raise RuntimeError(f"GET exhausted retries: {url}: {last_error}")

    def _data_list(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        payload = self._get(f"{DATA_API}{path}", params=params)
        if not isinstance(payload, list):
            raise RuntimeError(f"expected list from Data API {path}")
        return [row for row in payload if isinstance(row, dict)]

    def earliest_activity_timestamp(self, proxy_wallet: str) -> int | None:
        rows = self._data_list(
            "/activity",
            {
                "user": proxy_wallet,
                "limit": 1,
                "offset": 0,
                "sortDirection": "ASC",
            },
        )
        if not rows:
            return None
        timestamp = int(rows[0].get("timestamp") or 0)
        return timestamp if timestamp > 0 else None

    def activity_rows(
        self,
        proxy_wallet: str,
        start_ts: int,
        end_ts: int,
        *,
        depth: int = 0,
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        rows: list[dict[str, Any]] = []
        pages = 0
        for offset in range(0, ACTIVITY_WINDOW_CAP, ACTIVITY_PAGE_SIZE):
            page = self._data_list(
                "/activity",
                {
                    "user": proxy_wallet,
                    "start": start_ts,
                    "end": end_ts,
                    "limit": ACTIVITY_PAGE_SIZE,
                    "offset": offset,
                    "sortDirection": "ASC",
                },
            )
            pages += 1
            rows.extend(page)
            if len(page) < ACTIVITY_PAGE_SIZE:
                break
        if len(rows) < ACTIVITY_WINDOW_CAP:
            return rows, {
                "requests": pages,
                "split_nodes": 0,
                "leaf_windows": 1,
                "max_split_depth": depth,
            }
        if end_ts - start_ts <= 1:
            raise RuntimeError(
                f"activity exceeds {ACTIVITY_WINDOW_CAP} rows inside one second"
            )
        midpoint = (start_ts + end_ts) // 2
        left, left_stats = self.activity_rows(
            proxy_wallet, start_ts, midpoint, depth=depth + 1
        )
        right, right_stats = self.activity_rows(
            proxy_wallet, midpoint, end_ts, depth=depth + 1
        )
        return left + right, {
            "requests": pages + left_stats["requests"] + right_stats["requests"],
            "split_nodes": 1 + left_stats["split_nodes"] + right_stats["split_nodes"],
            "leaf_windows": left_stats["leaf_windows"] + right_stats["leaf_windows"],
            "max_split_depth": max(
                depth,
                left_stats["max_split_depth"],
                right_stats["max_split_depth"],
            ),
        }

    def current_positions(
        self, proxy_wallet: str
    ) -> tuple[list[dict[str, Any]], bool]:
        rows: list[dict[str, Any]] = []
        limit = 500
        truncated = False
        for offset in range(0, 10_001, limit):
            page = self._data_list(
                "/positions",
                {
                    "user": proxy_wallet,
                    "sizeThreshold": 0,
                    "limit": limit,
                    "offset": offset,
                    "sortBy": "TOKENS",
                    "sortDirection": "DESC",
                },
            )
            rows.extend(page)
            if len(page) < limit:
                break
            if offset == 10_000:
                truncated = True
        return rows, truncated

    def public_trades(
        self, proxy_wallet: str
    ) -> tuple[list[dict[str, Any]], bool]:
        """Fetch the public trade endpoint as an independent activity coverage check."""
        rows: list[dict[str, Any]] = []
        limit = 1_000
        truncated = False
        for offset in range(0, 10_001, limit):
            page = self._data_list(
                "/trades",
                {
                    "user": proxy_wallet,
                    "takerOnly": "false",
                    "limit": limit,
                    "offset": offset,
                },
            )
            rows.extend(page)
            if len(page) < limit:
                break
            if offset == 10_000:
                truncated = True
        return rows, truncated

    def relayer_transactions(
        self, api_key: str, api_key_address: str
    ) -> list[dict[str, Any]]:
        payload = self._get(
            f"{RELAYER_API}/transactions",
            headers={
                "RELAYER_API_KEY": api_key,
                "RELAYER_API_KEY_ADDRESS": api_key_address,
            },
        )
        if not isinstance(payload, list):
            raise RuntimeError("expected list from Relayer /transactions")
        return [row for row in payload if isinstance(row, dict)]


def _validate_source_wallet(
    rows: Iterable[Mapping[str, Any]],
    *,
    configured_wallet: str,
    field_names: tuple[str, ...],
    source: str,
) -> None:
    mismatches: set[str] = set()
    missing = 0
    for row in rows:
        found = False
        for field in field_names:
            raw = row.get(field)
            if raw:
                found = True
                observed = normalize_address(str(raw))
                if observed != configured_wallet:
                    mismatches.add(str(observed))
                break
        if not found:
            missing += 1
    if missing:
        raise RuntimeError(
            f"{source} returned {missing} row(s) without required account identity"
        )
    if mismatches:
        raise RuntimeError(
            f"{source} returned proxy wallet(s) outside configured account: {sorted(mismatches)}"
        )


def _dedupe_activity(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        out.setdefault(activity_event_key(row), row)
    return sorted(
        out.values(),
        key=lambda row: (
            int(row.get("timestamp") or 0),
            str(row.get("transactionHash") or ""),
            activity_event_key(row),
        ),
    )


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _insert_activity(
    conn: sqlite3.Connection,
    account: AccountConfig,
    run_id: str,
    observed_at: datetime,
    rows: Iterable[dict[str, Any]],
) -> int:
    before = conn.total_changes
    first_seen = utc_iso(observed_at)
    for row in rows:
        ts = int(row.get("timestamp") or 0)
        if ts <= 0:
            raise RuntimeError("public activity row has no positive timestamp")
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        conn.execute(
            """
            INSERT OR IGNORE INTO pm_activity_events(
                account_id, event_key, event_ts, event_ts_utc, event_date_bj,
                activity_type, condition_id, asset_id, side, price, size,
                usdc_size, transaction_hash, title, slug, event_slug, outcome,
                raw_json, first_seen_run_id, first_seen_at_utc
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account.account_id,
                activity_event_key(row),
                ts,
                utc_iso(dt),
                dt.astimezone(BJ).date().isoformat(),
                str(row.get("type") or "UNKNOWN").upper(),
                row.get("conditionId"),
                row.get("asset"),
                str(row.get("side") or "").upper() or None,
                _number(row.get("price")),
                _number(row.get("size")),
                _number(row.get("usdcSize")),
                row.get("transactionHash"),
                row.get("title"),
                row.get("slug"),
                row.get("eventSlug"),
                row.get("outcome"),
                canonical_json(row),
                run_id,
                first_seen,
            ),
        )
    return conn.total_changes - before


def _insert_positions(
    conn: sqlite3.Connection,
    account: AccountConfig,
    run_id: str,
    observed_at: datetime,
    rows: Iterable[dict[str, Any]],
) -> int:
    before = conn.total_changes
    observed = utc_iso(observed_at)
    for row in rows:
        conn.execute(
            """
            INSERT OR IGNORE INTO pm_position_snapshots(
                run_id, account_id, observed_at_utc, position_key, condition_id,
                asset_id, outcome, size, avg_price, initial_value, current_value,
                cash_pnl, realized_pnl, current_price, total_bought, redeemable,
                end_date, title, slug, event_slug, raw_json
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                account.account_id,
                observed,
                position_key(row),
                row.get("conditionId"),
                row.get("asset"),
                row.get("outcome"),
                _number(row.get("size")),
                _number(row.get("avgPrice")),
                _number(row.get("initialValue")),
                _number(row.get("currentValue")),
                _number(row.get("cashPnl")),
                _number(row.get("realizedPnl")),
                _number(row.get("curPrice")),
                _number(row.get("totalBought")),
                int(bool(row.get("redeemable"))),
                row.get("endDate"),
                row.get("title"),
                row.get("slug"),
                row.get("eventSlug"),
                canonical_json(row),
            ),
        )
    return conn.total_changes - before


def _insert_relayer_states(
    conn: sqlite3.Connection,
    account: AccountConfig,
    run_id: str,
    observed_at: datetime,
    rows: Iterable[dict[str, Any]],
) -> int:
    before = conn.total_changes
    first_seen = utc_iso(observed_at)
    for row in rows:
        transaction_id = str(row.get("transactionID") or "").strip()
        if not transaction_id:
            raise RuntimeError("Relayer transaction has no transactionID")
        state = str(row.get("state") or "UNKNOWN").strip().upper()
        source_updated = str(row.get("updatedAt") or row.get("createdAt") or first_seen)
        conn.execute(
            """
            INSERT OR IGNORE INTO pm_relayer_transaction_states(
                account_id, transaction_id, state, source_updated_at_utc,
                transaction_hash, signer_address, proxy_wallet, target_address,
                nonce, transaction_type, source_created_at_utc, raw_json,
                first_seen_run_id, first_seen_at_utc
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account.account_id,
                transaction_id,
                state,
                source_updated,
                row.get("transactionHash"),
                normalize_address(row.get("from"), required=False),
                normalize_address(row.get("proxyAddress"), required=False),
                normalize_address(row.get("to"), required=False),
                str(row.get("nonce") or "") or None,
                str(row.get("type") or "") or None,
                row.get("createdAt"),
                canonical_json(row),
                run_id,
                first_seen,
            ),
        )
    return conn.total_changes - before


def _insert_public_trades(
    conn: sqlite3.Connection,
    account: AccountConfig,
    run_id: str,
    observed_at: datetime,
    rows: Iterable[dict[str, Any]],
    activity_event_by_trade_record_key: Mapping[str, str],
) -> int:
    before = conn.total_changes
    first_seen = utc_iso(observed_at)
    for row in rows:
        ts = int(row.get("timestamp") or 0)
        if ts <= 0:
            raise RuntimeError("public trade row has no positive timestamp")
        conn.execute(
            """
            INSERT OR IGNORE INTO pm_public_trade_records(
                account_id, trade_record_key, matched_activity_event_key,
                event_ts, transaction_hash, condition_id, asset_id, side,
                price, net_fill_shares, outcome, raw_json, first_seen_run_id,
                first_seen_at_utc
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account.account_id,
                public_trade_record_key(row),
                activity_event_by_trade_record_key.get(public_trade_record_key(row)),
                ts,
                row.get("transactionHash"),
                row.get("conditionId"),
                row.get("asset"),
                str(row.get("side") or "").upper() or None,
                _number(row.get("price")),
                _number(row.get("size")),
                row.get("outcome"),
                canonical_json(row),
                run_id,
                first_seen,
            ),
        )
    return conn.total_changes - before


def sync_account(
    *,
    conn: sqlite3.Connection,
    account: AccountConfig,
    source: AccountSource,
    relayer_key_loader: Callable[[str, str], str] | None = None,
    full_backfill: bool = False,
    requested_start_ts: int | None = None,
    observed_at: datetime | None = None,
    incremental_overlap_sec: int = 86_400,
) -> dict[str, Any]:
    started_at = observed_at or utc_now()
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    started_at = started_at.astimezone(timezone.utc)
    register_accounts(conn, [account], observed_at=started_at)

    end_ts = int(started_at.timestamp()) + 1
    existing = conn.execute(
        "SELECT MAX(event_ts) AS max_ts FROM pm_activity_events WHERE account_id=?",
        (account.account_id,),
    ).fetchone()
    max_existing_ts = int(existing["max_ts"]) if existing and existing["max_ts"] is not None else None
    if requested_start_ts is not None:
        start_ts = int(requested_start_ts)
    elif full_backfill or max_existing_ts is None:
        start_ts = source.earliest_activity_timestamp(account.proxy_wallet)
    else:
        start_ts = max(0, max_existing_ts - max(0, incremental_overlap_sec))

    if start_ts is None or start_ts >= end_ts:
        activity: list[dict[str, Any]] = []
        activity_stats = {"requests": 0, "split_nodes": 0, "leaf_windows": 0, "max_split_depth": 0}
    else:
        activity, activity_stats = source.activity_rows(
            account.proxy_wallet, start_ts, end_ts
        )
        activity = _dedupe_activity(activity)
    _validate_source_wallet(
        activity,
        configured_wallet=account.proxy_wallet,
        field_names=("proxyWallet",),
        source="Data API activity",
    )

    positions, positions_truncated = source.current_positions(account.proxy_wallet)
    _validate_source_wallet(
        positions,
        configured_wallet=account.proxy_wallet,
        field_names=("proxyWallet",),
        source="Data API positions",
    )
    if positions_truncated:
        raise RuntimeError(
            "Data API positions reached the offset cap; refusing an incomplete snapshot"
        )

    public_trades_raw, public_trades_truncated = source.public_trades(
        account.proxy_wallet
    )
    _validate_source_wallet(
        public_trades_raw,
        configured_wallet=account.proxy_wallet,
        field_names=("proxyWallet",),
        source="Data API trades",
    )
    public_trades_by_record_key = {
        public_trade_record_key(row): row
        for row in public_trades_raw
        if int(row.get("timestamp") or 0) <= end_ts
    }
    public_trade_exact_duplicates = len(public_trades_raw) - len(
        public_trades_by_record_key
    )
    existing_activity_trades = [
        json.loads(row["raw_json"])
        for row in conn.execute(
            """
            SELECT raw_json FROM pm_activity_events
            WHERE account_id=? AND UPPER(activity_type)='TRADE'
            """,
            (account.account_id,),
        ).fetchall()
    ]
    combined_activity_trades = _dedupe_activity(
        [
        row
        for row in existing_activity_trades + activity
        if str(row.get("type") or "").upper() == "TRADE"
        and int(row.get("timestamp") or 0) <= end_ts
        ]
    )
    activity_groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in combined_activity_trades:
        activity_groups.setdefault(public_trade_join_key(row), []).append(row)
    public_trade_groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in public_trades_by_record_key.values():
        public_trade_groups.setdefault(public_trade_join_key(row), []).append(row)
    activity_trade_keys = set(activity_groups)
    public_trade_keys = set(public_trade_groups)
    trades_only = public_trade_keys - activity_trade_keys
    activity_only = activity_trade_keys - public_trade_keys
    group_count_mismatches = {
        key
        for key in activity_trade_keys & public_trade_keys
        if len(activity_groups[key]) != len(public_trade_groups[key])
    }
    if not public_trades_truncated and (
        trades_only
        or activity_only
        or group_count_mismatches
    ):
        raise RuntimeError(
            "public trade coverage mismatch: "
            f"trades_only={len(trades_only)} activity_only={len(activity_only)} "
            f"group_count_mismatches={len(group_count_mismatches)}"
        )
    public_trades_for_storage = (
        public_trades_by_record_key if not public_trades_truncated else {}
    )
    activity_event_by_trade_record_key: dict[str, str] = {}
    size_mismatches = 0
    for key in sorted(activity_trade_keys & public_trade_keys, key=str):
        activity_group = sorted(
            activity_groups[key],
            key=lambda row: (float(row.get("size") or 0), activity_event_key(row)),
        )
        trade_group = sorted(
            public_trade_groups[key],
            key=lambda row: (float(row.get("size") or 0), public_trade_record_key(row)),
        )
        if len(activity_group) != len(trade_group):
            continue
        for activity_row, trade_row in zip(activity_group, trade_group):
            activity_event_by_trade_record_key[public_trade_record_key(trade_row)] = (
                activity_event_key(activity_row)
            )
            if abs(
                float(activity_row.get("size") or 0)
                - float(trade_row.get("size") or 0)
            ) > 1e-8:
                size_mismatches += 1

    relayer: list[dict[str, Any]] = []
    if account.sync_relayer:
        if relayer_key_loader is None:
            raise RuntimeError("Relayer sync enabled without a key loader")
        assert account.keychain_account is not None
        assert account.relayer_api_key_service is not None
        assert account.relayer_api_key_address is not None
        key = relayer_key_loader(
            account.keychain_account, account.relayer_api_key_service
        )
        relayer = source.relayer_transactions(key, account.relayer_api_key_address)
        _validate_source_wallet(
            relayer,
            configured_wallet=account.proxy_wallet,
            field_names=("proxyAddress",),
            source="Relayer transactions",
        )
        signer_mismatches = {
            str(normalize_address(row.get("from"), required=False))
            for row in relayer
            if row.get("from")
            and normalize_address(row.get("from"), required=False)
            != account.relayer_api_key_address
        }
        if signer_mismatches:
            raise RuntimeError(
                f"Relayer signer mismatch for {account.account_id}: {sorted(signer_mismatches)}"
            )

    run_id = f"{account.account_id}-{started_at.strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:10]}"
    completed_at = utc_now()
    coverage = {
        "public_activity": {
            "requested_start_ts": start_ts,
            "requested_end_ts": end_ts,
            "earliest_fetched_ts": min((int(row.get("timestamp") or 0) for row in activity), default=None),
            "latest_fetched_ts": max((int(row.get("timestamp") or 0) for row in activity), default=None),
            "api_stats": activity_stats,
            "evidence_class": "public_activity_account_level",
            "cannot_recover": [
                "private_signals",
                "unfilled_or_cancelled_orders",
                "original_order_post_timestamp",
            ],
        },
        "positions": {"snapshot_at_utc": utc_iso(started_at)},
        "public_trade_crosscheck": {
            "raw_rows": len(public_trades_raw),
            "unique_rows": len(public_trades_by_record_key),
            "exact_duplicate_rows_collapsed": public_trade_exact_duplicates,
            "exact_duplicate_identity_note": (
                "The public API exposes no fill/order ID; byte-equivalent trade "
                "identities are indistinguishable and are collapsed."
            ),
            "activity_unique_trade_rows": len(combined_activity_trades),
            "activity_join_groups": len(activity_trade_keys),
            "public_trade_join_groups": len(public_trade_keys),
            "trades_only": len(trades_only),
            "activity_only": len(activity_only),
            "multi_fill_join_groups": sum(
                len(rows) > 1 for rows in activity_groups.values()
            ),
            "group_count_mismatches": len(group_count_mismatches),
            "share_quantity_mismatches": size_mismatches,
            "endpoint_truncated_at_offset_cap": public_trades_truncated,
            "trade_enrichment_written": not public_trades_truncated,
            "gate_pass": not public_trades_truncated
            and not trades_only
            and not activity_only
            and not group_count_mismatches,
        },
        "relayer": {
            "enabled": account.sync_relayer,
            "endpoint_scope": "most_recent_transactions_returned_by_relayer",
        },
    }

    with conn:
        conn.execute(
            """
            INSERT INTO pm_sync_runs(
                run_id, account_id, started_at_utc, completed_at_utc,
                requested_start_ts, requested_end_ts, full_backfill,
                public_activity_rows_fetched, public_activity_rows_inserted,
                position_rows_fetched, position_rows_inserted,
                relayer_rows_fetched, relayer_states_inserted, coverage_json
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 0, ?, 0, ?)
            """,
            (
                run_id,
                account.account_id,
                utc_iso(started_at),
                utc_iso(completed_at),
                start_ts,
                end_ts,
                int(full_backfill),
                len(activity),
                len(positions),
                len(relayer),
                canonical_json(coverage),
            ),
        )
        activity_inserted = _insert_activity(
            conn, account, run_id, started_at, activity
        )
        public_trades_inserted = _insert_public_trades(
            conn,
            account,
            run_id,
            started_at,
            public_trades_for_storage.values(),
            activity_event_by_trade_record_key,
        )
        positions_inserted = _insert_positions(
            conn, account, run_id, started_at, positions
        )
        relayer_inserted = _insert_relayer_states(
            conn, account, run_id, started_at, relayer
        )
        conn.execute(
            """
            UPDATE pm_sync_runs SET
                public_activity_rows_inserted=?,
                position_rows_inserted=?,
                relayer_states_inserted=?
            WHERE run_id=?
            """,
            (activity_inserted, positions_inserted, relayer_inserted, run_id),
        )

    totals = conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM pm_activity_events WHERE account_id=?) AS activity_rows,
            (SELECT COUNT(*) FROM v_pm_public_fills WHERE account_id=?) AS public_fills,
            (SELECT COUNT(DISTINCT transaction_hash) FROM v_pm_public_fills WHERE account_id=?) AS fill_transactions,
            (SELECT COUNT(*) FROM pm_relayer_transaction_states WHERE account_id=?) AS relayer_states,
            (SELECT COUNT(*) FROM pm_position_snapshots WHERE account_id=? AND run_id=?) AS current_positions
        """,
        (
            account.account_id,
            account.account_id,
            account.account_id,
            account.account_id,
            account.account_id,
            run_id,
        ),
    ).fetchone()
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "account": {
            "account_id": account.account_id,
            "account_label": account.account_label,
            "account_kind": account.account_kind,
            "proxy_wallet": account.proxy_wallet,
            "tags": list(account.tags),
        },
        "sync": {
            "full_backfill": full_backfill,
            "requested_start_ts": start_ts,
            "requested_end_ts": end_ts,
            "activity_fetched": len(activity),
            "activity_inserted": activity_inserted,
            "positions_fetched": len(positions),
            "position_snapshots_inserted": positions_inserted,
            "public_trade_records_fetched": len(public_trades_raw),
            "public_trade_records_unique": len(public_trades_by_record_key),
            "public_trade_records_inserted": public_trades_inserted,
            "relayer_fetched": len(relayer),
            "relayer_states_inserted": relayer_inserted,
            "api_stats": activity_stats,
            "public_trade_crosscheck": coverage["public_trade_crosscheck"],
        },
        "account_totals": dict(totals),
        "coverage_gaps": coverage["public_activity"]["cannot_recover"],
        "observed_at_utc": utc_iso(started_at),
    }
