"""Gọi LLM, luôn yêu cầu JSON để code kiểm được.

Chuỗi dự phòng: OpenRouter key 1 -> OpenRouter key 2 -> Gemini (endpoint tương thích OpenAI).
Một nhà cung cấp lỗi/hết tiền thì tự chuyển sang nhà cung cấp kế tiếp.
"""
from __future__ import annotations

import json
import os
import re
import time

import httpx

from .config import OPENROUTER_API_KEY, OPENROUTER_MODEL


class LLMError(RuntimeError):
    pass


def providers() -> list[dict]:
    out = []
    for key in (OPENROUTER_API_KEY, os.getenv("OPENROUTER_API_KEY_2", "")):
        if key:
            out.append({"name": "openrouter", "url": "https://openrouter.ai/api/v1/chat/completions",
                        "key": key, "model": OPENROUTER_MODEL, "timeout": 45})
    if os.getenv("GEMINI_API_KEY"):
        out.append({"name": "gemini", "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                    "key": os.getenv("GEMINI_API_KEY"), "model": os.getenv("GEMINI_MODEL", "gemini-3.5-flash"),
                    "timeout": 25})
    return out


def chat_json(system: str, user: str, model: str | None = None, temperature: float = 0.2,
              max_tokens: int = 1500, timeout: float | None = None) -> tuple[dict, dict]:
    """Trả (json_obj, meta). meta gồm latency_s, provider, model, usage."""
    provs = providers()
    if not provs:
        raise LLMError("Không có key LLM nào trong .env")
    errors = []
    t0 = time.perf_counter()
    for prov in provs:
        payload = {
            "model": model if (model and prov["name"] == "openrouter") else prov["model"],
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        for attempt in range(2):
            try:
                r = httpx.post(prov["url"], json=payload, timeout=timeout or prov["timeout"],
                               headers={"Authorization": f"Bearer {prov['key']}"})
                if r.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                    time.sleep(1.5)
                    continue
                if r.status_code >= 400:
                    errors.append(f"{prov['name']} HTTP {r.status_code}")
                    break  # 401/402/404: sang nhà cung cấp kế
                j = r.json()
                text = j["choices"][0]["message"]["content"] or ""
                return _parse(text), {"latency_s": round(time.perf_counter() - t0, 2), "provider": prov["name"],
                                      "model": j.get("model", payload["model"]), "usage": j.get("usage", {}),
                                      "fallbacks": errors}
            except (httpx.HTTPError, KeyError, ValueError, IndexError) as e:
                errors.append(f"{prov['name']}: {type(e).__name__}")
                if attempt == 0 and isinstance(e, (httpx.TransportError,)):
                    continue
                break
    raise LLMError("Mọi nhà cung cấp LLM đều lỗi: " + "; ".join(errors))


def _parse(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise
