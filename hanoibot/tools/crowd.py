"""Mức đông người qua BestTime.

- Dự báo theo giờ (0-100%, 100 = giờ đông nhất trong tuần): /forecasts, cache 7 ngày (tốn credit forecast).
- Đông hiện tại: /forecasts/live, cache 30 phút, chỉ có khi điểm đang mở và đủ dữ liệu.
Không có key, điểm công cộng (Hồ Gươm, phố cổ) hoặc BestTime không phủ -> 'không có dữ liệu', không đoán.
"""
from __future__ import annotations

from datetime import date, datetime

import httpx

from ..config import BESTTIME_API_KEY_PRIVATE
from ..pois import Poi
from ._http import cached

URL = "https://besttime.app/api/v1"


def _post(path: str, poi: Poi) -> dict:
    """Thử tên tiếng Việt, 404 thì thử tên tiếng Anh. Lỗi trả về KHÔNG chứa URL (URL có key)."""
    last = None
    for name in dict.fromkeys([poi.name, poi.name_en]):
        if not name:
            continue
        r = httpx.post(f"{URL}{path}", params={"api_key_private": BESTTIME_API_KEY_PRIVATE,
                                               "venue_name": name, "venue_address": poi.address}, timeout=30)
        if r.status_code == 404:
            last = 404
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"BestTime HTTP {r.status_code}")
        return r.json()
    raise RuntimeError(f"BestTime HTTP {last}: không tìm thấy địa điểm")


def _supported(poi: Poi) -> str | None:
    if not BESTTIME_API_KEY_PRIVATE:
        return "chưa có BESTTIME_API_KEY_PRIVATE"
    if not poi.address:
        return "khu vực công cộng, BestTime chỉ phủ địa điểm cụ thể"
    return None


def get_forecast(poi: Poi) -> dict:
    why = _supported(poi)
    if why:
        return {"available": False, "reason": why}

    def fetch() -> dict:
        j = _post("/forecasts", poi)
        if j.get("status") != "OK":
            return {"available": False, "reason": j.get("message", "BestTime không có dự báo")}
        by_day = {}
        hours = {}
        for d in j.get("analysis", []):
            di = d["day_info"]["day_int"]          # 0 = thứ Hai
            raw = d.get("day_raw") or []            # phần tử 0 = 6h sáng
            by_day[str(di)] = {str((6 + i) % 24): v for i, v in enumerate(raw)}
            v2 = (d["day_info"].get("venue_open_close_v2") or {}).get("24h") or []
            hours[str(di)] = [[x["opens"], x["closes"]] for x in v2]
        vi = j.get("venue_info", {})
        return {"available": True, "by_day": by_day, "bt_hours": hours, "venue_name": vi.get("venue_name"),
                "source": "BestTime (dự báo theo giờ)"}

    return cached(f"bt_forecast:{poi.id}", 7 * 24 * 3600, fetch)


def get_live(poi: Poi) -> dict:
    why = _supported(poi)
    if why:
        return {"available": False, "reason": why}

    def fetch() -> dict:
        j = _post("/forecasts/live", poi)
        a = j.get("analysis", {})
        return {"available": bool(a.get("venue_live_busyness_available")), "live_pct": a.get("venue_live_busyness"),
                "forecast_pct": a.get("venue_forecasted_busyness"), "message": j.get("message"),
                "source": "BestTime (đông hiện tại)"}

    return cached(f"bt_live:{poi.id}", 30 * 60, fetch)


def get_crowd(poi: Poi, d: date | None = None, now: datetime | None = None) -> dict:
    """Gộp dự báo theo giờ cho ngày d và (nếu d là hôm nay) mức đông hiện tại."""
    try:
        fc = get_forecast(poi)
    except Exception as e:  # BestTime lỗi không được làm hỏng lịch trình
        return {"available": False, "reason": f"BestTime lỗi: {type(e).__name__}"}
    if not fc.get("available"):
        return {"available": False, "reason": fc.get("reason") or fc.get("_error")}
    d = d or date.today()
    out = {"available": True, "hourly": fc["by_day"].get(str(d.weekday()), {}), "bt_hours": fc["bt_hours"].get(str(d.weekday())),
           "source": fc["source"], "live_pct": None}
    if now and d == now.date():
        try:
            lv = get_live(poi)
            if lv.get("available"):
                out["live_pct"] = lv.get("live_pct")
        except Exception:
            pass
    return out


def pct_at(crowd: dict, hour: int) -> int | None:
    v = (crowd.get("hourly") or {}).get(str(hour))
    return v if isinstance(v, int) and v > 0 else None


def level(pct: int | None, lang: str = "vi") -> str:
    if pct is None:
        return ""
    labels = {"vi": ["vắng", "vừa", "đông", "rất đông"], "en": ["quiet", "moderate", "busy", "very busy"]}[lang]
    return labels[0 if pct < 35 else 1 if pct < 60 else 2 if pct < 85 else 3]
