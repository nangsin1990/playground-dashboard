"""
security.py — access control, rate limit, ticker validation
ไม่บังคับ API key ในโหมด local/Colab ถ้ายังไม่ตั้ง DASHBOARD_API_KEY
"""
from __future__ import annotations

import hmac
import logging
import os
import re
import threading
import time
import uuid
from typing import Optional

from fastapi import Header, HTTPException, Query, Request

log = logging.getLogger("playground.security")

API_KEY = os.environ.get("DASHBOARD_API_KEY", "").strip()
ADMIN_KEY = os.environ.get("DASHBOARD_ADMIN_KEY", "").strip() or API_KEY
CORS_ORIGINS_RAW = os.environ.get("DASHBOARD_CORS_ORIGINS", "").strip()
ALLOW_TUNNELS = os.environ.get("DASHBOARD_ALLOW_TUNNELS", "1").strip() not in {"0", "false", "False"}
RATE_LIMIT_PER_MIN = int(os.environ.get("DASHBOARD_RATE_LIMIT", "60"))
EXPENSIVE_LIMIT_PER_MIN = int(os.environ.get("DASHBOARD_EXPENSIVE_LIMIT", "20"))
REFRESH_COOLDOWN_SEC = int(os.environ.get("DASHBOARD_REFRESH_COOLDOWN", "60"))

TICKER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-^=]{0,19}$")

_lock = threading.Lock()
_hits: dict[str, list[float]] = {}
_last_refresh_at = 0.0
_last_refresh_ip = ""


def cors_origins() -> list[str]:
    if CORS_ORIGINS_RAW == "*":
        return ["*"]
    if CORS_ORIGINS_RAW:
        return [x.strip() for x in CORS_ORIGINS_RAW.split(",") if x.strip()]
    return [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:7860",
        "http://127.0.0.1:7860",
    ]


def cors_origin_regex() -> Optional[str]:
    if "*" in cors_origins():
        return None
    if ALLOW_TUNNELS:
        return r"https://.*\.(ngrok-free\.app|ngrok\.app|ngrok\.io|railway\.app|onrender\.com)"
    return None


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip() or "unknown"
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _prune(bucket: list[float], now: float) -> list[float]:
    cutoff = now - 60.0
    return [t for t in bucket if t > cutoff]


def check_rate_limit(request: Request, *, expensive: bool = False) -> None:
    ip = client_ip(request)
    limit = EXPENSIVE_LIMIT_PER_MIN if expensive else RATE_LIMIT_PER_MIN
    now = time.time()
    with _lock:
        bucket = _prune(_hits.get(ip, []), now)
        if len(bucket) >= limit:
            log.warning("rate limit ip=%s expensive=%s count=%d", ip, expensive, len(bucket))
            raise HTTPException(status_code=429, detail="rate_limited")
        bucket.append(now)
        _hits[ip] = bucket


def _provided_key(x_api_key: Optional[str], api_key: Optional[str], authorization: Optional[str]) -> str:
    if x_api_key:
        return x_api_key.strip()
    if api_key:
        return api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def _first_party(request: Request) -> bool:
    origin = (request.headers.get("origin") or "").rstrip("/")
    referer = request.headers.get("referer") or ""
    allowed = {o.rstrip("/") for o in cors_origins() if o != "*"}
    if origin and origin in allowed:
        return True
    if any(referer.startswith(a) for a in allowed):
        return True
    regex = cors_origin_regex()
    if regex and origin:
        import re as _re
        if _re.fullmatch(regex, origin):
            return True
    return False


def require_api_access(
    request: Request,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    authorization: Optional[str] = Header(default=None),
    api_key: Optional[str] = Query(default=None),
) -> None:
    check_rate_limit(request, expensive=False)
    if not API_KEY:
        return
    if _first_party(request):
        return
    supplied = _provided_key(x_api_key, api_key, authorization)
    if not supplied or not hmac.compare_digest(supplied, API_KEY):
        raise HTTPException(status_code=401, detail="unauthorized")


def require_admin(
    request: Request,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    x_admin_key: Optional[str] = Header(default=None, alias="X-Admin-Key"),
    authorization: Optional[str] = Header(default=None),
    api_key: Optional[str] = Query(default=None),
) -> None:
    check_rate_limit(request, expensive=True)
    expected = ADMIN_KEY or API_KEY
    if expected:
        supplied = (x_admin_key or "").strip() or _provided_key(x_api_key, api_key, authorization)
        if not supplied or not hmac.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail="admin_unauthorized")
    allow_refresh_or_raise(request)


def allow_refresh_or_raise(request: Request) -> None:
    global _last_refresh_at, _last_refresh_ip
    now = time.time()
    ip = client_ip(request)
    with _lock:
        elapsed = now - _last_refresh_at
        if elapsed < REFRESH_COOLDOWN_SEC:
            wait = int(REFRESH_COOLDOWN_SEC - elapsed) + 1
            raise HTTPException(status_code=429, detail=f"refresh_cooldown_{wait}s")
        _last_refresh_at = now
        _last_refresh_ip = ip
    log.info("refresh accepted ip=%s", ip)


def validate_ticker(ticker: str) -> str:
    value = (ticker or "").strip()
    if not TICKER_RE.match(value):
        raise HTTPException(status_code=400, detail="invalid_ticker")
    return value


def new_error_id() -> str:
    return uuid.uuid4().hex[:8]
