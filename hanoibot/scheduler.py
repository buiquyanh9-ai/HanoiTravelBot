"""Biến danh sách điểm (do LLM chọn) thành giờ cụ thể. Phép tính giờ do code làm, không do model.

Với mỗi điểm: đến nơi = giờ rời điểm trước + thời gian di chuyển; nếu điểm chưa mở thì chờ đến giờ mở;
nếu không còn khung mở đủ dài trong khung của khách thì BỎ điểm và ghi lý do.
"""
from __future__ import annotations

import re
from datetime import date, time

from .pois import load_pois
from .textutil import fold
from .tools.travel import travel_minutes

MIN_VISIT = 30  # rút ngắn tối đa xuống 30 phút nếu khung mở còn lại ngắn hơn thời lượng đề xuất
MAX_WAIT = 90   # chờ quá 90 phút để điểm mở cửa thì coi là không hợp


def _m(t: str | time) -> int:
    if isinstance(t, str):
        t = time.fromisoformat(t[:5])
    return t.hour * 60 + t.minute


def _fmt(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def meal_label(start_min: int) -> str:
    """Nhãn bữa ăn theo giờ thật (code quyết, tránh 'ăn trưa' lúc 16:38)."""
    if 10 * 60 + 30 <= start_min <= 14 * 60:
        return "Ăn trưa"
    if 17 * 60 + 30 <= start_min <= 20 * 60 + 30:
        return "Ăn tối"
    return "Nghỉ, ăn nhẹ"


def schedule(stops: list[dict], req: dict) -> dict:
    pois = load_pois()
    d = date.fromisoformat(req["date"])
    cur = _m(req.get("start", "08:00"))
    end_win = _m(req.get("end", "21:00"))
    here = pois.get(req.get("replan_from") or "")
    items: list[dict] = []
    dropped: list[str] = []
    seen: set[str] = set()

    for st in stops:
        kind = st.get("kind", "visit")
        minutes = int(st.get("minutes") or 60)
        if kind != "visit":
            why = st.get("why", "")
            if kind not in ("meal", "rest") or fold(why).startswith("di chuyen"):
                continue  # di chuyển do code tính, bỏ mục LLM tự chèn
            if cur + minutes > end_win:
                continue
            area = re.sub(r"^(ăn (trưa|tối|sáng)|nghỉ( ngơi)?)\s*(tại|ở)?\s*", "", why, flags=re.I).strip()
            items.append({"kind": "meal", "poi_id": None, "start": _fmt(cur), "end": _fmt(cur + minutes),
                          "why": f"{meal_label(cur)}{' – ' + area if area else ''}"})
            cur += minutes
            continue

        pid = st.get("poi_id")
        p = pois.get(pid)
        if not p:
            dropped.append(f"Bỏ '{pid}': không có trong danh mục.")
            continue
        if pid in seen or pid in req.get("avoid", []):
            continue
        reason = p.closed_reason(d)
        if reason:
            dropped.append(f"Không đi được {p.name}: {reason} ({p.hours_text(d)}).")
            continue
        arrive = cur + (travel_minutes(here, p) if here else 0)
        placed = False
        for a, b in p.intervals_on(d):
            s = max(arrive, _m(a))
            if s - arrive > MAX_WAIT:
                continue
            e = min(s + minutes, _m(b), end_win)
            if e - s >= min(MIN_VISIT, minutes):
                items.append({"kind": "visit", "poi_id": pid, "start": _fmt(s), "end": _fmt(e), "why": st.get("why", ""),
                              "travel_min": arrive - cur, "wait_min": s - arrive, "shortened": e - s < minutes})
                cur, here, placed = e, p, True
                seen.add(pid)
                break
        if not placed:
            dropped.append(f"Không xếp được {p.name}: tới nơi khoảng {_fmt(arrive)}, không còn đủ giờ mở ({p.hours_text(d)}) "
                           f"trong khung {req.get('start')}-{req.get('end')}.")
    return {"items": items, "dropped": dropped}
