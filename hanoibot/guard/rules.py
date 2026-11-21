"""Bộ phát hiện bằng luật.

- `naive`: regex trên chữ gốc (giống cách làm nhanh không chuẩn hoá) – dùng làm baseline.
- `normalized`: cùng ý nhưng chạy trên 3 dạng chuẩn hoá (flat / despaced / joined).
"""
from __future__ import annotations

import re

from .normalize import variants

# Mẫu trên chữ đã bỏ dấu, có khoảng trắng
FLAT = [
    r"\b(bo qua|phot lo|lo di|quen|xoa bo|vo hieu)\b.{0,40}\b(huong dan|chi dan|chi thi|quy tac|lenh|cau hinh|rang buoc)\b",
    r"\b(ignore|disregard|forget|override|bypass)\b.{0,40}\b(instructions?|rules?|prompt|guidelines|above|previous)\b",
    r"\b(system|he thong) ?prompt\b|\bprompt (he thong|goc|an)\b",
    r"\b(in|hien thi|tiet lo|cho (toi|minh) (xem|biet)|show|reveal|print|repeat)\b.{0,40}\b(prompt|chi thi (an|goc)|huong dan (an|goc)|system message|instructions?)\b",
    r"\b(ban|you)( bay gio| gio| now)? (la|is|are|se la)\b.{0,30}\b(ai khac|mo hinh khac|another (ai|model)|dan|admin|"
    r"khong (co )?(gioi han|quy tac)|no rules|unrestricted|unfiltered)\b",
    r"\b(developer|dev|god|admin|debug|jailbreak|dan) mode\b|\bche do (nha phat trien|quan tri|khong gioi han|debug)\b",
    r"\b(khong (con )?(bi )?(gioi han|kiem duyet|rang buoc))\b|\bno (restrictions|limits|filter)\b",
    r"\b(dong vai|gia vo|roleplay|pretend|act as)\b.{0,40}\b(khong (co )?(gioi han|quy tac)|no rules|unfiltered|jailbroken|evil)\b",
    r"(api[_ ]?key|mat khau|password|secret|token)\b.{0,30}\b(gui|send|post|chuyen|tra ve|in ra|cho (toi|minh))\b",
    r"\b(gui|send|post|upload|exfiltrate)\b.{0,60}(https? ?: ?//|webhook)",
]
# Mẫu trên chữ dính liền (bắt kiểu tách ký tự, chèn dấu câu)
JOINED = ["boquamoihuongdan", "boquahuongdan", "boquatatcahuongdan", "ignoreallprevious", "ignorepreviousinstructions",
          "ignoreallinstructions", "systemprompt", "developermode", "jailbreak", "quenhethuongdan", "forgetyourinstructions",
          "promptgoc", "promptan"]
NAIVE = [re.compile(p, re.I) for p in [
    r"bỏ qua (mọi|tất cả|các)? ?hướng dẫn", r"ignore (all )?(previous )?instructions", r"system prompt", r"jailbreak",
    r"developer mode"]]
FLAT_RE = [re.compile(p) for p in FLAT]
# Bản "khoảng trắng tuỳ chọn" cho chữ bị tách rồi gộp lại ("h u o n g d a n" -> "huongdan")
LOOSE_RE = [re.compile(p.replace(" ?", "\x00").replace(" ", " ?").replace("\x00", " ?")) for p in FLAT]


def naive(text: str) -> bool:
    return any(p.search(text) for p in NAIVE)


def normalized(text: str) -> tuple[bool, str]:
    v = variants(text)
    for form in ("flat", "depunct", "despaced", "clean"):
        for p in FLAT_RE:
            if p.search(v[form]):
                return True, f"{form}:{p.pattern[:40]}"
    for p in LOOSE_RE:
        if p.search(v["clean"]):
            return True, f"loose:{p.pattern[:40]}"
    for k in JOINED:
        if k in v["joined"]:
            return True, f"joined:{k}"
    return False, ""
