"""Web HTTP tối giản, chỉ dùng thư viện chuẩn: python -m hanoibot.server --port 8000

GET  /            giao diện chat
GET  /health      {"ok": true}
POST /api/chat    {"message": "..."} -> {"answer", "intent", "lang", "latency_s", "n_llm"}
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .bot import handle
from .config import ROOT

INDEX = (ROOT / "hanoibot" / "static" / "index.html").read_bytes()
MAX_BODY = 8_000


class Handler(BaseHTTPRequestHandler):
    server_version = "HanoiTravelBot/0.2"

    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/index.html"):
            self._send(200, INDEX, "text/html; charset=utf-8")
        elif self.path == "/health":
            self._json(200, {"ok": True})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/chat":
            return self._json(404, {"error": "not found"})
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BODY:
            return self._json(400, {"error": "body rỗng hoặc quá lớn"})
        try:
            msg = str(json.loads(self.rfile.read(n).decode("utf-8")).get("message", "")).strip()
        except (ValueError, UnicodeDecodeError):
            return self._json(400, {"error": "JSON không hợp lệ"})
        if not msg:
            return self._json(400, {"error": "thiếu message"})
        out = handle(msg)
        self._json(200, {"answer": out["answer"], "intent": out.get("intent"), "lang": out.get("lang"),
                         "latency_s": out.get("latency_s"), "n_llm": len(out.get("llm_calls", []))})

    def log_message(self, fmt: str, *args) -> None:  # không ghi nội dung tin nhắn ra log
        print(f"{self.address_string()} {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")


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
