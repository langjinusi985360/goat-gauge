"""Local HTTP server and live snapshot service."""

from __future__ import annotations

import json
import math
import mimetypes
import os
import threading
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from . import __version__
from .commandcode_api import (
    CommandCodeAPIError,
    fetch_browser_snapshot,
    fetch_snapshot,
    fetch_usage_charts,
    fetch_usage_records,
)
from .credentials import CredentialError, CredentialStore, detect_source, mask_key, validate_key
from .store import DEFAULT_SETTINGS, Store


def _now_ms() -> int:
    return int(time.time() * 1000)


DEMO_MODELS = (
    "deepseek/deepseek-v4.1-flash",
    "qwen/qwen3.8-max",
    "moonshotai/kimi-k3",
    "minimax/MiniMax-M2.7",
)


def _demo_usage_records(count: int = 120) -> list[dict[str, Any]]:
    import random

    rng = random.Random(20260911)
    now = time.time()
    records: list[dict[str, Any]] = []
    for index in range(count):
        created = now - index * rng.uniform(120, 900)
        stamps = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(created))
        model = DEMO_MODELS[index % len(DEMO_MODELS)]
        tokens_in = rng.randint(8_000, 900_000)
        tokens_out = rng.randint(200, 12_000)
        cost = round(tokens_in / 1_000_000 * rng.uniform(0.8, 3.2) + tokens_out / 1_000_000 * 12, 6)
        records.append(
            {
                "id": f"demo-{index}",
                "created_at": f"{stamps}.000Z",
                "model": model,
                "provider": "demo",
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost_total": cost,
                "cost_input": round(cost * 0.72, 6),
                "cost_output": round(cost * 0.18, 6),
                "cost_cache": round(cost * 0.10, 6),
                "duration_ms": rng.randint(2_000, 40_000),
                "status": "completed" if index % 37 else "failed",
                "entry_type": "api",
                "mode": "api",
                "trace_id": f"demo-trace-{index}",
            }
        )
    return records


def _demo_usage_buckets(count: int = 120) -> list[dict[str, Any]]:
    import random

    rng = random.Random(20260912)
    now = datetime.now(timezone.utc)
    buckets: list[dict[str, Any]] = []
    for index in range(count):
        bucket = now - timedelta(minutes=5 * index)
        model = DEMO_MODELS[index % len(DEMO_MODELS)]
        tokens_in = rng.randint(200_000, 1_200_000)
        cache_read = int(tokens_in * rng.uniform(0.80, 0.97))
        buckets.append(
            {
                "model": model,
                "provider": "demo",
                "time_bucket": bucket.strftime("%Y-%m-%d %H:%M:%S"),
                "requests": rng.randint(1, 9),
                "tokens_in": tokens_in,
                "tokens_out": rng.randint(200, 9_000),
                "tokens_total": tokens_in + rng.randint(200, 9_000),
                "cache_read_tokens": cache_read,
                "cache_write_tokens": rng.randint(0, 40_000),
                "cost_total": round(tokens_in / 1_000_000 * 2.4, 6),
                "cost_cache": round(tokens_in / 1_000_000 * 0.3, 6),
                "cache_savings": round(tokens_in / 1_000_000 * 6.0, 6),
            }
        )
    return buckets


def _demo_snapshot() -> dict[str, Any]:
    now = _now_ms()
    phase = (time.time() % 3600) / 3600
    five_remaining = 68.0 + math.sin(phase * math.tau) * 8
    weekly_remaining = 83.0 + math.cos(phase * math.tau) * 4
    monthly_remaining = 55.0 + math.sin(phase * math.tau * 0.6) * 5

    def window(
        kind: str,
        label: str,
        cap: float,
        remaining_pct: float,
        reset_at: int,
    ) -> dict[str, Any]:
        used = cap * (1 - remaining_pct / 100)
        return {
            "kind": kind,
            "label": label,
            "used": round(used, 2),
            "cap": cap,
            "remaining": round(cap - used, 2),
            "remaining_pct": round(remaining_pct, 1),
            "exceeded": False,
            "reset_at": reset_at,
            "available": True,
            "note": "",
        }

    return {
        "fetched_at": now,
        "account": {
            "name": "GOAT Demo",
            "user_name": "demo",
            "user_id": "demo-user",
            "org_id": "demo-org",
            "org_login": "demo",
            "masked_key": "cc-*****DEMO",
        },
        "plan": {
            "plan_id": "individual-goat",
            "name": "GOAT",
            "status": "active",
            "monthly_credits": 70,
            "current_period_start": now - 6 * 86400 * 1000,
            "current_period_end": now + 24 * 86400 * 1000,
            "cancel_at_period_end": False,
        },
        "credits": {
            "monthly_remaining": round(70 * monthly_remaining / 100, 2),
            "purchased": 10,
            "free": 5,
            "below_threshold": False,
            "credit_threshold": 10,
        },
        "windows": {
            "five_hour": window("five_hour", "5 小时", 14, five_remaining, now + 2 * 3600 * 1000),
            "weekly": window("weekly", "本周", 35, weekly_remaining, now + 5 * 86400 * 1000),
            "monthly": window(
                "monthly",
                "本月",
                70,
                monthly_remaining,
                now + 24 * 86400 * 1000,
            ),
        },
        "usage": {
            "total_count": 186,
            "total_cost": 31.42,
            "average_cost": 0.1689,
            "success_rate": 98.4,
            "completed_count": 183,
            "failed_count": 3,
            "tokens_in": 8_420_000,
            "tokens_out": 612_000,
            "total_credits": 31.42,
            "period_basis": "billing-period",
        },
        "alerts": [],
        "studio_url": "https://commandcode.ai/demo/settings/usage",
        "api_base": "demo",
    }


class GaugeService:
    def __init__(self, *, demo: bool = False) -> None:
        self.demo = demo
        base_store = Store()
        self.store = Store(base_store.data_dir / "demo.db") if demo else base_store
        if demo:
            base_store.close()
        self.credential_store = CredentialStore(self.store.data_dir)
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._stop_callback: Any = None
        self._open_browser_callback: Callable[[], Any] | None = None
        self._browser_session_reader: Callable[[], list[dict[str, Any]] | None] | None = None
        self._background_thread: threading.Thread | None = None
        self._api_key: str | None = None
        self._browser_cookie = ""
        self._browser_user_agent = "Mozilla/5.0"
        self._credential_mode = "none"
        self._key_source = "未配置"
        self._snapshot: dict[str, Any] | None = None
        self._last_error: str | None = None
        self._refreshing = False
        self._next_refresh_at = 0
        self._usage_synced_at = 0
        self._usage_synced_count = 0
        self._usage_error: str | None = None
        self._load_initial_credential()

    @property
    def data_dir(self) -> str:
        return str(self.store.data_dir)

    def _load_initial_credential(self) -> None:
        if self.demo:
            self._snapshot = _demo_snapshot()
            self._key_source = "演示数据"
            self._next_refresh_at = _now_ms() + 60_000
            return

        cached = self.store.latest_snapshot()
        with self._lock:
            self._snapshot = cached

        detected_key, detected_source = detect_source()
        if detected_key and (detected_source or "").startswith("环境变量"):
            self._api_key = detected_key
            self._credential_mode = "api_key"
            self._key_source = detected_source or "环境变量"
            return

        saved_key = self.credential_store.load()
        if saved_key:
            self._api_key = saved_key
            self._credential_mode = "api_key"
            self._key_source = "本地加密凭据"
            return

        if detected_key:
            self._api_key = detected_key
            self._credential_mode = "api_key"
            self._key_source = detected_source or "本地配置文件"

    def set_stop_callback(self, callback: Any) -> None:
        self._stop_callback = callback

    def set_open_browser_callback(self, callback: Callable[[], Any]) -> None:
        self._open_browser_callback = callback

    def set_browser_session_reader(
        self,
        callback: Callable[[], list[dict[str, Any]] | None],
    ) -> None:
        self._browser_session_reader = callback

    def configured(self) -> bool:
        return self.demo or bool(self._api_key) or bool(self._browser_cookie)

    def masked_key(self) -> str:
        if self.demo:
            return "cc-*****DEMO"
        if self._credential_mode == "browser":
            return "Chrome 登录态"
        return mask_key(self._api_key or "")

    def state(self) -> dict[str, Any]:
        settings = self.store.get_settings()
        with self._lock:
            synced_at = self._usage_synced_at
            synced_count = self._usage_synced_count
            usage_error = self._usage_error
        bounds = self.store.usage_record_bounds()
        bucket_bounds = self.store.bucket_bounds()
        with self._lock:
            return {
                "configured": self.configured(),
                "snapshot": self._snapshot,
                "meta": {
                    "demo": self.demo,
                    "refreshing": self._refreshing,
                    "last_error": self._last_error,
                    "next_refresh_at": self._next_refresh_at,
                    "key_source": self._key_source,
                    "masked_key": self.masked_key(),
                    "credential_mode": self._credential_mode,
                    "data_dir": self.data_dir,
                    "server_time": _now_ms(),
                    "api_base": settings.get("api_base", DEFAULT_SETTINGS["api_base"]),
                    "usage_synced_at": synced_at,
                    "usage_synced_count": synced_count,
                    "usage_error": usage_error,
                    "usage_records": bounds["count"],
                    "usage_oldest_ms": bounds["oldest_ms"],
                    "usage_newest_ms": bounds["newest_ms"],
                    "cache_buckets": bucket_bounds["count"],
                    "cache_oldest_ms": bucket_bounds["oldest_ms"],
                    "cache_newest_ms": bucket_bounds["newest_ms"],
                },
                "settings": settings,
            }

    def dashboard(self, range_key: str = "today") -> dict[str, Any]:
        valid = range_key if range_key in ("today", "7d", "30d", "all") else "today"
        record_totals = self.store.usage_totals(valid)
        bucket_totals = self.store.bucket_totals(valid)
        cache = self.store.cache_stats(valid)
        has_buckets = int(bucket_totals.get("requests") or 0) > 0

        # 聚合指标优先用上游聚合桶 (覆盖完整窗口); 无桶时回退本地明细
        totals = dict(bucket_totals if has_buckets else record_totals)
        totals["completed"] = record_totals.get("completed", 0)
        totals["failed"] = record_totals.get("failed", 0)
        totals["success_rate"] = record_totals.get("success_rate", 0.0)
        if not has_buckets:
            totals["cache_read"] = cache["cache_read"]
            totals["cache_write"] = cache["cache_write"]
            totals["cache_miss"] = cache["cache_miss"]
            totals["hit_rate"] = cache["hit_rate"]
            totals["cache_savings"] = cache["cache_savings"]
        totals["detail_records"] = record_totals.get("requests", 0)
        totals["source"] = "aggregate" if has_buckets else "records"

        models = self.store.model_stats(valid)
        cache_by_model = self.store.model_cache_stats(valid)
        for item in models:
            extra = cache_by_model.get(item["model"])
            if extra:
                item["cache_read"] = extra["cache_read"]
                item["cache_write"] = extra["cache_write"]
                item["cache_miss"] = extra["cache_miss"]
                item["hit_rate"] = extra["hit_rate"]
                if extra["tokens_in"]:
                    item["tokens_in"] = extra["tokens_in"]
                    item["tokens_out"] = extra["tokens_out"]
                    item["tokens_total"] = extra["tokens_in"] + extra["tokens_out"]
                if extra["cost_total"]:
                    item["cost_total"] = extra["cost_total"]
                    item["cost_cache"] = extra["cost_cache"]
            else:
                item["cache_read"] = 0
                item["cache_write"] = 0
                item["cache_miss"] = 0
                item["hit_rate"] = 0.0
        models.sort(key=lambda item: item.get("tokens_total", 0), reverse=True)

        return {
            "range": valid,
            "totals": totals,
            "cache": cache,
            "has_buckets": has_buckets,
            "models": models,
            "daily": self.store.daily_stats("30d" if valid == "today" else valid),
            "models_list": self.store.list_models(),
            "bounds": self.store.usage_record_bounds(),
            "cache_bounds": self.store.bucket_bounds(),
            "server_time": _now_ms(),
        }

    def usage_page(
        self,
        *,
        page: int,
        page_size: int,
        model: str | None,
        range_key: str,
    ) -> dict[str, Any]:
        records, total = self.store.usage_records_page(
            page=page,
            page_size=page_size,
            model=model,
            range_key=range_key,
        )
        return {
            "records": records,
            "total": total,
            "page": page,
            "page_size": page_size,
            "models": self.store.list_models(),
            "range": range_key,
        }

    def sync_usage_records(self) -> dict[str, Any]:
        """Pull the newest upstream usage records into the local store."""
        if self.demo:
            inserted = self.store.insert_usage_records(_demo_usage_records())
            bucket_count = self.store.insert_usage_buckets(_demo_usage_buckets())
            bounds = self.store.usage_record_bounds()
            with self._lock:
                self._usage_synced_at = _now_ms()
                self._usage_synced_count = bounds["count"]
                self._usage_error = None
            return {"ok": True, "inserted": inserted, "buckets": bucket_count, **bounds}

        with self._lock:
            cookie = self._browser_cookie
            user_agent = self._browser_user_agent
            mode = self._credential_mode
        if mode != "browser" or not cookie:
            return {"ok": False, "error": "usage-records-requires-browser-session"}

        settings = self.store.get_settings()
        try:
            records, meta = fetch_usage_records(
                cookie,
                user_agent=user_agent,
                api_base=str(settings.get("api_base") or DEFAULT_SETTINGS["api_base"]),
            )
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._usage_error = str(exc)
            raise

        inserted = self.store.insert_usage_records(records)
        bucket_count = 0
        try:
            buckets = fetch_usage_charts(
                cookie,
                user_agent=user_agent,
                api_base=str(settings.get("api_base") or DEFAULT_SETTINGS["api_base"]),
            )
            bucket_count = self.store.insert_usage_buckets(buckets)
        except Exception as exc:  # noqa: BLE001
            # 缓存桶是附加指标, 失败不应影响主记录同步
            with self._lock:
                self._usage_error = f"缓存同步失败: {exc}"
        bounds = self.store.usage_record_bounds()
        with self._lock:
            self._usage_synced_at = _now_ms()
            self._usage_synced_count = bounds["count"]
            if bucket_count or not self._usage_error:
                self._usage_error = None
        return {
            "ok": True,
            "inserted": inserted,
            "buckets": bucket_count,
            "fetched": meta.get("fetched", 0),
            "window": meta.get("window", {}),
            **bounds,
        }

    def history(self, *, hours: int = 24, limit: int = 480) -> dict[str, Any]:
        return {
            "points": self.store.history(hours=hours, limit=limit),
            "recent": self.store.recent(10),
        }

    def save_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        clean: dict[str, Any] = {}
        if "refresh_seconds" in values:
            try:
                clean["refresh_seconds"] = max(
                    30, min(3600, int(values["refresh_seconds"]))
                )
            except (TypeError, ValueError):
                pass
        if "history_retention_days" in values:
            try:
                clean["history_retention_days"] = max(
                    1, min(365, int(values["history_retention_days"]))
                )
            except (TypeError, ValueError):
                pass
        if "api_base" in values:
            text = str(values["api_base"]).strip().rstrip("/")
            if text.startswith("https://") or text.startswith("http://"):
                clean["api_base"] = text
        return self.store.save_settings(clean)

    def set_key(self, api_key: str, *, remember: bool = True) -> dict[str, Any]:
        key = validate_key(api_key)
        settings = self.store.get_settings()
        snapshot = fetch_snapshot(
            key,
            api_base=str(settings.get("api_base") or DEFAULT_SETTINGS["api_base"]),
        )
        if remember:
            self.credential_store.save(key)
        else:
            self.credential_store.delete()
        with self._lock:
            self._api_key = key
            self._credential_mode = "api_key"
            self._key_source = "本地加密凭据" if remember else "本次运行"
            self._snapshot = snapshot.to_dict()
            self._last_error = None
            self._next_refresh_at = _now_ms() + int(settings["refresh_seconds"]) * 1000
        self.store.save_snapshot(snapshot.to_dict())
        return self.state()

    def clear_key(self) -> dict[str, Any]:
        self.credential_store.delete()
        with self._lock:
            self._api_key = None
            self._browser_cookie = ""
            self._credential_mode = "none"
            self._key_source = "未配置"
            self._last_error = None
            self._next_refresh_at = 0
        return self.state()

    def detect_credentials(self) -> dict[str, Any]:
        key, source = detect_source()
        if key:
            return self.set_key(key, remember=False)
        if self._browser_session_reader:
            cookies = self._browser_session_reader()
            if cookies:
                return self.set_browser_session(cookies)
        raise CredentialError("没有检测到 API Key 或 Chrome 登录态。")

    def set_browser_session(
        self,
        cookies: list[dict[str, Any]],
        *,
        user_agent: str = "Mozilla/5.0",
    ) -> dict[str, Any]:
        pairs: list[str] = []
        for cookie in cookies:
            name = str(cookie.get("name") or "").strip()
            value = str(cookie.get("value") or "")
            if not name or not value:
                continue
            domain = str(cookie.get("domain") or "")
            if "commandcode.ai" not in domain:
                continue
            pairs.append(f"{name}={value}")
        if not pairs:
            raise CredentialError("Chrome 当前没有可用的 Command Code 登录 Cookie。")
        cookie_header = "; ".join(dict.fromkeys(pairs))
        settings = self.store.get_settings()
        snapshot = fetch_browser_snapshot(
            cookie_header,
            user_agent=user_agent or "Mozilla/5.0",
            api_base=str(settings.get("api_base") or DEFAULT_SETTINGS["api_base"]),
        )
        payload = snapshot.to_dict()
        with self._lock:
            self._browser_cookie = cookie_header
            self._browser_user_agent = user_agent or "Mozilla/5.0"
            self._credential_mode = "browser"
            self._key_source = "本机 Chrome 登录态"
            self._snapshot = payload
            self._last_error = None
            self._next_refresh_at = _now_ms() + int(settings["refresh_seconds"]) * 1000
        self.store.save_snapshot(payload)
        try:
            self.sync_usage_records()
        except Exception:
            pass
        return self.state()

    def _wait_for_refresh(self) -> None:
        while True:
            with self._lock:
                refreshing = self._refreshing
            if not refreshing:
                return
            time.sleep(0.05)

    def refresh(self, *, force: bool = False) -> dict[str, Any]:
        if self.demo:
            snapshot = _demo_snapshot()
            with self._lock:
                self._snapshot = snapshot
                self._last_error = None
                self._next_refresh_at = _now_ms() + int(
                    self.store.get_settings()["refresh_seconds"]
                ) * 1000
            self.store.save_snapshot(snapshot)
            return self.state()

        wait_for_active = False
        with self._lock:
            if self._refreshing:
                wait_for_active = True
            elif self._credential_mode == "browser" and not self._browser_cookie:
                raise CredentialError("Chrome 登录态不可用，请重新打开浏览器。")
            elif self._credential_mode != "browser" and not self._api_key:
                raise CredentialError("尚未配置 Command Code API Key。")
            else:
                self._refreshing = True

        if wait_for_active:
            self._wait_for_refresh()
            return self.state()

        try:
            settings = self.store.get_settings()
            snapshot = fetch_snapshot(
                self._api_key or "",
                api_base=str(settings.get("api_base") or DEFAULT_SETTINGS["api_base"]),
            ) if self._credential_mode != "browser" else fetch_browser_snapshot(
                self._browser_cookie,
                user_agent=self._browser_user_agent,
                api_base=str(settings.get("api_base") or DEFAULT_SETTINGS["api_base"]),
            )
            payload = snapshot.to_dict()
            with self._lock:
                self._snapshot = payload
                self._last_error = None
            self.store.save_snapshot(payload)
            try:
                self.sync_usage_records()
            except Exception:
                pass
            return self.state()
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._last_error = str(exc)
            raise
        finally:
            with self._lock:
                self._refreshing = False
                interval = int(self.store.get_settings()["refresh_seconds"])
                self._next_refresh_at = _now_ms() + interval * 1000

    def ensure_fresh(self) -> None:
        if not self.configured():
            return
        settings = self.store.get_settings()
        interval_ms = int(settings["refresh_seconds"]) * 1000
        with self._lock:
            fetched_at = int((self._snapshot or {}).get("fetched_at") or 0)
            stale = _now_ms() - fetched_at >= interval_ms
            refreshing = self._refreshing
        if stale and not refreshing:
            threading.Thread(
                target=self._refresh_safely,
                daemon=True,
                name="goat-gauge-refresh",
            ).start()

    def _refresh_safely(self) -> None:
        try:
            self.refresh(force=True)
        except Exception:
            pass

    def start_background(self) -> None:
        if self._background_thread and self._background_thread.is_alive():
            return

        def loop() -> None:
            if self.configured():
                self._refresh_safely()
            while not self._stop_event.wait(5.0):
                self.ensure_fresh()

        self._background_thread = threading.Thread(
            target=loop,
            daemon=True,
            name="goat-gauge-background",
        )
        self._background_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._background_thread
        if thread and thread.is_alive():
            thread.join(timeout=2)
        self.store.close()

    def request_stop(self) -> None:
        if self._stop_callback:
            self._stop_callback()


class GaugeHTTPServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], service: GaugeService) -> None:
        super().__init__(address, GaugeHandler)
        self.service = service


def _resource_path(relative: str) -> Path:
    return Path(__file__).resolve().parent / "web" / relative


def _json_response(
    handler: BaseHTTPRequestHandler,
    data: Any,
    *,
    status: int = 200,
) -> None:
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _static_response(handler: BaseHTTPRequestHandler, relative: str) -> None:
    relative = relative.lstrip("/") or "index.html"
    parts = Path(relative).parts
    if ".." in parts:
        handler.send_error(403)
        return
    path = _resource_path(relative)
    if not path.is_file():
        handler.send_error(404)
        return
    content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    body = path.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-cache")
    handler.end_headers()
    handler.wfile.write(body)


def _read_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        return {}
    if length <= 0 or length > 1_000_000:
        return {}
    try:
        value = json.loads(handler.rfile.read(length).decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


class GaugeHandler(BaseHTTPRequestHandler):
    server_version = "GOATGauge/0.1"

    @property
    def service(self) -> GaugeService:
        return self.server.service  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path.startswith("/api/"):
            self._handle_api("GET", parsed.path, query)
            return
        _static_response(self, "index.html" if parsed.path == "/" else parsed.path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        self._handle_api("POST", parsed.path, parse_qs(parsed.query))

    def do_PUT(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        self._handle_api("PUT", parsed.path, parse_qs(parsed.query))

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        self._handle_api("DELETE", parsed.path, parse_qs(parsed.query))

    def _handle_api(
        self,
        method: str,
        path: str,
        query: dict[str, list[str]],
    ) -> None:
        try:
            self._dispatch_api(method, path, query)
        except CredentialError as exc:
            _json_response(self, {"ok": False, "error": str(exc)}, status=400)
        except CommandCodeAPIError as exc:
            _json_response(
                self,
                {"ok": False, "error": str(exc), "code": exc.code, "status": exc.status},
                status=exc.status if exc.status in (400, 401, 403, 429) else 502,
            )
        except Exception as exc:  # noqa: BLE001
            _json_response(self, {"ok": False, "error": str(exc)}, status=500)

    def _dispatch_api(
        self,
        method: str,
        path: str,
        query: dict[str, list[str]],
    ) -> None:
        if path == "/api/version" and method == "GET":
            _json_response(self, {"version": __version__})
            return

        if path == "/api/state" and method == "GET":
            _json_response(self, self.service.state())
            return

        if path == "/api/snapshot" and method == "GET":
            _json_response(self, self.service.state())
            return

        if path == "/api/history" and method == "GET":
            try:
                hours = max(1, min(168, int(query.get("hours", ["24"])[0])))
            except ValueError:
                hours = 24
            _json_response(self, self.service.history(hours=hours))
            return

        if path == "/api/dashboard" and method == "GET":
            range_key = (query.get("range") or ["today"])[0]
            _json_response(self, self.service.dashboard(range_key))
            return

        if path == "/api/usage/records" and method == "GET":
            try:
                page = max(1, int(query.get("page", ["1"])[0]))
            except ValueError:
                page = 1
            try:
                page_size = max(1, min(200, int(query.get("page_size", ["20"])[0])))
            except ValueError:
                page_size = 20
            model = (query.get("model") or [""])[0] or None
            range_key = (query.get("range") or ["today"])[0]
            _json_response(
                self,
                self.service.usage_page(
                    page=page,
                    page_size=page_size,
                    model=model,
                    range_key=range_key,
                ),
            )
            return

        if path == "/api/usage/sync" and method == "POST":
            result = self.service.sync_usage_records()
            _json_response(self, {"ok": True, **result})
            return

        if path == "/api/refresh" and method == "POST":
            state = self.service.refresh(force=True)
            _json_response(self, {"ok": True, **state})
            return

        if path == "/api/credentials" and method == "POST":
            body = _read_body(self)
            key = str(body.get("api_key") or "")
            remember = body.get("remember", True) is not False
            state = self.service.set_key(key, remember=remember)
            _json_response(self, {"ok": True, **state})
            return

        if path == "/api/credentials" and method == "DELETE":
            _json_response(self, {"ok": True, **self.service.clear_key()})
            return

        if path == "/api/credentials/detect" and method == "POST":
            state = self.service.detect_credentials()
            _json_response(self, {"ok": True, **state})
            return

        if path == "/api/browser-session" and method == "POST":
            body = _read_body(self)
            cookies = body.get("cookies")
            if not isinstance(cookies, list):
                raise CredentialError("浏览器会话数据格式不正确。")
            state = self.service.set_browser_session(
                cookies,
                user_agent=str(body.get("user_agent") or "Mozilla/5.0"),
            )
            _json_response(self, {"ok": True, **state})
            return

        if path == "/api/settings" and method == "GET":
            _json_response(self, self.service.store.get_settings())
            return

        if path == "/api/settings" and method == "PUT":
            body = _read_body(self)
            _json_response(self, self.service.save_settings(body))
            return

        if path == "/api/open-usage" and method == "POST":
            snapshot = self.service.state().get("snapshot") or {}
            url = str(snapshot.get("studio_url") or "https://commandcode.ai/settings/usage")
            webbrowser.open(url)
            _json_response(self, {"ok": True, "url": url})
            return

        if path == "/api/open-login" and method == "POST":
            if self.service._open_browser_callback:
                self.service._open_browser_callback()
            _json_response(self, {"ok": True})
            return

        if path == "/api/quit" and method == "POST":
            _json_response(self, {"ok": True})
            threading.Thread(
                target=self.service.request_stop,
                daemon=True,
                name="goat-gauge-quit",
            ).start()
            return

        _json_response(self, {"ok": False, "error": "not found"}, status=404)


_server: GaugeHTTPServer | None = None
_server_thread: threading.Thread | None = None


def start_server(
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    service: GaugeService,
) -> tuple[str, int]:
    global _server, _server_thread
    _server = GaugeHTTPServer((host, port), service)
    _server_thread = threading.Thread(
        target=_server.serve_forever,
        daemon=True,
        name="goat-gauge-http",
    )
    _server_thread.start()
    bound_host, bound_port = _server.server_address[:2]
    return str(bound_host), int(bound_port)


def stop_server() -> None:
    global _server, _server_thread
    if _server:
        _server.shutdown()
        _server.server_close()
        _server = None
    if _server_thread and _server_thread.is_alive():
        _server_thread.join(timeout=2)
        _server_thread = None


def run_foreground(*, host: str, port: int, demo: bool) -> None:
    service = GaugeService(demo=demo)
    bound_host, bound_port = start_server(host=host, port=port, service=service)
    service.start_background()
    print(f"GOAT Gauge: http://{bound_host}:{bound_port}/", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stop_server()
        service.stop()
