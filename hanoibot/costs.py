"""Tổng chi phí và quãng đi bộ của lịch trình – tính bằng code từ dữ liệu, không để LLM ước lượng."""
from __future__ import annotations

from .pois import load_pois
from .tools.travel import leg

MEAL_VND = 60000    # bữa chính bình dân / người (ước tính)
SNACK_VND = 30000   # ăn nhẹ, đồ uống / người (ước tính)


def _mins(s: str) -> int:
    return int(s[:2]) * 60 + int(s[3:5])


def plan_totals(items: list[dict], req: dict) -> dict:
    pois = load_pois()
    people = max(1, int(req.get("people") or 1))
    here = pois.get(req.get("replan_from") or "")
    tickets = transport = food = 0
    walk_km = 0.0
    for it in items:
        if it.get("kind") == "visit" and it.get("poi_id") in pois:
            p = pois[it["poi_id"]]
            lg = leg(here, p, people)
            transport += lg.cost_vnd
            if lg.mode == "walk":
                walk_km += lg.km
            tickets += p.ticket_vnd * people
            dur = _mins(it["end"]) - _mins(it["start"])
            walk_km += p.walk_km_onsite * min(1.0, dur / max(1, p.visit_minutes))
            here = p
        elif it.get("kind") == "meal":
            label = it.get("why", "")
            food += (MEAL_VND if label.startswith(("Ăn trưa", "Ăn tối", "Lunch", "Dinner")) else SNACK_VND) * people
    return {"people": people, "tickets_vnd": tickets, "transport_vnd": transport, "food_vnd": food,
            "total_vnd": tickets + transport + food, "walk_km": round(walk_km, 1)}
