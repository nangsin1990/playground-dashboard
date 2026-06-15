"""Minimal TTL cache decorator (replaces st.cache_data now that Streamlit
is gone). In-memory, per-process -- fine for a single-user local server."""

from __future__ import annotations
import functools
import time


def ttl_cache(ttl_seconds: float):
    def decorator(func):
        store: dict[tuple, tuple[float, object]] = {}

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            now = time.time()
            hit = store.get(key)
            if hit is not None and (now - hit[0]) < ttl_seconds:
                return hit[1]
            val = func(*args, **kwargs)
            store[key] = (now, val)
            return val

        wrapper.cache_clear = store.clear
        return wrapper
    return decorator
