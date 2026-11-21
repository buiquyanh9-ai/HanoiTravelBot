"""Gọi LLM qua OpenRouter (key lấy từ repo Day 03). Luôn yêu cầu JSON để code kiểm được."""
from __future__ import annotations

import json
import re
import time

import httpx

from .config import OPENROUTER_API_KEY, OPENROUTER_MODEL

URL = "https://openrouter.ai/api/v1/chat/completions"


class LLMError(RuntimeError):
    pass


def chat_json(system: str, user: str, model: str | None = None, temperature: float = 0.2,
              max_tokens: int = 1500, timeout: float = 45) -> tuple[dict, dict]:
    """Trả (json_obj, meta). meta gồm latency_s và usage."""
    if not OPENROUTER_API_KEY:
        raise LLMError("Thiếu OPENROUTER_API_KEY")
    payload = {
        "model": model or OPENROUTER_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    t0 = time.perf_counter()
    last = None
    for attempt in range(3):
        try:
            r = httpx.post(URL, json=payload, timeout=timeout,
                           headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"})
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            r.raise_for_status()
            j = r.json()
            text = j["choices"][0]["message"]["content"] or ""
            return _parse(text), {"latency_s": round(time.perf_counter() - t0, 2), "usage": j.get("usage", {}),
                                  "model": j.get("model", payload["model"])}
        except (httpx.HTTPError, KeyError, ValueError) as e:
            last = e
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    raise LLMError(f"LLM lỗi: {last}")


def _parse(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise
