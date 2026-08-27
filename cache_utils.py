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
def _disk_load(path: Path, ttl_seconds: float) -> tuple[bool, Any]:
    """
    Load from disk. Returns (hit, value).
    hit=False if file missing, expired, or corrupt.
    """
    if not path.exists():
        return False, None
    try:
        mtime = path.stat().st_mtime
        if (time.time() - mtime) >= ttl_seconds:
            return False, None          # expired
        with path.open("rb") as f:
            val = pickle.load(f)
        return True, val
    except Exception as e:
        log.debug("disk_load failed %s: %s", path.name, e)
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
        return False, None


def _disk_save(path: Path, value: Any) -> None:
    """Write value to disk atomically (tmp → rename)."""
    tmp = path.with_suffix(".tmp")
    try:
        with tmp.open("wb") as f:
            pickle.dump(value, f, protocol=pickle.HIGHEST_PROTOCOL)
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
def ttl_cache(ttl_seconds: float):
    """
    Thread-safe TTL cache with Google Drive persistence.

    Layer 1: in-memory dict (fastest, lost on restart)
    Layer 2: Drive pickle file (survives restart, slower on miss)

    .cache_clear()     — clear in-memory + delete all disk files for this func
    .cache_clear_key() — clear one specific key
    """
    def decorator(func):
        mem_store: dict[tuple, tuple[float, Any]] = {}
        lock      = threading.Lock()
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

            # ── Miss: compute ──
            log.info("cache MISS → computing %s ...", func_name)
            val = func(*args, **kwargs)
            ts  = time.time()

            with lock:
                mem_store[key] = (ts, val)

            # Write to disk outside lock (slow I/O)
            _disk_save(disk_path, val)

            return val

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
