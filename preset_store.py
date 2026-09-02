"""
preset_store.py — Drive-backed JSON store for user-saved screener presets

ต่างจาก cache_utils.ttl_cache ตรงที่ preset ไม่มี TTL (ไม่หมดอายุเอง) และเก็บเป็น
JSON ไฟล์เดียว (ไม่ใช่ pickle แยกไฟล์ต่อ key) เพราะ preset มีจำนวนน้อยและอ่าน/เขียนพร้อมกัน
ทั้งก้อนง่ายกว่า

ใช้ pattern เดียวกับ cache_utils.py: เขียนลง Google Drive ถ้า mount ไว้แล้ว
(/content/drive/MyDrive) เพื่อให้รอด Colab restart และให้ทุกเครื่อง/browser ที่เรียก
backend เดียวกัน (เช่น ผ่าน ngrok dev domain คงที่) เห็นชุด preset เดียวกัน — ต่างจาก
localStorage เดิมที่ผูกกับเครื่อง/browser นั้นๆ เท่านั้น

ถ้า Drive ไม่ได้ mount → fallback ไปใช้ /tmp (ใช้งานได้ปกติแต่ไม่รอด restart)
"""

from __future__ import annotations
import json
import logging
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger("playground.presets")

_DRIVE_ROOT = Path("/content/drive/MyDrive/playground_cache")
_FALLBACK   = Path("/tmp/playground_cache")
_FILENAME   = "screener_presets.json"

_lock = threading.Lock()


def _get_store_path() -> Path:
    drive_path = Path("/content/drive/MyDrive")
    d = _DRIVE_ROOT if drive_path.exists() else _FALLBACK
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        d = _FALLBACK
        d.mkdir(parents=True, exist_ok=True)
    return d / _FILENAME


def _read_all() -> dict[str, Any]:
    path = _get_store_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        log.warning("preset_store read failed (%s), returning empty: %s", path, e)
        return {}


def _write_all(data: dict[str, Any]) -> None:
    path = _get_store_path()
    tmp = path.with_suffix(".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(path)
    except Exception as e:
        log.error("preset_store write failed (%s): %s", path, e)
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def list_presets() -> dict[str, Any]:
    with _lock:
        return _read_all()


def save_preset(name: str, filters: dict[str, Any]) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise ValueError("preset name is required")
    if len(name) > 80:
        raise ValueError("preset name too long (max 80 chars)")
    with _lock:
        data = _read_all()
        data[name] = filters
        _write_all(data)
        return data


def delete_preset(name: str) -> dict[str, Any]:
    with _lock:
        data = _read_all()
        data.pop(name, None)
        _write_all(data)
        return data


def store_status() -> dict:
    path = _get_store_path()
    on_drive = Path("/content/drive/MyDrive").exists()
    return {
        "drive_mounted": on_drive,
        "store_path": str(path),
        "count": len(_read_all()),
    }
