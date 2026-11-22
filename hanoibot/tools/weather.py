"""Thời tiết: Open-Meteo (miễn phí, không key). Là ƯỚC TÍNH từ mô hình, không phải số đo tại chỗ."""
from __future__ import annotations

from ._http import cached, get_json

WMO = {
    0: "trời quang", 1: "ít mây", 2: "mây rải rác", 3: "nhiều mây", 45: "sương mù", 48: "sương mù",
    51: "mưa phùn nhẹ", 53: "mưa phùn", 55: "mưa phùn dày", 61: "mưa nhẹ", 63: "mưa vừa", 65: "mưa to",
    80: "mưa rào nhẹ", 81: "mưa rào", 82: "mưa rào rất to", 95: "dông", 96: "dông kèm mưa đá", 99: "dông kèm mưa đá",
}


def get_weather(lat: float, lon: float) -> dict:
    def fetch() -> dict:
        j = get_json(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat, "longitude": lon,
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m",
                "hourly": "temperature_2m,precipitation_probability,precipitation,weather_code",
                "timezone": "Asia/Bangkok", "forecast_days": FORECAST_DAYS,
            },
        )
        cur = j["current"]
        hourly = [
            {"time": t, "temp": tp, "rain_prob": pp, "rain_mm": pr, "desc": WMO.get(wc, f"mã {wc}")}
            for t, tp, pp, pr, wc in zip(
                j["hourly"]["time"], j["hourly"]["temperature_2m"], j["hourly"]["precipitation_probability"],
                j["hourly"]["precipitation"], j["hourly"]["weather_code"],
            )
        ]
        return {
            "source": "Open-Meteo (ước tính mô hình)",
            "model_time": cur["time"],
            "current": {
                "temp": cur["temperature_2m"], "feels_like": cur["apparent_temperature"],
                "humidity": cur["relative_humidity_2m"], "rain_mm": cur["precipitation"],
                "wind_kmh": cur["wind_speed_10m"], "desc": WMO.get(cur["weather_code"], f"mã {cur['weather_code']}"),
            },
            "hourly": hourly,
        }

    # Open-Meteo cập nhật theo giờ; cache 20 phút là đủ tươi
    return cached(f"weather{FORECAST_DAYS}:{lat:.3f},{lon:.3f}", 20 * 60, fetch)


def hourly_for_date(weather: dict, iso_date: str) -> list[dict]:
    return [h for h in weather.get("hourly", []) if h["time"].startswith(iso_date)]


FORECAST_DAYS = 7  # chỉ dùng 7 ngày (hôm nay + 6): xa hơn độ tin cậy dự báo thấp; bot nói không có dự báo, không đoán


def geocode(place: str) -> dict | None:
    """Tìm toạ độ địa danh THUỘC HÀ NỘI (Open-Meteo Geocoding, miễn phí). Nơi ngoài Hà Nội -> None."""
    def fetch() -> dict:
        j = get_json("https://geocoding-api.open-meteo.com/v1/search",
                     params={"name": place, "count": 10, "language": "vi", "countryCode": "VN"})
        return {"results": [{"name": r.get("name"), "admin1": r.get("admin1"), "admin2": r.get("admin2"),
                             "lat": r["latitude"], "lon": r["longitude"]} for r in j.get("results", []) or []]}
    res = cached(f"geo:{place.lower()}", 30 * 24 * 3600, fetch).get("results") or []
    if not res:
        return None
    hn = [r for r in res if "Hà Nội" in (r.get("admin1") or "") or "Hanoi" in (r.get("admin1") or "")]
    return hn[0] if hn else None


def daily_for_date(weather: dict, iso_date: str) -> dict | None:
    hs = hourly_for_date(weather, iso_date)
    if not hs:
        return None
    temps = [h["temp"] for h in hs if h["temp"] is not None]
    probs = [h["rain_prob"] for h in hs if h["rain_prob"] is not None]
    return {"tmin": min(temps), "tmax": max(temps), "rain_prob_max": max(probs) if probs else None,
            "rain_mm": round(sum(h["rain_mm"] or 0 for h in hs), 1)}
