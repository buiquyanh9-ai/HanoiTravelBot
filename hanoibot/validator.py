"""Code kiểm ràng buộc cứng của lịch trình (bài học Day 08: con số do code quyết, không do model).

Vi phạm (violation) -> trả lại cho LLM sửa. Cảnh báo (warning) -> chỉ hiển thị cho người dùng.
"""
from __future__ import annotations

from datetime import date, datetime, time

from .pois import load_pois
from .tools.travel import travel_minutes


def _t(s: str) -> time:
    return time.fromisoformat(s.strip()[:5])


def _mins(t: time) -> int:
    return t.hour * 60 + t.minute


def validate(plan: dict, req: dict, weather_hours: list[dict] | None = None, air: dict | None = None,
             now: datetime | None = None) -> dict:
    pois = load_pois()
    d = date.fromisoformat(req["date"])
    win_start, win_end = _t(req.get("start", "08:00")), _t(req.get("end", "21:00"))
    avoid = set(req.get("avoid", []))
    violations: list[str] = []
    warnings: list[str] = []
    items = plan.get("items", [])
    if not items:
        violations.append("Lịch trình rỗng.")
        return {"ok": False, "violations": violations, "warnings": warnings}

    rain_by_hour = {int(h["time"][11:13]): h for h in (weather_hours or [])}
    prev_end: time | None = None
    prev_poi = None
    seen: set[str] = set()

    for i, it in enumerate(items, 1):
        label = f"Mục {i}"
        try:
            s, e = _t(it["start"]), _t(it["end"])
        except (KeyError, ValueError):
            violations.append(f"{label}: thiếu hoặc sai định dạng giờ (HH:MM).")
            continue
        if _mins(e) <= _mins(s):
            violations.append(f"{label}: giờ kết thúc {e:%H:%M} không sau giờ bắt đầu {s:%H:%M}.")
        if s < win_start or e > win_end:
            violations.append(f"{label}: {s:%H:%M}-{e:%H:%M} nằm ngoài khung người dùng {win_start:%H:%M}-{win_end:%H:%M}.")
        if now and d == now.date() and s < now.time().replace(second=0, microsecond=0):
            violations.append(f"{label}: bắt đầu {s:%H:%M} đã qua (bây giờ {now:%H:%M}).")
        if prev_end and _mins(s) < _mins(prev_end):
            violations.append(f"{label}: chồng giờ với mục trước (mục trước kết thúc {prev_end:%H:%M}).")

        kind = it.get("kind", "visit")
        pid = it.get("poi_id")
        if kind == "visit":
            if pid not in pois:
                violations.append(f"{label}: poi_id '{pid}' không có trong danh mục. Chỉ dùng id được cung cấp.")
                continue
            p = pois[pid]
            if pid in avoid:
                violations.append(f"{label}: người dùng đã yêu cầu bỏ {p.name}.")
            if pid in seen:
                violations.append(f"{label}: {p.name} bị lặp lại.")
            seen.add(pid)
            if not p.is_open_between(d, s, e):
                violations.append(f"{label}: {p.name} KHÔNG mở trong {s:%H:%M}-{e:%H:%M}. Giờ mở ngày này: {p.hours_text(d)}.")
            dur = _mins(e) - _mins(s)
            if dur < max(20, p.visit_minutes // 2):
                warnings.append(f"{p.name}: chỉ {dur} phút, ít hơn nhiều so với khuyến nghị {p.visit_minutes} phút.")
            if prev_poi and prev_end:
                need = travel_minutes(prev_poi, p)
                gap = _mins(s) - _mins(prev_end)
                if gap < need:
                    violations.append(f"{label}: chỉ có {gap} phút để đi từ {prev_poi.name} tới {p.name}, cần khoảng {need} phút.")
            if not p.indoor:
                for hr in range(s.hour, e.hour + 1):
                    h = rain_by_hour.get(hr)
                    if h and (h.get("rain_prob") or 0) >= 60:
                        warnings.append(f"{p.name} (ngoài trời) lúc {hr}h: dự báo {h['rain_prob']}% mưa.")
                        break
                if air and (air.get("aqi") or 0) > 150:
                    warnings.append(f"{p.name} (ngoài trời): AQI {air['aqi']} ({air.get('level')}).")
            if p.confidence == "low":
                warnings.append(f"Giờ mở cửa {p.name} có độ tin cậy THẤP, nên gọi xác nhận trước.")
            prev_poi = p
        prev_end = e

    for pid in req.get("must_include", []):
        if pid in pois and pid not in seen:
            p = pois[pid]
            reason = p.closed_reason(d)
            if reason:
                warnings.append(f"Không xếp được {p.name}: {reason} vào {d:%d/%m}.")
            else:
                violations.append(f"Thiếu điểm người dùng yêu cầu: {p.name} (mở {p.hours_text(d)}).")

    return {"ok": not violations, "violations": violations, "warnings": sorted(set(warnings), key=warnings.index)}
