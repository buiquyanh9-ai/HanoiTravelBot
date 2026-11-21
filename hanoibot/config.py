"""Cấu hình chung: đọc .env, múi giờ Hà Nội, timeout."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

HANOI_TZ = timezone(timedelta(hours=7))
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "8"))
CACHE_DIR = ROOT / "runs" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
WAQI_TOKEN = os.getenv("WAQI_TOKEN", "")
BESTTIME_API_KEY_PRIVATE = os.getenv("BESTTIME_API_KEY_PRIVATE", "")
TOMTOM_API_KEY = os.getenv("TOMTOM_API_KEY", "")

# Tâm khu vực nghiên cứu (Hồ Gươm) dùng cho thời tiết / AQI chung
HANOI_CENTER = (21.0287, 105.8523)


def now_hanoi() -> datetime:
    return datetime.now(HANOI_TZ)
