"""SQLite persistence for snapshots and settings."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_SETTINGS: dict[str, Any] = {
    "refresh_seconds": 60,
    "history_retention_days": 30,
    "api_base": "https://api.commandcode.ai",
}


def data_dir() -> Path:
    configured = os.environ.get("GOATGAUGE_DATA", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(root) / "GOATGauge"
    return Path.home() / ".local" / "share" / "goat-gauge"


class Store:
    def __init__(self, path: Path | None = None) -> None:
        self.data_dir = data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = Path(path) if path else self.data_dir / "gauge.db"
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fetched_at INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    five_used REAL,
                    five_cap REAL,
                    five_remaining_pct REAL,
                    weekly_used REAL,
                    weekly_cap REAL,
                    weekly_remaining_pct REAL,
                    monthly_remaining REAL,
                    monthly_used REAL,
                    monthly_cap REAL,
                    monthly_remaining_pct REAL,
                    total_cost REAL,
                    total_count INTEGER,
                    tokens_in INTEGER,
                    tokens_out INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_snapshots_fetched
                    ON snapshots(fetched_at DESC);
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS usage_records (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    created_ms INTEGER NOT NULL,
                    model TEXT NOT NULL DEFAULT '',
                    provider TEXT NOT NULL DEFAULT '',
                    tokens_in INTEGER NOT NULL DEFAULT 0,
                    tokens_out INTEGER NOT NULL DEFAULT 0,
                    cost_total REAL NOT NULL DEFAULT 0,
                    cost_input REAL NOT NULL DEFAULT 0,
                    cost_output REAL NOT NULL DEFAULT 0,
                    cost_cache REAL NOT NULL DEFAULT 0,
                    duration_ms INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT '',
                    entry_type TEXT NOT NULL DEFAULT '',
                    mode TEXT NOT NULL DEFAULT '',
                    trace_id TEXT NOT NULL DEFAULT '',
                    synced_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_usage_created
                    ON usage_records(created_ms DESC);
                CREATE INDEX IF NOT EXISTS idx_usage_model
                    ON usage_records(model);
                CREATE TABLE IF NOT EXISTS usage_buckets (
                    model TEXT NOT NULL,
                    time_bucket TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT '',
                    requests INTEGER NOT NULL DEFAULT 0,
                    tokens_in INTEGER NOT NULL DEFAULT 0,
                    tokens_out INTEGER NOT NULL DEFAULT 0,
                    tokens_total INTEGER NOT NULL DEFAULT 0,
                    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
                    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
                    cost_total REAL NOT NULL DEFAULT 0,
                    cost_cache REAL NOT NULL DEFAULT 0,
                    cache_savings REAL NOT NULL DEFAULT 0,
                    bucket_ms INTEGER NOT NULL DEFAULT 0,
                    synced_at INTEGER NOT NULL,
                    PRIMARY KEY (model, time_bucket)
                );
                CREATE INDEX IF NOT EXISTS idx_bucket_ms
                    ON usage_buckets(bucket_ms DESC);
                """
            )
            for key, value in DEFAULT_SETTINGS.items():
                self._conn.execute(
                    "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                    (key, json.dumps(value)),
                )
            self._conn.commit()

    # ------------------------------------------------------------------
    # 使用记录
    # ------------------------------------------------------------------

    @staticmethod
    def _to_ms(created_at: str) -> int:
        text = (created_at or "").strip()
        if not text:
            return 0
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return 0
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp() * 1000)

    def insert_usage_records(self, records: list[dict[str, Any]]) -> int:
        if not records:
            return 0
        inserted = 0
        now_ms = int(time.time() * 1000)
        with self._lock:
            for record in records:
                record_id = str(record.get("id") or "").strip()
                created_at = str(record.get("created_at") or "").strip()
                if not record_id or not created_at:
                    continue
                created_ms = self._to_ms(created_at)
                cursor = self._conn.execute(
                    "SELECT 1 FROM usage_records WHERE id = ?",
                    (record_id,),
                ).fetchone()
                self._conn.execute(
                    """
                    INSERT INTO usage_records (
                        id, created_at, created_ms, model, provider,
                        tokens_in, tokens_out, cost_total, cost_input, cost_output,
                        cost_cache, duration_ms, status, entry_type, mode,
                        trace_id, synced_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        tokens_in = excluded.tokens_in,
                        tokens_out = excluded.tokens_out,
                        cost_total = excluded.cost_total,
                        cost_input = excluded.cost_input,
                        cost_output = excluded.cost_output,
                        cost_cache = excluded.cost_cache,
                        status = excluded.status,
                        synced_at = excluded.synced_at
                    """,
                    (
                        record_id,
                        created_at,
                        created_ms,
                        str(record.get("model") or ""),
                        str(record.get("provider") or ""),
                        int(record.get("tokens_in") or 0),
                        int(record.get("tokens_out") or 0),
                        float(record.get("cost_total") or 0.0),
                        float(record.get("cost_input") or 0.0),
                        float(record.get("cost_output") or 0.0),
                        float(record.get("cost_cache") or 0.0),
                        int(record.get("duration_ms") or 0),
                        str(record.get("status") or ""),
                        str(record.get("entry_type") or ""),
                        str(record.get("mode") or ""),
                        str(record.get("trace_id") or ""),
                        now_ms,
                    ),
                )
                if cursor is None:
                    inserted += 1
            self._conn.commit()
        return inserted

    def usage_range_ms(self, range_key: str) -> int | None:
        if range_key == "all":
            return None
        if range_key == "today":
            # 用本机时区的自然日, 避免 UTC 边界把凌晨记录算到昨天
            start = datetime.now().astimezone().replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
            return int(start.timestamp() * 1000)
        days = {"7d": 7, "30d": 30}.get(range_key, 1)
        return int((time.time() - days * 86400) * 1000)

    def usage_totals(self, range_key: str = "today") -> dict[str, Any]:
        since = self.usage_range_ms(range_key)
        where = "WHERE created_ms >= ?" if since is not None else ""
        params: tuple[Any, ...] = (since,) if since is not None else ()
        with self._lock:
            row = self._conn.execute(
                f"""
                SELECT COUNT(*) AS requests,
                       COALESCE(SUM(tokens_in), 0) AS tokens_in,
                       COALESCE(SUM(tokens_out), 0) AS tokens_out,
                       COALESCE(SUM(cost_total), 0) AS cost_total,
                       COALESCE(SUM(cost_cache), 0) AS cost_cache,
                       COALESCE(SUM(duration_ms), 0) AS duration_total,
                       SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
                       SUM(CASE WHEN status <> 'completed' AND status <> '' THEN 1 ELSE 0 END) AS failed
                FROM usage_records {where}
                """,
                params,
            ).fetchone()
            if not row or not row["requests"]:
                return {
                    "requests": 0,
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "tokens_total": 0,
                    "cost_total": 0.0,
                    "cost_cache": 0.0,
                    "average_cost": 0.0,
                    "success_rate": 0.0,
                    "completed": 0,
                    "failed": 0,
                    "duration_total": 0,
                }
        requests = int(row["requests"] or 0)
        completed = int(row["completed"] or 0)
        failed = int(row["failed"] or 0)
        tokens_in = int(row["tokens_in"] or 0)
        tokens_out = int(row["tokens_out"] or 0)
        cost_total = float(row["cost_total"] or 0.0)
        return {
            "requests": requests,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "tokens_total": tokens_in + tokens_out,
            "cost_total": cost_total,
            "cost_cache": float(row["cost_cache"] or 0.0),
            "average_cost": cost_total / requests if requests else 0.0,
            "success_rate": (completed / requests * 100.0) if requests else 0.0,
            "completed": completed,
            "failed": failed,
            "duration_total": int(row["duration_total"] or 0),
        }

    def model_stats(self, range_key: str = "today") -> list[dict[str, Any]]:
        since = self.usage_range_ms(range_key)
        where = "WHERE created_ms >= ?" if since is not None else ""
        params: tuple[Any, ...] = (since,) if since is not None else ()
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT model,
                       COUNT(*) AS requests,
                       COALESCE(SUM(tokens_in), 0) AS tokens_in,
                       COALESCE(SUM(tokens_out), 0) AS tokens_out,
                       COALESCE(SUM(cost_total), 0) AS cost_total,
                       COALESCE(SUM(cost_cache), 0) AS cost_cache
                FROM usage_records {where}
                GROUP BY model
                ORDER BY (SUM(tokens_in) + SUM(tokens_out)) DESC
                """,
                params,
            ).fetchall()
        return [
            {
                "model": row["model"] or "unknown",
                "requests": int(row["requests"] or 0),
                "tokens_in": int(row["tokens_in"] or 0),
                "tokens_out": int(row["tokens_out"] or 0),
                "tokens_total": int(row["tokens_in"] or 0) + int(row["tokens_out"] or 0),
                "cost_total": float(row["cost_total"] or 0.0),
                "cost_cache": float(row["cost_cache"] or 0.0),
            }
            for row in rows
        ]

    def daily_stats(self, range_key: str = "30d") -> list[dict[str, Any]]:
        since = self.usage_range_ms(range_key)
        where = "WHERE created_ms >= ?" if since is not None else ""
        params: tuple[Any, ...] = (since,) if since is not None else ()
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT strftime('%Y-%m-%d', created_ms / 1000, 'unixepoch', 'localtime') AS day,
                       COUNT(*) AS requests,
                       COALESCE(SUM(tokens_in), 0) AS tokens_in,
                       COALESCE(SUM(tokens_out), 0) AS tokens_out,
                       COALESCE(SUM(cost_total), 0) AS cost_total
                FROM usage_records {where}
                GROUP BY day
                ORDER BY day ASC
                """,
                params,
            ).fetchall()
        return [
            {
                "day": row["day"],
                "requests": int(row["requests"] or 0),
                "tokens_in": int(row["tokens_in"] or 0),
                "tokens_out": int(row["tokens_out"] or 0),
                "tokens_total": int(row["tokens_in"] or 0) + int(row["tokens_out"] or 0),
                "cost_total": float(row["cost_total"] or 0.0),
            }
            for row in rows
            if row["day"]
        ]

    def list_models(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT model FROM usage_records WHERE model <> '' ORDER BY model"
            ).fetchall()
        return [row["model"] for row in rows]

    def usage_records_page(
        self,
        page: int = 1,
        page_size: int = 20,
        model: str | None = None,
        range_key: str = "today",
    ) -> tuple[list[dict[str, Any]], int]:
        since = self.usage_range_ms(range_key)
        clauses: list[str] = []
        params: list[Any] = []
        if since is not None:
            clauses.append("created_ms >= ?")
            params.append(since)
        if model:
            clauses.append("model = ?")
            params.append(model)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        offset = max(0, (page - 1) * page_size)
        with self._lock:
            total = int(
                self._conn.execute(
                    f"SELECT COUNT(*) AS c FROM usage_records {where}",
                    tuple(params),
                ).fetchone()["c"]
            )
            rows = self._conn.execute(
                f"""
                SELECT id, created_at, created_ms, model, tokens_in, tokens_out,
                       cost_total, cost_input, cost_output, cost_cache,
                       duration_ms, status, entry_type, mode
                FROM usage_records {where}
                ORDER BY created_ms DESC
                LIMIT ? OFFSET ?
                """,
                (*params, page_size, offset),
            ).fetchall()
        return [dict(row) for row in rows], total

    def usage_record_bounds(self) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c, MIN(created_ms) AS oldest, MAX(created_ms) AS newest "
                "FROM usage_records"
            ).fetchone()
        return {
            "count": int(row["c"] or 0),
            "oldest_ms": int(row["oldest"] or 0),
            "newest_ms": int(row["newest"] or 0),
        }

    # ------------------------------------------------------------------
    # 缓存聚合桶 (来自 /internal/usage/charts)
    # ------------------------------------------------------------------

    @staticmethod
    def _bucket_ms(value: str) -> int:
        text = (value or "").strip()
        if not text:
            return 0
        normalized = text.replace(" ", "T")
        if not normalized.endswith("Z"):
            normalized += "Z"
        try:
            parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        except ValueError:
            return 0
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        # 上游桶时间戳是 UTC; charts from/to 用 UTC 边界
        return int(parsed.timestamp() * 1000)

    def insert_usage_buckets(self, buckets: list[dict[str, Any]]) -> int:
        if not buckets:
            return 0
        inserted = 0
        now_ms = int(time.time() * 1000)
        with self._lock:
            for bucket in buckets:
                model = str(bucket.get("model") or "").strip()
                time_bucket = str(bucket.get("time_bucket") or "").strip()
                if not model or not time_bucket:
                    continue
                exists = self._conn.execute(
                    "SELECT 1 FROM usage_buckets WHERE model = ? AND time_bucket = ?",
                    (model, time_bucket),
                ).fetchone()
                self._conn.execute(
                    """
                    INSERT INTO usage_buckets (
                        model, time_bucket, provider, requests,
                        tokens_in, tokens_out, tokens_total,
                        cache_read_tokens, cache_write_tokens,
                        cost_total, cost_cache, cache_savings,
                        bucket_ms, synced_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(model, time_bucket) DO UPDATE SET
                        requests = excluded.requests,
                        tokens_in = excluded.tokens_in,
                        tokens_out = excluded.tokens_out,
                        tokens_total = excluded.tokens_total,
                        cache_read_tokens = excluded.cache_read_tokens,
                        cache_write_tokens = excluded.cache_write_tokens,
                        cost_total = excluded.cost_total,
                        cost_cache = excluded.cost_cache,
                        cache_savings = excluded.cache_savings,
                        bucket_ms = excluded.bucket_ms,
                        synced_at = excluded.synced_at
                    """,
                    (
                        model,
                        time_bucket,
                        str(bucket.get("provider") or ""),
                        int(bucket.get("requests") or 0),
                        int(bucket.get("tokens_in") or 0),
                        int(bucket.get("tokens_out") or 0),
                        int(bucket.get("tokens_total") or 0),
                        int(bucket.get("cache_read_tokens") or 0),
                        int(bucket.get("cache_write_tokens") or 0),
                        float(bucket.get("cost_total") or 0.0),
                        float(bucket.get("cost_cache") or 0.0),
                        float(bucket.get("cache_savings") or 0.0),
                        self._bucket_ms(time_bucket),
                        now_ms,
                    ),
                )
                if exists is None:
                    inserted += 1
            self._conn.commit()
        return inserted

    def cache_stats(self, range_key: str = "today") -> dict[str, Any]:
        """缓存读/写与命中率; 口径: 命中率 = 缓存读 / 输入 (与上游一致)."""
        since = self.usage_range_ms(range_key)
        where = "WHERE bucket_ms >= ?" if since is not None else ""
        params: tuple[Any, ...] = (since,) if since is not None else ()
        with self._lock:
            row = self._conn.execute(
                f"""
                SELECT COALESCE(SUM(tokens_in), 0) AS tokens_in,
                       COALESCE(SUM(tokens_out), 0) AS tokens_out,
                       COALESCE(SUM(cache_read_tokens), 0) AS cache_read,
                       COALESCE(SUM(cache_write_tokens), 0) AS cache_write,
                       COALESCE(SUM(cost_cache), 0) AS cost_cache,
                       COALESCE(SUM(cache_savings), 0) AS cache_savings,
                       COALESCE(SUM(requests), 0) AS requests,
                       COUNT(*) AS buckets
                FROM usage_buckets {where}
                """,
                params,
            ).fetchone()
        tokens_in = int(row["tokens_in"] or 0)
        cache_read = int(row["cache_read"] or 0)
        cache_write = int(row["cache_write"] or 0)
        miss = max(0, tokens_in - cache_read)
        return {
            "tokens_in": tokens_in,
            "tokens_out": int(row["tokens_out"] or 0),
            "cache_read": cache_read,
            "cache_write": cache_write,
            "cache_miss": miss,
            "hit_rate": (cache_read / tokens_in * 100.0) if tokens_in else 0.0,
            "cost_cache": float(row["cost_cache"] or 0.0),
            "cache_savings": float(row["cache_savings"] or 0.0),
            "requests": int(row["requests"] or 0),
            "buckets": int(row["buckets"] or 0),
        }

    def model_cache_stats(self, range_key: str = "today") -> dict[str, dict[str, Any]]:
        since = self.usage_range_ms(range_key)
        where = "WHERE bucket_ms >= ?" if since is not None else ""
        params: tuple[Any, ...] = (since,) if since is not None else ()
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT model,
                       COALESCE(SUM(tokens_in), 0) AS tokens_in,
                       COALESCE(SUM(tokens_out), 0) AS tokens_out,
                       COALESCE(SUM(cache_read_tokens), 0) AS cache_read,
                       COALESCE(SUM(cache_write_tokens), 0) AS cache_write,
                       COALESCE(SUM(cost_total), 0) AS cost_total,
                       COALESCE(SUM(cost_cache), 0) AS cost_cache
                FROM usage_buckets {where}
                GROUP BY model
                """,
                params,
            ).fetchall()
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            tokens_in = int(row["tokens_in"] or 0)
            cache_read = int(row["cache_read"] or 0)
            result[row["model"]] = {
                "tokens_in": tokens_in,
                "tokens_out": int(row["tokens_out"] or 0),
                "cache_read": cache_read,
                "cache_write": int(row["cache_write"] or 0),
                "cache_miss": max(0, tokens_in - cache_read),
                "hit_rate": (cache_read / tokens_in * 100.0) if tokens_in else 0.0,
                "cost_total": float(row["cost_total"] or 0.0),
                "cost_cache": float(row["cost_cache"] or 0.0),
            }
        return result

    def bucket_bounds(self) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c, MIN(bucket_ms) AS oldest, MAX(bucket_ms) AS newest "
                "FROM usage_buckets"
            ).fetchone()
        return {
            "count": int(row["c"] or 0),
            "oldest_ms": int(row["oldest"] or 0),
            "newest_ms": int(row["newest"] or 0),
        }

    def bucket_totals(self, range_key: str = "today") -> dict[str, Any]:
        """上游聚合口径的窗口总计 (比本地明细样本更完整)."""
        since = self.usage_range_ms(range_key)
        where = "WHERE bucket_ms >= ?" if since is not None else ""
        params: tuple[Any, ...] = (since,) if since is not None else ()
        with self._lock:
            row = self._conn.execute(
                f"""
                SELECT COALESCE(SUM(requests), 0) AS requests,
                       COALESCE(SUM(tokens_in), 0) AS tokens_in,
                       COALESCE(SUM(tokens_out), 0) AS tokens_out,
                       COALESCE(SUM(cache_read_tokens), 0) AS cache_read,
                       COALESCE(SUM(cache_write_tokens), 0) AS cache_write,
                       COALESCE(SUM(cost_total), 0) AS cost_total,
                       COALESCE(SUM(cost_cache), 0) AS cost_cache,
                       COALESCE(SUM(cache_savings), 0) AS cache_savings
                FROM usage_buckets {where}
                """,
                params,
            ).fetchone()
        requests = int(row["requests"] or 0)
        tokens_in = int(row["tokens_in"] or 0)
        tokens_out = int(row["tokens_out"] or 0)
        cache_read = int(row["cache_read"] or 0)
        cost_total = float(row["cost_total"] or 0.0)
        return {
            "requests": requests,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "tokens_total": tokens_in + tokens_out,
            "cache_read": cache_read,
            "cache_write": int(row["cache_write"] or 0),
            "cache_miss": max(0, tokens_in - cache_read),
            "hit_rate": (cache_read / tokens_in * 100.0) if tokens_in else 0.0,
            "cost_total": cost_total,
            "cost_cache": float(row["cost_cache"] or 0.0),
            "cache_savings": float(row["cache_savings"] or 0.0),
            "average_cost": (cost_total / requests) if requests else 0.0,
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def get_settings(self) -> dict[str, Any]:
        with self._lock:
            rows = self._conn.execute("SELECT key, value FROM settings").fetchall()
        result = dict(DEFAULT_SETTINGS)
        for row in rows:
            try:
                result[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                result[row["key"]] = row["value"]
        return result

    def save_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        allowed = set(DEFAULT_SETTINGS)
        with self._lock:
            for key, value in values.items():
                if key not in allowed:
                    continue
                self._conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, json.dumps(value)),
                )
            self._conn.commit()
        return self.get_settings()

    def save_snapshot(self, snapshot: dict[str, Any]) -> None:
        windows = snapshot.get("windows") or {}
        five = windows.get("five_hour") or {}
        weekly = windows.get("weekly") or {}
        monthly = windows.get("monthly") or {}
        usage = snapshot.get("usage") or {}
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO snapshots (
                    fetched_at, payload,
                    five_used, five_cap, five_remaining_pct,
                    weekly_used, weekly_cap, weekly_remaining_pct,
                    monthly_remaining, monthly_used, monthly_cap, monthly_remaining_pct,
                    total_cost, total_count, tokens_in, tokens_out
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(snapshot.get("fetched_at") or time.time() * 1000),
                    json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
                    five.get("used"),
                    five.get("cap"),
                    five.get("remaining_pct"),
                    weekly.get("used"),
                    weekly.get("cap"),
                    weekly.get("remaining_pct"),
                    monthly.get("remaining"),
                    monthly.get("used"),
                    monthly.get("cap"),
                    monthly.get("remaining_pct"),
                    usage.get("total_cost"),
                    usage.get("total_count"),
                    usage.get("tokens_in"),
                    usage.get("tokens_out"),
                ),
            )
            self._conn.commit()
        self.prune()

    def latest_snapshot(self) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM snapshots ORDER BY fetched_at DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["payload"])
        except json.JSONDecodeError:
            return None

    def history(self, *, hours: int = 24, limit: int = 480) -> list[dict[str, Any]]:
        since = int((time.time() - max(1, hours) * 3600) * 1000)
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT fetched_at, five_used, five_cap, five_remaining_pct,
                       weekly_used, weekly_cap, weekly_remaining_pct,
                       monthly_remaining, monthly_used, monthly_cap, monthly_remaining_pct,
                       total_cost, total_count, tokens_in, tokens_out
                FROM snapshots
                WHERE fetched_at >= ?
                ORDER BY fetched_at ASC
                """,
                (since,),
            ).fetchall()
        points = [dict(row) for row in rows]
        if len(points) > limit:
            step = max(1, len(points) // limit)
            sampled = points[::step]
            if sampled[-1] != points[-1]:
                sampled.append(points[-1])
            points = sampled
        return points

    def recent(self, limit: int = 12) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM snapshots ORDER BY fetched_at DESC LIMIT ?",
                (max(1, min(limit, 50)),),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                result.append(json.loads(row["payload"]))
            except json.JSONDecodeError:
                continue
        return result

    def prune(self) -> None:
        retention = int(self.get_settings().get("history_retention_days") or 30)
        cutoff = int((time.time() - max(1, retention) * 86400) * 1000)
        with self._lock:
            self._conn.execute("DELETE FROM snapshots WHERE fetched_at < ?", (cutoff,))
            self._conn.commit()
