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

from .config import HANOI_CENTER, now_hanoi
from .dates import resolve_date
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
from .tools.weather import get_weather, hourly_for_date
from .tools.web import web_search
from .validator import validate

MAX_REPAIRS = 2

# Lớp chặn đầu vào tối thiểu (Day 11): chuẩn hoá rồi bắt mẫu injection phổ biến EN/VN
INJECTION = re.compile(
    r"(ignore (all|previous|the above)|system prompt|bo qua (moi|tat ca|huong dan)|quen (het|moi) (huong dan|chi dan)|"
    r"ban la (mot )?(ai|mo hinh) khac|developer mode|jailbreak|you are now)")

PLAN_WORDS = (r"(lich trinh|xep lich|di dau|nen di|di choi|tham quan|goi y|ke hoach|plan|itinerary|schedule|suggest|"
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
{"intent": "plan" | "hours" | "weather" | "info" | "other",
 "date": "YYYY-MM-DD",             // ngày khách muốn đi; mặc định hôm nay
 "start": "HH:MM", "end": "HH:MM",  // khung giờ khách rảnh; mặc định 08:00-21:00
 "poi_ids": [],                     // điểm khách nhắc tới, CHỈ dùng id trong danh mục
 "must_include": [], "avoid": [],   // id phải có / phải bỏ
 "people": 1,                       // số người đi
 "budget_vnd": null,                // ngân sách tổng (VND) nếu khách nói, ví dụ "350k" -> 350000
 "max_walk_km": null,               // giới hạn đi bộ (km) nếu khách nói; "ngại/ít đi bộ" -> 2
 "preferences": "",                 // sở thích, người đi cùng, sức khoẻ, ghét đông...
 "replan_from": null}               // id điểm khách ĐANG đứng nếu khách đang giữa chuyến và muốn đổi kế hoạch
intent: plan = muốn lịch trình / gợi ý đi đâu / đổi kế hoạch; hours = hỏi giờ mở cửa / có mở không;
weather = hỏi thời tiết, mưa, không khí; info = hỏi thông tin, giá vé một điểm; other = ngoài du lịch Hà Nội."""


def _defaults(req: dict, text: str, now: datetime, lang: str) -> dict:
    pois = load_pois()
    req.setdefault("intent", "plan")
    req["lang"] = lang
    req["_text"] = text[:300]
    req["date"] = req.get("date") or now.date().isoformat()
    d_code = resolve_date(text, now)  # code thắng model khi câu có mốc ngày rõ
    if d_code:
        req["date_llm"], req["date"] = req["date"], d_code.isoformat()
    req["start"] = req.get("start") or "08:00"
    req["end"] = req.get("end") or "21:00"
    req["people"] = max(1, int(req.get("people") or 1))
    for k in ("poi_ids", "must_include", "avoid"):
        req[k] = [p for p in (req.get(k) or []) if p in pois]
    if req.get("replan_from") not in pois:
        req["replan_from"] = None
    if req["intent"] == "plan":
        # Chỉ ép "phải đi" với điểm khách GỌI TÊN trong câu (code kiểm), không tin suy diễn của model
        named = mentioned_ids(text) | _mentioned_en(text)
        req["must_include"] = [p for p in dict.fromkeys(req["must_include"] + req["poi_ids"])
                               if p in named and p != req.get("replan_from") and p not in req["avoid"]]
    if req["date"] == now.date().isoformat():  # không xếp vào giờ đã qua
        nxt = (now + timedelta(minutes=15 - now.minute % 15)).strftime("%H:%M")
        if req["start"] < nxt:
            req["start"] = nxt
    return req


def fast_route(text: str, now: datetime, lang: str) -> dict | None:
    """Câu tra cứu rõ ràng -> trả req ngay, không tốn 1 lần gọi LLM (~2.5s)."""
    q = fold(text)
    if re.search(PLAN_WORDS, q):
        return None
    ids = sorted(mentioned_ids(text) | _mentioned_en(text))
    if re.search(WEATHER_WORDS, q) and not re.search(HOURS_WORDS, q.replace("open-meteo", "")):
        return _defaults({"intent": "weather", "poi_ids": ids}, text, now, lang)
    if ids and re.search(HOURS_WORDS, q):
        return _defaults({"intent": "hours", "poi_ids": ids}, text, now, lang)
    return None


def parse_request(text: str, now: datetime, lang: str) -> tuple[dict, dict | None]:
    fast = fast_route(text, now, lang)
    if fast:
        return fast, None
    pois = load_pois()
    catalog = ", ".join(f"{p.id}={p.name} / {p.name_en}" for p in pois.values())
    user = (f"Bây giờ: {now:%Y-%m-%d %H:%M} ({DAYS['vi'][now.weekday()]}), Hà Nội.\nDanh mục: {catalog}\n"
            f"Tin nhắn: {text}")
    req, meta = chat_json(INTENT_SYSTEM, user, max_tokens=400, temperature=0)
    # Câu có từ xin gợi ý/lịch trình thì là "plan", kể cả khi có hỏi kèm thời tiết/giờ mở cửa
    if req.get("intent") in ("weather", "hours", "info") and re.search(PLAN_WORDS, fold(text)):
        req["intent_llm"], req["intent"] = req["intent"], "plan"
    return _defaults(req, text, now, lang), meta


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


def gather(req: dict, now: datetime | None = None) -> dict:
    now = now or now_hanoi()
    pois = list(load_pois().values())
    d = date.fromisoformat(req["date"])
    is_plan = req.get("intent") == "plan"
    news_ids = list(dict.fromkeys(req.get("must_include", []) + req.get("poi_ids", [])))[:3]
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=16) as ex:
        f_weather = ex.submit(get_weather, *HANOI_CENTER)
        f_air = ex.submit(get_air, *HANOI_CENTER)
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
def answer_hours(req: dict, now: datetime, news: dict | None = None) -> str:
    pois = load_pois()
    lang = req["lang"]
    d = date.fromisoformat(req["date"])
    ids = req["poi_ids"] or list(pois)
    lines = [t("hours_title", lang, day=DAYS[lang][d.weekday()], date=d.strftime("%d/%m/%Y"))]
    for pid in ids:
        p = pois[pid]
        status = ""
        if d == now.date():
            status = t("open_now", lang) if p.is_open_at(now) else t("closed_now", lang)
        lines.append(f"• {p.display(lang)}: {p.hours_text(d, lang)}{status} [{t('conf', lang)}: {p.confidence}]")
        if pid in req["poi_ids"]:
            if lang == "vi":
                lines.append(f"  Vé: {p.ticket}. Ghi chú: {p.notes}")
            else:
                lines.append(f"  Adult ticket: ~{money(p.ticket_vnd, 'en')} VND" if p.ticket_vnd else "  Ticket: free")
            if p.sources:
                lines.append(f"  {t('source', lang)}: {p.sources[0]}")
    lines += _news_lines(news or {}, lang)
    return "\n".join(lines)


def answer_weather(ctx: dict, req: dict) -> str:
    lang = req["lang"]
    w, a = ctx["weather"], ctx["air"]
    if "current" not in w:
        return f"Weather unavailable ({w.get('_error')})." if lang == "en" else f"Không lấy được thời tiết ({w.get('_error')})."
    c = w["current"]
    stale = (" [STALE DATA]" if lang == "en" else " [DỮ LIỆU CŨ do API lỗi]") if w.get("_stale") else ""
    if lang == "en":
        lines = [f"Weather around Hoan Kiem at {w['model_time'][11:16]} – Open-Meteo (model estimate){stale}",
                 f"• {c['temp']}°C (feels {c['feels_like']}°C), humidity {c['humidity']}%, wind {c['wind_kmh']} km/h, rain {c['rain_mm']} mm"]
    else:
        lines = [f"Thời tiết khu Hoàn Kiếm lúc {w['model_time'][11:16]} – {w['source']}{stale}",
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
        rain = "rain" if lang == "en" else "mưa"
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

SUMMARY_SYSTEM = """Viết 2-3 câu giải thích lịch trình CUỐI CÙNG cho khách bằng ngôn ngữ "{lang}" (vi = tiếng Việt, en = English):
vì sao hợp với khách, đánh đổi gì. Chỉ nhắc các điểm có trong "lich_trinh" hoặc "khong_di_duoc".
Không thêm điểm khác, không viết con số giờ, giá hay km (hệ thống tự hiển thị).
"dong_pct" là mức đông dự báo (0-100) lúc tới nơi, >=85 là rất đông. KHÔNG được nói lịch "tránh đông/vắng" nếu có điểm
dong_pct >= 60; khi đó nói thật điểm đó có thể đông. Trả JSON {{"summary": "..."}}"""


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
    if req and crowd and re.search(HATES_CROWD, fold(req.get("preferences", "") + " " + req.get("_text", ""))):
        pois = load_pois()
        for it in sched["items"]:
            pct = pct_at(crowd.get(it.get("poi_id") or "", {}), int(it["start"][:2])) if it.get("poi_id") else None
            if pct and pct >= 85:
                out.append(f"Khách ngại đông nhưng {pois[it['poi_id']].name} lúc {it['start'][:2]}h đông ~{pct}%: "
                           f"đổi thứ tự sang giờ vắng hơn hoặc chọn điểm khác.")
    return out


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

    summary, meta = write_summary(sched, req, ctx["crowd"])
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
    rest = [x for x in check["warnings"] if x not in closed_asked]
    check["warnings"] = closed_asked + sorted(rest, key=lambda x: not x.startswith("Không xếp được"))
    return {"plan": plan, "check": check, "repairs": attempts, "llm_calls": calls, "date": d.isoformat(),
            "dropped": sched["dropped"]}


def write_summary(sched: dict, req: dict, crowd: dict | None = None) -> tuple[str, dict]:
    pois = load_pois()
    final = []
    for i in sched["items"]:
        row = {"luc": f"{i['start']}-{i['end']}", "muc": pois[i["poi_id"]].name if i["poi_id"] else i["why"]}
        if i["poi_id"] and crowd:
            pct = pct_at(crowd.get(i["poi_id"], {}), int(i["start"][:2]))
            if pct:
                row["dong_pct"] = pct
        final.append(row)
    payload = json.dumps({"so_thich_khach": req.get("preferences", ""), "lich_trinh": final,
                          "khong_di_duoc": sched["dropped"]}, ensure_ascii=False)
    try:
        j, meta = chat_json(SUMMARY_SYSTEM.format(lang=req["lang"]), payload, max_tokens=250)
        text = str(j.get("summary", ""))
    except LLMError as e:
        return "", {"error": str(e)}
    # Lưới an toàn: bỏ câu nhắc tới điểm không có trong lịch và không nằm trong danh sách "không đi được"
    allowed = {i["poi_id"] for i in sched["items"] if i["poi_id"]} | mentioned_ids(" ".join(sched["dropped"]))
    kept = [s for s in re.split(r"(?<=[.!?])\s+", text) if (mentioned_ids(s) | _mentioned_en(s)) <= allowed]
    return " ".join(kept), meta


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
    for it in plan.get("items", []):
        if it.get("kind") == "visit" and it.get("poi_id") in pois:
            p = pois[it["poi_id"]]
            mv = ""
            if it.get("travel_mode") in ("walk", "taxi"):
                mv = f"  ← {t(it['travel_mode'], lang)} {it['travel_min']}'"
            tk = (p.ticket if lang == "vi" else (f"~{money(p.ticket_vnd, 'en')} VND" if p.ticket_vnd else "free"))
            cr = ctx["crowd"].get(p.id, {})
            pct = pct_at(cr, int(it["start"][:2])) if cr.get("available") else None
            cw = f"; {'đông' if lang == 'vi' else 'crowd'} ~{pct}% ({level(pct, lang)})" if pct else ""
            lines.append(f"• {it['start']}-{it['end']}  {p.display(lang)} ({t('open', lang)} {p.hours_text(d, lang)}; "
                         f"{t('ticket', lang)}: {tk}{cw}){mv}")
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
        lines.append("\n" + t("totals", lang, people=tot["people"], total=money(tot["total_vnd"], lang),
                                tickets=money(tot["tickets_vnd"], lang), transport=money(tot["transport_vnd"], lang),
                                food=money(tot["food_vnd"], lang), walk=tot["walk_km"]))
    if not check["ok"]:
        lines.append("\n" + t("still_bad", lang))
        lines += [f"  - {tr(v)}" for v in check["violations"]]
    if check["warnings"]:
        lines.append("\n" + t("notes", lang))
        lines += [f"  - {tr(x)}" for x in check["warnings"]]
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
def handle(text: str, now: datetime | None = None, use_validator: bool = True) -> dict:
    now = now or now_hanoi()
    t0 = time.perf_counter()
    lang = detect_lang(text)
    blocked = guard_input(text, lang)
    if blocked:
        return {"answer": blocked, "intent": "blocked", "lang": lang, "latency_s": 0.0, "llm_calls": []}
    try:
        req, m_intent = parse_request(text, now, lang)
    except LLMError as e:
        return {"answer": t("llm_down", lang, e=e), "intent": "error", "lang": lang,
                "latency_s": round(time.perf_counter() - t0, 2), "llm_calls": []}
    out: dict = {"req": req, "intent": req["intent"], "lang": lang, "llm_calls": [m_intent] if m_intent else [],
                 "fast_route": m_intent is None}
    try:
        if req["intent"] == "other":
            out["answer"] = t("other", lang)
        elif req["intent"] in ("hours", "info"):
            ids = req["poi_ids"][:2]
            with ThreadPoolExecutor(max_workers=2) as ex:
                news = dict(zip(ids, ex.map(_safe_news, ids)))
            out["answer"] = answer_hours(req, now, news)
        else:
            ctx = gather(req, now)
            out["gather_s"] = ctx["gather_s"]
            if req["intent"] == "weather":
                out["answer"] = answer_weather(ctx, req)
            else:
                res = make_plan(req, ctx, now, use_validator=use_validator)
                out.update(plan=res["plan"], check=res["check"], repairs=res["repairs"])
                out["llm_calls"] += res["llm_calls"]
                out["answer"] = render_plan(res, ctx, req)
    except LLMError as e:
        out["answer"] = t("llm_down", lang, e=e)
        out["intent"] = "error"
    out["latency_s"] = round(time.perf_counter() - t0, 2)
    return out
