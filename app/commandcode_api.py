"""Command Code account and usage API client."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .credentials import mask_key


API_BASE = "https://api.commandcode.ai"

INTERNAL_CREDITS_PATH = "/internal/billing/credits"
INTERNAL_SUBSCRIPTIONS_PATH = "/internal/billing/subscriptions"
INTERNAL_USAGE_SUMMARY_PATH = "/internal/usage/summary"
INTERNAL_USAGE_PATH = "/internal/usage"
INTERNAL_USAGE_CHARTS_PATH = "/internal/usage/charts"
USAGE_PAGE_LIMIT = 25
USAGE_MAX_RECORDS = 200
USAGE_CHART_WINDOW_HOURS = 48

KNOWN_PLANS: dict[str, tuple[str, float]] = {
    "individual-go": ("Go", 10),
    "individual-goat": ("GOAT", 70),
    "individual-pro": ("Pro", 30),
    "individual-pro-v1": ("Pro", 80),
    "individual-provider": ("Provider", 15),
    "individual-max": ("Max", 150),
    "individual-ultra": ("Ultra", 300),
    "teams-pro": ("Teams Pro", 40),
}


class CommandCodeAPIError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, code: str = "api") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass
class QuotaWindow:
    kind: str
    label: str
    used: float | None
    cap: float | None
    remaining: float | None
    remaining_pct: float | None
    exceeded: bool
    reset_at: int
    available: bool
    note: str = ""


@dataclass
class Snapshot:
    fetched_at: int
    account: dict[str, Any]
    plan: dict[str, Any]
    credits: dict[str, Any]
    windows: dict[str, dict[str, Any]]
    usage: dict[str, Any]
    alerts: list[dict[str, str]] = field(default_factory=list)
    studio_url: str = "https://commandcode.ai/settings/usage"
    api_base: str = API_BASE

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _int(value: Any) -> int | None:
    number = _num(value)
    return None if number is None else int(number)


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _epoch_ms(value: Any) -> int:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        return int(number * 1000) if number < 100_000_000_000 else int(number)
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.isdigit():
            return _epoch_ms(int(text))
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return int(parsed.timestamp() * 1000)
        except ValueError:
            return 0
    return 0


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_object(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict):
            return value
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value[0]
    return {}


def _pick(mapping: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    return None


def plan_info(plan_id: str) -> tuple[str, float] | None:
    normalized = (plan_id or "").lower().replace("_", "-")
    for prefix in sorted(KNOWN_PLANS, key=len, reverse=True):
        if normalized.startswith(prefix):
            return KNOWN_PLANS[prefix]
    return None


def _normalize_window(
    raw: Any,
    *,
    kind: str,
    label: str,
    limited: bool,
) -> QuotaWindow:
    source = _object(raw)
    if not source:
        return QuotaWindow(
            kind=kind,
            label=label,
            used=None,
            cap=None,
            remaining=None,
            remaining_pct=None,
            exceeded=False,
            reset_at=0,
            available=False,
            note="当前套餐不受此窗口限制" if not limited else "服务端暂未返回该窗口",
        )

    used = _num(_pick(source, ("used", "usage", "usedCredits", "used_credits")))
    cap = _num(_pick(source, ("cap", "limit", "capCredits", "cap_credits")))
    exceeded_value = _pick(source, ("exceeded", "isExceeded", "is_exceeded"))
    exceeded = _bool(exceeded_value) or (
        used is not None and cap is not None and cap > 0 and used >= cap
    )
    reset_at = _epoch_ms(_pick(source, ("resetAt", "reset_at", "resetsAt", "resets_at")))
    available = limited and used is not None and cap is not None and cap > 0
    if not available:
        note = "当前套餐不受此窗口限制" if not limited else "服务端暂未返回完整额度"
    else:
        note = ""

    remaining = max(float(cap) - float(used), 0.0) if available else None
    remaining_pct = (
        max(0.0, min(100.0, (remaining / float(cap)) * 100.0))
        if remaining is not None and cap
        else None
    )
    return QuotaWindow(
        kind=kind,
        label=label,
        used=float(used) if used is not None else None,
        cap=float(cap) if cap is not None else None,
        remaining=remaining,
        remaining_pct=remaining_pct,
        exceeded=exceeded,
        reset_at=reset_at,
        available=available,
        note=note,
    )


def _normalize_usage(raw: Any) -> dict[str, Any]:
    root = _object(raw)
    source = _object(root.get("data")) or root
    success_rate = _num(_pick(source, ("successRate", "success_rate")))
    if success_rate is not None and success_rate <= 1:
        success_rate *= 100
    return {
        "total_count": _int(_pick(source, ("totalCount", "total_count"))) or 0,
        "total_cost": _num(_pick(source, ("totalCost", "total_cost"))) or 0.0,
        "average_cost": _num(_pick(source, ("averageCost", "average_cost"))),
        "success_rate": success_rate or 0.0,
        "completed_count": _int(_pick(source, ("completedCount", "completed_count"))) or 0,
        "failed_count": _int(_pick(source, ("failedCount", "failed_count"))) or 0,
        "tokens_in": _int(_pick(source, ("totalTokensIn", "total_tokens_in"))) or 0,
        "tokens_out": _int(_pick(source, ("totalTokensOut", "total_tokens_out"))) or 0,
        "total_credits": _num(_pick(source, ("totalCredits", "total_credits"))) or 0.0,
        "period_basis": _string(_pick(source, ("periodBasis", "period_basis"))),
    }


def _normalize_credits(raw: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    root = _object(raw)
    source = _object(root.get("data")) or root
    credits = _object(source.get("credits"))
    limits = _object(source.get("windowLimits")) or _object(source.get("window_limits"))
    return credits, limits


def _normalize_subscription(raw: Any) -> dict[str, Any]:
    root = _object(raw)
    source = _first_object(root.get("data"), root.get("subscription"), root)
    return source


def _request_json(
    url: str,
    *,
    api_key: str = "",
    cookie_header: str = "",
    user_agent: str = "GOATGauge/0.1",
    timeout: float = 12.0,
) -> dict[str, Any]:
    headers = {
        "Accept": "application/json",
        "User-Agent": user_agent,
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if cookie_header:
        headers["Cookie"] = cookie_header
        headers["Origin"] = "https://commandcode.ai"
        headers["Referer"] = "https://commandcode.ai/"
    request = urllib.request.Request(
        url,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        message = {
            401: "API Key 无效或已过期。",
            403: "当前 API Key 无权读取 Command Code 用量。",
            429: "Command Code 暂时限制了查询，请稍后重试。",
        }.get(exc.code, f"Command Code 请求失败（HTTP {exc.code}）。")
        raise CommandCodeAPIError(message, status=exc.code) from exc
    except urllib.error.URLError as exc:
        raise CommandCodeAPIError(f"无法连接 Command Code：{exc.reason}", code="network") from exc
    except TimeoutError as exc:
        raise CommandCodeAPIError("连接 Command Code 超时。", code="timeout") from exc

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise CommandCodeAPIError("Command Code 返回了无法识别的数据。", code="parse") from exc
    if not isinstance(parsed, dict):
        raise CommandCodeAPIError("Command Code 返回结构异常。", code="parse")
    return parsed


def _normalize_usage_record(raw: Any) -> dict[str, Any] | None:
    """Convert one /internal/usage entry into a flat local record."""
    source = _object(raw)
    record_id = _string(source.get("id"))
    created_at = _string(source.get("createdAt"))
    if not record_id or not created_at:
        return None
    meta = _object(source.get("meta"))
    return {
        "id": record_id,
        "created_at": created_at,
        "model": _string(meta.get("model")) or "unknown",
        "provider": "",
        "tokens_in": _int(source.get("tokensIn")) or 0,
        "tokens_out": _int(source.get("tokensOut")) or 0,
        "cost_total": _num(meta.get("totalCost")) or 0.0,
        "cost_input": _num(meta.get("inputCost")) or 0.0,
        "cost_output": _num(meta.get("outputCost")) or 0.0,
        "cost_cache": _num(meta.get("cacheCost")) or 0.0,
        "duration_ms": _int(source.get("durationTotal")) or 0,
        "status": _string(source.get("status")),
        "entry_type": _string(source.get("type")),
        "mode": _string(source.get("mode")),
        "trace_id": _string(meta.get("traceId")),
    }


def fetch_usage_records(
    cookie_header: str,
    *,
    user_agent: str = "Mozilla/5.0",
    api_base: str = API_BASE,
    max_records: int = USAGE_MAX_RECORDS,
    timeout: float = 15.0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch the newest usage records available to the signed-in account.

    The upstream endpoint exposes a bounded plan window (currently 1 day /
    100 entries), so this returns the newest records it can page through.
    Longer ranges are assembled locally over time.
    """
    base = (api_base or API_BASE).rstrip("/")
    collected: dict[str, dict[str, Any]] = {}
    cursor = ""
    window: dict[str, Any] = {}
    period_basis = ""

    while len(collected) < max_records:
        query = f"?limit={USAGE_PAGE_LIMIT}"
        if cursor:
            query += f"&cursor={urllib.parse.quote(cursor)}"
        payload = _request_json(
            f"{base}{INTERNAL_USAGE_PATH}{query}",
            cookie_header=cookie_header,
            user_agent=user_agent,
            timeout=timeout,
        )
        window = _object(payload.get("window")) or window
        period_basis = _string(payload.get("periodBasis")) or period_basis
        rows = payload.get("usages")
        if not isinstance(rows, list) or not rows:
            break
        for raw in rows:
            record = _normalize_usage_record(raw)
            if record:
                collected[record["id"]] = record
        next_cursor = _string(payload.get("nextCursor"))
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor

    records = sorted(
        collected.values(),
        key=lambda item: str(item.get("created_at") or ""),
        reverse=True,
    )[:max_records]
    return records, {
        "window": window,
        "period_basis": period_basis,
        "fetched": len(records),
    }


def _normalize_chart_bucket(raw: Any) -> dict[str, Any] | None:
    """Convert one /internal/usage/charts row into a flat cache bucket."""
    source = _object(raw)
    model = _string(source.get("model"))
    bucket = _string(source.get("timeBucket"))
    if not model or not bucket:
        return None
    return {
        "model": model,
        "provider": _string(source.get("provider")),
        "time_bucket": bucket,
        "requests": _int(source.get("requests")) or 0,
        "tokens_in": _int(_pick(source, ("tokensIn", "tokens_in"))) or 0,
        "tokens_out": _int(_pick(source, ("tokensOut", "tokens_out"))) or 0,
        "tokens_total": _int(_pick(source, ("tokensTotal", "tokens_total"))) or 0,
        "cache_read_tokens": _int(
            _pick(source, ("cacheReadInputTokens", "cache_read_input_tokens"))
        )
        or 0,
        "cache_write_tokens": _int(
            _pick(source, ("cacheCreationInputTokens", "cache_creation_input_tokens"))
        )
        or 0,
        "cost_total": _num(_pick(source, ("totalCost", "total_cost"))) or 0.0,
        "cost_cache": _num(_pick(source, ("cacheCost", "cache_cost"))) or 0.0,
        "cache_savings": _num(_pick(source, ("cacheSavings", "cache_savings"))) or 0.0,
    }


def fetch_usage_charts(
    cookie_header: str,
    *,
    user_agent: str = "Mozilla/5.0",
    api_base: str = API_BASE,
    hours: int = USAGE_CHART_WINDOW_HOURS,
    timeout: float = 20.0,
) -> list[dict[str, Any]]:
    """Fetch per-model time buckets, which carry cache read/write tokens.

    The upstream endpoint accepts an explicit ``from``/``to`` window and is
    capped to roughly one day of history, so this asks for the widest window
    it will honour and lets the local store accumulate buckets over time.
    """
    base = (api_base or API_BASE).rstrip("/")
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=max(1, hours))
    query = urllib.parse.urlencode(
        {
            "from": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    )
    payload = _request_json(
        f"{base}{INTERNAL_USAGE_CHARTS_PATH}?{query}",
        cookie_header=cookie_header,
        user_agent=user_agent,
        timeout=timeout,
    )
    rows = payload.get("data")
    if not isinstance(rows, list):
        return []
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in rows:
        bucket = _normalize_chart_bucket(raw)
        if bucket:
            buckets[(bucket["model"], bucket["time_bucket"])] = bucket
    return sorted(buckets.values(), key=lambda item: (item["time_bucket"], item["model"]))


def fetch_snapshot(
    api_key: str,
    *,
    api_base: str = API_BASE,
    timeout: float = 12.0,
) -> Snapshot:
    base = (api_base or API_BASE).rstrip("/")

    whoami = _request_json(f"{base}/alpha/whoami", api_key=api_key, timeout=timeout)
    user = _object(whoami.get("user"))
    org = _object(whoami.get("org"))
    org_id = _string(org.get("id"))
    query = f"?orgId={urllib.parse.quote(org_id)}" if org_id else ""

    credits_root = _request_json(
        f"{base}/alpha/billing/credits",
        api_key=api_key,
        timeout=timeout,
    )
    subscription_root = _request_json(
        f"{base}/alpha/billing/subscriptions{query}",
        api_key=api_key,
        timeout=timeout,
    )

    summary_root: dict[str, Any] = {}
    try:
        summary_root = _request_json(
            f"{base}/alpha/usage/summary",
            api_key=api_key,
            timeout=timeout,
        )
    except CommandCodeAPIError as exc:
        if exc.status in (401, 403):
            raise

    credits_raw, limits = _normalize_credits(credits_root)
    subscription = _normalize_subscription(subscription_root)
    plan_id = (
        _string(_pick(subscription, ("planId", "plan_id")))
        or _string(_pick(credits_raw, ("planId", "plan_id")))
    )
    known_plan = plan_info(plan_id)
    plan_name = known_plan[0] if known_plan else (plan_id or "未知套餐")
    monthly_cap = known_plan[1] if known_plan else None

    monthly_remaining = _num(_pick(credits_raw, ("monthlyCredits", "monthly_credits")))
    purchased = _num(_pick(credits_raw, ("purchasedCredits", "purchased_credits"))) or 0.0
    free = _num(_pick(credits_raw, ("freeCredits", "free_credits"))) or 0.0
    limited = limits.get("limited")
    window_limited = True if limited is None else _bool(limited)

    five_hour_raw = _pick(limits, ("fiveHour", "five_hour", "rolling5h", "5h"))
    weekly_raw = _pick(limits, ("weekly", "week"))
    five_hour = _normalize_window(
        five_hour_raw,
        kind="five_hour",
        label="5 小时",
        limited=window_limited,
    )
    weekly = _normalize_window(
        weekly_raw,
        kind="weekly",
        label="本周",
        limited=window_limited,
    )

    period_end = _epoch_ms(
        _pick(subscription, ("currentPeriodEnd", "current_period_end"))
    )
    if monthly_cap is not None and monthly_remaining is not None:
        monthly_used = max(0.0, min(float(monthly_cap), float(monthly_cap) - monthly_remaining))
        monthly = QuotaWindow(
            kind="monthly",
            label="本月",
            used=monthly_used,
            cap=float(monthly_cap),
            remaining=max(0.0, float(monthly_remaining)),
            remaining_pct=max(
                0.0,
                min(100.0, (float(monthly_remaining) / float(monthly_cap)) * 100.0),
            ),
            exceeded=monthly_used >= float(monthly_cap),
            reset_at=period_end,
            available=True,
        )
    else:
        note = (
            f"剩余 {monthly_remaining:g} credits；未知套餐不推算上限"
            if monthly_remaining is not None
            else "服务端暂未返回月余额"
        )
        monthly = QuotaWindow(
            kind="monthly",
            label="本月",
            used=None,
            cap=None,
            remaining=monthly_remaining,
            remaining_pct=None,
            exceeded=False,
            reset_at=period_end,
            available=False,
            note=note,
        )

    account_name = (
        _string(user.get("userName"))
        or _string(user.get("username"))
        or _string(user.get("name"))
        or _string(org.get("login"))
        or "Command Code"
    )
    studio_slug = _string(org.get("login")) or _string(user.get("userName")) or ""
    studio_url = (
        f"https://commandcode.ai/{urllib.parse.quote(studio_slug)}/settings/usage"
        if studio_slug
        else "https://commandcode.ai/settings/usage"
    )

    alerts: list[dict[str, str]] = []
    exceeded = _string(limits.get("exceeded"))
    if exceeded:
        alerts.append({"level": "danger", "message": f"当前受限窗口：{exceeded}"})
    if five_hour.exceeded:
        alerts.append({"level": "danger", "message": "5 小时额度已用尽，请等待窗口重置。"})
    if weekly.exceeded:
        alerts.append({"level": "danger", "message": "本周额度已用尽，请等待周额度重置。"})
    if monthly.exceeded:
        alerts.append({"level": "danger", "message": "本月额度已用尽。"})
    if _bool(_pick(credits_raw, ("belowThreshold", "below_threshold"))):
        alerts.append({"level": "warning", "message": "账户余额已低于预警阈值。"})
    if _bool(_pick(subscription, ("cancelAtPeriodEnd", "cancel_at_period_end"))):
        alerts.append({"level": "warning", "message": "订阅将在当前账期结束后取消。"})
    pending_phase = subscription.get("pendingPhase")
    if pending_phase:
        alerts.append({"level": "info", "message": "账户存在待生效的套餐变更。"})

    return Snapshot(
        fetched_at=int(time.time() * 1000),
        account={
            "name": account_name,
            "user_name": _string(user.get("userName")) or _string(user.get("username")),
            "user_id": _string(user.get("id")),
            "org_id": org_id,
            "org_login": _string(org.get("login")),
            "masked_key": mask_key(api_key),
        },
        plan={
            "plan_id": plan_id,
            "name": plan_name,
            "status": _string(subscription.get("status")),
            "monthly_credits": monthly_cap,
            "current_period_start": _epoch_ms(
                _pick(subscription, ("currentPeriodStart", "current_period_start"))
            ),
            "current_period_end": period_end,
            "cancel_at_period_end": _bool(
                _pick(subscription, ("cancelAtPeriodEnd", "cancel_at_period_end"))
            ),
        },
        credits={
            "monthly_remaining": monthly_remaining,
            "purchased": purchased,
            "free": free,
            "below_threshold": _bool(
                _pick(credits_raw, ("belowThreshold", "below_threshold"))
            ),
            "credit_threshold": _num(
                _pick(credits_raw, ("creditThreshold", "credit_threshold"))
            ),
        },
        windows={
            "five_hour": asdict(five_hour),
            "weekly": asdict(weekly),
            "monthly": asdict(monthly),
        },
        usage=_normalize_usage(summary_root),
        alerts=alerts,
        studio_url=studio_url,
        api_base=base,
    )


def fetch_browser_snapshot(
    cookie_header: str,
    *,
    user_agent: str = "Mozilla/5.0",
    api_base: str = API_BASE,
    timeout: float = 12.0,
) -> Snapshot:
    """Fetch a snapshot through a signed-in commandcode.ai browser session."""
    base = (api_base or API_BASE).rstrip("/")
    credits_root = _request_json(
        f"{base}{INTERNAL_CREDITS_PATH}",
        cookie_header=cookie_header,
        user_agent=user_agent,
        timeout=timeout,
    )
    subscription_root = _request_json(
        f"{base}{INTERNAL_SUBSCRIPTIONS_PATH}",
        cookie_header=cookie_header,
        user_agent=user_agent,
        timeout=timeout,
    )

    summary_root: dict[str, Any] = {}
    try:
        summary_root = _request_json(
            f"{base}{INTERNAL_USAGE_SUMMARY_PATH}",
            cookie_header=cookie_header,
            user_agent=user_agent,
            timeout=timeout,
        )
    except CommandCodeAPIError as exc:
        if exc.status in (401, 403):
            raise

    credits_raw, limits = _normalize_credits(credits_root)
    subscription = _normalize_subscription(subscription_root)
    plan_id = (
        _string(_pick(subscription, ("planId", "plan_id")))
        or _string(_pick(credits_raw, ("planId", "plan_id")))
    )
    known_plan = plan_info(plan_id)
    plan_name = known_plan[0] if known_plan else (plan_id or "未知套餐")
    monthly_cap = known_plan[1] if known_plan else None
    monthly_remaining = _num(_pick(credits_raw, ("monthlyCredits", "monthly_credits")))
    purchased = _num(_pick(credits_raw, ("purchasedCredits", "purchased_credits"))) or 0.0
    free = _num(_pick(credits_raw, ("freeCredits", "free_credits"))) or 0.0
    limited = limits.get("limited")
    window_limited = True if limited is None else _bool(limited)

    five_hour = _normalize_window(
        _pick(limits, ("fiveHour", "five_hour", "rolling5h", "5h")),
        kind="five_hour",
        label="5 小时",
        limited=window_limited,
    )
    weekly = _normalize_window(
        _pick(limits, ("weekly", "week")),
        kind="weekly",
        label="本周",
        limited=window_limited,
    )
    period_end = _epoch_ms(
        _pick(subscription, ("currentPeriodEnd", "current_period_end"))
    )
    if monthly_cap is not None and monthly_remaining is not None:
        monthly_used = max(0.0, min(float(monthly_cap), float(monthly_cap) - monthly_remaining))
        monthly = QuotaWindow(
            kind="monthly",
            label="本月",
            used=monthly_used,
            cap=float(monthly_cap),
            remaining=max(0.0, float(monthly_remaining)),
            remaining_pct=max(
                0.0,
                min(100.0, (float(monthly_remaining) / float(monthly_cap)) * 100.0),
            ),
            exceeded=monthly_used >= float(monthly_cap),
            reset_at=period_end,
            available=True,
        )
    else:
        note = (
            f"剩余 {monthly_remaining:g} credits；未知套餐不推算上限"
            if monthly_remaining is not None
            else "服务端暂未返回月余额"
        )
        monthly = QuotaWindow(
            kind="monthly",
            label="本月",
            used=None,
            cap=None,
            remaining=monthly_remaining,
            remaining_pct=None,
            exceeded=False,
            reset_at=period_end,
            available=False,
            note=note,
        )

    alerts: list[dict[str, str]] = []
    exceeded = _string(limits.get("exceeded"))
    if exceeded:
        alerts.append({"level": "danger", "message": f"当前受限窗口：{exceeded}"})
    if five_hour.exceeded:
        alerts.append({"level": "danger", "message": "5 小时额度已用尽，请等待窗口重置。"})
    if weekly.exceeded:
        alerts.append({"level": "danger", "message": "本周额度已用尽，请等待周额度重置。"})
    if monthly.exceeded:
        alerts.append({"level": "danger", "message": "本月额度已用尽。"})
    if _bool(_pick(credits_raw, ("belowThreshold", "below_threshold"))):
        alerts.append({"level": "warning", "message": "账户余额已低于预警阈值。"})
    if _bool(_pick(subscription, ("cancelAtPeriodEnd", "cancel_at_period_end"))):
        alerts.append({"level": "warning", "message": "订阅将在当前账期结束后取消。"})

    return Snapshot(
        fetched_at=int(time.time() * 1000),
        account={
            "name": "Command Code",
            "user_name": "",
            "user_id": "",
            "org_id": "",
            "org_login": "",
            "masked_key": "Chrome 登录态",
        },
        plan={
            "plan_id": plan_id,
            "name": plan_name,
            "status": _string(subscription.get("status")),
            "monthly_credits": monthly_cap,
            "current_period_start": _epoch_ms(
                _pick(subscription, ("currentPeriodStart", "current_period_start"))
            ),
            "current_period_end": period_end,
            "cancel_at_period_end": _bool(
                _pick(subscription, ("cancelAtPeriodEnd", "cancel_at_period_end"))
            ),
        },
        credits={
            "monthly_remaining": monthly_remaining,
            "purchased": purchased,
            "free": free,
            "below_threshold": _bool(
                _pick(credits_raw, ("belowThreshold", "below_threshold"))
            ),
            "credit_threshold": _num(
                _pick(credits_raw, ("creditThreshold", "credit_threshold"))
            ),
        },
        windows={
            "five_hour": asdict(five_hour),
            "weekly": asdict(weekly),
            "monthly": asdict(monthly),
        },
        usage=_normalize_usage(summary_root),
        alerts=alerts,
        studio_url="https://commandcode.ai/settings/usage",
        api_base=base,
    )
