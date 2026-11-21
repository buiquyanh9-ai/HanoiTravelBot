"""Chất lượng không khí. Ưu tiên WAQI (số ĐO THẬT từ trạm quan trắc), thiếu token thì dùng Open-Meteo (ước tính mô hình CAMS)."""
from __future__ import annotations

from ..config import WAQI_TOKEN
from ._http import cached, get_json


def aqi_level(aqi: float | None) -> str:
    if aqi is None:
        return "không rõ"
    for limit, label in [(50, "tốt"), (100, "trung bình"), (150, "kém cho nhóm nhạy cảm"), (200, "kém"), (300, "rất kém")]:
        if aqi <= limit:
            return label
    return "nguy hại"


def get_air(lat: float, lon: float) -> dict:
    if WAQI_TOKEN:
        def fetch_waqi() -> dict:
            j = get_json(f"https://api.waqi.info/feed/geo:{lat};{lon}/", params={"token": WAQI_TOKEN})
            if j.get("status") != "ok":
                raise RuntimeError(f"WAQI: {j.get('data')}")
            d = j["data"]
            return {
                "source": f"WAQI – trạm {d['city']['name']} (đo thật)", "measured": True,
                "aqi": d.get("aqi"), "pm25": d.get("iaqi", {}).get("pm25", {}).get("v"),
                "obs_time": d.get("time", {}).get("s"),
            }
        res = cached(f"waqi:{lat:.3f},{lon:.3f}", 30 * 60, fetch_waqi)
        if "aqi" in res:
            res["level"] = aqi_level(res["aqi"])
            return res

    def fetch_om() -> dict:
        j = get_json(
            "https://air-quality-api.open-meteo.com/v1/air-quality",
            params={"latitude": lat, "longitude": lon, "current": "us_aqi,pm2_5", "timezone": "Asia/Bangkok"},
        )
        c = j["current"]
        return {"source": "Open-Meteo Air Quality (ước tính mô hình)", "measured": False,
                "aqi": c["us_aqi"], "pm25": c["pm2_5"], "obs_time": c["time"]}

    res = cached(f"om_air:{lat:.3f},{lon:.3f}", 30 * 60, fetch_om)
    res["level"] = aqi_level(res.get("aqi"))
    return res
