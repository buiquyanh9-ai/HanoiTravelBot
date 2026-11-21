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
    children = max(0, min(int(req.get("children") or 0), people))
    adults = people - children
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
            child_price = p.child_ticket_vnd if p.child_ticket_vnd is not None else p.ticket_vnd
            tickets += p.ticket_vnd * adults + child_price * children
            dur = _mins(it["end"]) - _mins(it["start"])
            walk_km += p.walk_km_onsite * min(1.0, dur / max(1, p.visit_minutes))
            here = p
        elif it.get("kind") == "meal":
            label = it.get("why", "")
            food += (MEAL_VND if label.startswith(("Ăn trưa", "Ăn tối", "Lunch", "Dinner")) else SNACK_VND) * people
    return {"people": people, "children": children, "tickets_vnd": tickets, "transport_vnd": transport,
            "food_vnd": food, "total_vnd": tickets + transport + food, "walk_km": round(walk_km, 1)}


def trim_to_budget(items: list[dict], req: dict) -> tuple[list[dict], list[str], bool]:
    """Bỏ dần mục làm tốn tiền nhất cho tới khi vừa ngân sách (code quyết, không nhờ LLM).
    Trả (items mới, danh sách mục đã bỏ, có vừa ngân sách không)."""
    budget = req.get("budget_vnd")
    if not budget:
        return items, [], True
    budget = int(budget)
    pois = load_pois()
    items = list(items)
    removed: list[str] = []
    while plan_totals(items, req)["total_vnd"] > budget:
        visits = [i for i in items if i.get("kind") == "visit"]
        if not items or (len(items) == 1 and visits):
            break
        best, best_total = None, None
        for idx, it in enumerate(items):
            if it.get("kind") == "visit" and len(visits) == 1:
                continue  # giữ ít nhất 1 điểm tham quan
            total = plan_totals(items[:idx] + items[idx + 1:], req)["total_vnd"]
            if best_total is None or total < best_total:
                best, best_total = idx, total
        if best is None:
            break
        it = items.pop(best)
        removed.append(pois[it["poi_id"]].name if it.get("poi_id") in pois else it.get("why", "bữa ăn"))
    return items, removed, plan_totals(items, req)["total_vnd"] <= budget
