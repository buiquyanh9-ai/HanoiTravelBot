"""Tổng chi phí và quãng đi bộ của lịch trình – tính bằng code từ dữ liệu, không để LLM ước lượng.

Vé theo ticket_rules của từng điểm (nguồn chính thức): miễn/giảm chỉ khi BIẾT tuổi trẻ hoặc khách nói có thẻ HSSV.
Chưa biết tuổi -> tính như người lớn (an toàn cho ngân sách) và ghi chú điều kiện để khách cho biết tuổi.
"""
from __future__ import annotations

from .pois import load_pois
from .tools.travel import leg

MEAL_VND = 60000    # bữa chính bình dân / người (ước tính)
SNACK_VND = 30000   # ăn nhẹ, đồ uống / người (ước tính)


def _mins(s: str) -> int:
    return int(s[:2]) * 60 + int(s[3:5])


def travelers(req: dict) -> dict:
    """Nhóm khách: người lớn, trẻ biết tuổi, trẻ chưa rõ tuổi, HSSV (có thẻ)."""
    people = max(1, int(req.get("people") or 1))
    children = max(0, min(int(req.get("children") or 0), people))
    ages = [int(a) for a in (req.get("child_ages") or []) if isinstance(a, (int, float)) and 0 <= a <= 17][:children]
    students = max(0, min(int(req.get("students") or 0), people - children))
    level = req.get("student_level") if req.get("student_level") in ("hs", "sv") else "hssv"
    return {"people": people, "children": children, "ages": ages, "unknown_kids": children - len(ages),
            "students": students, "level": level, "adults": people - children - students}


def ticket_cost(p, req: dict) -> tuple[int, list[dict]]:
    """Tiền vé cả nhóm cho một điểm + các quy tắc miễn/giảm đã áp dụng."""
    g = travelers(req)
    total = p.ticket_vnd * (g["adults"] + g["unknown_kids"])
    used = []
    for a in g["ages"]:
        price, rule = p.price_for(age=a)
        total += price
        if rule:
            used.append(rule)
    if g["students"]:
        price, rule = p.price_for(card=g["level"])
        total += price * g["students"]
        if rule:
            used.append(rule)
    return total, used


def plan_totals(items: list[dict], req: dict) -> dict:
    pois = load_pois()
    g = travelers(req)
    people = g["people"]
    here = pois.get(req.get("replan_from") or "")
    tickets = transport = food = 0
    walk_km = walk_legs_km = 0.0
    applied: dict[str, set[str]] = {}
    for it in items:
        if it.get("kind") == "visit" and it.get("poi_id") in pois:
            p = pois[it["poi_id"]]
            lg = leg(here, p, people)
            transport += lg.cost_vnd
            if lg.mode == "walk":
                walk_km += lg.km
                walk_legs_km += lg.km
            cost, used = ticket_cost(p, req)
            tickets += cost
            for r in used:
                applied.setdefault(p.id, set()).add(r["label"])
            dur = _mins(it["end"]) - _mins(it["start"])
            walk_km += p.walk_km_onsite * min(1.0, dur / max(1, p.visit_minutes))
            here = p
        elif it.get("kind") == "meal":
            label = it.get("why", "")
            food += (MEAL_VND if label.startswith(("Ăn trưa", "Ăn tối", "Lunch", "Dinner")) else SNACK_VND) * people
    return {"people": people, "children": g["children"], "tickets_vnd": tickets, "transport_vnd": transport,
            "food_vnd": food, "total_vnd": tickets + transport + food, "walk_km": round(walk_km, 1),
            "walk_legs_km": round(walk_legs_km, 1), "walk_onsite_km": round(walk_km - walk_legs_km, 1),
            "discounts": {k: sorted(v) for k, v in applied.items()}, "unknown_age_kids": g["unknown_kids"]}


def ticket_notes(items: list[dict], req: dict, lang: str = "vi") -> list[str]:
    """Ghi chú vé cho nhóm có trẻ em/HSSV: đã áp dụng miễn/giảm nào, cần giấy tờ gì, chỗ nào chưa tính được."""
    pois = load_pois()
    g = travelers(req)
    vi = lang == "vi"
    visited = [pois[i["poi_id"]] for i in items if i.get("kind") == "visit" and i.get("poi_id") in pois]
    out = []
    for p in visited:
        name = p.display(lang)
        hits = {}
        for a in g["ages"]:
            _, r = p.price_for(age=a)
            if r:
                hits[r["label"]] = r
        if g["students"]:
            _, r = p.price_for(card=g["level"])
            if r:
                hits[r["label"]] = r
        for r in hits.values():
            lab = r["label"] if vi else r.get("label_en", r["label"])
            price = ("miễn phí" if vi else "free") if r["price"] == 0 else (f"{r['price']:,}đ".replace(",", ".") if vi else f"{r['price']:,} VND")
            proof = f" – mang {r['proof']}" if vi and r.get("proof") else ""
            first = lab.split()[0]
            lab = lab if first.isupper() and len(first) > 1 else lab[0].lower() + lab[1:]  # giữ "HSSV", hạ "Dưới" -> "dưới"
            out.append(f"{name}: đã tính vé {lab} {price}{proof}." if vi else f"{name}: applied the {lab} ticket ({price}).")
        hr = p.height_rule()
        if hr and g["children"]:
            vnd = f"{hr['price']:,}".replace(",", ".")
            out.append(f"{name}: trẻ cao từ 1,3m trở xuống chỉ {vnd}đ; mình chưa biết chiều cao nên đã tính giá người lớn."
                       if vi else f"{name}: children 1.3 m or under pay {hr['price']:,} VND; height unknown, so the adult price was used.")
        if p.id == "lang_bac" and any(a < 2 for a in g["ages"]):
            out.append("Lăng Bác không tiếp nhận trẻ dưới 2 tuổi vào viếng." if vi else "The Mausoleum does not admit children under 2.")
    if g["unknown_kids"] and any(any("max_age" in r for r in p.ticket_rules) for p in visited):
        out.append(f"Chưa biết tuổi của {g['unknown_kids']} trẻ nên vé tính như người lớn; nhiều điểm miễn vé dưới 16 tuổi – "
                   f"cho mình biết tuổi để tính lại." if vi else
                   f"Ages of {g['unknown_kids']} child(ren) unknown, so adult prices were used; several sites are free under 16 – "
                   f"tell me their ages to recalculate.")
    return out


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
