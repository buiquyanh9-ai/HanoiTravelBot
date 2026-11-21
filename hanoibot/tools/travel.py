"""Thời gian di chuyển giữa các điểm (taxi/xe máy). Tính sẵn bằng ước lượng khoảng cách, không gọi API lúc chạy.

MVP: khoảng cách đường chim bay x hệ số đường phố 1.4, tốc độ trung bình nội đô 18 km/h, cộng 5 phút gọi xe/gửi xe.
Có TOMTOM_API_KEY thì sau này thay bằng thời gian có tính tắc đường thật.
"""
from __future__ import annotations

import math

from ..pois import Poi

ROAD_FACTOR = 1.4
AVG_KMH = 18.0
OVERHEAD_MIN = 5


def distance_km(a: Poi, b: Poi) -> float:
    r = 6371.0
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp, dl = p2 - p1, math.radians(b.lon - a.lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def travel_minutes(a: Poi, b: Poi) -> int:
    if a.id == b.id:
        return 0
    km = distance_km(a, b) * ROAD_FACTOR
    if km < 0.8:  # gần thì đi bộ ~4.5 km/h
        return max(3, round(km / 4.5 * 60))
    return round(km / AVG_KMH * 60) + OVERHEAD_MIN


def matrix(pois: list[Poi]) -> dict[str, dict[str, int]]:
    return {a.id: {b.id: travel_minutes(a, b) for b in pois} for a in pois}
