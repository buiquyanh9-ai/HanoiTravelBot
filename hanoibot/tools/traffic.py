"""Thời gian đi ô tô có tính giao thông, lấy 1 lần cho mọi cặp điểm bằng TomTom Matrix Routing v2.

Hôm nay: departAt=now (giao thông hiện tại). Ngày khác: departAt 10:00 ngày đó (TomTom dùng dữ liệu lịch sử).
Kết quả được 'kích hoạt' cho lượt xử lý hiện tại; travel.leg() đọc từ đây, không gọi mạng.
Không có key hoặc lỗi -> travel.leg() dùng ước lượng khoảng cách.
"""
from __future__ import annotations

import contextvars
from datetime import date, datetime

import httpx

from ..config import TOMTOM_API_KEY
from ._http import cached

_active: contextvars.ContextVar[dict | None] = contextvars.ContextVar("traffic_matrix", default=None)


def fetch_matrix(pois: list, d: date, now: datetime) -> dict:
    if not TOMTOM_API_KEY:
        return {"available": False, "reason": "chưa có TOMTOM_API_KEY"}
    depart = "now" if d == now.date() else f"{d.isoformat()}T10:00:00+07:00"
    bucket = now.strftime("%Y%m%d%H") + str(now.minute // 15) if depart == "now" else d.isoformat()
    ids = [p.id for p in pois]

    def fetch() -> dict:
        pts = [{"point": {"latitude": p.lat, "longitude": p.lon}} for p in pois]
        r = httpx.post("https://api.tomtom.com/routing/matrix/2", params={"key": TOMTOM_API_KEY}, timeout=20,
                       json={"origins": pts, "destinations": pts,
                             "options": {"departAt": depart, "traffic": "live" if depart == "now" else "historical",
                                         "travelMode": "car"}})
        r.raise_for_status()
        cells = {}
        for c in r.json().get("data", []):
            s = c.get("routeSummary")
            if s:
                cells[f"{ids[c['originIndex']]}|{ids[c['destinationIndex']]}"] = [
                    round(s["travelTimeInSeconds"] / 60), round(s["lengthInMeters"] / 1000, 2),
                    round(s.get("trafficDelayInSeconds", 0) / 60)]
        return {"available": bool(cells), "cells": cells, "depart": depart, "source": "TomTom (giao thông)"}

    return cached(f"tomtom:{bucket}:{','.join(ids)}", 15 * 60 if depart == "now" else 24 * 3600, fetch)


def activate(matrix: dict | None) -> None:
    _active.set(matrix if matrix and matrix.get("available") else None)


def lookup(a_id: str, b_id: str) -> tuple[int, float, int] | None:
    m = _active.get()
    if not m:
        return None
    v = m["cells"].get(f"{a_id}|{b_id}")
    return tuple(v) if v else None
