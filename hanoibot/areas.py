"""Quận/huyện Hà Nội cho dự báo thời tiết: gợi ý khi gõ ("Ha" -> Hai Bà Trưng, Hà Đông) và nhận diện trong câu chat.

Khớp không dấu, không phân biệt hoa thường, bỏ tiền tố "quận/huyện/thị xã" và đuôi ", Hà Nội".
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache

from .config import ROOT
from .textutil import fold

PREFIX = re.compile(r"^(quan|huyen|thi xa|tx|q)\.?\s+")
SUFFIX = re.compile(r"[,\s]+(tp\.?\s*)?(thanh pho\s+)?(ha noi|hanoi|hn)$")


@dataclass(frozen=True)
class Area:
    id: str
    name: str
    kind: str
    lat: float
    lon: float

    @property
    def key(self) -> str:
        return fold(self.name)

    @property
    def label(self) -> str:
        return f"{self.kind.lower()} {self.name}"

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "kind": self.kind, "label": f"{self.kind} {self.name}"}


@lru_cache(maxsize=1)
def load() -> tuple[Area, ...]:
    raw = json.loads((ROOT / "data" / "hanoi_areas.json").read_text(encoding="utf-8"))
    return tuple(Area(**a) for a in raw["areas"])


def _clean(text: str) -> str:
    q = fold(text or "").strip()
    q = SUFFIX.sub("", q).strip()
    return PREFIX.sub("", q).strip()


def search(text: str, limit: int = 8) -> list[Area]:
    """Gợi ý theo chữ đang gõ: khớp đầu tên > khớp đầu một chữ > chứa chuỗi; giữ thứ tự danh sách khi bằng điểm."""
    q = _clean(text)
    areas = load()
    if not q:
        return list(areas[:limit])
    compact = q.replace(" ", "")
    scored = []
    for i, a in enumerate(areas):
        k = a.key
        if k.startswith(q):
            s = 0
        elif any(w.startswith(q) for w in k.split()) or k.replace(" ", "").startswith(compact):
            s = 1
        elif q in k or compact in k.replace(" ", ""):
            s = 2
        else:
            continue
        scored.append((s, i, a))
    if any(s < 2 for s, _, _ in scored):  # có tên bắt đầu bằng chữ đang gõ -> bỏ kết quả chỉ chứa chuỗi ở giữa
        scored = [x for x in scored if x[0] < 2]
    return [a for _, _, a in sorted(scored)[:limit]]


def resolve(text: str | None) -> Area | None:
    """Tên khách gõ -> quận/huyện. Khớp đúng tên trước; gõ dở (>= 2 ký tự) thì lấy gợi ý đầu tiên."""
    q = _clean(text or "")
    if not q:
        return None
    for a in load():
        if a.key == q or a.id == q.replace(" ", "_"):
            return a
    hits = search(q, 1)
    return hits[0] if hits and len(q) >= 2 else None


def mentioned(text: str) -> Area | None:
    """Quận/huyện được gọi tên trong câu chat ("thời tiết Cầu Giấy chiều nay"). Tên dài nhất thắng."""
    q = fold(text or "")
    hits = [a for a in load() if re.search(rf"\b{re.escape(a.key)}\b", q)]
    return max(hits, key=lambda a: len(a.key)) if hits else None
