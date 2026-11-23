"""SQLite storage layer for instruments, constituent snapshots, raw series, indicator values, and job runs."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from app.config import (
    DB_PATH,
    DEFAULT_EQUITIES,
    DEFAULT_INSTRUMENT_NAME,
    DEFAULT_INSTRUMENT_SYMBOL,
    INSTRUMENTS,
    SOURCE_PRIORITY_PROFILES,
    resolve_default_source_priority,
)
from app.models import ConstituentQuote, EquitySnapshot, IndicatorPoint


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def canonical_params(params: dict[str, Any] | None) -> str:
    if not params:
        return "{}"
    return json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS instruments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'index',
    en_name TEXT NOT NULL DEFAULT '',
    sector TEXT NOT NULL DEFAULT '',
    group_name TEXT NOT NULL DEFAULT '',
    currency TEXT NOT NULL DEFAULT 'USD',
    etf_ticker TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS constituent_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    instrument_id INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    weight REAL NOT NULL,
    price REAL,
    eps_ttm REAL,
    eps_fwd REAL,
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    FOREIGN KEY (instrument_id) REFERENCES instruments(id),
    UNIQUE (date, instrument_id, ticker, source)
);
CREATE INDEX IF NOT EXISTS idx_constituent_inst_date
    ON constituent_snapshots (instrument_id, date);

CREATE TABLE IF NOT EXISTS equity_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    instrument_id INTEGER NOT NULL,
    price REAL,
    eps_ttm REAL,
    eps_fwd REAL,
    market_cap REAL,
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    FOREIGN KEY (instrument_id) REFERENCES instruments(id),
    UNIQUE (date, instrument_id, source)
);
CREATE INDEX IF NOT EXISTS idx_equity_snapshots_inst_date
    ON equity_snapshots (instrument_id, date);

CREATE TABLE IF NOT EXISTS raw_series (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument_id INTEGER NOT NULL,
    series_id TEXT NOT NULL,
    date TEXT NOT NULL,
    value REAL NOT NULL,
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    FOREIGN KEY (instrument_id) REFERENCES instruments(id),
    UNIQUE (instrument_id, series_id, date, source)
);
CREATE INDEX IF NOT EXISTS idx_raw_series_lookup
    ON raw_series (instrument_id, series_id, date);

CREATE TABLE IF NOT EXISTS indicator_values (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    indicator_id TEXT NOT NULL,
    instrument_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    value REAL,
    params TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL DEFAULT 'computed',
    is_incomplete INTEGER NOT NULL DEFAULT 0,
    meta TEXT NOT NULL DEFAULT '{}',
    computed_at TEXT NOT NULL,
    FOREIGN KEY (instrument_id) REFERENCES instruments(id),
    UNIQUE (indicator_id, instrument_id, date, params)
);
CREATE INDEX IF NOT EXISTS idx_indicator_values_lookup
    ON indicator_values (instrument_id, indicator_id, params, date);

CREATE TABLE IF NOT EXISTS job_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    error TEXT,
    details TEXT
);
CREATE INDEX IF NOT EXISTS idx_job_runs_job_started
    ON job_runs (job, started_at DESC);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

_INSTRUMENT_EXTRA_COLUMNS: list[tuple[str, str]] = [
    ("kind", "TEXT NOT NULL DEFAULT 'index'"),
    ("en_name", "TEXT NOT NULL DEFAULT ''"),
    ("sector", "TEXT NOT NULL DEFAULT ''"),
    ("group_name", "TEXT NOT NULL DEFAULT ''"),
    ("currency", "TEXT NOT NULL DEFAULT 'USD'"),
    ("etf_ticker", "TEXT NOT NULL DEFAULT ''"),
    ("description", "TEXT NOT NULL DEFAULT ''"),
    ("enabled", "INTEGER NOT NULL DEFAULT 1"),
    ("created_at", "TEXT NOT NULL DEFAULT ''"),
]

_INSTRUMENT_SELECT_COLS = (
    "id, symbol, name, kind, en_name, sector, group_name, currency, etf_ticker, description, enabled, created_at"
)

_INSTRUMENT_EXCLUSIVE_JOBS: dict[str, set[str]] = {
    "qqq_holdings": {"NDX"},
    "spy_holdings": {"SPY"},
    "siblis_free": {"NDX"},
    "siblis_paid": {"NDX"},
    "multpl": {"SPY"},
    "shiller": {"SPY"},
}

_INDEX_ONLY_JOBS: set[str] = set(_INSTRUMENT_EXCLUSIVE_JOBS.keys())
_EQUITY_ONLY_JOBS: set[str] = {
    "sec_edgar",
    "yfinance_history",
    "refresh_equities",
    "backfill_equities",
}


class ValuationStore:
    """Thin SQLite repository encapsulating instruments, snapshots, raw series, indicators, and job runs."""

    def __init__(self, db_path: str | Path = DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_db(self) -> None:
        ts = utc_now_iso()
        with self.connect() as conn:
            conn.executescript(SCHEMA_SQL)

            existing_cols = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(instruments)").fetchall()
            }
            for col_name, col_def in _INSTRUMENT_EXTRA_COLUMNS:
                if col_name not in existing_cols:
                    conn.execute(
                        f"ALTER TABLE instruments ADD COLUMN {col_name} {col_def}"
                    )

            for meta in INSTRUMENTS.values():
                conn.execute(
                    """
                    INSERT INTO instruments
                        (symbol, name, kind, en_name, currency, etf_ticker, description, enabled, created_at)
                    VALUES (?, ?, 'index', ?, ?, ?, ?, 1, ?)
                    ON CONFLICT(symbol) DO UPDATE SET
                        kind = 'index',
                        en_name = CASE WHEN instruments.en_name = '' THEN excluded.en_name ELSE instruments.en_name END,
                        etf_ticker = CASE WHEN instruments.etf_ticker = '' THEN excluded.etf_ticker ELSE instruments.etf_ticker END,
                        description = CASE WHEN instruments.description = '' THEN excluded.description ELSE instruments.description END,
                        created_at = CASE WHEN instruments.created_at = '' THEN excluded.created_at ELSE instruments.created_at END
                    """,
                    (
                        meta["symbol"],
                        meta["name"],
                        meta.get("en_name", ""),
                        meta.get("currency", "USD"),
                        meta.get("etf_ticker", ""),
                        meta.get("description", ""),
                        ts,
                    ),
                )

            seeded_row = conn.execute(
                "SELECT value FROM app_settings WHERE key = 'default_equities_seeded'"
            ).fetchone()
            if not seeded_row or str(seeded_row["value"]) != "1":
                eq_count = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM instruments WHERE kind = 'equity'"
                ).fetchone()["cnt"]
                if int(eq_count or 0) == 0:
                    for eq in DEFAULT_EQUITIES:
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO instruments
                                (symbol, name, kind, en_name, sector, group_name, currency, etf_ticker, description, enabled, created_at)
                            VALUES (?, ?, 'equity', ?, ?, ?, ?, '', ?, 1, ?)
                            """,
                            (
                                eq["symbol"],
                                eq["name"],
                                eq.get("en_name", ""),
                                eq.get("sector", ""),
                                eq.get("group_name", ""),
                                eq.get("currency", "USD"),
                                eq.get("description", ""),
                                ts,
                            ),
                        )
                conn.execute(
                    """
                    INSERT INTO app_settings (key, value, updated_at)
                    VALUES ('default_equities_seeded', '1', ?)
                    ON CONFLICT(key) DO UPDATE SET value = '1', updated_at = excluded.updated_at
                    """,
                    (ts,),
                )

    # ------------------------------------------------------------------
    # instruments
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_instrument_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["enabled"] = bool(item.get("enabled", 1))
        return item

    def list_instruments(
        self,
        kind: str | None = None,
        enabled_only: bool = True,
    ) -> list[dict[str, Any]]:
        query = f"SELECT {_INSTRUMENT_SELECT_COLS} FROM instruments WHERE 1=1"
        args: list[Any] = []
        if kind is not None:
            query += " AND kind = ?"
            args.append(kind)
        if enabled_only:
            query += " AND enabled = 1"
        query += " ORDER BY CASE WHEN kind = 'index' THEN 0 ELSE 1 END ASC, id ASC"
        with self.connect() as conn:
            rows = conn.execute(query, args).fetchall()
            return [self._normalize_instrument_row(r) for r in rows]

    def get_or_create_instrument(
        self,
        symbol: str,
        name: str | None = None,
        *,
        kind: str | None = None,
        en_name: str | None = None,
        sector: str | None = None,
        group_name: str | None = None,
        currency: str | None = None,
        etf_ticker: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        symbol_upper = symbol.strip().upper()
        with self.connect() as conn:
            row = conn.execute(
                f"SELECT {_INSTRUMENT_SELECT_COLS} FROM instruments WHERE symbol = ?",
                (symbol_upper,),
            ).fetchone()
            if row is not None:
                return self._normalize_instrument_row(row)

            default_meta = INSTRUMENTS.get(symbol_upper)
            eff_kind = kind or ("index" if default_meta else "equity")
            display_name = name or (default_meta["name"] if default_meta else symbol_upper)
            eff_en_name = en_name if en_name is not None else (default_meta.get("en_name", "") if default_meta else "")
            eff_sector = sector or ""
            eff_group = group_name or ""
            eff_currency = currency or (default_meta.get("currency", "USD") if default_meta else "USD")
            eff_etf = etf_ticker if etf_ticker is not None else (default_meta.get("etf_ticker", "") if default_meta else "")
            eff_desc = description if description is not None else (
                default_meta.get("description", "")
                if default_meta
                else f"日频追踪 {display_name} ({symbol_upper}) 滚动 PE、前瞻 PE 及历史估值分位"
            )
            ts = utc_now_iso()
            cur = conn.execute(
                """
                INSERT INTO instruments
                    (symbol, name, kind, en_name, sector, group_name, currency, etf_ticker, description, enabled, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    symbol_upper,
                    display_name,
                    eff_kind,
                    eff_en_name,
                    eff_sector,
                    eff_group,
                    eff_currency,
                    eff_etf,
                    eff_desc,
                    ts,
                ),
            )
            created = conn.execute(
                f"SELECT {_INSTRUMENT_SELECT_COLS} FROM instruments WHERE id = ?",
                (cur.lastrowid,),
            ).fetchone()
            return self._normalize_instrument_row(created)  # type: ignore[arg-type]

    def upsert_instrument(
        self,
        symbol: str,
        *,
        name: str | None = None,
        kind: str = "equity",
        en_name: str | None = None,
        sector: str | None = None,
        group_name: str | None = None,
        currency: str | None = None,
        etf_ticker: str | None = None,
        description: str | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        symbol_upper = symbol.strip().upper()
        existing = self.get_instrument(symbol_upper)
        if existing is None:
            return self.get_or_create_instrument(
                symbol_upper,
                name=name,
                kind=kind,
                en_name=en_name,
                sector=sector,
                group_name=group_name,
                currency=currency,
                etf_ticker=etf_ticker,
                description=description,
            )
        updated = self.update_instrument(
            symbol_upper,
            name=name,
            en_name=en_name,
            sector=sector,
            group_name=group_name,
            currency=currency,
            etf_ticker=etf_ticker,
            description=description,
            enabled=enabled,
        )
        return updated or existing

    def update_instrument(
        self,
        symbol: str,
        *,
        name: str | None = None,
        en_name: str | None = None,
        sector: str | None = None,
        group_name: str | None = None,
        currency: str | None = None,
        etf_ticker: str | None = None,
        description: str | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any] | None:
        symbol_upper = symbol.strip().upper()
        updates: list[str] = []
        args: list[Any] = []
        field_map: list[tuple[str, Any]] = [
            ("name", name),
            ("en_name", en_name),
            ("sector", sector),
            ("group_name", group_name),
            ("currency", currency),
            ("etf_ticker", etf_ticker),
            ("description", description),
            ("enabled", (1 if enabled else 0) if enabled is not None else None),
        ]
        for col, val in field_map:
            if val is not None:
                updates.append(f"{col} = ?")
                args.append(val)
        if not updates:
            return self.get_instrument(symbol_upper)

        args.append(symbol_upper)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE instruments SET {', '.join(updates)} WHERE symbol = ?",
                args,
            )
            row = conn.execute(
                f"SELECT {_INSTRUMENT_SELECT_COLS} FROM instruments WHERE symbol = ?",
                (symbol_upper,),
            ).fetchone()
            return self._normalize_instrument_row(row) if row else None

    def delete_instrument(self, symbol: str) -> bool:
        symbol_upper = symbol.strip().upper()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id, kind FROM instruments WHERE symbol = ?",
                (symbol_upper,),
            ).fetchone()
            if row is None:
                return False
            if row["kind"] == "index":
                raise ValueError(f"Cannot delete index instrument {symbol_upper!r}")
            inst_id = int(row["id"])
            conn.execute("DELETE FROM equity_snapshots WHERE instrument_id = ?", (inst_id,))
            conn.execute("DELETE FROM constituent_snapshots WHERE instrument_id = ?", (inst_id,))
            conn.execute("DELETE FROM raw_series WHERE instrument_id = ?", (inst_id,))
            conn.execute("DELETE FROM indicator_values WHERE instrument_id = ?", (inst_id,))
            conn.execute("DELETE FROM instruments WHERE id = ?", (inst_id,))
            return True

    def get_instrument(self, symbol: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                f"SELECT {_INSTRUMENT_SELECT_COLS} FROM instruments WHERE symbol = ?",
                (symbol.strip().upper(),),
            ).fetchone()
            return self._normalize_instrument_row(row) if row else None

    # ------------------------------------------------------------------
    # app_settings & source priority
    # ------------------------------------------------------------------
    def get_setting(self, key: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = ?",
                (key,),
            ).fetchone()
            return str(row["value"]) if row else None

    def set_setting(self, key: str, value: str) -> None:
        ts = utc_now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, ts),
            )

    def get_source_priority(self) -> tuple[str, list[str]]:
        """Return `(profile_id, ordered_source_ids)` from `app_settings` or default config."""
        profile = self.get_setting("source_priority_profile")
        raw_list = self.get_setting("source_priority_list")
        if raw_list:
            try:
                parsed = json.loads(raw_list)
                if isinstance(parsed, list):
                    cleaned = [str(item).strip() for item in parsed if str(item).strip()]
                    if cleaned:
                        return (profile or "custom"), cleaned
            except json.JSONDecodeError:
                pass
        if profile and profile in SOURCE_PRIORITY_PROFILES:
            return profile, list(SOURCE_PRIORITY_PROFILES[profile]["priority"])
        return resolve_default_source_priority()

    def set_source_priority(
        self,
        priority: list[str] | None = None,
        profile: str | None = None,
    ) -> tuple[str, list[str]]:
        """Persist source priority preset or custom order into `app_settings`."""
        if priority is not None:
            cleaned = list(
                dict.fromkeys(str(item).strip() for item in priority if str(item).strip())
            )
            if not cleaned:
                raise ValueError("Source priority list must not be empty")
            # Preserve any known sources omitted when reordering from a kind-filtered UI view
            _, current_order = self.get_source_priority()
            for sid in current_order:
                if sid not in cleaned:
                    cleaned.append(sid)
            matched_profile = next(
                (
                    pid
                    for pid, meta in SOURCE_PRIORITY_PROFILES.items()
                    if list(meta["priority"]) == cleaned
                ),
                "custom",
            )
            effective_profile = matched_profile
            effective_priority = cleaned
        elif profile is not None:
            profile_key = profile.strip().lower()
            if profile_key not in SOURCE_PRIORITY_PROFILES:
                raise ValueError(f"Unknown source priority profile: {profile!r}")
            effective_profile = profile_key
            effective_priority = list(SOURCE_PRIORITY_PROFILES[profile_key]["priority"])
        else:
            raise ValueError("Either profile or priority must be provided")

        ts = utc_now_iso()
        serialized = json.dumps(effective_priority, ensure_ascii=False)
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                [
                    ("source_priority_profile", effective_profile, ts),
                    ("source_priority_list", serialized, ts),
                ],
            )
        return effective_profile, effective_priority

    # ------------------------------------------------------------------
    # constituent_snapshots
    # ------------------------------------------------------------------
    def save_constituent_snapshots(
        self,
        instrument_id: int,
        date: str,
        quotes: list[ConstituentQuote],
        fetched_at: str | None = None,
    ) -> int:
        if not quotes:
            return 0
        ts = fetched_at or utc_now_iso()
        rows = [
            (
                date,
                instrument_id,
                q.ticker.strip().upper(),
                float(q.weight),
                float(q.price) if q.price is not None else None,
                float(q.eps_ttm) if q.eps_ttm is not None else None,
                float(q.eps_fwd) if q.eps_fwd is not None else None,
                q.source,
                ts,
            )
            for q in quotes
        ]
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO constituent_snapshots
                    (date, instrument_id, ticker, weight, price, eps_ttm, eps_fwd, source, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date, instrument_id, ticker, source) DO UPDATE SET
                    weight = excluded.weight,
                    price = excluded.price,
                    eps_ttm = excluded.eps_ttm,
                    eps_fwd = excluded.eps_fwd,
                    fetched_at = excluded.fetched_at
                """,
                rows,
            )
        return len(rows)

    def get_constituent_snapshots(
        self,
        instrument_id: int,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, list[ConstituentQuote]]:
        query = """
            SELECT date, ticker, weight, price, eps_ttm, eps_fwd, source
            FROM constituent_snapshots
            WHERE instrument_id = ?
        """
        args: list[Any] = [instrument_id]
        if start_date:
            query += " AND date >= ?"
            args.append(start_date)
        if end_date:
            query += " AND date <= ?"
            args.append(end_date)
        query += " ORDER BY date ASC, weight DESC, ticker ASC"

        grouped: dict[str, list[ConstituentQuote]] = {}
        with self.connect() as conn:
            for row in conn.execute(query, args).fetchall():
                grouped.setdefault(row["date"], []).append(
                    ConstituentQuote(
                        ticker=row["ticker"],
                        weight=row["weight"],
                        price=row["price"],
                        eps_ttm=row["eps_ttm"],
                        eps_fwd=row["eps_fwd"],
                        source=row["source"],
                    )
                )
        return grouped

    def get_constituent_quotes_for_date(
        self,
        date_str: str,
        tickers: list[str],
    ) -> dict[str, ConstituentQuote]:
        """Look up already-fetched constituent quotes on `date_str` (for reuse by equity refresh)."""
        cleaned = list(dict.fromkeys(t.strip().upper() for t in tickers if t.strip()))
        if not cleaned:
            return {}
        # Also include dash/dot variants (e.g. BRK.B <-> BRK-B)
        variants: dict[str, str] = {}
        for tk in cleaned:
            variants[tk] = tk
            variants[tk.replace(".", "-")] = tk
            variants[tk.replace("-", ".")] = tk

        placeholders = ",".join("?" for _ in variants)
        query = f"""
            SELECT ticker, weight, price, eps_ttm, eps_fwd, source
            FROM constituent_snapshots
            WHERE date = ? AND ticker IN ({placeholders})
              AND price IS NOT NULL AND price > 0
            ORDER BY fetched_at DESC, id DESC
        """
        found: dict[str, ConstituentQuote] = {}
        with self.connect() as conn:
            for row in conn.execute(query, [date_str, *variants.keys()]).fetchall():
                canonical = variants.get(row["ticker"].upper(), row["ticker"].upper())
                if canonical not in found:
                    found[canonical] = ConstituentQuote(
                        ticker=canonical,
                        weight=row["weight"],
                        price=row["price"],
                        eps_ttm=row["eps_ttm"],
                        eps_fwd=row["eps_fwd"],
                        source=row["source"],
                    )
        return found

    # ------------------------------------------------------------------
    # equity_snapshots
    # ------------------------------------------------------------------
    def save_equity_snapshot(
        self,
        instrument_id: int,
        date: str,
        snapshot: EquitySnapshot,
        fetched_at: str | None = None,
    ) -> int:
        ts = fetched_at or utc_now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO equity_snapshots
                    (date, instrument_id, price, eps_ttm, eps_fwd, market_cap, source, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date, instrument_id, source) DO UPDATE SET
                    price = excluded.price,
                    eps_ttm = excluded.eps_ttm,
                    eps_fwd = excluded.eps_fwd,
                    market_cap = excluded.market_cap,
                    fetched_at = excluded.fetched_at
                """,
                (
                    date,
                    instrument_id,
                    float(snapshot.price) if snapshot.price is not None else None,
                    float(snapshot.eps_ttm) if snapshot.eps_ttm is not None else None,
                    float(snapshot.eps_fwd) if snapshot.eps_fwd is not None else None,
                    float(snapshot.market_cap) if snapshot.market_cap is not None else None,
                    snapshot.source,
                    ts,
                ),
            )
        return 1

    def get_equity_snapshots(
        self,
        instrument_id: int,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, list[EquitySnapshot]]:
        query = """
            SELECT date, price, eps_ttm, eps_fwd, market_cap, source
            FROM equity_snapshots
            WHERE instrument_id = ?
        """
        args: list[Any] = [instrument_id]
        if start_date:
            query += " AND date >= ?"
            args.append(start_date)
        if end_date:
            query += " AND date <= ?"
            args.append(end_date)
        query += " ORDER BY date ASC, fetched_at DESC, id DESC"

        grouped: dict[str, list[EquitySnapshot]] = {}
        with self.connect() as conn:
            for row in conn.execute(query, args).fetchall():
                grouped.setdefault(row["date"], []).append(
                    EquitySnapshot(
                        price=row["price"],
                        eps_ttm=row["eps_ttm"],
                        eps_fwd=row["eps_fwd"],
                        market_cap=row["market_cap"],
                        source=row["source"],
                    )
                )
        return grouped

    def get_latest_equity_snapshot(self, instrument_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT date, price, eps_ttm, eps_fwd, market_cap, source, fetched_at
                FROM equity_snapshots
                WHERE instrument_id = ? AND price IS NOT NULL AND price > 0
                ORDER BY date DESC, fetched_at DESC, id DESC
                LIMIT 1
                """,
                (instrument_id,),
            ).fetchone()
            return dict(row) if row else None

    # ------------------------------------------------------------------
    # raw_series
    # ------------------------------------------------------------------
    def save_raw_series(
        self,
        instrument_id: int,
        series_id: str,
        rows: list[tuple[str, float]],
        source: str,
        fetched_at: str | None = None,
    ) -> int:
        if not rows:
            return 0
        ts = fetched_at or utc_now_iso()
        payload = [
            (instrument_id, series_id, date_str, float(val), source, ts)
            for date_str, val in rows
        ]
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO raw_series
                    (instrument_id, series_id, date, value, source, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(instrument_id, series_id, date, source) DO UPDATE SET
                    value = excluded.value,
                    fetched_at = excluded.fetched_at
                """,
                payload,
            )
        return len(payload)

    def get_raw_series(
        self,
        instrument_id: int,
        series_id: str,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        query = """
            SELECT date, value, source, fetched_at
            FROM raw_series
            WHERE instrument_id = ? AND series_id = ?
        """
        args: list[Any] = [instrument_id, series_id]
        if source:
            query += " AND source = ?"
            args.append(source)
        query += " ORDER BY date ASC, source ASC"

        with self.connect() as conn:
            return [dict(r) for r in conn.execute(query, args).fetchall()]

    def delete_raw_series_by_source(self, instrument_id: int, source: str) -> int:
        """Delete raw_series rows for one instrument & source (e.g. upgrading seed_csv)."""
        with self.connect() as conn:
            cur = conn.execute(
                "DELETE FROM raw_series WHERE instrument_id = ? AND source = ?",
                (instrument_id, source),
            )
            return int(cur.rowcount)

    def get_source_coverage(self, instrument_id: int) -> dict[str, dict[str, dict[str, Any]]]:
        """Return `{source_id: {series_id: {min_date, max_date, count}}}` for an instrument."""
        coverage: dict[str, dict[str, dict[str, Any]]] = {}
        with self.connect() as conn:
            raw_rows = conn.execute(
                """
                SELECT source, series_id, MIN(date) AS min_date, MAX(date) AS max_date, COUNT(*) AS cnt
                FROM raw_series
                WHERE instrument_id = ?
                GROUP BY source, series_id
                ORDER BY source ASC, series_id ASC
                """,
                (instrument_id,),
            ).fetchall()
            for r in raw_rows:
                coverage.setdefault(r["source"], {})[r["series_id"]] = {
                    "min_date": r["min_date"],
                    "max_date": r["max_date"],
                    "count": int(r["cnt"]),
                }

            # Constituent snapshots map to `computed` and to their constituent tag (e.g. qqq+yfinance)
            overall_const = conn.execute(
                """
                SELECT
                    MIN(CASE WHEN price IS NOT NULL AND price > 0 AND eps_ttm IS NOT NULL THEN date END) AS ttm_min,
                    MAX(CASE WHEN price IS NOT NULL AND price > 0 AND eps_ttm IS NOT NULL THEN date END) AS ttm_max,
                    COUNT(DISTINCT CASE WHEN price IS NOT NULL AND price > 0 AND eps_ttm IS NOT NULL THEN date END) AS ttm_cnt,
                    MIN(CASE WHEN price IS NOT NULL AND price > 0 AND eps_fwd IS NOT NULL THEN date END) AS fwd_min,
                    MAX(CASE WHEN price IS NOT NULL AND price > 0 AND eps_fwd IS NOT NULL THEN date END) AS fwd_max,
                    COUNT(DISTINCT CASE WHEN price IS NOT NULL AND price > 0 AND eps_fwd IS NOT NULL THEN date END) AS fwd_cnt
                FROM constituent_snapshots
                WHERE instrument_id = ?
                """,
                (instrument_id,),
            ).fetchone()
            if overall_const:
                if int(overall_const["ttm_cnt"] or 0) > 0:
                    coverage.setdefault("computed", {})["pe_ttm"] = {
                        "min_date": overall_const["ttm_min"],
                        "max_date": overall_const["ttm_max"],
                        "count": int(overall_const["ttm_cnt"]),
                    }
                if int(overall_const["fwd_cnt"] or 0) > 0:
                    coverage.setdefault("computed", {})["pe_fwd"] = {
                        "min_date": overall_const["fwd_min"],
                        "max_date": overall_const["fwd_max"],
                        "count": int(overall_const["fwd_cnt"]),
                    }

            per_src_const = conn.execute(
                """
                SELECT
                    source,
                    MIN(CASE WHEN price IS NOT NULL AND price > 0 AND eps_ttm IS NOT NULL THEN date END) AS ttm_min,
                    MAX(CASE WHEN price IS NOT NULL AND price > 0 AND eps_ttm IS NOT NULL THEN date END) AS ttm_max,
                    COUNT(DISTINCT CASE WHEN price IS NOT NULL AND price > 0 AND eps_ttm IS NOT NULL THEN date END) AS ttm_cnt,
                    MIN(CASE WHEN price IS NOT NULL AND price > 0 AND eps_fwd IS NOT NULL THEN date END) AS fwd_min,
                    MAX(CASE WHEN price IS NOT NULL AND price > 0 AND eps_fwd IS NOT NULL THEN date END) AS fwd_max,
                    COUNT(DISTINCT CASE WHEN price IS NOT NULL AND price > 0 AND eps_fwd IS NOT NULL THEN date END) AS fwd_cnt
                FROM constituent_snapshots
                WHERE instrument_id = ?
                GROUP BY source
                """,
                (instrument_id,),
            ).fetchall()
            for r in per_src_const:
                src = r["source"]
                if int(r["ttm_cnt"] or 0) > 0:
                    coverage.setdefault(src, {})["pe_ttm"] = {
                        "min_date": r["ttm_min"],
                        "max_date": r["ttm_max"],
                        "count": int(r["ttm_cnt"]),
                    }
                if int(r["fwd_cnt"] or 0) > 0:
                    coverage.setdefault(src, {})["pe_fwd"] = {
                        "min_date": r["fwd_min"],
                        "max_date": r["fwd_max"],
                        "count": int(r["fwd_cnt"]),
                    }

            # Equity snapshots map to `computed` and to their source tag (e.g. equity+yfinance)
            overall_eq = conn.execute(
                """
                SELECT
                    MIN(CASE WHEN price IS NOT NULL AND price > 0 AND eps_ttm IS NOT NULL THEN date END) AS ttm_min,
                    MAX(CASE WHEN price IS NOT NULL AND price > 0 AND eps_ttm IS NOT NULL THEN date END) AS ttm_max,
                    COUNT(DISTINCT CASE WHEN price IS NOT NULL AND price > 0 AND eps_ttm IS NOT NULL THEN date END) AS ttm_cnt,
                    MIN(CASE WHEN price IS NOT NULL AND price > 0 AND eps_fwd IS NOT NULL THEN date END) AS fwd_min,
                    MAX(CASE WHEN price IS NOT NULL AND price > 0 AND eps_fwd IS NOT NULL THEN date END) AS fwd_max,
                    COUNT(DISTINCT CASE WHEN price IS NOT NULL AND price > 0 AND eps_fwd IS NOT NULL THEN date END) AS fwd_cnt
                FROM equity_snapshots
                WHERE instrument_id = ?
                """,
                (instrument_id,),
            ).fetchone()
            if overall_eq:
                if int(overall_eq["ttm_cnt"] or 0) > 0:
                    coverage.setdefault("computed", {})["pe_ttm"] = {
                        "min_date": overall_eq["ttm_min"],
                        "max_date": overall_eq["ttm_max"],
                        "count": int(overall_eq["ttm_cnt"]),
                    }
                if int(overall_eq["fwd_cnt"] or 0) > 0:
                    coverage.setdefault("computed", {})["pe_fwd"] = {
                        "min_date": overall_eq["fwd_min"],
                        "max_date": overall_eq["fwd_max"],
                        "count": int(overall_eq["fwd_cnt"]),
                    }

            per_src_eq = conn.execute(
                """
                SELECT
                    source,
                    MIN(CASE WHEN price IS NOT NULL AND price > 0 AND eps_ttm IS NOT NULL THEN date END) AS ttm_min,
                    MAX(CASE WHEN price IS NOT NULL AND price > 0 AND eps_ttm IS NOT NULL THEN date END) AS ttm_max,
                    COUNT(DISTINCT CASE WHEN price IS NOT NULL AND price > 0 AND eps_ttm IS NOT NULL THEN date END) AS ttm_cnt,
                    MIN(CASE WHEN price IS NOT NULL AND price > 0 AND eps_fwd IS NOT NULL THEN date END) AS fwd_min,
                    MAX(CASE WHEN price IS NOT NULL AND price > 0 AND eps_fwd IS NOT NULL THEN date END) AS fwd_max,
                    COUNT(DISTINCT CASE WHEN price IS NOT NULL AND price > 0 AND eps_fwd IS NOT NULL THEN date END) AS fwd_cnt
                FROM equity_snapshots
                WHERE instrument_id = ?
                GROUP BY source
                """,
                (instrument_id,),
            ).fetchall()
            for r in per_src_eq:
                src = r["source"]
                if int(r["ttm_cnt"] or 0) > 0:
                    coverage.setdefault(src, {})["pe_ttm"] = {
                        "min_date": r["ttm_min"],
                        "max_date": r["ttm_max"],
                        "count": int(r["ttm_cnt"]),
                    }
                if int(r["fwd_cnt"] or 0) > 0:
                    coverage.setdefault(src, {})["pe_fwd"] = {
                        "min_date": r["fwd_min"],
                        "max_date": r["fwd_max"],
                        "count": int(r["fwd_cnt"]),
                    }

        return coverage

    # ------------------------------------------------------------------
    # indicator_values
    # ------------------------------------------------------------------
    def save_indicator_values(
        self,
        instrument_id: int,
        indicator_id: str,
        params: dict[str, Any] | None,
        points: list[IndicatorPoint],
        computed_at: str | None = None,
        replace: bool = False,
    ) -> int:
        if not points:
            return 0
        ts = computed_at or utc_now_iso()
        params_json = canonical_params(params)
        payload = [
            (
                indicator_id,
                instrument_id,
                pt.date,
                float(pt.value) if pt.value is not None else None,
                params_json,
                pt.source,
                1 if pt.is_incomplete else 0,
                json.dumps(pt.meta or {}, ensure_ascii=False),
                ts,
            )
            for pt in points
        ]
        with self.connect() as conn:
            if replace:
                conn.execute(
                    """
                    DELETE FROM indicator_values
                    WHERE instrument_id = ? AND indicator_id = ? AND params = ?
                    """,
                    (instrument_id, indicator_id, params_json),
                )
            conn.executemany(
                """
                INSERT INTO indicator_values
                    (indicator_id, instrument_id, date, value, params, source, is_incomplete, meta, computed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(indicator_id, instrument_id, date, params) DO UPDATE SET
                    value = excluded.value,
                    source = excluded.source,
                    is_incomplete = excluded.is_incomplete,
                    meta = excluded.meta,
                    computed_at = excluded.computed_at
                """,
                payload,
            )
        return len(payload)

    def get_indicator_values(
        self,
        instrument_id: int,
        indicator_id: str,
        params: dict[str, Any] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[IndicatorPoint]:
        params_json = canonical_params(params)
        query = """
            SELECT date, value, source, is_incomplete, meta
            FROM indicator_values
            WHERE instrument_id = ? AND indicator_id = ? AND params = ?
        """
        args: list[Any] = [instrument_id, indicator_id, params_json]
        if start_date:
            query += " AND date >= ?"
            args.append(start_date)
        if end_date:
            query += " AND date <= ?"
            args.append(end_date)
        query += " ORDER BY date ASC"

        points: list[IndicatorPoint] = []
        with self.connect() as conn:
            for row in conn.execute(query, args).fetchall():
                meta_raw = row["meta"] or "{}"
                try:
                    meta = json.loads(meta_raw)
                except json.JSONDecodeError:
                    meta = {}
                points.append(
                    IndicatorPoint(
                        date=row["date"],
                        value=row["value"],
                        source=row["source"],
                        is_incomplete=bool(row["is_incomplete"]),
                        meta=meta,
                    )
                )
        return points

    def get_latest_indicator_points(
        self,
        instrument_id: int,
        indicator_id: str,
        params: dict[str, Any] | None = None,
        limit: int = 2,
        include_null: bool = False,
    ) -> list[IndicatorPoint]:
        params_json = canonical_params(params)
        null_clause = "" if include_null else " AND value IS NOT NULL"
        query = f"""
            SELECT date, value, source, is_incomplete, meta
            FROM indicator_values
            WHERE instrument_id = ? AND indicator_id = ? AND params = ? {null_clause}
            ORDER BY date DESC
            LIMIT ?
        """
        points: list[IndicatorPoint] = []
        with self.connect() as conn:
            for row in conn.execute(
                query, (instrument_id, indicator_id, params_json, limit)
            ).fetchall():
                try:
                    meta = json.loads(row["meta"] or "{}")
                except json.JSONDecodeError:
                    meta = {}
                points.append(
                    IndicatorPoint(
                        date=row["date"],
                        value=row["value"],
                        source=row["source"],
                        is_incomplete=bool(row["is_incomplete"]),
                        meta=meta,
                    )
                )
        return points

    def get_indicator_coverage_summary(
        self,
        instrument_id: int,
        indicator_ids: tuple[str, ...] = ("pe_ttm", "pe_fwd"),
    ) -> dict[str, Any]:
        """Fast SQL summary of non-null history span and sources for watchlist overview."""
        placeholders = ",".join("?" for _ in indicator_ids)
        with self.connect() as conn:
            span = conn.execute(
                f"""
                SELECT MIN(date) AS min_date, MAX(date) AS max_date, COUNT(DISTINCT date) AS cnt
                FROM indicator_values
                WHERE instrument_id = ? AND indicator_id IN ({placeholders})
                  AND params = '{{}}' AND value IS NOT NULL
                """,
                [instrument_id, *indicator_ids],
            ).fetchone()
            src_rows = conn.execute(
                f"""
                SELECT DISTINCT source
                FROM indicator_values
                WHERE instrument_id = ? AND indicator_id IN ({placeholders})
                  AND params = '{{}}' AND value IS NOT NULL
                ORDER BY source ASC
                """,
                [instrument_id, *indicator_ids],
            ).fetchall()
        return {
            "min_date": span["min_date"] if span else None,
            "max_date": span["max_date"] if span else None,
            "count": int(span["cnt"] or 0) if span else 0,
            "sources": [str(r["source"]) for r in src_rows if r["source"]],
        }

    # ------------------------------------------------------------------
    # job_runs
    # ------------------------------------------------------------------
    def start_job_run(self, job: str, details: dict[str, Any] | None = None) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO job_runs (job, started_at, status, details)
                VALUES (?, ?, 'running', ?)
                """,
                (job, utc_now_iso(), json.dumps(details or {}, ensure_ascii=False)),
            )
            return int(cur.lastrowid)

    def finish_job_run(
        self,
        job_id: int,
        status: str,
        error: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as conn:
            if details is not None:
                conn.execute(
                    """
                    UPDATE job_runs
                    SET finished_at = ?, status = ?, error = ?, details = ?
                    WHERE id = ?
                    """,
                    (
                        utc_now_iso(),
                        status,
                        error,
                        json.dumps(details, ensure_ascii=False),
                        job_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE job_runs
                    SET finished_at = ?, status = ?, error = ?
                    WHERE id = ?
                    """,
                    (utc_now_iso(), status, error, job_id),
                )

    def update_job_run_details(self, job_id: int, details: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE job_runs SET details = ? WHERE id = ?",
                (json.dumps(details, ensure_ascii=False), job_id),
            )

    def get_job_run(self, job_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id, job, started_at, finished_at, status, error, details FROM job_runs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                return None
            item = dict(row)
            try:
                item["details"] = json.loads(item["details"] or "{}")
            except json.JSONDecodeError:
                item["details"] = {}
            return item

    def get_latest_job_runs(
        self,
        instrument_symbol: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return the most recent run for each distinct job name, plus last_success_at.

        When `instrument_symbol` is specified, filters out runs belonging exclusively to
        other instruments or other instrument kinds (index vs equity).
        """
        if instrument_symbol is None:
            with self.connect() as conn:
                rows = conn.execute(
                    """
                    SELECT j.id, j.job, j.started_at, j.finished_at, j.status, j.error, j.details,
                           (
                               SELECT MAX(COALESCE(s.finished_at, s.started_at))
                               FROM job_runs s
                               WHERE s.job = j.job AND s.status IN ('success', 'partial')
                           ) AS last_success_at
                    FROM job_runs j
                    INNER JOIN (
                        SELECT job, MAX(id) AS max_id
                        FROM job_runs
                        GROUP BY job
                    ) latest ON j.id = latest.max_id
                    ORDER BY j.job ASC
                    """
                ).fetchall()
                result = []
                for r in rows:
                    item = dict(r)
                    try:
                        item["details"] = json.loads(item["details"] or "{}")
                    except json.JSONDecodeError:
                        item["details"] = {}
                    result.append(item)
                return result

        sym = instrument_symbol.strip().upper()
        inst = self.get_instrument(sym)
        inst_kind = inst.get("kind", "index") if inst else ("index" if sym in INSTRUMENTS else "equity")

        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, job, started_at, finished_at, status, error, details
                FROM job_runs
                ORDER BY id DESC
                """
            ).fetchall()

        latest_by_job: dict[str, dict[str, Any]] = {}
        last_success_by_job: dict[str, str] = {}

        for r in rows:
            job_name = r["job"]
            if inst_kind == "equity":
                if job_name in _INDEX_ONLY_JOBS:
                    continue
            else:
                if job_name in _EQUITY_ONLY_JOBS:
                    continue
                allowed_syms = _INSTRUMENT_EXCLUSIVE_JOBS.get(job_name)
                if allowed_syms is not None and sym not in allowed_syms:
                    continue

            try:
                details = json.loads(r["details"] or "{}")
            except json.JSONDecodeError:
                details = {}
            if not isinstance(details, dict):
                details = {}

            row_inst = details.get("instrument")
            if row_inst is not None and str(row_inst).strip().upper() != sym:
                continue

            row_symbols = details.get("symbols")
            if row_inst is None and isinstance(row_symbols, list) and row_symbols:
                sym_set = {str(s).strip().upper() for s in row_symbols if str(s).strip()}
                if sym_set and sym not in sym_set:
                    continue

            if r["status"] in ("success", "partial"):
                ts = r["finished_at"] or r["started_at"]
                if ts and (
                    job_name not in last_success_by_job or ts > last_success_by_job[job_name]
                ):
                    last_success_by_job[job_name] = ts

            if job_name not in latest_by_job:
                item = dict(r)
                item["details"] = details
                latest_by_job[job_name] = item

        result: list[dict[str, Any]] = []
        for job_name in sorted(latest_by_job.keys()):
            item = latest_by_job[job_name]
            item["last_success_at"] = last_success_by_job.get(job_name)
            result.append(item)
        return result
