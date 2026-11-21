"""Tìm tin trên web qua Tavily (dùng lại cách Day 03) – để bắt thông báo đóng cửa đột xuất / sự kiện."""
from __future__ import annotations

from ..config import TAVILY_API_KEY
from ._http import cached, post_json


def web_search(query: str, days: int = 7, max_results: int = 5) -> dict:
    if not TAVILY_API_KEY:
        return {"items": [], "_error": "chưa có TAVILY_API_KEY"}

    def fetch() -> dict:
        j = post_json(
            "https://api.tavily.com/search",
            {"api_key": TAVILY_API_KEY, "query": query, "topic": "news", "days": days,
             "max_results": max_results, "search_depth": "basic"},
            timeout=15,
        )
        return {"items": [{"title": r.get("title"), "url": r.get("url"), "snippet": (r.get("content") or "")[:300],
                           "published": r.get("published_date")} for r in j.get("results", [])]}

    return cached(f"web:{query}:{days}", 6 * 3600, fetch)
