"""Gọi HTTP có cache theo TTL. API lỗi thì trả giá trị cũ gần nhất kèm cờ stale."""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Callable

import httpx

from ..config import CACHE_DIR, HTTP_TIMEOUT

_client = httpx.Client(timeout=HTTP_TIMEOUT, headers={"User-Agent": "HanoiTravelBot-MVP/0.1"})


def get_json(url: str, params: dict | None = None, headers: dict | None = None, retries: int = 2) -> Any:
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            r = _client.get(url, params=params, headers=headers)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(0.6 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError) as e:  # ValueError: JSON hỏng
            last = e
            if attempt < retries:
                time.sleep(0.6 * (attempt + 1))
    raise RuntimeError(f"HTTP lỗi: {last}")


def post_json(url: str, payload: dict, headers: dict | None = None, timeout: float | None = None) -> Any:
    r = _client.post(url, json=payload, headers=headers, timeout=timeout or HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


def cached(key: str, ttl_s: int, fetch: Callable[[], dict]) -> dict:
    """Trả dict có thêm `_fetched_at` (epoch) và `_stale` (True nếu dùng bản cũ do lỗi)."""
    path = CACHE_DIR / (hashlib.sha1(key.encode()).hexdigest()[:16] + ".json")
    old = None
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            if time.time() - old["_fetched_at"] < ttl_s:
                return {**old, "_stale": False, "_from_cache": True}
        except (ValueError, KeyError):
            old = None
    try:
        data = fetch()
        data = {**data, "_fetched_at": time.time()}
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return {**data, "_stale": False, "_from_cache": False}
    except Exception as e:
        if old:
            return {**old, "_stale": True, "_error": str(e)}
        return {"_error": str(e), "_stale": True, "_fetched_at": None}
