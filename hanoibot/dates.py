"""Tính ngày từ câu chữ tiếng Việt bằng code. LLM hay nhầm thứ -> ngày (eval kịch bản 7)."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from .textutil import fold

WEEKDAY_WORDS = {
    0: [r"\bthu hai\b", r"thu 2", r"\bt2\b", r"\bmonday\b"], 1: [r"\bthu ba\b", r"thu 3", r"\bt3\b", r"\btuesday\b"],
    2: [r"\bthu tu\b", r"thu 4", r"\bt4\b", r"\bwednesday\b"], 3: [r"\bthu nam\b", r"thu 5", r"\bt5\b", r"\bthursday\b"],
    4: [r"\bthu sau\b", r"thu 6", r"\bt6\b", r"\bfriday\b"], 5: [r"\bthu bay\b", r"thu 7", r"\bt7\b", r"\bsaturday\b"],
    6: [r"\bchu nhat\b", r"\bcn\b", r"\bsunday\b"],
}


def invalid_date(text: str, now: datetime) -> str | None:
    """Trả chuỗi ngày nếu khách ghi một ngày KHÔNG tồn tại (31/02, 30/2, 32/1, 15/13)."""
    for m in re.finditer(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{4}))?\b", fold(text)):
        dd, mm, yy = int(m.group(1)), int(m.group(2)), int(m.group(3) or now.year)
        try:
            date(yy, mm, dd)
        except ValueError:
            return m.group(0)
    return None


def resolve_date(text: str, now: datetime) -> date | None:
    q = fold(text)
    today = now.date()
    m = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{4}))?\b", q)
    if m:
        dd, mm, yy = int(m.group(1)), int(m.group(2)), int(m.group(3) or today.year)
        try:
            d = date(yy, mm, dd)
            return d if m.group(3) or d >= today else date(yy + 1, mm, dd)
        except ValueError:
            pass
    m = re.search(r"\b(\d{1,3}) ngay (nua|toi)\b|\bsau (\d{1,3}) ngay\b|\bin (\d{1,3}) days\b|\b(\d{1,3}) days (from now|later)\b", q)
    if m:
        return today + timedelta(days=int(next(g for g in m.groups() if g and g.isdigit())))
    m = re.search(r"\b(\d{1,2}) tuan (nua|toi)\b|\bin (\d{1,2}) weeks\b", q)
    if m:
        return today + timedelta(days=7 * int(next(g for g in m.groups() if g and g.isdigit())))
    next_week = bool(re.search(r"tuan (sau|toi)|next week", q))
    for wd, pats in WEEKDAY_WORDS.items():
        if any(re.search(p, q) for p in pats):
            delta = (wd - today.weekday()) % 7
            d = today + timedelta(days=delta)
            if next_week and d.isocalendar()[1] == today.isocalendar()[1]:
                d += timedelta(days=7)
            return d
    if re.search(r"ngay kia|\bngay mot\b|day after tomorrow", q):
        return today + timedelta(days=2)
    if re.search(r"(ngay mai|\bmai\b(?! chau)|\btomorrow\b)", q):
        return today + timedelta(days=1)
    if re.search(r"hom nay|bay gio|chieu nay|sang nay|toi nay|luc nay|\btoday\b|\btonight\b|"
                 r"\bthis (morning|afternoon|evening)\b|\bnow\b", q):
        return today
    return None
