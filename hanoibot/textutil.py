import unicodedata


def fold(s: str) -> str:
    """Chữ thường, bỏ dấu tiếng Việt (giống bước chuẩn hoá ở Day 11)."""
    s = unicodedata.normalize("NFKD", s.lower()).replace("đ", "d")
    return "".join(c for c in s if not unicodedata.combining(c))
