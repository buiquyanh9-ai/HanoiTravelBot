"""Câu hỏi về DỮ LIỆU CÓ SẴN trong ứng dụng (lọc danh mục điểm) – trả lời bằng code, không gọi LLM.

Ví dụ: "điểm nào miễn phí?", "những chỗ nào trong nhà?", "thứ Hai điểm nào mở?", "chỗ nào mở buổi tối?",
"có những điểm nào?", "vé rẻ nhất?". Câu xin LỊCH TRÌNH ("đi đâu chiều nay") không thuộc đây.
"""
from __future__ import annotations

import re
from datetime import datetime, time

from .dates import resolve_date
from .i18n import DAYS, money
from .pois import load_pois
from .textutil import fold

ASK_LIST = (r"\b(diem nao|nhung diem|cac diem|noi nao|nhung noi|cho nao|nhung cho|dia diem nao|co nhung (diem|dia diem|noi|cho)|"
            r"danh sach|bao nhieu diem|liet ke|which (places|sites|ones)|what places|list( of)? (places|sites|attractions)|"
            r"which (is|are)|any (places|sites))\b")
STRONG_PLAN = r"\b(lich trinh|xep lich|ke hoach|plan|itinerary|schedule|di dau|nen di|goi y|suggest|recommend)\b"

FILTERS = {
    "free": r"\b(mien phi|khong mat tien|khong ton tien|free)\b",
    "indoor": r"\b(trong nha|indoor|co mai che|khi mua)\b",
    "outdoor": r"\b(ngoai troi|outdoor|open air)\b",
    "evening": r"\b(buoi toi|ban dem|ve dem|mo khuya|toi muon|evening|at night|late)\b",
    "cheapest": r"\b(re nhat|gia thap nhat|cheapest|lowest price)\b",
    "priciest": r"\b(dat nhat|dat tien nhat|most expensive)\b",
    "low_conf": r"\b(do tin cay thap|chua chac|khong chac|low confidence|uncertain)\b",
}


def is_catalog_question(text: str, now: datetime) -> bool:
    q = fold(text)
    if re.search(STRONG_PLAN, q):
        return False
    has_filter = any(re.search(p, q) for p in FILTERS.values())
    return bool(re.search(ASK_LIST, q) and (has_filter or resolve_date(text, now) or
                                             re.search(r"\b(mo cua|mo|open)\b", q) or re.search(r"co nhung|danh sach|list|liet ke|bao nhieu", q))) \
        or bool(re.search(FILTERS["cheapest"] + "|" + FILTERS["priciest"], q))


def answer_catalog(text: str, now: datetime, lang: str = "vi") -> tuple[str, list[dict]]:
    pois = list(load_pois().values())
    q = fold(text)
    d = resolve_date(text, now) or now.date()
    active = {k for k, p in FILTERS.items() if re.search(p, q)}
    asks_open = bool(re.search(r"\b(mo cua|mo|open)\b", q)) or d != now.date()
    rows = pois
    labels = []
    if "free" in active:
        rows = [p for p in rows if p.ticket_vnd == 0]
        labels.append("miễn phí" if lang == "vi" else "free")
    if "indoor" in active:
        rows = [p for p in rows if p.indoor]
        labels.append("trong nhà" if lang == "vi" else "indoor")
    if "outdoor" in active:
        rows = [p for p in rows if not p.indoor]
        labels.append("ngoài trời" if lang == "vi" else "outdoor")
    if "low_conf" in active:
        rows = [p for p in rows if p.confidence == "low"]
        labels.append("độ tin cậy thấp" if lang == "vi" else "low confidence")
    if "evening" in active:
        rows = [p for p in rows if any(b >= time(19, 0) for _, b in p.intervals_on(d))]
        labels.append("mở tới tối" if lang == "vi" else "open in the evening")
    elif asks_open:
        rows = [p for p in rows if p.intervals_on(d)]
        labels.append(("mở " if lang == "vi" else "open on ") + f"{DAYS[lang][d.weekday()]} {d:%d/%m}")
    if "cheapest" in active or "priciest" in active:
        rows = sorted(rows, key=lambda p: p.ticket_vnd, reverse="priciest" in active)[:3]
        labels.append(("rẻ nhất" if "cheapest" in active else "đắt nhất") if lang == "vi"
                      else ("cheapest" if "cheapest" in active else "most expensive"))

    crit = ", ".join(labels) or ("tất cả" if lang == "vi" else "all")
    if not rows:
        msg = (f"Không có điểm nào khớp ({crit}) trong {len(pois)} điểm của ứng dụng." if lang == "vi"
               else f"No place matches ({crit}) among the app's {len(pois)} places.")
        return msg, [{"tab": "places", "label": "Xem tất cả điểm" if lang == "vi" else "See all places"}]
    head = (f"{len(rows)}/{len(pois)} điểm khớp ({crit}), giờ mở {DAYS['vi'][d.weekday()]} {d:%d/%m}:" if lang == "vi"
            else f"{len(rows)}/{len(pois)} places match ({crit}); hours on {DAYS['en'][d.weekday()]} {d:%d/%m}:")
    lines = [head]
    for p in rows:
        place = ("trong nhà" if p.indoor else "ngoài trời") if lang == "vi" else ("indoor" if p.indoor else "outdoor")
        if p.free:  # đến là vào được: không ghi "giá 0đ"; đang lọc "miễn phí" thì khỏi nhắc lại
            price = "" if "free" in active else ("; vào tự do" if lang == "vi" else "; free entry")
        else:
            price = f"; vé {money(p.ticket_vnd, lang)}đ" if lang == "vi" else f"; ticket {money(p.ticket_vnd, lang)} VND"
        hours = p.hours_text(d, lang)
        hours = (f"mở {hours}" if lang == "vi" else f"open {hours}") if p.intervals_on(d) else hours
        lines.append(f"• {p.display(lang)} – {hours}{price}; {place}"
                     + (f" [{'độ tin cậy' if lang == 'vi' else 'confidence'}: {p.confidence}]" if p.confidence == "low" else ""))
    lines.append("")
    paid = any(not p.free for p in rows)
    foot = (("Giá là vé người lớn, dùng để tính ngân sách; " if paid else "") + "chi tiết từng điểm ở tab Điểm tham quan."
            if lang == "vi" else ("Prices are adult tickets used for budgeting; " if paid else "") + "see the Places tab for details.")
    lines.append(foot[0].upper() + foot[1:])
    flt = {"date": d.isoformat(), "free": "free" in active, "indoor": "indoor" in active,
           "open_only": asks_open or "evening" in active}
    return "\n".join(lines), [{"tab": "places", "label": ("Mở trong tab Điểm tham quan" if lang == "vi" else "Open in Places"),
                               "filter": flt}]
