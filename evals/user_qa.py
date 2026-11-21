"""QA theo hành vi người dùng (bổ sung sau review Codex #7): hội thoại nhiều lượt, đầu vào lỗi, ngân sách nhóm,
thời tiết theo địa danh/ngày, yêu cầu không làm được. Gọi qua HTTP thật (server trong tiến trình) để bắt lỗi rơi kết nối.

python -m evals.user_qa
"""
from __future__ import annotations

import json
import re
import sys
import threading
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hanoibot import server  # noqa: E402


def money_values(ans: str) -> list[int]:
    m = re.search(r"(?:Chi phí ước tính cho \d+ người|Estimated cost for \d+ person\(s\)): ([\d.,]+)", ans)
    return [int(re.sub(r"[.,]", "", m.group(1)))] if m else []


CASES = [
    {"id": "U1 ngân sách nhóm", "turns": ["Chủ nhật này nhóm 3 người, tổng ngân sách 150k, đi từ 8h tới 12h, thích lịch sử"],
     "check": lambda a: (money_values(a[-1]) and money_values(a[-1])[0] <= 150000) or "không đủ" in a[-1]},
    {"id": "U2 nhiều lượt: bỏ điểm", "turns": ["Thứ Bảy này đi Văn Miếu và Hỏa Lò, từ 8h đến 12h nhé", "Bỏ Hỏa Lò đi"],
     "check": lambda a: "Văn Miếu" in a[-1] and "Hỏa Lò" not in a[-1].split("\n\n")[0] and "Thứ Bảy" in a[-1]},
    {"id": "U3 nhiều lượt: thêm người", "turns": ["Chủ nhật này 8h-12h đi Văn Miếu và Hoàng thành", "Thêm 2 người nữa, tổng 3 người"],
     "check": lambda a: "cho 3 người" in a[-1] and "Chủ nhật" in a[-1]},
    {"id": "U4 ngày không tồn tại", "turns": ["Ngày 31/02 đi Văn Miếu được không?"], "check": lambda a: "không tồn tại" in a[-1]},
    {"id": "U5 ngày đã qua", "turns": ["Lên lịch ngày 01/01/2026 đi Hỏa Lò"], "check": lambda a: "đã qua" in a[-1]},
    {"id": "U6 không đoán vị trí", "turns": ["Gần đây có chỗ nào chơi không?"], "check": lambda a: "đang ở khu nào" in a[-1]},
    {"id": "U7 thời tiết nơi khác, quá xa", "turns": ["Thời tiết ở Ba Vì 90 ngày nữa thế nào?"],
     "check": lambda a: "16 ngày" in a[-1] and "Hoàn Kiếm" not in a[-1]},
    {"id": "U8 thời tiết nơi khác, ngày mai", "turns": ["Ngày mai thời tiết ở Ba Vì thế nào?"],
     "check": lambda a: "Ba Vì" in a[-1] and "Hoàn Kiếm" not in a[-1]},
    {"id": "U9 không đặt vé", "turns": ["Đặt giúp mình 2 vé vào Hoàng thành chiều nay"], "check": lambda a: "không đặt vé" in a[-1]},
    {"id": "U10 nhóm có trẻ em", "turns": ["Chủ nhật này 8h-12h gia đình 4 người gồm 2 bé 10 tuổi đi Văn Miếu và Hoàng thành"],
     "check": lambda a: "cho 4 người" in a[-1]},
    {"id": "U11 đầu vào lỗi", "turns": ["???!!! 🙃🙃", "asdkjh qweoiu zxcmnb"], "check": lambda a: all(x.strip() for x in a)},
    {"id": "U12 tiếng Anh nhiều lượt", "turns": ["Plan Sunday 8am-12pm, I like museums", "Remove the zoo and add Hoa Lo"],
     "check": lambda a: "Itinerary for Sunday" in a[-1] and "Zoo" not in a[-1].split("\n\n")[0]},
]


def post(base: str, msg: str, sid: str) -> dict:
    req = urllib.request.Request(base + "/api/chat", data=json.dumps({"message": msg, "session": sid}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return json.load(e)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    rows = []
    try:
        for i, c in enumerate(CASES):
            sid = f"qa{i}-{datetime.now():%H%M%S}"
            answers = []
            for turn in c["turns"]:
                try:
                    answers.append(post(base, turn, sid).get("answer") or "")
                except Exception as e:  # rơi kết nối = trượt
                    answers.append(f"<lỗi HTTP: {type(e).__name__}>")
            ok = bool(c["check"](answers))
            rows.append({"id": c["id"], "ok": ok, "turns": c["turns"], "answers": answers})
            print(f"[{'ĐẠT' if ok else 'TRƯỢT'}] {c['id']}" + ("" if ok else f"  -> {answers[-1][:160]!r}"))
    finally:
        srv.shutdown()
        srv.server_close()
    print(f"\nQA người dùng: {sum(r['ok'] for r in rows)}/{len(rows)} đạt")
    out = ROOT / "runs" / f"user_qa_{datetime.now():%Y%m%dT%H%M%S}.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Ghi:", out)


if __name__ == "__main__":
    main()
