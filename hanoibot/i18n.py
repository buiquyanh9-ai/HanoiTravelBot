"""Song ngữ Việt/Anh. Câu chữ do code sinh nên dịch bằng mẫu cố định (không nhờ LLM dịch số liệu)."""
from __future__ import annotations

import re

from .pois import load_pois
from .textutil import fold

DAYS = {
    "vi": ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ nhật"],
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
}

T = {
    "plan_title": {"vi": "Lịch trình {day} {date}:", "en": "Itinerary for {day} {date}:"},
    "open": {"vi": "mở", "en": "open"},
    "ticket": {"vi": "vé", "en": "ticket"},
    "walk": {"vi": "đi bộ", "en": "walk"},
    "taxi": {"vi": "taxi", "en": "taxi"},
    "notes": {"vi": "Lưu ý:", "en": "Notes:"},
    "still_bad": {"vi": "⚠ Bộ kiểm tra vẫn còn lỗi sau khi sửa – KHÔNG nên dùng nguyên lịch này:",
                  "en": "⚠ The checker still found problems after repair – do NOT use this itinerary as is:"},
    "totals": {"vi": "Chi phí ước tính cho {people} người: {total}đ (vé {tickets}đ + đi lại {transport}đ + ăn uống {food}đ). Đi bộ khoảng {walk} km.",
               "en": "Estimated cost for {people} person(s): {total} VND (tickets {tickets} + transport {transport} + food {food}). About {walk} km of walking."},
    "data": {"vi": "Dữ liệu: thời tiết {w} lúc {wt}; AQI {aqi} ({lvl}) – {asrc}; mức đông: {crowd}; thời gian đi: {trf}; chi phí taxi/ăn uống: ước tính.",
             "en": "Data: weather {w} at {wt}; AQI {aqi} – {asrc}; crowd level: {crowd}; travel time: {trf}; taxi/food cost: estimates."},
    "no_crowd": {"vi": "chưa có nguồn (thiếu key BestTime)", "en": "no source yet (BestTime key missing)"},
    "news": {"vi": "Tin gần đây cần tự kiểm (tìm qua Tavily):", "en": "Recent news to check yourself (via Tavily):"},
    "hours_title": {"vi": "Giờ mở cửa {day} {date} (bảng dữ liệu kiểm ngày 21/11/2026):",
                    "en": "Opening hours on {day} {date} (table checked on 21 Nov 2026):"},
    "open_now": {"vi": " – ĐANG MỞ", "en": " – OPEN NOW"},
    "closed_now": {"vi": " – đang đóng", "en": " – closed now"},
    "conf": {"vi": "độ tin cậy", "en": "confidence"},
    "source": {"vi": "Nguồn", "en": "Source"},
    "other": {"vi": "Mình chỉ hỗ trợ du lịch Hà Nội: giờ mở cửa, thời tiết, thông tin điểm tham quan và lên lịch trình.",
              "en": "I only help with Hanoi sightseeing: opening hours, weather, place info and itineraries."},
    "blocked": {"vi": "Mình chỉ hỗ trợ tra cứu và lên lịch tham quan Hà Nội, không thực hiện yêu cầu thay đổi cách hoạt động.",
                "en": "I only help with Hanoi sightseeing and cannot change how I operate."},
    "too_long": {"vi": "Tin nhắn quá dài, bạn rút gọn giúp mình (tối đa 1500 ký tự).",
                 "en": "Your message is too long, please shorten it (max 1500 characters)."},
    "bad_date": {"vi": "Ngày {d} không tồn tại. Bạn kiểm tra lại ngày giúp mình (ví dụ 28/02 hoặc 01/03).",
                 "en": "The date {d} does not exist. Please check it (e.g. 28/02 or 01/03)."},
    "past_date": {"vi": "Ngày {d} đã qua. Mình chỉ lên lịch cho hôm nay hoặc các ngày tới.",
                  "en": "{d} is in the past. I can only plan for today or future dates."},
    "bad_window": {"vi": "Khung giờ {a}-{b} không hợp lệ (giờ kết thúc phải sau giờ bắt đầu).",
                   "en": "The time window {a}-{b} is invalid (end must be after start)."},
    "need_location": {"vi": "Bạn đang ở khu nào (ví dụ Hồ Gươm, Văn Miếu, Lăng Bác)? Mình không tự lấy vị trí của bạn.",
                      "en": "Where are you now (e.g. Hoan Kiem Lake, Temple of Literature)? I don't access your location."},
    "no_booking": {"vi": "Mình không đặt vé, đặt bàn hay thanh toán được. Bạn mua vé tại quầy hoặc kênh chính thức của điểm tham quan; mình có thể cho giờ mở cửa và giá vé tham khảo.",
                   "en": "I can't book, reserve or pay for anything. Please buy tickets at the venue or its official channel; I can give opening hours and reference prices."},
    "weather_far": {"vi": "Chưa có dự báo cho {d}: dự báo chỉ có tối đa 16 ngày tới. Mình không đoán thời tiết xa hơn.",
                    "en": "No forecast for {d} yet: forecasts only cover the next 16 days. I won't guess beyond that."},
    "place_not_found": {"vi": "Mình không tìm thấy địa danh \"{p}\" ở Việt Nam. Bạn ghi rõ hơn (ví dụ: Ba Vì, Hà Nội)?",
                        "en": "I couldn't find \"{p}\" in Vietnam. Could you be more specific?"},
    "internal": {"vi": "Xin lỗi, có lỗi khi xử lý câu này. Bạn thử diễn đạt lại hoặc hỏi ngắn hơn nhé.",
                 "en": "Sorry, something went wrong with this request. Please rephrase or ask something shorter."},
    "llm_down": {"vi": "Xin lỗi, hệ thống AI đang lỗi ({e}). Bạn thử lại sau.",
                 "en": "Sorry, the AI service is unavailable ({e}). Please try again later."},
}

MEAL_EN = {"Ăn trưa": "Lunch", "Ăn tối": "Dinner", "Nghỉ, ăn nhẹ": "Break / snack"}
REASON_EN = {"nghỉ cố định ngày này trong tuần": "regular weekly closing day", "Tu bổ định kỳ": "periodic maintenance",
             "đóng cửa đặc biệt": "special closure"}


def t(key: str, lang: str, **kw) -> str:
    return T[key][lang].format(**kw)


def money(v: int, lang: str) -> str:
    s = f"{v:,}"
    return s.replace(",", ".") if lang == "vi" else s


def detect_lang(text: str) -> str:
    """vi nếu có dấu tiếng Việt hoặc từ tiếng Việt không dấu thường gặp; en nếu có từ tiếng Anh thường gặp."""
    if re.search(r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", text.lower()):
        return "vi"
    q = f" {fold(text)} "
    vi_words = [" di ", " dau ", " may gio ", " hom nay ", " ngay mai ", " khong ", " minh ", " lich trinh ", " co mo "]
    en_words = [" the ", " what ", " where ", " when ", " open", " visit", " plan", " itinerary", " weather", " is ",
                " can ", " my ", " today", " tomorrow", " museum", " please"]
    vi = sum(w in q for w in vi_words)
    en = sum(w in q for w in en_words)
    return "en" if en > vi else "vi"


def en_text(s: str) -> str:
    """Dịch các câu cảnh báo do code sinh sang tiếng Anh bằng mẫu cố định, thay tên điểm bằng tên tiếng Anh."""
    for p in load_pois().values():
        s = s.replace(p.name, p.name_en or p.name)
    for vi, en in REASON_EN.items():
        s = s.replace(vi, en)
    rules = [
        (r"^Giờ mở cửa (.+) có độ tin cậy THẤP, nên gọi xác nhận trước\.$", r"Opening hours of \1 are LOW confidence – call ahead to confirm."),
        (r"^(.+) \(ngoài trời\) lúc (\d+)h: dự báo (\d+)% mưa\.$", r"\1 (outdoor) at \2:00: \3% chance of rain."),
        (r"^(.+) \(ngoài trời\): AQI (\d+) \((.+)\)\.$", r"\1 (outdoor): AQI \2."),
        (r"^(.+): chỉ (\d+) phút, ít hơn nhiều so với khuyến nghị (\d+) phút\.$", r"\1: only \2 min, much less than the recommended \3 min."),
        (r"^Không xếp được (.+): (.+) vào (\d+/\d+)\.$", r"Could not include \1: \2 on \3."),
        (r"^Không đi được (.+): (.+) \((.+)\)\.$", r"Cannot visit \1: \2."),
        (r"^Không xếp được (.+): chỉ mở (.+), không trùng khung (.+) của bạn\.$", r"Could not include \1: open only \2, outside your \3 window."),
        (r"^Không xếp được (.+): tới nơi khoảng (\d\d:\d\d), không còn đủ giờ mở \((.+)\) trong khung (.+)\.$",
         r"Could not fit \1: arriving around \2, not enough opening time (\3) within \4."),
        (r"^(.+) lúc (\d+)h thường rất đông \(~(\d+)% so với giờ đông nhất tuần\)\.$",
         r"\1 at \2:00 is usually very busy (~\3% of the weekly peak)."),
        (r"^Tổng chi phí ước tính (.+)đ vượt ngân sách (.+)đ (.+)$", r"Estimated total \1 VND exceeds the budget of \2 VND."),
        (r"^Tổng đi bộ ước tính (.+) km vượt giới hạn (.+) km\.$", r"Estimated walking \1 km exceeds the \2 km limit."),
    ]
    for pat, rep in rules:
        if re.match(pat, s):
            return re.sub(pat, rep, s)
    return s
