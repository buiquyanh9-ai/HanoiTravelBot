"""Danh mục điểm tham quan và kiểm tra giờ mở cửa bằng code (không để LLM tự nói)."""
from __future__ import annotations

import json
import re
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
    ticket_en: str = ""
    ticket_rules: list[dict] = field(default_factory=list)  # miễn/giảm theo tuổi, thẻ HSSV, chiều cao (xem _meta)
    tips: list[str] = field(default_factory=list)      # lưu ý khi tham quan ĐÃ KIỂM CHỨNG (có nguồn)
    tips_en: list[str] = field(default_factory=list)
    seasonal_hours: list[dict] = field(default_factory=list)  # [{"from": "04-01", "to": "10-31", "hours": {...}}]

    def display(self, lang: str = "vi") -> str:
        return self.name_en if lang == "en" and self.name_en else self.name

    @property
    def free(self) -> bool:
        return self.ticket_vnd == 0

    def price_for(self, age: int | None = None, card: str | None = None) -> tuple[int, dict | None]:
        """Giá vé một người theo quy tắc chính thức. Chỉ áp dụng miễn/giảm khi BIẾT tuổi hoặc có thẻ;
        không đoán chiều cao. card: "hs", "sv" hoặc "hssv" (là HSSV nhưng chưa rõ cấp -> lấy mức cao hơn cho an toàn).
        Ưu tiên miễn trước giảm: lấy mức thấp nhất trong các quy tắc đủ điều kiện."""
        best: tuple[int, dict | None] = (self.ticket_vnd, None)
        card_hits = []
        for r in self.ticket_rules:
            if "max_age" in r and age is not None and age <= r["max_age"] and r["price"] < best[0]:
                best = (r["price"], r)
            if "card" in r and card:
                if r["card"] == card or r["card"] == "hssv" or (card == "hssv" and r["card"] in ("hs", "sv")):
                    card_hits.append(r)
        if card_hits:
            r = max(card_hits, key=lambda x: x["price"]) if card == "hssv" else min(card_hits, key=lambda x: x["price"])
            if r["price"] < best[0]:
                best = (r["price"], r)
        return best

    def height_rule(self) -> dict | None:
        return next((r for r in self.ticket_rules if "max_height_m" in r), None)

    def ticket_text(self, lang: str = "vi") -> str:
        """Cùng một nội dung vé cho cả hai ngôn ngữ (kể cả ghi chú 'nguồn mâu thuẫn').
        Điểm vào tự do (phố cổ, Lăng Bác...) -> "" để không ghi "vé: miễn phí"; chỉ giữ ghi chú phụ nếu có
        (ví dụ Hồ Gươm: "đền Ngọc Sơn thu vé riêng")."""
        raw = (self.ticket_en or "") if lang == "en" else self.ticket
        if self.free:
            extra = re.search(r"\((.+)\)", raw or "")
            return extra.group(1) if extra else ""
        if lang == "en" and not raw:
            return f"~{self.ticket_vnd:,} VND"
        return raw

    def intervals_on(self, d: date) -> list[tuple[time, time]]:
        """Các khung giờ mở trong ngày d (rỗng = đóng cả ngày)."""
        iso = d.isoformat()
        for start, end, *_ in self.closed_ranges:
            if start <= iso <= end:
                return []
        raw = self.week_hours(d).get(WEEKDAYS[d.weekday()], [])
        return [(time.fromisoformat(a), time.fromisoformat(b)) for a, b in raw]

    def week_hours(self, d: date) -> dict:
        """Bảng giờ theo tuần áp dụng cho ngày d: seasonal_hours (vd Lăng Bác mùa nóng 01/04–31/10) đè bảng thường."""
        md = d.strftime("%m-%d")
        for s in self.seasonal_hours:
            a, b = s["from"], s["to"]
            if (a <= md <= b) if a <= b else (md >= a or md <= b):  # hỗ trợ khoảng vắt qua năm
                return s["hours"]
        return self.hours

    def closed_reason(self, d: date) -> str | None:
        iso = d.isoformat()
        for start, end, *reason in self.closed_ranges:
            if start <= iso <= end:
                return reason[0] if reason else "đóng cửa đặc biệt"
        if not self.week_hours(d).get(WEEKDAYS[d.weekday()]):
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
        if len(iv) == 1 and iv[0][0] == time(0, 0) and iv[0][1] >= time(23, 59):
            return "open all day" if lang == "en" else "cả ngày"  # phố cổ, Hồ Gươm: không ghi "00:00-23:59"
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
