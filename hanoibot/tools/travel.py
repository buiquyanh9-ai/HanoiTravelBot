"""Di chuyển giữa các điểm: chọn đi bộ hay taxi, thời gian, quãng đường, chi phí ước tính.

MVP: khoảng cách đường chim bay x 1.4 (hệ số đường phố).
- < 0.8 km: đi bộ 4.5 km/h
- còn lại: taxi/xe công nghệ, 18 km/h nội đô + 5 phút gọi xe
Chi phí taxi ước tính: max(20.000đ, 15.000đ/km) mỗi chặng, 1 xe cho tối đa 4 người.
Có TOMTOM_API_KEY thì sau này thay thời gian bằng số liệu giao thông thật.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..pois import Poi
from . import traffic

ROAD_FACTOR = 1.4
AVG_KMH = 18.0
WALK_KMH = 4.5
WALK_MAX_KM = 0.8
OVERHEAD_MIN = 5
TAXI_VND_PER_KM = 15000
TAXI_MIN_VND = 20000


@dataclass
class Leg:
    mode: str      # "walk" | "taxi" | "none"
    km: float
    minutes: int
    cost_vnd: int  # cho cả nhóm


def distance_km(a: Poi, b: Poi) -> float:
    r = 6371.0
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp, dl = p2 - p1, math.radians(b.lon - a.lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def leg(a: Poi | None, b: Poi, people: int = 1) -> Leg:
    if a is None or a.id == b.id:
        return Leg("none", 0.0, 0, 0)
    km = round(distance_km(a, b) * ROAD_FACTOR, 2)
    if km < WALK_MAX_KM:
        return Leg("walk", km, max(3, round(km / WALK_KMH * 60)), 0)
    cars = max(1, math.ceil(people / 4))
    live = traffic.lookup(a.id, b.id)  # TomTom nếu đã kích hoạt cho lượt này
    if live:
        minutes, km, _delay = live
        minutes += OVERHEAD_MIN
    else:
        minutes = round(km / AVG_KMH * 60) + OVERHEAD_MIN
    cost = max(TAXI_MIN_VND, round(km * TAXI_VND_PER_KM, -3)) * cars
    return Leg("taxi", km, minutes, int(cost))


def travel_minutes(a: Poi, b: Poi) -> int:
    return leg(a, b).minutes


def matrix(pois: list[Poi]) -> dict[str, dict[str, int]]:
    return {a.id: {b.id: travel_minutes(a, b) for b in pois} for a in pois}
