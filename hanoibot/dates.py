"""Tính ngày từ câu chữ tiếng Việt bằng code. LLM hay nhầm thứ -> ngày (eval kịch bản 7)."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from .textutil import fold

WEEKDAY_WORDS = {
    0: [r"thu hai", r"thu 2", r"\bt2\b"], 1: [r"thu ba", r"thu 3", r"\bt3\b"], 2: [r"thu tu", r"thu 4", r"\bt4\b"],
    3: [r"thu nam", r"thu 5", r"\bt5\b"], 4: [r"thu sau", r"thu 6", r"\bt6\b"], 5: [r"thu bay", r"thu 7", r"\bt7\b"],
    6: [r"chu nhat", r"\bcn\b"],
}


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
    next_week = bool(re.search(r"tuan (sau|toi)", q))
    for wd, pats in WEEKDAY_WORDS.items():
        if any(re.search(p, q) for p in pats):
            delta = (wd - today.weekday()) % 7
            d = today + timedelta(days=delta)
            if next_week and d.isocalendar()[1] == today.isocalendar()[1]:
                d += timedelta(days=7)
            return d
    if re.search(r"ngay kia|\bngay mot\b", q):
        return today + timedelta(days=2)
    if re.search(r"(ngay mai|\bmai\b(?! chau))", q):
        return today + timedelta(days=1)
    if re.search(r"hom nay|bay gio|chieu nay|sang nay|toi nay|luc nay", q):
        return today
    return None
