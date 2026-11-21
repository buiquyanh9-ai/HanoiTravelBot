"""Mức đông người qua BestTime. Không có key hoặc điểm không được phủ thì trả 'không có dữ liệu' – không đoán."""
from __future__ import annotations

import httpx

from ..config import BESTTIME_API_KEY_PRIVATE
from ..pois import Poi
from ._http import cached


def get_crowd(poi: Poi) -> dict:
    if not BESTTIME_API_KEY_PRIVATE:
        return {"available": False, "reason": "chưa có BESTTIME_API_KEY_PRIVATE"}
    if poi.id in ("ho_guom", "pho_co"):
        return {"available": False, "reason": "khu vực công cộng, BestTime chỉ phủ địa điểm cụ thể"}

    def fetch() -> dict:
        r = httpx.post(
            "https://besttime.app/api/v1/forecasts/live",
            params={"api_key_private": BESTTIME_API_KEY_PRIVATE, "venue_name": poi.name,
                    "venue_address": f"{poi.district}, Hà Nội, Việt Nam"},
            timeout=15,
        )
        r.raise_for_status()
        j = r.json()
        a = j.get("analysis", {})
        return {
            "available": a.get("venue_live_busyness_available", False),
            "live_pct": a.get("venue_live_busyness"),
            "forecast_pct": a.get("venue_forecasted_busyness"),
            "source": "BestTime (live)",
        }

    return cached(f"crowd:{poi.id}", 60 * 60, fetch)
