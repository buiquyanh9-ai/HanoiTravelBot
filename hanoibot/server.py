"""Web HTTP tối giản, chỉ dùng thư viện chuẩn: python -m hanoibot.server --port 8000

GET  /            ứng dụng nhiều tab (Trò chuyện, Điểm tham quan, Thời tiết, Tự xếp lịch, Giới thiệu)
GET  /health      {"ok": true}
POST /api/chat    {"message", "session"} -> {"answer", "intent", "lang", "latency_s", "n_llm", "actions", "reused_weather"}
Các API của tab (phần lớn không gọi LLM): xem hanoibot/api.py
"""
from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import api
from .bot import handle
from .config import CACHE_DIR, ROOT, now_hanoi

INDEX = (ROOT / "hanoibot" / "static" / "index.html").read_bytes()
MAX_BODY = 8_000
SESSION_TTL = 2 * 3600
SESSION_FILE = CACHE_DIR / "sessions.json"  # runs/cache/ nằm trong .gitignore
_lock = threading.Lock()


def _load_sessions() -> dict[str, dict]:
    """Ngữ cảnh nhiều lượt được ghi xuống đĩa để khởi động lại máy chủ không mất; hết hạn sau 2 giờ."""
    try:
        data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        now = time.time()
        return {k: v for k, v in data.items() if isinstance(v, dict) and now - v.get("ts", 0) <= SESSION_TTL}
    except (OSError, ValueError):
        return {}


def _save_sessions() -> None:
    try:
        with _lock:
            # khoá "_" (dữ liệu thời tiết đã lấy...) chỉ giữ trong RAM, không ghi đĩa
            snap = json.dumps({sid: {k: v for k, v in s.items() if not k.startswith("_")} for sid, s in SESSIONS.items()},
                              ensure_ascii=False, default=str)
    except (RuntimeError, TypeError, ValueError):  # phiên khác đang ghi dở: lần lưu sau sẽ bắt kịp
        return
    try:
        tmp = SESSION_FILE.with_suffix(".tmp")
        tmp.write_text(snap, encoding="utf-8")
        tmp.replace(SESSION_FILE)
    except OSError:
        pass  # lưu phiên là phụ, lỗi ghi đĩa thì vẫn chạy bằng RAM


SESSIONS: dict[str, dict] = _load_sessions()


def _session(sid: str) -> dict | None:
    if not sid:
        return None
    now = time.time()
    with _lock:
        for k in [k for k, v in SESSIONS.items() if now - v["ts"] > SESSION_TTL]:
            del SESSIONS[k]
        if len(SESSIONS) > 1000:
            SESSIONS.pop(next(iter(SESSIONS)))
        s = SESSIONS.setdefault(sid, {"ts": now})
        s["ts"] = now
        return s


class Handler(BaseHTTPRequestHandler):
    server_version = "HanoiTravelBot/0.3"

    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _run(self, fn, *args) -> None:
        """Gọi một API của tab: lỗi đầu vào -> 400 có thông điệp; lỗi khác -> 500, không rơi kết nối."""
        try:
            self._json(200, fn(*args))
        except api.BadRequest as e:
            self._json(400, {"error": str(e)})
        except Exception as e:
            print(f"api error: {type(e).__name__}")
            self._json(500, {"error": "Có lỗi khi xử lý, bạn thử lại nhé."})

    def do_GET(self) -> None:  # noqa: N802
        u = urlsplit(self.path)
        qs = {k: v[0][:200] for k, v in parse_qs(u.query).items()}
        if u.path in ("/", "/index.html"):
            self._send(200, INDEX, "text/html; charset=utf-8")
        elif u.path == "/health":
            self._json(200, {"ok": True})
        elif u.path == "/api/app":
            self._run(api.app_info, qs.get("lang"))
        elif u.path == "/api/places":
            self._run(api.places, qs, now_hanoi())
        elif u.path.startswith("/api/place/"):
            self._run(api.place_detail, u.path.rsplit("/", 1)[-1][:40], qs, now_hanoi())
        elif u.path == "/api/content":
            self._run(api.content, qs, now_hanoi())
        elif u.path == "/api/areas":
            self._run(api.area_suggest, qs)
        elif u.path == "/api/weather":
            sid = qs.get("session", "")[:64]
            self._run(api.weather, qs, now_hanoi(), _session(sid))  # chung bộ nhớ thời tiết với chat
        else:
            self._json(404, {"error": "not found"})

    def _body(self) -> dict | None:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BODY:
            self._json(400, {"error": "body rỗng hoặc quá lớn"})
            return None
        try:
            body = json.loads(self.rfile.read(n).decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError
            return body
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"error": "JSON không hợp lệ", "answer": "Yêu cầu không hợp lệ, bạn gửi lại nhé."})
            return None

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path not in ("/api/chat", "/api/schedule", "/api/plan"):
            return self._json(404, {"error": "not found"})
        body = self._body()
        if body is None:
            return None
        sid = str(body.get("session", ""))[:64]
        if path == "/api/schedule":
            self._run(api.manual_schedule, body, now_hanoi(), _session(sid))
            return _save_sessions() if sid else None
        if path == "/api/plan":
            self._run(api.ai_plan, body, now_hanoi(), _session(sid))
            return _save_sessions() if sid else None
        msg = str(body.get("message", "")).strip()
        if not msg:
            return self._json(400, {"error": "thiếu message", "answer": "Bạn chưa nhập câu hỏi."})
        try:
            out = handle(msg, session=_session(sid))
        except Exception as e:  # không để rơi kết nối: luôn trả JSON
            print(f"handle error: {type(e).__name__}")
            return self._json(500, {"answer": "Xin lỗi, có lỗi khi xử lý câu này. Bạn thử diễn đạt lại nhé.",
                                    "intent": "error"})
        if sid:
            _save_sessions()
        self._json(200, {"answer": out["answer"], "intent": out.get("intent"), "lang": out.get("lang"),
                         "latency_s": out.get("latency_s"), "n_llm": len(out.get("llm_calls", [])),
                         "actions": out.get("actions", []), "reused_weather": bool(out.get("reused_weather"))})

    def log_message(self, fmt: str, *args) -> None:  # không ghi nội dung tin nhắn hay query (tên nơi) ra log
        print(f"{self.address_string()} {self.command} {urlsplit(self.path).path} -> {args[1] if len(args) > 1 else ''}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"HanoiTravelBot web: http://{a.host}:{a.port}/")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
