"""Thread-local SQLite repository with WAL and upserted health history."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .models import ProxyProtocol, ProxyRecord, ProxyStatus, utc_now_iso


def default_data_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share")
    return base / "ProxyPulse"


class ProxyRepository:
    def __init__(self, path: str | Path | None = None) -> None:
        data_dir = default_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = Path(path) if path else data_dir / "proxypulse.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        try:
            yield connection
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS proxies (
                    id INTEGER PRIMARY KEY,
                    protocol TEXT NOT NULL,
                    host TEXT NOT NULL,
                    port INTEGER NOT NULL,
                    sources_json TEXT NOT NULL DEFAULT '[]',
                    country TEXT NOT NULL DEFAULT '',
                    country_code TEXT NOT NULL DEFAULT '',
                    city TEXT NOT NULL DEFAULT '',
                    anonymity TEXT NOT NULL DEFAULT 'unknown',
                    advertised_ssl INTEGER,
                    advertised_uptime REAL,
                    advertised_latency_ms REAL,
                    status TEXT NOT NULL DEFAULT 'new',
                    latency_ms REAL,
                    exit_ip TEXT NOT NULL DEFAULT '',
                    hides_ip INTEGER,
                    score INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '',
                    tested_at TEXT NOT NULL DEFAULT '',
                    last_seen_at TEXT NOT NULL,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(protocol, host, port)
                );
                CREATE INDEX IF NOT EXISTS idx_proxies_status_score
                    ON proxies(status, score DESC, latency_ms ASC);
                CREATE TABLE IF NOT EXISTS check_history (
                    id INTEGER PRIMARY KEY,
                    proxy_id INTEGER NOT NULL REFERENCES proxies(id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    latency_ms REAL,
                    exit_ip TEXT NOT NULL DEFAULT '',
                    score INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '',
                    checked_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_history_proxy_time
                    ON check_history(proxy_id, checked_at DESC);
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL
                );
                PRAGMA user_version=1;
                """
            )

    @staticmethod
    def _nullable_bool(value: bool | None) -> int | None:
        return None if value is None else int(value)

    def upsert_many(self, records: Iterable[ProxyRecord]) -> int:
        rows = list(records)
        if not rows:
            return 0
        now = utc_now_iso()
        sql = """
            INSERT INTO proxies (
                protocol, host, port, sources_json, country, country_code, city,
                anonymity, advertised_ssl, advertised_uptime, advertised_latency_ms,
                status, latency_ms, exit_ip, hides_ip, score, error, tested_at,
                last_seen_at, success_count, failure_count
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(protocol, host, port) DO UPDATE SET
                sources_json=excluded.sources_json,
                country=CASE WHEN excluded.country<>'' THEN excluded.country ELSE proxies.country END,
                country_code=CASE WHEN excluded.country_code<>'' THEN excluded.country_code ELSE proxies.country_code END,
                city=CASE WHEN excluded.city<>'' THEN excluded.city ELSE proxies.city END,
                anonymity=CASE WHEN excluded.anonymity<>'unknown' THEN excluded.anonymity ELSE proxies.anonymity END,
                advertised_ssl=COALESCE(excluded.advertised_ssl, proxies.advertised_ssl),
                advertised_uptime=COALESCE(excluded.advertised_uptime, proxies.advertised_uptime),
                advertised_latency_ms=COALESCE(excluded.advertised_latency_ms, proxies.advertised_latency_ms),
                status=excluded.status,
                latency_ms=excluded.latency_ms,
                exit_ip=excluded.exit_ip,
                hides_ip=excluded.hides_ip,
                score=excluded.score,
                error=excluded.error,
                tested_at=excluded.tested_at,
                last_seen_at=excluded.last_seen_at,
                success_count=MAX(proxies.success_count, excluded.success_count),
                failure_count=MAX(proxies.failure_count, excluded.failure_count)
        """
        # 660k için tek executemany OOM/lock yapmasın -> 4000'lik chunk'lar halinde commit
        CHUNK = 4000
        total = 0
        for start in range(0, len(rows), CHUNK):
            chunk = rows[start : start + CHUNK]
            params = [
                (
                    record.protocol.value,
                    record.host,
                    record.port,
                    json.dumps(sorted(record.sources), ensure_ascii=False),
                    record.country,
                    record.country_code,
                    record.city,
                    record.anonymity,
                    self._nullable_bool(record.advertised_ssl),
                    record.advertised_uptime,
                    record.advertised_latency_ms,
                    record.status.value,
                    record.latency_ms,
                    record.exit_ip,
                    self._nullable_bool(record.hides_ip),
                    record.score,
                    record.error,
                    record.tested_at,
                    now,
                    record.success_count,
                    record.failure_count,
                )
                for record in chunk
            ]
            with self._connect() as connection:
                connection.executemany(sql, params)
            total += len(chunk)
        return total

    def record_check(self, record: ProxyRecord) -> None:
        self.upsert_many([record])
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM proxies WHERE protocol=? AND host=? AND port=?",
                (record.protocol.value, record.host, record.port),
            ).fetchone()
            if row:
                connection.execute(
                    """INSERT INTO check_history
                       (proxy_id,status,latency_ms,exit_ip,score,error,checked_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (
                        row["id"],
                        record.status.value,
                        record.latency_ms,
                        record.exit_ip,
                        record.score,
                        record.error,
                        record.tested_at or utc_now_iso(),
                    ),
                )

    def replace_snapshot(self, records: Iterable[ProxyRecord]) -> int:
        return self.upsert_many(records)

    def load_all(self, limit: int = 100_000) -> list[ProxyRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM proxies
                   ORDER BY CASE status WHEN 'alive' THEN 0 WHEN 'new' THEN 1 ELSE 2 END,
                            score DESC, latency_ms ASC
                   LIMIT ?""",
                (max(1, min(int(limit), 200_000)),),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def clear(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM check_history")
            connection.execute("DELETE FROM proxies")
            # WAL checkpoint ve vakum ile dosya şişmesini önle
            try:
                connection.execute("DELETE FROM sqlite_sequence WHERE name IN ('proxies','check_history')")
            except Exception:
                pass

    def clear_by_status(self, statuses: list[str]) -> int:
        if not statuses:
            return 0
        placeholders = ",".join("?" for _ in statuses)
        with self._connect() as connection:
            cursor = connection.execute(f"DELETE FROM proxies WHERE status IN ({placeholders})", statuses)
            return cursor.rowcount if cursor.rowcount is not None else 0

    def set_setting(self, key: str, value: object) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO settings(key,value_json) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                (key, json.dumps(value, ensure_ascii=False)),
            )

    def get_setting(self, key: str, default: object = None) -> object:
        with self._connect() as connection:
            row = connection.execute("SELECT value_json FROM settings WHERE key=?", (key,)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value_json"])
        except json.JSONDecodeError:
            return default

    @staticmethod
    def _from_row(row: sqlite3.Row) -> ProxyRecord:
        try:
            sources = set(json.loads(row["sources_json"]))
        except (json.JSONDecodeError, TypeError):
            sources = set()
        return ProxyRecord(
            host=row["host"],
            port=row["port"],
            protocol=ProxyProtocol.from_value(row["protocol"]),
            sources=sources,
            country=row["country"],
            country_code=row["country_code"],
            city=row["city"],
            anonymity=row["anonymity"],
            advertised_ssl=None if row["advertised_ssl"] is None else bool(row["advertised_ssl"]),
            advertised_uptime=row["advertised_uptime"],
            advertised_latency_ms=row["advertised_latency_ms"],
            status=ProxyStatus(row["status"]),
            latency_ms=row["latency_ms"],
            exit_ip=row["exit_ip"],
            hides_ip=None if row["hides_ip"] is None else bool(row["hides_ip"]),
            score=row["score"],
            error=row["error"],
            tested_at=row["tested_at"],
            success_count=row["success_count"],
            failure_count=row["failure_count"],
        )
