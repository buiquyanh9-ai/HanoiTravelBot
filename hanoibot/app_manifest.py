"""Cấu trúc ứng dụng – NGUỒN DUY NHẤT cho cả giao diện (GET /api/app dựng tab) và chatbot (trả lời "app làm được gì",
"xem giá vé ở đâu"...). Sửa tính năng thì sửa ở đây, giao diện và bot tự khớp theo.

Chatbot trả lời các câu hỏi về ứng dụng bằng code từ bảng này, không gọi LLM.
"""
from __future__ import annotations

import re

from .textutil import fold

TABS = [
    {
        "id": "chat",
        "title": {"vi": "Trò chuyện", "en": "Chat"},
        "summary": {"vi": "Hỏi tự nhiên: lên lịch, giờ mở cửa, thời tiết. AI hiểu ý, còn giờ giấc và chi phí do code tính.",
                    "en": "Ask in plain words: itineraries, opening hours, weather. AI reads intent; times and costs are computed by code."},
        "uses_ai": True,
        "keywords": ["chat", "tro chuyen", "hoi dap", "nhan tin", "ask"],
        "steps": {"vi": ["Gõ câu hỏi vào ô dưới cùng, Enter để gửi.",
                         "Có thể sửa tiếp lịch: \"bỏ Hỏa Lò\", \"thêm 2 người\", \"ngân sách 300k\".",
                         "Bấm nút dưới câu trả lời để mở tab liên quan."],
                  "en": ["Type a question at the bottom and press Enter.",
                         "Refine a plan: \"remove Hoa Lo\", \"add 2 people\", \"budget 300k\".",
                         "Use the buttons under an answer to open the related tab."]},
    },
    {
        "id": "places",
        "title": {"vi": "Điểm tham quan", "en": "Places"},
        "summary": {"vi": "9 điểm: giờ mở cửa theo ngày, đang mở hay không, giá vé, độ tin cậy, nguồn, mức đông theo giờ.",
                    "en": "9 places: opening hours by date, open now, tickets, confidence, sources, hourly crowd levels."},
        "uses_ai": False,
        "keywords": ["diem tham quan", "dia diem", "gio mo cua", "gia ve", "ve vao", "mien phi", "trong nha", "ngoai troi",
                     "muc dong", "danh sach", "loc", "nguon", "do tin cay", "places", "opening hours", "ticket", "price",
                     "free", "indoor", "crowd", "filter", "list"],
        "steps": {"vi": ["Chọn ngày ở đầu tab để xem giờ mở của ngày đó.",
                         "Lọc: Mở ngày này, Trong nhà, Miễn phí, hoặc gõ tên để tìm.",
                         "Mở \"Chi tiết\" để xem ghi chú, nguồn và mức đông theo giờ.",
                         "Bấm \"Thêm vào lịch\" để đưa điểm sang tab Tự xếp lịch."],
                  "en": ["Pick a date at the top to see that day's hours.",
                         "Filter: Open that day, Indoor, Free, or search by name.",
                         "Open \"Details\" for notes, sources and hourly crowd levels.",
                         "Press \"Add to plan\" to send a place to the Planner tab."]},
    },
    {
        "id": "weather",
        "title": {"vi": "Thời tiết", "en": "Weather"},
        "summary": {"vi": "Dự báo thời tiết các quận/huyện ở Hà Nội theo giờ, tối đa 7 ngày (mặc định Hoàn Kiếm); AQI chỉ có cho hôm nay.",
                    "en": "Hourly forecast for Hanoi districts, up to 7 days (default Hoan Kiem); AQI for today only."},
        "uses_ai": False,
        "keywords": ["thoi tiet", "quan huyen", "mua", "nhiet do", "khong khi", "aqi", "du bao", "weather", "rain", "forecast", "temperature"],
        "steps": {"vi": ["Gõ tên quận/huyện, ví dụ \"Ha\" rồi chọn Hai Bà Trưng trong danh sách gợi ý; để trống là Hoàn Kiếm.",
                         "Chọn ngày trong 7 ngày tới rồi bấm \"Xem\".",
                         "Bảng theo giờ có nhiệt độ và khả năng mưa."],
                  "en": ["Type a district, e.g. \"Ha\" and pick Hai Ba Trung from the suggestions; empty means Hoan Kiem.",
                         "Pick a date within 7 days and press \"Show\".",
                         "The hourly table lists temperature and chance of rain."]},
    },
    {
        "id": "planner",
        "title": {"vi": "Tự xếp lịch", "en": "Planner"},
        "summary": {"vi": "Bạn chọn điểm và thứ tự; code tính giờ (chờ mở cửa, thời gian đi), chi phí, quãng đi bộ và kiểm lỗi – không dùng AI. "
                          "Muốn AI chọn giúp thì bấm \"Nhờ AI chọn điểm\".",
                    "en": "You pick places and order; code computes times (waiting for opening, travel), cost, walking and checks – no AI. "
                          "Press \"Let AI choose\" for suggestions."},
        "uses_ai": False,
        "keywords": ["tu xep", "xep lich", "tu lap", "tu sap", "thu tu", "ngan sach", "chi phi", "di bo", "planner",
                     "my own plan", "build", "order", "budget", "cost", "walking"],
        "steps": {"vi": ["Chọn ngày, khung giờ, số người, ngân sách, giới hạn đi bộ.",
                         "Thêm điểm (hoặc bữa ăn), dùng ↑ ↓ để đổi thứ tự, sửa số phút.",
                         "Bấm \"Xếp giờ\": code trả lịch, chi phí và cảnh báo ngay, không gọi AI.",
                         "Bấm \"Nhờ AI chọn điểm\" nếu muốn AI đề xuất theo sở thích."],
                  "en": ["Set date, time window, people, budget, walking limit.",
                         "Add places (or a meal), reorder with ↑ ↓, edit minutes.",
                         "Press \"Schedule\": code returns times, cost and warnings instantly, no AI.",
                         "Press \"Let AI choose\" for AI suggestions based on your interests."]},
    },
    {
        "id": "about",
        "title": {"vi": "Giới thiệu", "en": "About"},
        "summary": {"vi": "Các tính năng, nguồn dữ liệu và giới hạn của ứng dụng.",
                    "en": "Features, data sources and limits of the app."},
        "uses_ai": False,
        "keywords": ["gioi thieu", "nguon du lieu", "gioi han", "about", "data source", "limits"],
        "steps": {"vi": ["Đọc danh sách tính năng và nguồn dữ liệu."], "en": ["Read the feature list and data sources."]},
    },
]

DATA_SOURCES = {
    "vi": ["Giờ mở cửa, vé: bảng tự soạn data/pois.json (kiểm 22/11/2026, phần lớn theo trang chính thức), có độ tin cậy, nguồn và lưu ý tham quan cho từng điểm.",
           "Thời tiết: Open-Meteo (ước tính mô hình). AQI: trạm WAQI (đo thật).",
           "Mức đông: BestTime (dự báo theo giờ). Giao thông: TomTom.",
           "Chi phí taxi, ăn uống: công thức ước tính."],
    "en": ["Hours and tickets: curated table data/pois.json (checked 22 Nov 2026, mostly official sites), with confidence, sources and visiting tips per place.",
           "Weather: Open-Meteo (model estimate). AQI: WAQI station (measured).",
           "Crowds: BestTime (hourly forecast). Traffic: TomTom.",
           "Taxi and food costs: formula estimates."],
}

LIMITS = {
    "vi": ["Không đặt vé, đặt bàn hay thanh toán.", "Chỉ có 9 điểm tham quan trong nội thành Hà Nội.",
           "Không tự lấy vị trí của bạn.", "Thời tiết chỉ cho Hà Nội, tối đa 7 ngày."],
    "en": ["No booking or payments.", "Only 9 places in central Hanoi.", "Does not access your location.",
           "Weather for Hanoi only, up to 7 days."],
}


def manifest(lang: str = "vi") -> dict:
    """Dạng gửi cho giao diện (GET /api/app)."""
    return {"tabs": [{"id": x["id"], "title": x["title"][lang], "summary": x["summary"][lang], "uses_ai": x["uses_ai"],
                      "steps": x["steps"][lang]} for x in TABS],
            "data_sources": DATA_SOURCES[lang], "limits": LIMITS[lang]}


# ---------- Chatbot hiểu cấu trúc ứng dụng ----------
APP_WORDS = (r"\b(ung dung|app|chuc nang|tinh nang|giao dien|tab|huong dan( su dung)?|cach dung|su dung the nao|"
             r"ban (lam|giup) (duoc )?(gi|nhung gi)|ban co the lam gi|ban biet lam gi|what can you do|features?|"
             r"how (do i|to) use|help me use|user guide)\b")
HOW_WORDS = r"\b(lam sao|lam the nao|cach (nao|de)|o dau|cho nao de|cho nao|how (do|can) i|how to|where (do|can) i)\b"
ACTION_WORDS = r"\b(xem|loc|tim|tu xep|xep|sap|doi thu tu|tinh|check|see|view|filter|find|build|reorder|compare)\b"


def _score(tab: dict, q: str) -> int:
    return sum(len(k) for k in tab["keywords"] if re.search(rf"\b{re.escape(k)}\b", q))


def is_app_question(text: str) -> bool:
    q = fold(text)
    if re.search(APP_WORDS, q):
        return True
    # "làm sao để xem giá vé", "lọc điểm trong nhà ở đâu": hỏi CÁCH dùng + có động từ thao tác + khớp một tab
    return bool(re.search(HOW_WORDS, q) and re.search(ACTION_WORDS, q) and any(_score(t, q) for t in TABS))


def answer_app(text: str, lang: str = "vi") -> tuple[str, list[dict]]:
    """Trả (câu trả lời, nút mở tab). Khớp được tab cụ thể thì hướng dẫn tab đó, không thì giới thiệu cả ứng dụng."""
    q = fold(text)
    ranked = sorted(TABS, key=lambda t: _score(t, q), reverse=True)
    best = ranked[0] if _score(ranked[0], q) and not re.search(APP_WORDS, q) else None
    open_lbl = "Mở tab" if lang == "vi" else "Open"
    if best:
        lines = [f"{best['title'][lang]}: {best['summary'][lang]}", ""]
        lines += [f"{i}. {s}" for i, s in enumerate(best["steps"][lang], 1)]
        return "\n".join(lines), [{"tab": best["id"], "label": f"{open_lbl} {best['title'][lang]}"}]
    head = "Ứng dụng có các phần sau:" if lang == "vi" else "The app has these parts:"
    ai = " (dùng AI)" if lang == "vi" else " (uses AI)"
    noai = " (không dùng AI, trả lời tức thì)" if lang == "vi" else " (no AI, instant)"
    lines = [head]
    lines += [f"• {t['title'][lang]}{ai if t['uses_ai'] else noai}: {t['summary'][lang]}" for t in TABS if t["id"] != "about"]
    lines += ["", "Mình không làm được:" if lang == "vi" else "I can't:"] + [f"  - {x}" for x in LIMITS[lang]]
    acts = [{"tab": t["id"], "label": t["title"][lang]} for t in TABS if t["id"] not in ("chat",)]
    return "\n".join(lines), acts
