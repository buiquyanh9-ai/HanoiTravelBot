"""Chuẩn hoá chống lách cho tiếng Việt/Anh (mở rộng từ Day 11).

Sinh 2 dạng: `flat` (giữ khoảng trắng) và `joined` (bỏ hết khoảng trắng/dấu câu) để bắt kiểu "b ỏ q u a".
"""
from __future__ import annotations

import re
import unicodedata

ZERO_WIDTH = re.compile(r"[​-‏⁠-⁤﻿­]")
# Chữ Cyrillic/Greek trông giống Latin hay dùng để lách bộ lọc
HOMOGLYPH = str.maketrans({"а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ј": "j",
                           "ѕ": "s", "ԁ": "d", "һ": "h", "ӏ": "l", "ո": "n", "α": "a", "ο": "o", "ρ": "p", "ν": "v",
                           "Α": "a", "Β": "b", "Ε": "e", "Η": "h", "Ι": "i", "Κ": "k", "Μ": "m", "Ν": "n", "Ο": "o",
                           "Ρ": "p", "Τ": "t", "Χ": "x"})
LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i"})
# Teencode / viết tắt hay gặp
TEEN = [(r"\bko\b|\bk\b|\bkhg\b|\bkh\b", "khong"), (r"\bbo qa\b|\bbo wa\b|\bbq\b", "bo qua"), (r"\bh dan\b|\bhd\b", "huong dan"),
        (r"\bj\b", "gi"), (r"\bdc\b|\bdk\b", "duoc"), (r"\bns\b", "noi"), (r"\btl\b", "tra loi")]


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = ZERO_WIDTH.sub("", s).translate(HOMOGLYPH)
    s = unicodedata.normalize("NFKD", s.lower()).replace("đ", "d").replace("Đ", "d")
    return "".join(c for c in s if not unicodedata.combining(c))


def variants(text: str) -> dict[str, str]:
    base = fold(text)
    flat = re.sub(r"\s+", " ", base).strip()
    leet = flat.translate(LEET)
    for pat, rep in TEEN:
        leet = re.sub(pat, rep, leet)
    # Bỏ dấu câu chèn giữa chữ: "b.o.q.u.a" -> "boqua"
    depunct = re.sub(r"(?<=[a-z0-9])[.\-_*,~^`'](?=[a-z0-9])", "", leet)
    joined = re.sub(r"[^a-z0-9]", "", leet)
    # Gộp chữ bị tách từng ký tự: "b o q u a" -> "boqua" (chuỗi >= 3 ký tự đơn liền nhau)
    despaced = re.sub(r"\b(?:[a-z0-9] ){2,}[a-z0-9]\b", lambda m: m.group(0).replace(" ", ""), depunct)
    clean = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9/: ]", " ", despaced)).strip()  # mọi dấu câu -> khoảng trắng
    return {"flat": leet, "depunct": depunct, "despaced": despaced, "clean": clean, "joined": joined}
