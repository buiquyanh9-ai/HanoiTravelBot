"""Danh mục điểm tham quan và kiểm tra giờ mở cửa bằng code (không để LLM tự nói)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, time
from functools import lru_cache

from .config import ROOT
from .textutil import fold

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@dataclass
class Poi:
    id: str
    name: str
    district: str
    lat: float
    lon: float
    indoor: bool
    visit_minutes: int
    hours: dict[str, list[list[str]]]
    closed_ranges: list[list[str]]
    ticket: str
    confidence: str
    notes: str
    sources: list[str] = field(default_factory=list)
    name_en: str = ""
    ticket_vnd: int = 0
    walk_km_onsite: float = 0.0
    address: str = ""
    child_ticket_vnd: int | None = None

    def display(self, lang: str = "vi") -> str:
        return self.name_en if lang == "en" and self.name_en else self.name

    def intervals_on(self, d: date) -> list[tuple[time, time]]:
        """Các khung giờ mở trong ngày d (rỗng = đóng cả ngày)."""
        iso = d.isoformat()
        for start, end, *_ in self.closed_ranges:
            if start <= iso <= end:
                return []
        raw = self.hours.get(WEEKDAYS[d.weekday()], [])
        return [(time.fromisoformat(a), time.fromisoformat(b)) for a, b in raw]

    def closed_reason(self, d: date) -> str | None:
        iso = d.isoformat()
        for start, end, *reason in self.closed_ranges:
            if start <= iso <= end:
                return reason[0] if reason else "đóng cửa đặc biệt"
        if not self.hours.get(WEEKDAYS[d.weekday()]):
            return "nghỉ cố định ngày này trong tuần"
        return None

    def is_open_between(self, d: date, start: time, end: time) -> bool:
        return any(a <= start and end <= b for a, b in self.intervals_on(d))

    def is_open_at(self, dt: datetime) -> bool:
        t = dt.time()
        return any(a <= t <= b for a, b in self.intervals_on(dt.date()))

    def hours_text(self, d: date, lang: str = "vi") -> str:
        iv = self.intervals_on(d)
        if not iv:
            if lang == "en":
                from .i18n import REASON_EN
                r = self.closed_reason(d) or ""
                return f"CLOSED ({REASON_EN.get(r, r)})"
            return f"ĐÓNG CỬA ({self.closed_reason(d)})"
        return ", ".join(f"{a:%H:%M}-{b:%H:%M}" for a, b in iv)


@lru_cache(maxsize=1)
def load_pois() -> dict[str, Poi]:
    raw = json.loads((ROOT / "data" / "pois.json").read_text(encoding="utf-8"))
    return {p["id"]: Poi(**p) for p in raw["pois"]}


def find_poi(text: str) -> Poi | None:
    """Tìm POI theo id hoặc tên gần đúng (không dấu, chữ thường)."""

    pois = load_pois()
    if text in pois:
        return pois[text]
    q = fold(text)
    for pid, names in ALIASES.items():
        if any(n in q for n in names):
            return pois[pid]
    return None


def mentioned_ids(text: str) -> set[str]:
    q = fold(text)
    return {pid for pid, names in ALIASES.items() if any(n in q for n in names)}


ALIASES = {
    "lang_bac": ["lang bac", "lang chu tich", "lang ho chi minh", "mausoleum"],
    "van_mieu": ["van mieu", "quoc tu giam", "temple of literature"],
    "hoang_thanh": ["hoang thanh", "thang long", "citadel"],
    "bt_dan_toc_hoc": ["dan toc hoc", "ethnology"],
    "vuon_thu": ["vuon thu", "thu le", "so thu", "zoo"],
    "hoa_lo": ["hoa lo"],
    "bt_lich_su": ["lich su quoc gia", "bao tang lich su", "history museum", "museum of vietnamese history"],
    "ho_guom": ["ho guom", "ho hoan kiem", "pho di bo", "ngoc son", "hoan kiem lake", "sword lake"],
    "pho_co": ["pho co", "old quarter"],
}
