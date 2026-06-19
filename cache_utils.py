"""
cache_utils.py — Thread-safe TTL cache decorator
v2: added threading.Lock to prevent race conditions on concurrent requests
"""

from __future__ import annotations
import functools
import time
import threading


def ttl_cache(ttl_seconds: float):
    """
    Thread-safe in-memory TTL cache.
    - Keyed on (args, sorted kwargs)
    - .cache_clear() method for manual invalidation
    - Lock prevents race conditions on concurrent API calls
    """
    def decorator(func):
        store: dict[tuple, tuple[float, object]] = {}
        lock = threading.Lock()

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            now = time.time()

            with lock:
                hit = store.get(key)
                if hit is not None and (now - hit[0]) < ttl_seconds:
                    return hit[1]

            # Compute outside lock so other threads aren't blocked during fetch
            val = func(*args, **kwargs)

            with lock:
                store[key] = (time.time(), val)

            return val

        def _cache_clear():
            with lock:
                store.clear()

        wrapper.cache_clear = _cache_clear
        return wrapper
    return decorator
