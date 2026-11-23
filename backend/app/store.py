"""SQLite storage layer for instruments, constituent snapshots, raw series, indicator values, and job runs."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from app.config import DB_PATH, DEFAULT_INSTRUMENT_NAME, DEFAULT_INSTRUMENT_SYMBOL
from app.models import ConstituentQuote, IndicatorPoint


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
    name TEXT NOT NULL
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
"""


class ValuationStore:
    """Thin SQLite repository encapsulating all 5 tables."""

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
        with self.connect() as conn:
            conn.executescript(SCHEMA_SQL)
            conn.execute(
                "INSERT OR IGNORE INTO instruments (symbol, name) VALUES (?, ?)",
                (DEFAULT_INSTRUMENT_SYMBOL, DEFAULT_INSTRUMENT_NAME),
            )

    # ------------------------------------------------------------------
    # instruments
    # ------------------------------------------------------------------
    def get_or_create_instrument(self, symbol: str, name: str | None = None) -> dict[str, Any]:
        symbol_upper = symbol.upper()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id, symbol, name FROM instruments WHERE symbol = ?",
                (symbol_upper,),
            ).fetchone()
            if row is not None:
                return dict(row)
            display_name = name or symbol_upper
            cur = conn.execute(
                "INSERT INTO instruments (symbol, name) VALUES (?, ?)",
                (symbol_upper, display_name),
            )
            return {"id": cur.lastrowid, "symbol": symbol_upper, "name": display_name}

    def get_instrument(self, symbol: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id, symbol, name FROM instruments WHERE symbol = ?",
                (symbol.upper(),),
            ).fetchone()
            return dict(row) if row else None

    def list_instruments(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT id, symbol, name FROM instruments ORDER BY id").fetchall()
            return [dict(r) for r in rows]

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

    def get_latest_job_runs(self) -> list[dict[str, Any]]:
        """Return the most recent run for each distinct job name, plus last_success_at."""
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
