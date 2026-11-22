"""Luồng chính của bot.

1. Chặn đầu vào (Day 11) -> câu tra cứu rõ ràng đi đường nhanh bằng rule (không gọi LLM)
2. Còn lại: LLM tách ý định thành JSON; ngày tháng do code tính (dates.py)
3. Lấy dữ liệu SONG SONG: thời tiết, AQI, mức đông, tin mới (Tavily) – có cache
4. Lập lịch: LLM chọn điểm/thứ tự/thời lượng -> scheduler.py tính giờ -> LLM sửa nếu có điểm bị bỏ,
   vượt ngân sách hoặc đi bộ quá giới hạn -> LLM viết tóm tắt từ lịch cuối -> validator.py kiểm lần cuối
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime

from . import areas
from .app_manifest import APP_WORDS, answer_app, is_app_question
from .catalog import answer_catalog, is_catalog_question
from .config import HANOI_CENTER, now_hanoi
from .dates import ambiguous_next_week, invalid_date, resolve_date
from .guard import normalized as injection_rules
from .i18n import DAYS, MEAL_EN, detect_lang, en_text, money, t
from .llm import LLMError, chat_json
from .pois import ALIASES, load_pois, mentioned_ids
from .scheduler import schedule
from .textutil import fold
from .tools.air import get_air
from .tools import traffic
from .tools.crowd import get_crowd, level, pct_at
from .tools.travel import matrix
from .tools.weather import FORECAST_DAYS, daily_for_date, geocode, get_weather, hourly_for_date
from .costs import ticket_notes, trim_to_budget
from .tools.web import web_search
from .validator import validate

MAX_REPAIRS = 2

# Lớp chặn đầu vào tối thiểu (Day 11): chuẩn hoá rồi bắt mẫu injection phổ biến EN/VN
INJECTION = re.compile(
    r"(ignore (all|previous|the above)|system prompt|bo qua (moi|tat ca|huong dan)|quen (het|moi) (huong dan|chi dan)|"
    r"ban la (mot )?(ai|mo hinh) khac|developer mode|jailbreak|you are now)")

PLAN_WORDS = (r"(lich trinh|xep lich|len lich|lap lich|di dau|nen di|di choi|tham quan|goi y|ke hoach|plan|itinerary|schedule|suggest|"
              r"recommend|places|things to do|where (should|to)|what to do)")
HOURS_WORDS = r"(may gio|gio mo|co mo|mo cua|dong cua|con mo|vao duoc|opening|open|close|hours)"
WEATHER_WORDS = r"(thoi tiet|mua khong|co mua|troi mua|khong khi|aqi|nhiet do|bui min|weather|rain|air quality|temperature)"


def guard_input(text: str, lang: str = "vi") -> str | None:
    if len(text) > 1500:
        return t("too_long", lang)
    if INJECTION.search(fold(text)) or injection_rules(text)[0]:  # luật ViGuard: chống bỏ dấu, tách chữ, chữ giả dạng...
        return t("blocked", lang)
    return None


# ---------- 1. Hiểu yêu cầu ----------
INTENT_SYSTEM = """Bạn phân tích tin nhắn của khách du lịch Hà Nội (tiếng Việt hoặc tiếng Anh) thành JSON. Chỉ trả JSON.
Schema:
{"intent": "plan" | "hours" | "weather" | "info" | "app" | "other",
 "date": "YYYY-MM-DD",             // ngày khách muốn đi; mặc định hôm nay
 "start": "HH:MM", "end": "HH:MM",  // khung giờ khách rảnh; mặc định 08:00-21:00
 "poi_ids": [],                     // điểm khách nhắc tới, CHỈ dùng id trong danh mục
 "must_include": [], "avoid": [],   // id phải có / phải bỏ
 "people": 1,                       // tổng số người đi (gồm cả trẻ em)
 "children": 0,                     // số trẻ em trong nhóm (dưới 16 tuổi)
 "child_ages": [],                  // tuổi từng trẻ NẾU khách nói ("2 bé 10 tuổi" -> [10, 10]); không đoán
 "students": 0,                     // số người là học sinh/sinh viên CÓ THẺ nếu khách nói
 "student_level": null,             // "hs" (học sinh) | "sv" (sinh viên) | null nếu không rõ
 "place": null,                     // địa danh khách hỏi nếu KHÔNG thuộc danh mục (ví dụ "Ba Vì"), không thì null
 "budget_vnd": null,                // ngân sách tổng (VND) nếu khách nói, ví dụ "350k" -> 350000
 "max_walk_km": null,               // giới hạn đi bộ (km) nếu khách nói; "ngại/ít đi bộ" -> 2
 "preferences": "",                 // sở thích, người đi cùng, sức khoẻ, ghét đông...
 "replan_from": null}               // id điểm khách ĐANG đứng nếu khách đang giữa chuyến và muốn đổi kế hoạch
intent: plan = muốn lịch trình / gợi ý đi đâu / đổi kế hoạch; hours = hỏi giờ mở cửa / có mở không;
weather = hỏi thời tiết, mưa, không khí; info = hỏi thông tin, giá vé một điểm;
app = hỏi về chính ứng dụng/chatbot (có tính năng gì, dùng thế nào, xem ở đâu); other = ngoài du lịch Hà Nội.
Nếu có "Yêu cầu trước" và tin nhắn mới là CHỈNH SỬA (bỏ/thêm điểm, đổi giờ, thêm người, đổi ngân sách, "còn ... thì sao"),
hãy giữ nguyên các trường cũ và chỉ đổi phần khách nhắc tới."""


def _defaults(req: dict, text: str, now: datetime, lang: str) -> dict:
    pois = load_pois()
    req.setdefault("intent", "plan")
    req["lang"] = lang
    req["_text"] = text[:300]
    req["date"] = req.get("date") or now.date().isoformat()
    d_code = resolve_date(text, now)  # code thắng model khi câu có mốc ngày rõ
    if d_code:
        req["date_llm"], req["date"] = req["date"], d_code.isoformat()
    bad = invalid_date(text, now)
    try:
        d_req = date.fromisoformat(str(req["date"])[:10])
        req["date"] = d_req.isoformat()
        if d_req < now.date():
            req["_error"] = ("past_date", {"d": d_req.strftime("%d/%m/%Y")})
    except ValueError:
        req["date"] = now.date().isoformat()
        bad = bad or str(req.get("date_llm") or req.get("date"))
    if bad:
        req["_error"] = ("bad_date", {"d": bad})
    amb = ambiguous_next_week(text, now)
    if amb and amb[1].isoformat() == req["date"]:
        day = DAYS[lang][amb[1].weekday()]
        phrase = f"{day} next week" if lang == "en" else f"{day} tuần sau"
        req["date_note"] = t("date_note", lang, phrase=phrase, d=amb[1].strftime("%d/%m"), alt=amb[2].strftime("%d/%m"))
    hhmm = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
    req["start"] = req.get("start") if hhmm.match(str(req.get("start") or "")) else "08:00"
    req["end"] = req.get("end") if hhmm.match(str(req.get("end") or "")) else "21:00"
    if req["end"] <= req["start"] and not req.get("_error"):
        req["_error"] = ("bad_window", {"a": req["start"], "b": req["end"]})
    try:
        req["people"] = max(1, min(50, int(req.get("people") or 1)))
        req["children"] = max(0, min(int(req.get("children") or 0), req["people"]))
    except (TypeError, ValueError):
        req["people"], req["children"] = 1, 0
    ages = [int(a) for a in (req.get("child_ages") or []) if isinstance(a, (int, float)) and 0 <= a <= 17]
    if ages and len(ages) > req["children"]:  # "bé 5 tuổi" mà model quên đếm children
        req["children"] = min(len(ages), req["people"])
    req["child_ages"] = ages[:req["children"]]
    try:
        req["students"] = max(0, min(int(req.get("students") or 0), req["people"] - req["children"]))
    except (TypeError, ValueError):
        req["students"] = 0
    req["student_level"] = req.get("student_level") if req.get("student_level") in ("hs", "sv") else None
    # LLM có thể trả null/sai kiểu (nhất là khi lượt trước là câu thời tiết/giờ mở cửa) -> code chuẩn hoá
    req["preferences"] = str(req.get("preferences") or "")[:300]
    for k in ("budget_vnd", "max_walk_km"):
        try:
            req[k] = float(req[k]) if req.get(k) not in (None, "") and float(req[k]) > 0 else None
            if k == "budget_vnd" and req[k] is not None:
                req[k] = int(req[k])
        except (TypeError, ValueError):
            req[k] = None
    for k in ("poi_ids", "must_include", "avoid"):
        req[k] = [p for p in (req.get(k) or []) if isinstance(p, str) and p in pois]
    if req.get("replan_from") not in pois:
        req["replan_from"] = None
    if req["intent"] == "plan":
        # Chỉ ép "phải đi" với điểm khách GỌI TÊN trong câu (code kiểm), không tin suy diễn của model
        named = mentioned_ids(text) | _mentioned_en(text)
        req["must_include"] = [p for p in dict.fromkeys(req["must_include"] + req["poi_ids"])
                               if p in named and p != req.get("replan_from") and p not in req["avoid"]]
        _apply_edits(req, text)
    _bump_start(req, now)
    return req


def _bump_start(req: dict, now: datetime) -> None:
    """Hôm nay thì không xếp vào giờ đã qua. Khung giờ khách nói đã qua hết -> báo rõ thay vì 'khung 13:00-12:00'."""
    if req["date"] != now.date().isoformat():
        return
    nxt = _next_slot(now)
    if req["intent"] == "plan" and nxt >= req["end"] and not req.get("_error"):
        req["_error"] = ("window_passed", {"a": req["start"], "b": req["end"], "now": now.strftime("%H:%M")})
        return
    if req["start"] < nxt:
        req["start"] = nxt


def fast_route(text: str, now: datetime, lang: str) -> dict | None:
    """Câu tra cứu rõ ràng -> trả req ngay, không tốn 1 lần gọi LLM (~2.5s)."""
    q = fold(text)
    ids = sorted(mentioned_ids(text) | _mentioned_en(text))
    # "Cho tôi biết về Hỏa Lò, giờ mở cửa và lưu ý khi tham quan" là hỏi THÔNG TIN, không phải xin lịch trình
    # (chữ "tham quan" không đủ để coi là lập lịch)
    if ids and len(ids) <= 2 and re.search(INFO_ASK, q) and not re.search(STRONG_PLAN, q):
        return _defaults({"intent": "info", "poi_ids": ids}, text, now, lang)
    if re.search(PLAN_WORDS, q):
        return None
    other_place = re.search(r"\b(o|tai|in|at|khu|vung|huyen|quan|xa)\s+(?!ha noi|hanoi|ho guom|hoan kiem|day\b|nha\b)[a-z]", q)
    is_weather = re.search(WEATHER_WORDS, q) and not re.search(HOURS_WORDS, q.replace("open-meteo", ""))
    area = areas.mentioned(text)
    if is_weather and area:  # "thời tiết Cầu Giấy chiều nay" -> biết quận/huyện, không cần LLM
        return _defaults({"intent": "weather", "poi_ids": ids, "place": area.name}, text, now, lang)
    if is_weather and (ids or not other_place):
        return _defaults({"intent": "weather", "poi_ids": ids}, text, now, lang)
    if ids and re.search(HOURS_WORDS, q):
        return _defaults({"intent": "hours", "poi_ids": ids}, text, now, lang)
    if ids and re.search(TICKET_WORDS, q):  # "vé Hỏa Lò bao nhiêu" -> bảng dữ liệu, không cần LLM
        return _defaults({"intent": "info", "poi_ids": ids}, text, now, lang)
    return None


INFO_ASK = (r"\b(cho (toi|minh|em|tui) biet|thong tin|gioi thieu|luu y|kinh nghiem|can biet|nen biet|co gi (hay|dac biet|dang xem)|"
            r"o dau|dia chi|tell me about|info(rmation)? (about|on)|tips?|what should i know|what is there|worth)\b")
STRONG_PLAN = (r"\b(lich trinh|xep lich|len lich|lap lich|ke hoach|plan|itinerary|schedule|di dau|nen di|"
               r"goi y (lich|diem|cho)|suggest|recommend|things to do|half[- ]day|nua ngay|ca ngay|1 ngay|mot ngay)\b")
TICKET_WORDS = (r"(gia ve|ve vao|\bve\b.{0,30}\bbao nhieu\b|bao nhieu tien|mat phi|phi vao|ticket|how much|price|"
                r"entry fee|admission)")


def parse_request(text: str, now: datetime, lang: str, prev: dict | None = None) -> tuple[dict, dict | None]:
    fast = None if prev and prev.get("intent") == "plan" and not _looks_standalone(text) else fast_route(text, now, lang)
    if fast:
        return fast, None
    pois = load_pois()
    catalog = ", ".join(f"{p.id}={p.name} / {p.name_en}" for p in pois.values())
    user = (f"Bây giờ: {now:%Y-%m-%d %H:%M} ({DAYS['vi'][now.weekday()]}), Hà Nội.\nDanh mục: {catalog}\n"
            f"Tin nhắn: {text}")
    if prev:
        keep = {k: prev.get(k) for k in ("intent", "date", "start", "end", "poi_ids", "must_include", "avoid", "people",
                                         "children", "child_ages", "students", "student_level",
                                         "budget_vnd", "max_walk_km", "preferences", "replan_from")
                if prev.get(k) not in (None, "", [])}
        user += "\nYêu cầu trước: " + json.dumps(keep, ensure_ascii=False)
    req, meta = chat_json(INTENT_SYSTEM, user, max_tokens=400, temperature=0)
    # Câu có từ xin gợi ý/lịch trình thì là "plan", kể cả khi có hỏi kèm thời tiết/giờ mở cửa
    qf = fold(text)
    if req.get("intent") in ("weather", "hours", "info") and re.search(PLAN_WORDS, qf) \
            and not (re.search(INFO_ASK, qf) and not re.search(STRONG_PLAN, qf)):
        req["intent_llm"], req["intent"] = req["intent"], "plan"
    req = _defaults(req, text, now, lang)
    if prev and req["intent"] == "plan":
        _carry(req, prev, text, now)
    return req, meta


REMOVE_VERB = r"\b(bo|bot|khong (di|muon|can|thich)|tru|loai|huy|remove|skip|drop|without|no)\b"
ADD_VERB = r"\b(them|bo sung|cong them|di them|add|include|plus|thay bang|instead)\b"


def _apply_edits(req: dict, text: str) -> None:
    """Code tách vế câu: vế có 'bỏ/không đi/remove' -> loại điểm; vế có 'thêm/add' -> thêm điểm.
    Không chỉ dựa vào LLM (review Codex #7: 'Bỏ Hỏa Lò đi' vẫn giữ Hỏa Lò)."""
    for clause in re.split(r"\b(?:va|and|roi|nhung|but|con)\b|[,;.]", fold(text)):
        ids = mentioned_ids(clause) | _mentioned_en(clause)
        if not ids:
            continue
        if re.search(REMOVE_VERB, clause):
            req["avoid"] = list(dict.fromkeys(req.get("avoid", []) + sorted(ids)))
            req["must_include"] = [p for p in req.get("must_include", []) if p not in ids]
        elif re.search(ADD_VERB, clause):
            req["must_include"] = list(dict.fromkeys(req.get("must_include", []) + sorted(ids)))
            req["avoid"] = [p for p in req.get("avoid", []) if p not in ids]
            req["_added"] = list(dict.fromkeys(req.get("_added", []) + sorted(ids)))


def _looks_standalone(text: str) -> bool:
    """Câu tra cứu độc lập (có tên điểm + hỏi giờ, hoặc hỏi thời tiết) thì không cần ngữ cảnh."""
    q = fold(text)
    return bool(re.search(WEATHER_WORDS, q) or (mentioned_ids(text) and re.search(HOURS_WORDS, q))
                or (mentioned_ids(text) and re.search(INFO_ASK, q) and not re.search(STRONG_PLAN, q)))


def _carry(req: dict, prev: dict, text: str, now: datetime) -> None:
    """Giữ thông tin cũ khi tin nhắn mới chỉ chỉnh một phần (code đảm bảo, không chỉ dựa vào LLM)."""
    q = fold(text)
    defaults = {"people": 1, "children": 0, "child_ages": [], "students": 0, "student_level": None,
                "budget_vnd": None, "max_walk_km": None, "preferences": ""}
    for k, dv in defaults.items():
        if req.get(k) in (dv, None, "", []) and prev.get(k) not in (dv, None, "", []):
            req[k] = prev[k]
    if resolve_date(text, now) is None and not re.search(r"hom nay|today", q) and prev.get("date"):
        req["date"] = prev["date"]
    if not re.search(r"\d{1,2} ?(h|gio|:)|sang|chieu|toi|morning|afternoon|evening", q):
        req["start"], req["end"] = prev.get("start", req["start"]), prev.get("end", req["end"])
    added = set(req.get("_added", []))  # khách vừa nói "thêm" thì bỏ khỏi danh sách loại cũ
    req["avoid"] = list(dict.fromkeys([p for p in prev.get("avoid", []) if p not in added] + req.get("avoid", [])))
    req["must_include"] = [p for p in dict.fromkeys(prev.get("must_include", []) + req.get("must_include", []))
                           if p not in req["avoid"]]
    if req.get("_error", ("",))[0] == "window_passed":  # khung cũ của lượt trước có thể vẫn còn hợp lệ
        req.pop("_error")
    _bump_start(req, now)


# ---------- 2. Dữ liệu song song ----------
def _recent_news(poi_id: str) -> list[dict]:
    p = load_pois()[poi_id]
    res = web_search(f"{p.name} Hà Nội thông báo mở cửa đóng cửa", days=14, max_results=5)
    out = []
    for it in res.get("items", []):
        title = fold(it.get("title") or "")
        if not any(a in title for a in ALIASES.get(poi_id, [])):
            continue  # chỉ giữ tin đúng địa điểm
        if injection_rules((it.get("title") or "") + " " + (it.get("snippet") or ""))[0]:
            continue  # nội dung web là dữ liệu chưa tin cậy: có dấu hiệu injection thì bỏ
        try:
            pub = parsedate_to_datetime(it["published"]).date().isoformat() if it.get("published") else "?"
        except (TypeError, ValueError):
            pub = "?"
        out.append({"title": it["title"], "url": it["url"], "published": pub})
    return out[:2]


def _safe_news(pid: str) -> list[dict]:
    try:
        return _recent_news(pid)
    except Exception:  # tin tức là phụ, lỗi thì bỏ qua
        return []


def gather(req: dict, now: datetime | None = None, wx: dict | None = None) -> dict:
    """wx: thời tiết Hoàn Kiếm khách vừa hỏi trong phiên (còn tươi) -> dùng lại, không gọi lại API."""
    now = now or now_hanoi()
    pois = list(load_pois().values())
    d = date.fromisoformat(req["date"])
    is_plan = req.get("intent") == "plan"
    news_ids = list(dict.fromkeys(req.get("must_include", []) + req.get("poi_ids", [])))[:3]
    t0 = time.perf_counter()

    def _given(v: dict):
        return lambda: v

    with ThreadPoolExecutor(max_workers=16) as ex:
        f_weather = ex.submit(_given(wx["weather"])) if wx else ex.submit(get_weather, *HANOI_CENTER)
        f_air = ex.submit(_given(wx["air"])) if wx and time.time() - wx["air_ts"] < AIR_REUSE_S \
            else ex.submit(get_air, *HANOI_CENTER)
        f_traffic = ex.submit(_safe_traffic, pois, d, now) if is_plan else None
        f_crowd = {p.id: ex.submit(get_crowd, p, d, now) for p in pois} if is_plan else {}
        f_news = {pid: ex.submit(_recent_news, pid) for pid in news_ids}
        weather, air = f_weather.result(), f_air.result()
        crowd = {k: f.result() for k, f in f_crowd.items()}
        tmatrix = f_traffic.result() if f_traffic else {"available": False}
        news = {}
        for k, f in f_news.items():
            try:
                news[k] = f.result()
            except Exception:  # tin tức là phụ, lỗi thì bỏ qua
                news[k] = []
    traffic.activate(tmatrix)
    return {"weather": weather, "air": air, "crowd": crowd, "news": news, "traffic": tmatrix,
            "gather_s": round(time.perf_counter() - t0, 2)}


def _safe_traffic(pois: list, d: date, now: datetime) -> dict:
    try:
        return traffic.fetch_matrix(pois, d, now)
    except Exception as e:
        return {"available": False, "reason": type(e).__name__}


def _news_lines(news: dict, lang: str) -> list[str]:
    pois = load_pois()
    rows = [(pid, it) for pid, items in news.items() for it in items]
    if not rows:
        return []
    lines = ["", t("news", lang)]
    lines += [f"  - [{pois[pid].display(lang)}] {it['title']} ({it['published']}) {it['url']}" for pid, it in rows]
    return lines


# ---------- 3. Trả lời tra cứu bằng code ----------
def _open_status(p, now: datetime, lang: str) -> str:
    """'đang đóng' chung chung dễ hiểu nhầm: nói rõ chưa mở / nghỉ trưa / đã hết giờ / đóng cả ngày."""
    if p.is_open_at(now):
        return t("open_now", lang)
    iv = p.intervals_on(now.date())
    if not iv:
        return t("closed_today", lang)
    tnow = now.time()
    later = [a for a, _ in iv if a > tnow]
    if not later:
        return t("closed_for_day", lang)
    if any(b <= tnow for _, b in iv):
        return t("on_break", lang, t=f"{later[0]:%H:%M}")
    return t("not_yet_open", lang, t=f"{later[0]:%H:%M}")


def visit_tips(p, d: date, lang: str) -> list[str]:
    """Lưu ý khi tham quan: mẹo đã kiểm chứng trong data/pois.json (tips/tips_en) + điều suy ra từ dữ liệu có sẵn
    (ngày nghỉ, giờ vắng nhất theo BestTime, thời lượng, trong nhà/ngoài trời). Không để LLM tự bịa."""
    vi = lang == "vi"
    out = list((p.tips if vi else p.tips_en) or [])
    closed = [DAYS[lang][i] for i, k in enumerate(["mon", "tue", "wed", "thu", "fri", "sat", "sun"]) if not p.week_hours(d).get(k)]
    if closed:
        out.append(("Nghỉ cố định: " if vi else "Closed every ") + ", ".join(closed) + ".")
    try:
        cr = get_crowd(p, d)
    except Exception:
        cr = {}
    if cr.get("available") and p.intervals_on(d):
        open_h = {h for a, b in p.intervals_on(d) for h in range(a.hour, b.hour)}
        hourly = {int(h): v for h, v in (cr.get("hourly") or {}).items() if v and int(h) in open_h}
        if hourly:
            quiet = min(hourly, key=hourly.get)
            busy = max(hourly, key=hourly.get)
            out.append((f"Vắng nhất khoảng {quiet}h (~{hourly[quiet]}%), đông nhất khoảng {busy}h (~{hourly[busy]}%) – dự báo BestTime."
                        if vi else f"Quietest around {quiet}:00 (~{hourly[quiet]}%), busiest around {busy}:00 (~{hourly[busy]}%) – BestTime forecast."))
    out.append((f"Nên dành khoảng {p.visit_minutes} phút; " + ("trong nhà, hợp ngày mưa." if p.indoor else "phần lớn ngoài trời, nên xem dự báo mưa.")
                if vi else f"Allow about {p.visit_minutes} min; " + ("indoor, fine on rainy days." if p.indoor else "mostly outdoors, check the rain forecast.")))
    if p.walk_km_onsite >= 1:
        out.append(f"Đi bộ nhiều (~{p.walk_km_onsite} km bên trong), nên đi giày thoải mái." if vi
                   else f"Lots of walking (~{p.walk_km_onsite} km inside); wear comfortable shoes.")
    if p.confidence == "low" and not any(re.search(r"xac nhan|confirm", fold(x)) for x in out):
        out.append("Giờ mở cửa chưa chắc chắn (nguồn mâu thuẫn), nên gọi xác nhận trước." if vi
                   else "Opening hours are uncertain (sources conflict); call ahead.")
    return out


def answer_hours(req: dict, now: datetime, news: dict | None = None) -> str:
    pois = load_pois()
    lang = req["lang"]
    d = date.fromisoformat(req["date"])
    ids = req["poi_ids"] or list(pois)
    title = "info_title" if req.get("intent") == "info" else "hours_title"
    lines = [t(title, lang, day=DAYS[lang][d.weekday()], date=d.strftime("%d/%m/%Y"))]
    for pid in ids:
        p = pois[pid]
        status = ""
        if d == now.date():
            status = _open_status(p, now, lang)
        lines.append(f"• {p.display(lang)}: {p.hours_text(d, lang)}{status} [{t('conf', lang)}: {p.confidence}]")
        if pid in req["poi_ids"]:
            tk = p.ticket_text(lang)
            if not p.free:
                lines.append(f"  {'Vé' if lang == 'vi' else 'Ticket'}: {tk}")
            tips = visit_tips(p, d, lang)
            if p.free and tk:  # vào tự do nhưng có khoản thu riêng (đền Ngọc Sơn) -> đưa vào lưu ý
                tips.insert(0, tk[0].upper() + tk[1:] + ".")
            if tips:
                lines.append("  " + ("Lưu ý khi tham quan:" if lang == "vi" else "Visiting tips:"))
                lines += [f"    - {x}" for x in tips]
            if p.sources:
                lines.append(f"  {t('source', lang)}: {p.sources[0]}")
    lines += _news_lines(news or {}, lang)
    return "\n".join(lines)


def answer_weather(ctx: dict, req: dict, now: datetime | None = None, loc: dict | None = None) -> str:
    """Thời tiết đúng NƠI (loc từ geocode, mặc định Hoàn Kiếm) và đúng NGÀY khách hỏi."""
    lang = req["lang"]
    now = now or now_hanoi()
    w = ctx["weather"]
    place = (loc or {}).get("name") or ("Hoan Kiem" if lang == "en" else "khu Hoàn Kiếm")
    if "hourly" not in w:
        return f"Weather unavailable ({w.get('_error')})." if lang == "en" else f"Không lấy được thời tiết ({w.get('_error')})."
    d = date.fromisoformat(req["date"])
    stale = (" [STALE DATA]" if lang == "en" else " [DỮ LIỆU CŨ do API lỗi]") if w.get("_stale") else ""
    rain = "rain" if lang == "en" else "mưa"
    if d != now.date():  # ngày khác hôm nay: tóm tắt dự báo theo giờ của đúng ngày đó
        day = daily_for_date(w, req["date"])
        if not day:
            return t("weather_far", lang, d=d.strftime("%d/%m/%Y"))
        hs = [h for h in hourly_for_date(w, req["date"]) if req["start"] <= h["time"][11:16] <= req["end"]][::2][:8]
        head = (f"Forecast for {place} on {DAYS['en'][d.weekday()]} {d:%d/%m/%Y} – Open-Meteo (model estimate){stale}" if lang == "en"
                else f"Dự báo {place} {DAYS['vi'][d.weekday()]} {d:%d/%m/%Y} – Open-Meteo (ước tính mô hình){stale}")
        lines = [head, f"• {day['tmin']}–{day['tmax']}°C, {rain} max {day['rain_prob_max']}%, {day['rain_mm']} mm"]
        if hs:
            lines.append("• " + "; ".join(f"{h['time'][11:13]}h {h['temp']}°C {rain} {h['rain_prob']}%" for h in hs))
        lines.append("• AQI: " + ("only available for today" if lang == "en" else "chỉ có cho hôm nay (không dự báo)"))
        return "\n".join(lines)
    a = ctx["air"]
    c = w["current"]
    if lang == "en":
        lines = [f"Weather in {place} at {w['model_time'][11:16]} – Open-Meteo (model estimate){stale}",
                 f"• {c['temp']}°C (feels {c['feels_like']}°C), humidity {c['humidity']}%, wind {c['wind_kmh']} km/h, rain {c['rain_mm']} mm"]
    else:
        lines = [f"Thời tiết {place} lúc {w['model_time'][11:16]} – {w['source']}{stale}",
                 f"• {c['desc']}, {c['temp']}°C (cảm giác {c['feels_like']}°C), độ ẩm {c['humidity']}%, gió {c['wind_kmh']} km/h, mưa {c['rain_mm']} mm"]
    if a.get("aqi") is not None:
        if lang == "en":
            lines.append(f"• Air: AQI {a['aqi']}, PM2.5 {a.get('pm25')} µg/m³ – {'WAQI station' if a.get('measured') else 'Open-Meteo model'}")
        else:
            note = "" if a.get("measured") else " (AQI tính theo trung bình 24h, PM2.5 là giá trị giờ hiện tại)"
            lines.append(f"• Không khí: AQI {a['aqi']} ({a['level']}), PM2.5 {a.get('pm25')} µg/m³ – {a['source']}, "
                         f"lúc {str(a.get('obs_time'))[11:16]}{note}")
    nxt = [h for h in hourly_for_date(w, req["date"]) if h["time"][11:16] >= req["start"]][:6]
    if nxt:
        lab = "Next hours" if lang == "en" else "Các giờ tới"
        lines.append(f"• {lab}: " + "; ".join(f"{h['time'][11:13]}h {h['temp']}°C {rain} {h['rain_prob']}%" for h in nxt))
    return "\n".join(lines)


# ---------- 4. Lập lịch ----------
PLAN_SYSTEM = """Bạn là hướng dẫn viên lập lịch trình tham quan Hà Nội. Bạn QUYẾT ĐỊNH: đi những điểm nào, theo thứ tự nào,
ở mỗi điểm bao lâu, ăn uống/nghỉ ở đâu. Giờ cụ thể, chi phí, quãng đi bộ do hệ thống tự tính, bạn KHÔNG viết giờ.
Chỉ dùng poi_id trong danh mục.
Nguyên tắc:
- Chỉ chọn điểm MỞ trong khung giờ của khách (xem open_today). Điểm chỉ mở buổi sáng phải xếp đầu ngày.
- Mọi điểm trong must_include phải có nếu điểm đó mở; không chọn điểm trong avoid.
- Nhịp độ theo người đi: người lớn tuổi, trẻ nhỏ, người mệt -> ít điểm (2-4 điểm/ngày), có nghỉ; đi khỏe -> nhiều hơn.
  Tổng (thời lượng + di chuyển travel_min) phải VỪA khung giờ của khách.
- Nếu có budget_vnd: tổng vé (ticket_vnd x people) + taxi + ăn uống (bữa chính ~60.000đ, ăn nhẹ ~30.000đ mỗi người)
  phải dưới ngân sách; ưu tiên điểm miễn phí/rẻ và điểm gần nhau đi bộ được.
- Nếu có max_walk_km: hạn chế điểm có walk_km_onsite lớn (phố cổ, Hồ Gươm, vườn thú), rút ngắn thời lượng ở đó.
- Đi điểm gần nhau liền nhau. Mưa nhiều hoặc AQI xấu -> ưu tiên trong nhà (indoor=true).
- crowd_pct_by_hour (0-100, BestTime): khách ghét đông -> sắp thứ tự để tới điểm đông vào giờ vắng hơn.
- Khung giờ có trưa (11:00-13:00) hoặc tối (18:00-20:00) thì chèn bữa ăn (kind "meal", 45-75 phút) ĐÚNG lúc đó, không chèn giữa
  buổi tham quan ngắn. Không thêm mục di chuyển.
- Nếu replan_from có giá trị, khách đang đứng ở đó: điểm đầu tiên nên gần đó.
Viết "why" bằng ngôn ngữ của khách (trường lang: vi hoặc en).
Trả JSON:
{"stops":[{"kind":"visit","poi_id":"...","minutes":90,"why":"lý do ngắn"},
          {"kind":"meal","minutes":60,"why":"khu phố cổ"}]}
Không bịa tên quán cụ thể, chỉ nói khu vực."""

# Chế độ BASELINE (để so sánh): LLM tự viết cả giờ, giống hỏi thẳng chatbot thông thường.
BASELINE_SYSTEM = """Bạn là hướng dẫn viên lập lịch trình tham quan Hà Nội. Nhiệm vụ: chọn điểm, sắp thứ tự, phân giờ
sao cho hợp sở thích khách VÀ đúng ràng buộc thật. Chỉ dùng điểm trong danh mục (đúng poi_id).
Ràng buộc cứng: chỉ xếp vào khung giờ điểm đó MỞ; để đủ thời gian di chuyển theo bảng travel_min;
không chồng giờ; nằm trong khung giờ khách; tôn trọng must_include/avoid, ngân sách và giới hạn đi bộ nếu có.
Trả JSON:
{"items":[{"kind":"visit","poi_id":"...","start":"HH:MM","end":"HH:MM","why":"lý do ngắn"},
          {"kind":"meal","poi_id":null,"start":"HH:MM","end":"HH:MM","why":"ăn trưa khu ..."}],
 "summary":"2-3 câu"}"""

SUMMARY_SYSTEM = """Viết ĐÚNG 2 câu ngắn cho khách bằng ngôn ngữ "{lang}" (vi = tiếng Việt, en = English) về lịch trình CUỐI CÙNG.
Câu 1: lý do CỤ THỂ của cách xếp, dựa trên dữ kiện được cho: sở thích khách, các điểm cùng quận/gần nhau, trong nhà hay
ngoài trời so với thời tiết, điểm chỉ mở buổi sáng nên đi trước, đã đổi thứ tự để tránh giờ đông (da_doi_thu_tu_tranh_dong).
Câu 2: một đánh đổi hoặc mẹo thực tế (điểm có thể đông, điểm không đi được và vì sao, đi giày thoải mái...).
Chỉ khuyên mang ô khi "mua_max_pct" >= 40. Tên điểm viết đúng như trong "lich_trinh".
Chỉ nói "chỉ mở buổi sáng" khi "gio_mo" của điểm đó kết thúc trước 12:00. Mẹo phải khớp dữ kiện (đi taxi thì không nói đi bộ nhiều).
CẤM các câu chung chung không thêm thông tin, ví dụ: "lịch trình này rất phù hợp với bạn", "mang lại trải nghiệm thú vị",
"bạn sẽ có thời gian nghỉ ngơi", "well-suited for you", "a great experience". Không mở đầu bằng "Lịch trình này".
Chỉ nhắc các điểm có trong "lich_trinh" hoặc "khong_di_duoc". Không thêm điểm khác, không viết con số giờ, giá hay km.
"dong_pct" là mức đông dự báo (0-100) lúc tới nơi, >=85 là rất đông. KHÔNG được nói lịch "tránh đông/vắng" nếu có điểm
dong_pct >= 60; khi đó nói thật điểm đó có thể đông. Trả JSON {{"summary": "..."}}"""

# Lưới an toàn cho câu sáo rỗng mà model vẫn hay viết dù đã cấm
FLUFF = re.compile(r"(phu hop voi (ban|khach|nhu cau)|trai nghiem (thu vi|tuyet voi|dang nho)|"
                   r"co thoi gian nghi ngoi|well[- ]suited|perfect for you|great experience|"
                   r"memorable experience|suits you)")


def _plan_context(req: dict, ctx: dict) -> str:
    pois = load_pois()
    d = date.fromisoformat(req["date"])
    rows = []
    for p in pois.values():
        cr = ctx["crowd"].get(p.id, {})
        hourly = {h: v for h, v in (cr.get("hourly") or {}).items() if v} if cr.get("available") else {}
        row = {"poi_id": p.id, "name": p.name, "district": p.district, "indoor": p.indoor,
               "open_today": p.hours_text(d), "visit_min": p.visit_minutes, "ticket_vnd": p.ticket_vnd,
               "walk_km_onsite": p.walk_km_onsite, "crowd_pct_by_hour": hourly or "không có dữ liệu"}
        if cr.get("live_pct") is not None:
            row["crowd_live_pct"] = cr["live_pct"]
        rows.append(row)
    hours = [{"h": h["time"][11:13], "rain_prob": h["rain_prob"], "temp": h["temp"]}
             for h in hourly_for_date(ctx["weather"], req["date"])]
    a = ctx["air"]
    return json.dumps({
        "date": req["date"], "weekday": DAYS["vi"][d.weekday()],
        "window": [req["start"], req["end"]], "preferences": req.get("preferences", ""),
        "lang": req["lang"], "people": req["people"], "budget_vnd": req.get("budget_vnd"),
        "max_walk_km": req.get("max_walk_km"),
        "must_include": req["must_include"], "avoid": req["avoid"], "replan_from": req.get("replan_from"),
        "pois": rows, "travel_min": matrix(list(pois.values())), "weather_hourly": hours,
        "aqi": {"value": a.get("aqi"), "level": a.get("level")},
    }, ensure_ascii=False)


HATES_CROWD = r"(dong nguoi|ghet dong|tranh dong|so dong|ngai dong|vang ve|it nguoi|crowd|quiet|busy)"


def _problems(sched: dict, check: dict, req: dict | None = None, crowd: dict | None = None) -> list[str]:
    """Những gì đáng bắt LLM đề xuất lại: điểm bị bỏ vì không vừa giờ, vượt ngân sách, đi bộ quá giới hạn,
    và (khi khách ngại đông) điểm rơi vào giờ rất đông theo BestTime."""
    out = [x for x in sched["dropped"] if x.startswith(("Không xếp được", "Bỏ '"))]
    out += [v for v in check["violations"] if v.startswith(("Tổng chi phí", "Tổng đi bộ", "Thiếu điểm"))]
    if not sched["items"]:
        out.append("Lịch rỗng.")
    if req and crowd and re.search(HATES_CROWD, fold(f"{req.get('preferences') or ''} {req.get('_text') or ''}")):
        pois = load_pois()
        for it in sched["items"]:
            pct = pct_at(crowd.get(it.get("poi_id") or "", {}), int(it["start"][:2])) if it.get("poi_id") else None
            if pct and pct >= 85:
                out.append(f"Khách ngại đông nhưng {pois[it['poi_id']].name} lúc {it['start'][:2]}h đông ~{pct}%: "
                           f"đổi thứ tự sang giờ vắng hơn hoặc chọn điểm khác.")
    return out


CROWD_OK = 70        # dưới mức này coi là chấp nhận được
MAX_EXTRA_TRAVEL = 20  # đổi thứ tự không được làm tăng tổng thời gian di chuyển quá 20 phút


def _crowd_penalty(items: list[dict], crowd: dict) -> int:
    """Tổng phần vượt ngưỡng CROWD_OK của mức đông BestTime trong suốt thời gian ở mỗi điểm."""
    total = 0
    for it in items:
        if not it.get("poi_id"):
            continue
        c = crowd.get(it["poi_id"], {})
        pcts = [pct_at(c, h) or 0 for h in range(int(it["start"][:2]), int(it["end"][:2]) + 1)]
        total += max([0] + [p - CROWD_OK for p in pcts])
    return total


def _sched_stats(sched: dict) -> tuple[int, int, int]:
    visits = [i for i in sched["items"] if i.get("poi_id")]
    minutes = sum(int(i["end"][:2]) * 60 + int(i["end"][3:]) - int(i["start"][:2]) * 60 - int(i["start"][3:])
                  for i in visits)
    return len(visits), minutes, sum(i.get("travel_min", 0) for i in visits)


def _very_busy(items: list[dict], req: dict, crowd: dict) -> list[str]:
    pois = load_pois()
    out = []
    for it in items:
        pid = it.get("poi_id")
        if pid and pid not in req.get("must_include", []):
            pct = pct_at(crowd.get(pid, {}), int(it["start"][:2]))
            if pct and pct >= 85:
                out.append(f"{pid} ({pois[pid].name}) lúc {it['start'][:2]}h đông ~{pct}%")
    return out


def reorder_for_crowd(stops: list[dict], req: dict, crowd: dict) -> tuple[list[dict], dict, bool]:
    """Code thử mọi thứ tự điểm tham quan (bữa ăn giữ nguyên vị trí) và chọn thứ tự ít đông nhất,
    miễn là không mất điểm, không rút ngắn thời gian tham quan và không tăng di chuyển quá nhiều.
    LLM vẫn quyết định ĐI ĐÂU; code chỉ tối ưu THỨ TỰ theo số liệu BestTime."""
    from itertools import permutations

    base = schedule(stops, req)
    base_pen = _crowd_penalty(base["items"], crowd)
    vis_idx = [i for i, s in enumerate(stops) if s.get("kind", "visit") == "visit"]
    if base_pen == 0 or not 2 <= len(vis_idx) <= 6:
        return stops, base, False
    n0, m0, tr0 = _sched_stats(base)
    best = (base_pen, tr0, stops, base)
    visits = [stops[i] for i in vis_idx]
    for perm in permutations(visits):
        cand = list(stops)
        for i, s in zip(vis_idx, perm):
            cand[i] = s
        sc = schedule(cand, req)
        n, m, tr = _sched_stats(sc)
        if n < n0 or m < m0 - 15 or tr > tr0 + MAX_EXTRA_TRAVEL:
            continue
        pen = _crowd_penalty(sc["items"], crowd)
        if (pen, tr) < best[:2]:
            best = (pen, tr, cand, sc)
    if base_pen - best[0] < 10:  # cải thiện không đáng kể thì giữ thứ tự của LLM
        return stops, base, False
    return best[2], best[3], True


def make_plan(req: dict, ctx: dict, now: datetime, use_validator: bool = True) -> dict:
    d = date.fromisoformat(req["date"])
    w_hours = hourly_for_date(ctx["weather"], req["date"])
    context = _plan_context(req, ctx)

    if not use_validator:  # baseline: LLM tự viết giờ, không kiểm, không sửa
        plan, meta = chat_json(BASELINE_SYSTEM, context, max_tokens=1200)
        check = validate(plan, req, w_hours, ctx["air"], now)
        return {"plan": plan, "check": check, "repairs": 0, "llm_calls": [meta], "date": d.isoformat(), "dropped": []}

    proposal, meta = chat_json(PLAN_SYSTEM, context, max_tokens=900)
    calls = [meta]
    sched = schedule(proposal.get("stops", []), req)
    check = validate({"items": sched["items"]}, req, w_hours, ctx["air"], now)
    attempts = 0
    while attempts < MAX_REPAIRS and (probs := _problems(sched, check, req, ctx["crowd"])):
        attempts += 1
        fix_user = (context + "\n\nĐề xuất trước:\n" + json.dumps(proposal, ensure_ascii=False)
                    + "\n\nHệ thống xếp giờ/kiểm tra báo:\n- " + "\n- ".join(probs)
                    + "\nHãy đề xuất lại (đổi điểm/thứ tự/thời lượng) cho vừa. Điểm đóng cửa thì bỏ.")
        proposal, meta = chat_json(PLAN_SYSTEM, fix_user, max_tokens=900)
        calls.append(meta)
        sched = schedule(proposal.get("stops", []), req)
        check = validate({"items": sched["items"]}, req, w_hours, ctx["air"], now)

    if check["ok"]:  # chỉ tối ưu thứ tự khi lịch đã hợp lệ; lịch mới phải vẫn hợp lệ
        stops, sched2, changed = reorder_for_crowd(proposal.get("stops", []), req, ctx["crowd"])
        if changed:
            check2 = validate({"items": sched2["items"]}, req, w_hours, ctx["air"], now)
            if check2["ok"]:
                proposal, sched, check = {"stops": stops}, sched2, check2
                sched["reordered"] = True
        # Đổi thứ tự vẫn không tránh được: điểm khách KHÔNG gọi tên mà rất đông -> nhờ LLM cân nhắc thay 1 lần
        busy = _very_busy(sched["items"], req, ctx["crowd"])
        if busy:
            fix_user = (context + "\n\nĐề xuất trước:\n" + json.dumps(proposal, ensure_ascii=False)
                        + "\n\nTheo BestTime, các điểm sau rất đông đúng lúc khách tới:\n- " + "\n- ".join(busy)
                        + "\nĐổi thứ tự không tránh được. Nếu có điểm khác MỞ, hợp sở thích và vắng hơn (xem "
                          "crowd_pct_by_hour) thì thay vào; không có thì giữ nguyên.")
            try:
                prop2, meta = chat_json(PLAN_SYSTEM, fix_user, max_tokens=900)
                calls.append(meta)
                stops2, sched2, _ = reorder_for_crowd(prop2.get("stops", []), req, ctx["crowd"])
                check2 = validate({"items": sched2["items"]}, req, w_hours, ctx["air"], now)
                if (check2["ok"] and _sched_stats(sched2)[0] >= _sched_stats(sched)[0]
                        and _crowd_penalty(sched2["items"], ctx["crowd"]) < _crowd_penalty(sched["items"], ctx["crowd"])):
                    proposal, sched, check = {"stops": stops2}, sched2, check2
                    sched["reordered"] = True
            except LLMError:
                pass  # giữ lịch đang có
    for it in sched["items"]:  # ghi chú bữa ăn nhắc tên điểm ("sau khi thăm Văn Miếu") dễ sai sau khi đổi -> bỏ
        if it["kind"] == "meal" and (mentioned_ids(it["why"]) | _mentioned_en(it["why"])):
            it["why"] = it["why"].split(" – ")[0]

    trimmed, removed, fits = trim_to_budget(sched["items"], req)
    if removed:
        sched["items"] = trimmed
        sched["dropped"] = sched["dropped"] + [f"Bỏ bớt {x} để vừa ngân sách." for x in removed]
    summary, meta = write_summary(sched, req, ctx["crowd"], w_hours)
    calls.append(meta)
    plan = {"items": sched["items"], "summary": summary}
    check = validate(plan, req, w_hours, ctx["air"], now)  # lưới an toàn cuối
    closed_asked = [x for x in sched["dropped"] if x.startswith("Không đi được")]
    pois = load_pois()
    for it in sched["items"]:  # cảnh báo đông do code sinh từ số liệu BestTime
        if it.get("poi_id"):
            pct = pct_at(ctx["crowd"].get(it["poi_id"], {}), int(it["start"][:2]))
            if pct and pct >= 85:
                check["warnings"].append(
                    f"{pois[it['poi_id']].name} lúc {it['start'][:2]}h thường rất đông (~{pct}% so với giờ đông nhất tuần).")
    closed_asked += [x for x in sched["dropped"] if x.startswith("Bỏ bớt")]
    if not fits:
        check["warnings"].append(f"Ngân sách {int(req['budget_vnd']):,}đ không đủ: lịch tối thiểu vẫn tốn "
                                 f"{check['totals']['total_vnd']:,}đ.".replace(",", "."))
    items = sched["items"]
    if items:
        last_end = items[-1]["end"]
        gap = (int(req["end"][:2]) * 60 + int(req["end"][3:])) - (int(last_end[:2]) * 60 + int(last_end[3:]))
        if 60 <= gap and req["end"] <= "19:00":  # khung tối mặc định 21:00 thì không cần nhắc
            # Code kiểm thật: còn điểm nào mở >= 45 phút trong khoảng trống không (không nói "hết điểm" khi còn)
            pois = load_pois()
            used = {i["poi_id"] for i in items if i.get("poi_id")} | set(req.get("avoid", []))
            s0 = int(last_end[:2]) * 60 + int(last_end[3:]) + 15
            e0 = int(req["end"][:2]) * 60 + int(req["end"][3:])
            more = [p.display(req["lang"]) for p in pois.values() if p.id not in used and any(
                min(b.hour * 60 + b.minute, e0) - max(a.hour * 60 + a.minute, s0) >= 45 for a, b in p.intervals_on(d))]
            if more:
                check["warnings"].append(t("ends_early_more", req["lang"], e=last_end, end=req["end"],
                                           places=", ".join(more[:4])))
            else:
                check["warnings"].append(t("ends_early", req["lang"], e=last_end, w=f"{req['start']}-{req['end']}"))
    rest = [x for x in check["warnings"] if x not in closed_asked]
    check["warnings"] = closed_asked + sorted(rest, key=lambda x: not x.startswith("Không xếp được"))
    return {"plan": plan, "check": check, "repairs": attempts, "llm_calls": calls, "date": d.isoformat(),
            "dropped": sched["dropped"]}


def write_summary(sched: dict, req: dict, crowd: dict | None = None,
                  weather_hours: list[dict] | None = None) -> tuple[str, dict]:
    pois = load_pois()
    final = []
    for i in sched["items"]:
        row = {"luc": f"{i['start']}-{i['end']}", "muc": pois[i["poi_id"]].display(req["lang"]) if i["poi_id"] else i["why"]}
        if i["poi_id"]:
            p = pois[i["poi_id"]]
            row.update(quan=p.district, trong_nha=p.indoor, di_chuyen=i.get("travel_mode"),
                       gio_mo=p.hours_text(date.fromisoformat(req["date"])))
            pct = pct_at((crowd or {}).get(i["poi_id"], {}), int(i["start"][:2]))
            if pct:
                row["dong_pct"] = pct
        final.append(row)
    rain = max((h.get("rain_prob") or 0 for h in (weather_hours or [])
                if req["start"] <= h["time"][11:16] <= req["end"]), default=None)
    payload = json.dumps({"so_thich_khach": req.get("preferences", ""), "so_nguoi": req.get("people"),
                          "lich_trinh": final, "khong_di_duoc": sched["dropped"], "mua_max_pct": rain,
                          "da_doi_thu_tu_tranh_dong": bool(sched.get("reordered"))}, ensure_ascii=False)
    try:
        j, meta = chat_json(SUMMARY_SYSTEM.format(lang=req["lang"]), payload, max_tokens=250)
        text = str(j.get("summary", ""))
    except LLMError as e:
        return "", {"error": str(e)}
    # Lưới an toàn: bỏ câu nhắc tới điểm không có trong lịch và không nằm trong danh sách "không đi được"
    allowed = {i["poi_id"] for i in sched["items"] if i["poi_id"]} | mentioned_ids(" ".join(sched["dropped"]))
    busy = any(r.get("dong_pct", 0) >= 60 for r in final)
    dry = rain is None or rain < 40

    def ok(s: str) -> bool:
        f = fold(s)
        if (mentioned_ids(s) | _mentioned_en(s)) - allowed or FLUFF.search(f):
            return False
        if busy and AVOID_CLAIM.search(f):  # nói "tránh đông" trong khi vẫn có điểm đông
            return False
        if MORNING_ONLY.search(f):  # "Hỏa Lò chỉ mở buổi sáng" trong khi Hỏa Lò mở tới 17h -> bịa
            named = mentioned_ids(s) | _mentioned_en(s)
            d0 = date.fromisoformat(req["date"])
            if not named or any(b.hour >= 12 for pid in named for _, b in pois[pid].intervals_on(d0)):
                return False
        return not (dry and RAIN_TIP.search(f))  # khuyên mang ô khi gần như không mưa

    kept = [s for s in re.split(r"(?<=[.!?])\s+", text) if ok(s)]
    return " ".join(kept), meta


MORNING_ONLY = re.compile(r"chi mo (cua )?(vao )?buoi sang|chi mo sang|only open(s)? in the morning|morning[- ]only|only opens? mornings")
AVOID_CLAIM = re.compile(r"tranh (gio |luc |thoi diem )?(dong|cao diem)|vang (nguoi|ve)|avoid(ing)? (the )?(crowd|peak|busy)|quiet")
RAIN_TIP = re.compile(r"\bmang (theo )?o\b|ao mua|umbrella|raincoat")


def _mentioned_en(s: str) -> set[str]:
    q = s.lower()
    return {p.id for p in load_pois().values() if p.name_en and p.name_en.split(" (")[0].lower() in q}


def render_plan(res: dict, ctx: dict, req: dict) -> str:
    pois = load_pois()
    lang = req["lang"]
    tr = en_text if lang == "en" else (lambda s: s)
    d = date.fromisoformat(res["date"])
    plan, check = res["plan"], res["check"]
    lines = [t("plan_title", lang, day=DAYS[lang][d.weekday()], date=d.strftime("%d/%m/%Y"))]
    if req.get("date_note"):
        lines.insert(0, f"({req['date_note']})")
    for it in plan.get("items", []):
        if it.get("kind") == "visit" and it.get("poi_id") in pois:
            p = pois[it["poi_id"]]
            mv = ""
            if it.get("travel_mode") in ("walk", "taxi"):
                mv = f"  ← {t(it['travel_mode'], lang)} {it['travel_min']}'"
            tk = p.ticket_text(lang)
            cr = ctx["crowd"].get(p.id, {})
            pct = pct_at(cr, int(it["start"][:2])) if cr.get("available") else None
            cw = f"; {'đông' if lang == 'vi' else 'crowd'} ~{pct}% ({level(pct, lang)})" if pct else ""
            tkp = f"; {t('ticket', lang)}: {tk}" if not p.free else (f"; {tk}" if tk else "")  # vào tự do: không ghi vé
            lines.append(f"• {it['start']}-{it['end']}  {p.display(lang)} ({t('open', lang)} {p.hours_text(d, lang)}"
                         f"{tkp}{cw}){mv}")
        else:
            why = it.get("why", "")
            if lang == "en":
                for vi, en in MEAL_EN.items():
                    why = why.replace(vi, en)
                why = en_text(why)
            lines.append(f"• {it.get('start')}-{it.get('end')}  {why}")
    if plan.get("summary"):
        lines.append(f"\n{plan['summary']}")
    tot = check.get("totals")
    if tot and plan.get("items"):
        onsite, legs = tot.get("walk_onsite_km", 0), tot.get("walk_legs_km", 0)
        walk_note = ("" if not onsite else t("walk_onsite_only", lang) if not legs
                     else t("walk_split", lang, onsite=onsite, legs=legs))
        n = tot["people"]
        # chỉ liệt kê khoản > 0 (không ghi "vé 0đ" khi toàn điểm vào tự do, "ăn uống 0đ" khi không có bữa ăn)
        names = {"vi": [("tickets_vnd", "vé"), ("transport_vnd", "đi lại"), ("food_vnd", "ăn uống")],
                 "en": [("tickets_vnd", "tickets"), ("transport_vnd", "transport"), ("food_vnd", "food")]}[lang]
        cur = "đ" if lang == "vi" else ""
        parts = [f"{lab} {money(tot[k], lang)}{cur}" for k, lab in names if tot[k]]
        parts_s = (f" ({' + '.join(parts)})" if len(parts) > 1
                   else f" ({parts[0].rsplit(' ', 1)[0]})" if parts else "")  # 1 khoản: chỉ nói tên khoản
        lines.append("\n" + t("totals", lang, people=n, people_en=f"{n} {'person' if n == 1 else 'people'}",
                                total=money(tot["total_vnd"], lang), parts=parts_s,
                                walk=tot["walk_km"], walk_note=walk_note))
    if not check["ok"]:
        lines.append("\n" + t("manual_bad" if res.get("manual") else "still_bad", lang))
        lines += [f"  - {tr(v)}" for v in check["violations"]]
    tnotes = ticket_notes(plan.get("items", []), req, lang)  # miễn/giảm vé đã áp dụng, giấy tờ cần mang (sinh sẵn theo lang)
    if check["warnings"] or tnotes:
        lines.append("\n" + t("notes", lang))
        lines += [f"  - {tr(x)}" for x in check["warnings"]]
        lines += [f"  - {x}" for x in tnotes]
    lines += _news_lines(ctx.get("news", {}), lang)
    w, a = ctx["weather"], ctx["air"]
    wsrc = w.get("source", "?") if lang == "vi" else "Open-Meteo (model estimate)"
    asrc = a.get("source", "?") if lang == "vi" else (
        "WAQI station (measured)" if a.get("measured") else "Open-Meteo (model estimate)")
    crowd = "BestTime" if any(c.get("available") for c in ctx["crowd"].values()) else t("no_crowd", lang)
    tm = ctx.get("traffic") or {}
    if tm.get("available"):
        live = tm.get("depart") == "now"
        trf = ("TomTom, giao thông " + ("hiện tại" if live else "dự báo")) if lang == "vi" \
            else ("TomTom " + ("live" if live else "predicted") + " traffic")
    else:
        trf = "ước lượng theo khoảng cách" if lang == "vi" else "distance-based estimate"
    lines.append("\n" + t("data", lang, w=wsrc, wt=str(w.get("model_time", "?"))[11:16],
                          aqi=a.get("aqi"), lvl=a.get("level"), asrc=asrc, crowd=crowd, trf=trf))
    return "\n".join(lines)


# ---------- Điểm vào ----------
BOOKING = (r"\b(dat|mua|giu)\b(?:(?!lich|lo trinh).){0,25}\b(ve|ban|phong|cho ngoi|tour)\b|\bthanh toan\b|"
           r"\bbook(ing)?\b|\bbuy (a |\d+ )?tickets?\b|\breserv(e|ation)\b|\bpay for\b")
NEAR_ME = r"\b(gan day|gan toi|gan minh|quanh day|xung quanh (toi|minh|day)|cho toi dang o|near me|nearby|around here|close to me)\b"


# Câu CHỈ gồm các từ chung chung này (không ngày, không người, không ngân sách, không sở thích) -> hỏi lại
VAGUE_WORDS = {"minh", "toi", "em", "anh", "chi", "ban", "muon", "can", "hay", "giup", "cho", "nen", "gi", "dau", "di",
               "choi", "tham", "quan", "goi", "y", "lich", "trinh", "ke", "hoach", "o", "ha", "noi", "hanoi", "lam",
               "nhe", "nha", "voi", "the", "nao", "plan", "a", "trip", "my", "please", "suggest", "something",
               "itinerary", "some", "places", "go", "to", "in", "where", "should", "i", "what", "do", "visit",
               "len", "xep", "chuyen"}
ANY_DETAIL = r"^(tuy (ban|y)|sao cung duoc|gi cung duoc|anything|whatever|up to you|you choose|you pick)\b"


def too_vague(text: str) -> bool:
    words = re.sub(r"[^a-z0-9 ]", " ", fold(text)).split()
    return 0 < len(words) <= 6 and all(w in VAGUE_WORDS for w in words) and bool(re.search(PLAN_WORDS + r"|\bdi\b", " ".join(words)))


def handle(text: str, now: datetime | None = None, use_validator: bool = True, session: dict | None = None) -> dict:
    """session: dict do người gọi giữ giữa các lượt (CLI/web) để hiểu tin nhắn chỉnh sửa như "bỏ Hỏa Lò đi".
    Khoá bắt đầu bằng "_" trong session chỉ giữ trong RAM (ví dụ dữ liệu thời tiết đã lấy), không ghi đĩa."""
    now = now or now_hanoi()
    t0 = time.perf_counter()
    text = (text or "").strip()
    lang = detect_lang(text) if text else "vi"

    def quick(answer: str, intent: str, actions: list | None = None, **extra) -> dict:
        return {"answer": answer, "intent": intent, "lang": lang, "actions": actions or [],
                "latency_s": round(time.perf_counter() - t0, 2), "llm_calls": [], **extra}

    if not text:
        return quick(t("internal", lang), "empty")
    blocked = guard_input(text, lang)
    if blocked:
        return quick(blocked, "blocked")
    q = fold(text)
    if re.search(BOOKING, q):
        return quick(t("no_booking", lang), "refused",
                     [{"tab": "places", "label": "Xem giá vé tham khảo" if lang == "vi" else "See reference prices"}])
    bad = invalid_date(text, now)
    if bad:
        return quick(t("bad_date", lang, d=bad), "bad_input")
    prev = (session or {}).get("last_req")

    # Câu hỏi về ỨNG DỤNG và về DỮ LIỆU CÓ SẴN: code trả lời từ app_manifest / danh mục, không gọi LLM
    if is_app_question(text) and not (mentioned_ids(text) and not re.search(APP_WORDS, q)):
        ans, acts = answer_app(text, lang)
        return quick(ans, "app", acts)
    if is_catalog_question(text, now):
        ans, acts = answer_catalog(text, now, lang)
        return quick(ans, "catalog", acts)
    # Hỏi tiếp về thời tiết CÙNG nơi ("còn ngày mai?", "ở đó chiều nay?") -> dùng lại dữ liệu đã lấy, không gọi LLM/API
    wf = weather_followup(text, prev, now, lang)
    if wf:
        ans, reused = _weather_reply(wf, now, session)
        if session is not None:
            session["last_req"] = {k: v for k, v in wf.items() if not k.startswith("_")}
        return quick(ans, "weather", _actions(wf, None), reused_weather=reused, fast_route=True, req=wf)

    if not prev and too_vague(text):
        if session is not None:  # lượt sau (ví dụ "mai, 2 người, thích lịch sử") được hiểu là bổ sung cho yêu cầu lịch trình
            session["last_req"] = {"intent": "plan", "awaiting_details": True}
        return quick(t("clarify_plan", lang), "clarify",
                     [{"tab": "planner", "label": "Hoặc tự xếp lịch" if lang == "vi" else "Or build it yourself"}])
    try:
        req, m_intent = parse_request(text, now, lang, prev)
    except LLMError as e:
        return quick(t("llm_down", lang, e=e), "error")
    if prev and prev.get("awaiting_details") and (req["intent"] == "other" or re.search(ANY_DETAIL, q)):
        req["intent"] = "plan"  # "tùy bạn" sau câu hỏi lại -> lên lịch với mặc định
    out: dict = {"req": req, "intent": req["intent"], "lang": lang, "llm_calls": [m_intent] if m_intent else [],
                 "fast_route": m_intent is None, "actions": []}
    if req.get("_error"):
        key, kw = req["_error"]
        out.update(answer=t(key, lang, **kw), intent="bad_input", latency_s=round(time.perf_counter() - t0, 2))
        return out
    if req["intent"] in ("plan", "other", "info") and re.search(NEAR_ME, q) and not req.get("replan_from") \
            and not req.get("poi_ids") and not req.get("place"):
        out.update(answer=t("need_location", lang), intent="clarify", latency_s=round(time.perf_counter() - t0, 2))
        return out
    res = None
    try:
        if req["intent"] == "app":
            out["answer"], out["actions"] = answer_app(text, lang)
        elif req["intent"] == "other":
            out["answer"] = t("other", lang)
            out["actions"] = answer_app("", lang)[1]
        elif req["intent"] in ("hours", "info"):
            ids = req["poi_ids"][:2]
            with ThreadPoolExecutor(max_workers=2) as ex:
                news = dict(zip(ids, ex.map(_safe_news, ids)))
            out["answer"] = answer_hours(req, now, news)
        elif req["intent"] == "weather":
            out["answer"], out["reused_weather"] = _weather_reply(req, now, session)
        else:
            ctx = gather(req, now, _fresh_wx(session, HK_KEY))
            out["gather_s"] = ctx["gather_s"]
            if not _fresh_wx(session, HK_KEY):  # lượt sau hỏi thời tiết Hoàn Kiếm thì dùng lại
                _store_wx(session, HK_KEY, ctx["weather"], ctx["air"], None, HANOI_CENTER)
            res = make_plan(req, ctx, now, use_validator=use_validator)
            out.update(plan=res["plan"], check=res["check"], repairs=res["repairs"])
            out["llm_calls"] += res["llm_calls"]
            out["answer"] = render_plan(res, ctx, req)
    except LLMError as e:
        out["answer"] = t("llm_down", lang, e=e)
        out["intent"] = "error"
    if out["intent"] != "error" and not out["actions"]:
        out["actions"] = _actions(req, res)
    if session is not None and req["intent"] in ("plan", "hours", "weather", "info"):
        session["last_req"] = {k: v for k, v in req.items() if not k.startswith("_")}
    out["latency_s"] = round(time.perf_counter() - t0, 2)
    return out


def _actions(req: dict, res: dict | None) -> list[dict]:
    """Nút dưới câu trả lời: mở đúng tab của ứng dụng với dữ liệu điền sẵn (giao diện đọc theo app_manifest)."""
    vi = req.get("lang", "vi") == "vi"
    pois = load_pois()
    if req["intent"] in ("hours", "info"):
        acts = [{"tab": "places", "poi": pid, "filter": {"date": req["date"]},
                 "label": ("Xem thẻ " if vi else "Open ") + pois[pid].display(req["lang"])} for pid in req["poi_ids"][:2]]
        return acts or [{"tab": "places", "filter": {"date": req["date"]}, "label": "Xem tất cả điểm" if vi else "See all places"}]
    if req["intent"] == "weather":
        return [{"tab": "weather", "place": req.get("place") or "", "date": req["date"],
                 "label": "Xem bảng theo giờ" if vi else "Hourly table"}]
    if req["intent"] == "plan" and res and res["plan"].get("items"):
        stops = [{"kind": "visit", "poi_id": i["poi_id"], "minutes": _dur(i)} if i.get("poi_id")
                 else {"kind": "meal", "minutes": _dur(i)} for i in res["plan"]["items"]]
        plan = {k: req.get(k) for k in ("date", "start", "end", "people", "children", "child_ages", "students",
                                        "student_level", "budget_vnd", "max_walk_km", "preferences")}
        return [{"tab": "planner", "plan": {**plan, "stops": stops},
                 "label": "Chỉnh lịch này trong Tự xếp lịch" if vi else "Edit in Planner"}]
    return []


def _dur(it: dict) -> int:
    return (int(it["end"][:2]) * 60 + int(it["end"][3:5])) - (int(it["start"][:2]) * 60 + int(it["start"][3:5]))


# ---------- Thời tiết: nhớ theo phiên ----------
WX_REUSE_S = 3600   # dự báo theo giờ đổi chậm: dùng lại trong 1 giờ cho cùng một nơi
AIR_REUSE_S = 1800  # AQI là số đo hiện tại: chỉ dùng lại 30 phút
FOLLOW_WORDS = {
    # từ nối
    "con", "thi", "sao", "the", "nao", "vay", "nhi", "nhe", "a", "oi", "ok", "and", "what", "about", "how", "is", "it",
    "will", "be", "for", "on", "at", "in", "of", "va", "luc", "vao", "ngay", "hom", "buoi",
    # nơi đã hỏi
    "o", "do", "cho", "khu", "noi", "vung", "day", "there", "that", "place",
    # thời gian
    "mai", "kia", "mot", "nay", "sang", "trua", "chieu", "toi", "dem", "cuoi", "tuan", "sau", "thu", "hai", "ba", "tu",
    "nam", "bay", "chu", "nhat", "today", "tomorrow", "tonight", "morning", "afternoon", "evening", "night", "next",
    "week", "weekend", "after", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    # thời tiết
    "thoi", "tiet", "co", "mua", "khong", "nong", "lanh", "ret", "nang", "gio", "nhiet", "troi", "dep", "am",
    "weather", "rain", "rainy", "hot", "cold", "sunny", "temperature", "wind",
}
TOD = [(r"\b(sang|morning)\b", "06:00", "11:00"), (r"\b(trua|noon)\b", "11:00", "14:00"),
       (r"\b(chieu|afternoon)\b", "12:00", "18:00"), (r"\b(toi|dem|tonight|evening|night)\b", "18:00", "23:00")]
THERE = r"\b(o do|cho do|noi do|khu do|vung do|there|that place)\b"


def weather_followup(text: str, prev: dict | None, now: datetime, lang: str) -> dict | None:
    """Lượt trước là thời tiết, lượt này chỉ đổi NGÀY/BUỔI, nói "ở đó", hoặc đổi sang quận/huyện khác
    ("còn Cầu Giấy?") -> không cần LLM."""
    if not prev or prev.get("intent") != "weather":
        return None
    q = fold(text)
    area = areas.mentioned(text)
    rest = re.sub(rf"\b{re.escape(area.key)}\b", " ", q) if area else q
    words = re.sub(r"[^a-z0-9 ]", " ", rest).split()
    if len(words) > 12 or re.search(PLAN_WORDS, q) or (not words and not area):
        return None
    if not all(w in FOLLOW_WORDS or re.fullmatch(r"\d{1,4}h?", w) for w in words):
        return None  # có từ lạ (thường là tên nơi khác) -> để luồng thường xử lý
    if not (area or resolve_date(text, now) or re.search(THERE, q) or any(re.search(p, q) for p, _, _ in TOD)
            or re.search(WEATHER_WORDS, q)):
        return None
    place = area.name if area else prev.get("place")
    req = {"intent": "weather", "poi_ids": [] if area else list(prev.get("poi_ids") or []), "place": place}
    if resolve_date(text, now) is None and prev.get("date"):
        req["date"] = prev["date"]
    req = _defaults(req, text, now, lang)
    for pat, a, b in TOD:
        if re.search(pat, q):
            req["start"], req["end"] = a, b
            break
    if req["date"] == now.date().isoformat():
        nxt = _next_slot(now)
        if nxt < req["end"]:
            req["start"] = max(req["start"], nxt)
    req["_followup"] = True
    return req


def _next_slot(now: datetime) -> str:
    return (now + timedelta(minutes=15 - now.minute % 15)).strftime("%H:%M")


HK_KEY = "hoan_kiem"  # khu mặc định (trung tâm, cùng toạ độ HANOI_CENTER mà lập lịch dùng)
WX_KEEP = 5           # số nơi nhớ trong một phiên


def _place_key(place: str | None) -> str:
    """Khoá bộ nhớ thời tiết: id quận/huyện nếu nhận ra, không thì tên đã chuẩn hoá (địa danh khác trong Hà Nội)."""
    p = fold((place or "").strip())
    if not p or p in ("ha noi", "hanoi", "hn", "ho guom") or mentioned_ids(p):
        return HK_KEY
    a = areas.resolve(place)
    return a.id if a else p


def _fresh_wx(session: dict | None, key: str) -> dict | None:
    wx = ((session or {}).get("_wx") or {}).get(key)
    if wx and time.time() - wx["ts"] < WX_REUSE_S:
        return wx
    return None


def _store_wx(session: dict | None, key: str, weather: dict, air: dict, loc: dict | None, latlon: tuple) -> None:
    if session is None or "hourly" not in weather:
        return
    store = session.get("_wx")
    if not isinstance(store, dict) or "key" in store:  # dạng cũ (1 nơi) -> làm mới
        store = session["_wx"] = {}
    ts = time.time()
    store[key] = {"key": key, "ts": ts, "air_ts": ts, "weather": weather, "air": air, "loc": loc, "latlon": latlon}
    while len(store) > WX_KEEP:
        store.pop(min(store, key=lambda k: store[k]["ts"]))


def weather_data(req: dict, now: datetime, session: dict | None = None) -> dict:
    """Lấy thời tiết cho nơi/ngày của req. Chỉ nhận khu vực THUỘC HÀ NỘI.
    Trả {"error": text} hoặc {"weather", "air", "loc", "reused", "ts"}; cùng nơi trong phiên thì dùng lại."""
    lang = req["lang"]
    d = date.fromisoformat(req["date"])
    if (d - now.date()).days >= FORECAST_DAYS:
        return {"error": t("weather_far", lang, d=d.strftime("%d/%m/%Y"), n=FORECAST_DAYS)}
    key = _place_key(req.get("place"))
    wx = _fresh_wx(session, key)
    if wx:  # cùng nơi vừa hỏi: dùng lại, không gọi Open-Meteo/geocode nữa
        if time.time() - wx["air_ts"] >= AIR_REUSE_S:
            wx.update(air=get_air(*wx["latlon"]), air_ts=time.time())
        return {"weather": wx["weather"], "air": wx["air"], "loc": wx["loc"], "reused": True, "ts": wx["ts"], "key": key}
    area = next((a for a in areas.load() if a.id == key), None)
    if area:
        loc = {"name": area.label if lang == "vi" else f"{area.name} {'district' if area.kind == 'Quận' else area.kind.lower()}",
               "lat": area.lat, "lon": area.lon, "area_id": area.id}
        if key == HK_KEY:
            loc["name"] = "khu Hoàn Kiếm" if lang == "vi" else "Hoan Kiem"
    else:  # tên khác trong Hà Nội (phường, khu): geocode chỉ nhận kết quả thuộc Hà Nội
        loc = geocode(req["place"].strip())
        if not loc:
            sug = ", ".join(a.name for a in areas.search(req["place"], 3)) or "Hoàn Kiếm, Cầu Giấy, Ba Vì"
            return {"error": t("place_not_hanoi", lang, p=req["place"], s=sug)}
    lat, lon = loc["lat"], loc["lon"]
    with ThreadPoolExecutor(max_workers=2) as ex:
        fw, fa = ex.submit(get_weather, lat, lon), ex.submit(get_air, lat, lon)
        weather, air = fw.result(), fa.result()
    _store_wx(session, key, weather, air, loc, (lat, lon))
    return {"weather": weather, "air": air, "loc": loc, "reused": False, "ts": time.time(), "key": key}


def _weather_reply(req: dict, now: datetime, session: dict | None = None) -> tuple[str, bool]:
    w = weather_data(req, now, session)
    if "error" in w:
        return w["error"], False
    text = answer_weather({"weather": w["weather"], "air": w["air"]}, req, now, w["loc"])
    if w["reused"]:
        text += "\n" + t("wx_reused", req["lang"], t=datetime.fromtimestamp(w["ts"], now.tzinfo).strftime("%H:%M"))
    return text, w["reused"]
