"""
cache_utils.py — Thread-safe TTL cache with Google Drive persistence
v3: Drive-backed cache survives Colab restarts

สำหรับ Colab: mount Drive ก่อนรัน server จาก Cell 0
  from google.colab import drive
  drive.mount('/content/drive')

Cache path: /content/drive/MyDrive/playground_cache/
- ไฟล์แต่ละ key = 1 pickle file
- ถ้า Drive ไม่ได้ mount → fallback ใช้ in-memory เหมือนเดิม (ไม่ crash)
- TTL ถูก enforce ทั้ง in-memory และ disk
"""

from __future__ import annotations
import functools
import hashlib
import hmac
import logging
import os
import pickle
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("playground.cache")

# ── Drive cache config ─────────────────────────────────────────────────────────
_DRIVE_ROOT = Path("/content/drive/MyDrive/playground_cache")
_FALLBACK   = Path("/tmp/playground_cache")   # ถ้า Drive ไม่ mount

# ── ตรวจสอบ Drive ─────────────────────────────────────────────────────────────
def _get_cache_dir() -> Path:
    """Return Drive cache dir if mounted, else /tmp fallback."""
    drive_path = Path("/content/drive/MyDrive")
    if drive_path.exists():
        d = _DRIVE_ROOT
    else:
        d = _FALLBACK
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        d = _FALLBACK
        d.mkdir(parents=True, exist_ok=True)
    return d


def _key_to_path(cache_dir: Path, func_name: str, key: tuple) -> Path:
    """Convert cache key → safe filename (SHA256 prefix to avoid collisions)."""
    raw  = f"{func_name}::{repr(key)}"
    sha  = hashlib.sha256(raw.encode()).hexdigest()[:16]
    name = f"{func_name}__{sha}.pkl"
    return cache_dir / name


# ── Core disk ops (pickle) ────────────────────────────────────────────────────
def _cache_secret() -> bytes:
    env = (os.environ.get("DASHBOARD_CACHE_SECRET") or "").strip()
    if env:
        return env.encode("utf-8")
    cache_dir = _get_cache_dir()
    secret_path = cache_dir / ".cache_secret"
    try:
        if secret_path.exists():
            raw = secret_path.read_bytes().strip()
            if raw:
                return raw
        raw = os.urandom(32)
        secret_path.write_bytes(raw)
        try:
            secret_path.chmod(0o600)
        except Exception:
            pass
        return raw
    except Exception:
        return b"playground-local-cache"


def _sign_blob(raw: bytes) -> bytes:
    mac = hmac.new(_cache_secret(), raw, hashlib.sha256).hexdigest().encode("ascii")
    return b"PDG1" + mac + raw


def _unsign_blob(blob: bytes) -> bytes | None:
    if not blob.startswith(b"PDG1") or len(blob) < 4 + 64:
        return None
    mac = blob[4:68]
    raw = blob[68:]
    expect = hmac.new(_cache_secret(), raw, hashlib.sha256).hexdigest().encode("ascii")
    if not hmac.compare_digest(mac, expect):
        return None
    return raw


def _disk_load(path: Path, ttl_seconds: float) -> tuple[bool, Any]:
    """
    Load from disk. Returns (hit, value).
    hit=False if file missing, expired, corrupt, or signature mismatch.
    """
    if not path.exists():
        return False, None
    try:
        mtime = path.stat().st_mtime
        if (time.time() - mtime) >= ttl_seconds:
            return False, None
        with path.open("rb") as f:
            blob = f.read()
        raw = _unsign_blob(blob)
        if raw is None:
            if (os.environ.get("DASHBOARD_ALLOW_LEGACY_PICKLE") or "").strip() == "1":
                val = pickle.loads(blob)
                return True, val
            log.debug("unsigned/legacy cache ignored %s", path.name)
            return False, None
        return True, pickle.loads(raw)
    except Exception as e:
        log.debug("disk_load failed %s: %s", path.name, e)
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
        return False, None


def _disk_save(path: Path, value: Any) -> None:
    """Write HMAC-wrapped pickle atomically (tmp → rename)."""
    tmp = path.with_suffix(".tmp")
    try:
        raw = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
        with tmp.open("wb") as f:
            f.write(_sign_blob(raw))
        tmp.replace(path)
    except Exception as e:
        log.debug("disk_save failed %s: %s", path.name, e)
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def _disk_delete(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


# ── Decorator ─────────────────────────────────────────────────────────────────
def ttl_cache(ttl_seconds: float, flight_wait: float | None = None):
    """
    Thread-safe TTL cache with Google Drive persistence.

    Layer 1: in-memory dict (fastest, lost on restart)
    Layer 2: Drive pickle file (survives restart, slower on miss)

    .cache_clear()     — clear in-memory + delete all disk files for this func
    .cache_clear_key() — clear one specific key

    flight_wait: seconds a waiter blocks for the owner compute.
    Default max(30, ttl_seconds). A timed-out waiter may compute locally
    but must not pop the owner's inflight slot.
    """
    wait_s = max(30.0, float(ttl_seconds)) if flight_wait is None else max(0.0, float(flight_wait))

    def decorator(func):
        mem_store: dict[tuple, tuple[float, Any]] = {}
        lock      = threading.Lock()
        inflight: dict[tuple, threading.Event] = {}
        func_name = func.__name__

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            key     = (args, tuple(sorted(kwargs.items())))
            now     = time.time()
            cache_dir = _get_cache_dir()

            # ── Layer 1: memory ──
            with lock:
                hit = mem_store.get(key)
                if hit is not None and (now - hit[0]) < ttl_seconds:
                    return hit[1]

            # ── Layer 2: disk ──
            disk_path = _key_to_path(cache_dir, func_name, key)
            ok, val   = _disk_load(disk_path, ttl_seconds)
            if ok:
                with lock:
                    mem_store[key] = (time.time(), val)
                log.info("cache HIT (disk) → %s", func_name)
                return val

            # ── Single-flight: only the owner of inflight[key] may pop it ──
            owner_ev = None
            waiter = None
            with lock:
                hit = mem_store.get(key)
                if hit is not None and (time.time() - hit[0]) < ttl_seconds:
                    return hit[1]
                ev = inflight.get(key)
                if ev is None:
                    ev = threading.Event()
                    inflight[key] = ev
                    owner_ev = ev
                else:
                    waiter = ev
            if waiter is not None:
                waiter.wait(timeout=wait_s)
                with lock:
                    hit = mem_store.get(key)
                    if hit is not None:
                        return hit[1]
                ok, val = _disk_load(disk_path, ttl_seconds)
                if ok:
                    with lock:
                        mem_store[key] = (time.time(), val)
                    return val
                log.warning(
                    "single-flight wait ended without value for %s; fallback compute without taking inflight",
                    func_name,
                )
                val = func(*args, **kwargs)
                ts = time.time()
                with lock:
                    existing = mem_store.get(key)
                    if existing is None or (ts - existing[0]) >= ttl_seconds:
                        mem_store[key] = (ts, val)
                _disk_save(disk_path, val)
                return val

            log.info("cache MISS → computing %s ...", func_name)
            try:
                val = func(*args, **kwargs)
                ts  = time.time()
                with lock:
                    mem_store[key] = (ts, val)
                _disk_save(disk_path, val)
                return val
            finally:
                with lock:
                    if owner_ev is not None and inflight.get(key) is owner_ev:
                        inflight.pop(key, None)
                if owner_ev is not None:
                    owner_ev.set()

        # ── cache_clear: wipe memory + disk files for this function ──
        def _cache_clear():
            with lock:
                mem_store.clear()
            cache_dir = _get_cache_dir()
            prefix    = f"{func_name}__"
            deleted   = 0
            try:
                for f in cache_dir.glob(f"{prefix}*.pkl"):
                    _disk_delete(f)
                    deleted += 1
            except Exception as e:
                log.debug("cache_clear disk error: %s", e)
            log.info("cache cleared: %s (%d disk files)", func_name, deleted)

        # ── cache_clear_key: wipe one specific key ──
        def _cache_clear_key(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            with lock:
                mem_store.pop(key, None)
            cache_dir = _get_cache_dir()
            _disk_delete(_key_to_path(cache_dir, func_name, key))

        wrapper.cache_clear     = _cache_clear
        wrapper.cache_clear_key = _cache_clear_key
        return wrapper

    return decorator


# ── Utility: global cache ops ─────────────────────────────────────────────────
def clear_all_drive_cache() -> int:
    """Delete ALL cache files from Drive. Use when data is stale or corrupted."""
    cache_dir = _get_cache_dir()
    deleted   = 0
    try:
        for f in cache_dir.glob("*.pkl"):
            _disk_delete(f)
            deleted += 1
    except Exception as e:
        log.warning("clear_all_drive_cache error: %s", e)
    log.info("Cleared %d files from %s", deleted, cache_dir)
    return deleted


def cache_status() -> dict:
    """Return info about current cache state (for /api/status endpoint)."""
    cache_dir = _get_cache_dir()
    on_drive  = Path("/content/drive/MyDrive").exists()
    try:
        files = list(cache_dir.glob("*.pkl"))
        total_mb = sum(f.stat().st_size for f in files) / 1024 / 1024
        return {
            "drive_mounted": on_drive,
            "cache_dir":     str(cache_dir),
            "files":         len(files),
            "size_mb":       round(total_mb, 2),
        }
    except Exception as e:
        return {"drive_mounted": on_drive, "error": str(e)}
