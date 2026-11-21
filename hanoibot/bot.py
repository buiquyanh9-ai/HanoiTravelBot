"""Luồng chính của bot.

1. Hiểu yêu cầu (LLM -> JSON có cấu trúc)
2. Lấy dữ liệu SONG SONG (thời tiết, AQI, mức đông) – có cache
3. Câu tra cứu đơn giản -> trả lời bằng code, không gọi LLM lần 2
4. Lập lịch -> LLM đề xuất JSON -> code kiểm -> sai thì trả lại LLM sửa (tối đa 2 lần)
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

from .config import HANOI_CENTER, now_hanoi
from .dates import resolve_date
from .llm import LLMError, chat_json
from .pois import load_pois, mentioned_ids
from .scheduler import schedule
from .textutil import fold
from .tools.air import get_air
from .tools.crowd import get_crowd
from .tools.travel import matrix
from .tools.weather import get_weather, hourly_for_date
from .tools.web import web_search
from .validator import validate

MAX_REPAIRS = 2
VN_DAYS = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ nhật"]

# Lớp chặn đầu vào tối thiểu (ý tưởng Day 11): chuẩn hoá rồi bắt mẫu injection phổ biến EN/VN
INJECTION = re.compile(
    r"(ignore (all|previous)|system prompt|bo qua (moi|tat ca|huong dan)|quen (het|moi) (huong dan|chi dan)|"
    r"ban la (mot )?(ai|mo hinh) khac|developer mode|jailbreak)")


def guard_input(text: str) -> str | None:
    if len(text) > 1500:
        return "Tin nhắn quá dài, bạn rút gọn giúp mình (tối đa 1500 ký tự)."
    if INJECTION.search(fold(text)):
        return "Mình chỉ hỗ trợ tra cứu và lên lịch tham quan Hà Nội, không thực hiện yêu cầu thay đổi cách hoạt động."
    return None


# ---------- 1. Hiểu yêu cầu ----------
INTENT_SYSTEM = """Bạn phân tích tin nhắn của khách du lịch Hà Nội thành JSON. Chỉ trả JSON, không giải thích.
Schema:
{"intent": "plan" | "hours" | "weather" | "info" | "other",
 "date": "YYYY-MM-DD",            // ngày khách muốn đi; mặc định hôm nay
 "start": "HH:MM", "end": "HH:MM", // khung giờ khách rảnh; mặc định 08:00-21:00 (nếu là hôm nay thì start >= giờ hiện tại)
 "poi_ids": [],                    // điểm khách nhắc tới, CHỈ dùng id trong danh mục
 "must_include": [], "avoid": [],  // id phải có / phải bỏ
 "preferences": "",                // tóm tắt sở thích, người đi cùng, sức khoẻ, ghét đông...
 "replan_from": null}              // id điểm khách đang đứng nếu khách đang giữa chuyến và muốn đổi kế hoạch
"intent": plan = muốn lịch trình / gợi ý đi đâu / đổi kế hoạch; hours = hỏi giờ mở cửa / có mở không;
weather = hỏi thời tiết, mưa, không khí; info = hỏi thông tin, giá vé một điểm; other = ngoài du lịch Hà Nội."""


def parse_request(text: str, now: datetime) -> tuple[dict, dict]:
    pois = load_pois()
    catalog = ", ".join(f"{p.id}={p.name}" for p in pois.values())
    user = (f"Bây giờ: {now:%Y-%m-%d %H:%M} ({VN_DAYS[now.weekday()]}), Hà Nội.\nDanh mục: {catalog}\n"
            f"Tin nhắn: {text}")
    req, meta = chat_json(INTENT_SYSTEM, user, max_tokens=400, temperature=0)
    req.setdefault("intent", "plan")
    req.setdefault("date", now.date().isoformat())
    d_code = resolve_date(text, now)          # code thắng model khi câu có mốc ngày rõ
    if d_code:
        req["date_llm"], req["date"] = req["date"], d_code.isoformat()
    req.setdefault("start", "08:00")
    req.setdefault("end", "21:00")
    for k in ("poi_ids", "must_include", "avoid"):
        req[k] = [p for p in (req.get(k) or []) if p in pois]
    # Khách nêu tên điểm khi xin lịch trình = muốn đi điểm đó (trừ điểm đang đứng khi đổi kế hoạch)
    if req["intent"] == "plan":
        req["must_include"] = list(dict.fromkeys(
            req["must_include"] + [p for p in req["poi_ids"] if p != req.get("replan_from") and p not in req["avoid"]]))
    # ép start >= bây giờ nếu là hôm nay (code quyết, không tin model)
    if req["date"] == now.date().isoformat():
        nxt = (now + timedelta(minutes=15 - now.minute % 15)).strftime("%H:%M")
        if req["start"] < nxt:
            req["start"] = nxt
    return req, meta


# ---------- 2. Dữ liệu song song ----------
def gather(req: dict) -> dict:
    pois = list(load_pois().values())
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=12) as ex:
        f_weather = ex.submit(get_weather, *HANOI_CENTER)
        f_air = ex.submit(get_air, *HANOI_CENTER)
        f_crowd = {p.id: ex.submit(get_crowd, p) for p in pois}
        weather, air = f_weather.result(), f_air.result()
        crowd = {k: f.result() for k, f in f_crowd.items()}
    return {"weather": weather, "air": air, "crowd": crowd, "gather_s": round(time.perf_counter() - t0, 2)}


# ---------- 3. Trả lời tra cứu bằng code ----------
def _ts(epoch: float | None) -> str:
    return datetime.fromtimestamp(epoch, now_hanoi().tzinfo).strftime("%H:%M %d/%m") if epoch else "?"


def answer_hours(req: dict, now: datetime) -> str:
    pois = load_pois()
    d = date.fromisoformat(req["date"])
    ids = req["poi_ids"] or list(pois)
    lines = [f"Giờ mở cửa {VN_DAYS[d.weekday()]} {d:%d/%m/%Y} (bảng dữ liệu kiểm ngày 21/11/2026):"]
    for pid in ids:
        p = pois[pid]
        status = ""
        if d == now.date():
            status = " – ĐANG MỞ" if p.is_open_at(now) else " – đang đóng"
        lines.append(f"• {p.name}: {p.hours_text(d)}{status} [độ tin cậy: {p.confidence}]")
        if pid in req["poi_ids"]:
            lines.append(f"  Vé: {p.ticket}. Ghi chú: {p.notes}")
            if p.sources:
                lines.append(f"  Nguồn: {p.sources[0]}")
    return "\n".join(lines)


def answer_weather(ctx: dict, req: dict) -> str:
    w, a = ctx["weather"], ctx["air"]
    if "current" not in w:
        return f"Không lấy được thời tiết ({w.get('_error')})."
    c = w["current"]
    lines = [
        f"Thời tiết khu Hoàn Kiếm lúc {w['model_time'][11:16]} – {w['source']}"
        + (" [DỮ LIỆU CŨ do API lỗi]" if w.get("_stale") else ""),
        f"• {c['desc']}, {c['temp']}°C (cảm giác {c['feels_like']}°C), độ ẩm {c['humidity']}%, gió {c['wind_kmh']} km/h, mưa {c['rain_mm']} mm",
    ]
    if a.get("aqi") is not None:
        note = "" if a.get("measured") else " (AQI tính theo trung bình 24h, PM2.5 là giá trị giờ hiện tại)"
        lines.append(f"• Không khí: AQI {a['aqi']} ({a['level']}), PM2.5 {a.get('pm25')} µg/m³ – {a['source']}, "
                     f"lúc {str(a.get('obs_time'))[11:16]}{note}")
    nxt = [h for h in hourly_for_date(w, req["date"]) if h["time"][11:16] >= req["start"]][:6]
    if nxt:
        lines.append("• Các giờ tới: " + "; ".join(f"{h['time'][11:13]}h {h['temp']}°C mưa {h['rain_prob']}%" for h in nxt))
    return "\n".join(lines)


# ---------- 4. Lập lịch ----------
# Chế độ BOT: LLM quyết định chọn điểm, thứ tự, thời lượng, chỗ ăn; CODE tính giờ (scheduler.py).
PLAN_SYSTEM = """Bạn là hướng dẫn viên lập lịch trình tham quan Hà Nội. Bạn QUYẾT ĐỊNH: đi những điểm nào, theo thứ tự nào,
ở mỗi điểm bao lâu, ăn uống/nghỉ ở đâu. Giờ cụ thể do hệ thống tự tính từ giờ mở cửa và thời gian di chuyển,
bạn KHÔNG viết giờ. Chỉ dùng poi_id trong danh mục.
Nguyên tắc:
- Chỉ chọn điểm MỞ trong khung giờ của khách (xem open_today). Điểm chỉ mở buổi sáng phải xếp đầu ngày.
- Mọi điểm trong must_include phải có nếu điểm đó mở; không chọn điểm trong avoid.
- Nhịp độ theo người đi: người lớn tuổi, trẻ nhỏ, người mệt -> ít điểm (2-4 điểm/ngày), có nghỉ; đi khỏe -> nhiều hơn.
  Tổng (thời lượng + di chuyển travel_min) phải VỪA khung giờ của khách.
- Đi điểm gần nhau liền nhau. Mưa nhiều hoặc AQI xấu -> ưu tiên trong nhà (indoor=true).
- Khung giờ có trưa (11:00-13:00) hoặc tối (18:00-20:00) thì chèn bữa ăn (kind "meal", 45-75 phút).
- Nếu replan_from có giá trị, khách đang đứng ở đó: điểm đầu tiên nên gần đó.
Trả JSON:
{"stops":[{"kind":"visit","poi_id":"...","minutes":90,"why":"lý do ngắn"},
          {"kind":"meal","minutes":60,"why":"ăn trưa khu phố cổ"}],
 "summary":"2-3 câu: vì sao lịch hợp với khách, nêu đánh đổi và điểm khách hỏi nhưng không đi được (nếu có)"}
Không bịa tên quán cụ thể, chỉ nói khu vực."""

# Chế độ BASELINE (để so sánh): LLM tự viết cả giờ, giống hỏi thẳng chatbot thông thường.
BASELINE_SYSTEM = """Bạn là hướng dẫn viên lập lịch trình tham quan Hà Nội. Nhiệm vụ: chọn điểm, sắp thứ tự, phân giờ
sao cho hợp sở thích khách VÀ đúng ràng buộc thật. Chỉ dùng điểm trong danh mục (đúng poi_id).
Ràng buộc cứng: chỉ xếp vào khung giờ điểm đó MỞ; để đủ thời gian di chuyển theo bảng travel_min;
không chồng giờ; nằm trong khung giờ khách; tôn trọng must_include/avoid.
Ưu tiên mềm: điểm trong nhà khi xác suất mưa cao hoặc AQI xấu; điểm gần nhau đi liền; chừa bữa trưa/tối (kind "meal").
Trả JSON:
{"items":[{"kind":"visit","poi_id":"...","start":"HH:MM","end":"HH:MM","why":"lý do ngắn"},
          {"kind":"meal","poi_id":null,"start":"HH:MM","end":"HH:MM","why":"ăn trưa khu ..."}],
 "summary":"2-3 câu giải thích vì sao lịch này hợp với khách, nêu đánh đổi nếu có"}
Không bịa tên quán ăn cụ thể, chỉ nói khu vực."""


def _plan_context(req: dict, ctx: dict) -> str:
    pois = load_pois()
    d = date.fromisoformat(req["date"])
    rows = []
    for p in pois.values():
        cr = ctx["crowd"].get(p.id, {})
        crowd = f"{cr.get('live_pct')}%" if cr.get("available") else "không có dữ liệu"
        rows.append({"poi_id": p.id, "name": p.name, "district": p.district, "indoor": p.indoor,
                     "open_today": p.hours_text(d), "visit_min": p.visit_minutes, "crowd_now": crowd})
    tm = matrix(list(pois.values()))
    hours = [{"h": h["time"][11:13], "rain_prob": h["rain_prob"], "temp": h["temp"]}
             for h in hourly_for_date(ctx["weather"], req["date"])]
    a = ctx["air"]
    return json.dumps({
        "date": req["date"], "weekday": VN_DAYS[d.weekday()],
        "window": [req["start"], req["end"]], "preferences": req.get("preferences", ""),
        "must_include": req["must_include"], "avoid": req["avoid"], "replan_from": req.get("replan_from"),
        "pois": rows, "travel_min": tm, "weather_hourly": hours,
        "aqi": {"value": a.get("aqi"), "level": a.get("level")},
    }, ensure_ascii=False)


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
    attempts = 0
    # Sửa khi có điểm bị bỏ mà vẫn còn chỗ trống đáng kể, hoặc lịch rỗng
    while attempts < 1 and (not sched["items"] or _needs_repair(sched, req)):
        attempts += 1
        fix_user = (context + "\n\nĐề xuất trước:\n" + json.dumps(proposal, ensure_ascii=False)
                    + "\n\nHệ thống xếp giờ báo:\n- " + "\n- ".join(sched["dropped"] or ["lịch rỗng"])
                    + "\nHãy đề xuất lại (đổi điểm/thứ tự/thời lượng) cho vừa khung giờ. Điểm đóng cửa thì bỏ và nói rõ trong summary.")
        proposal, meta = chat_json(PLAN_SYSTEM, fix_user, max_tokens=900)
        calls.append(meta)
        sched = schedule(proposal.get("stops", []), req)

    summary, meta = write_summary(sched, req)
    calls.append(meta)
    plan = {"items": sched["items"], "summary": summary}
    check = validate(plan, req, w_hours, ctx["air"], now)  # lưới an toàn cuối
    # Điểm khách hỏi mà không đi được -> đưa lên đầu phần lưu ý
    closed_asked = [x for x in sched["dropped"] if x.startswith("Không đi được")]
    rest = [x for x in check["warnings"] if x not in closed_asked]
    check["warnings"] = closed_asked + sorted(rest, key=lambda x: not x.startswith("Không xếp được"))
    return {"plan": plan, "check": check, "repairs": attempts, "llm_calls": calls, "date": d.isoformat(),
            "dropped": sched["dropped"]}


SUMMARY_SYSTEM = """Viết 2-3 câu tiếng Việt giải thích lịch trình CUỐI CÙNG cho khách: vì sao hợp với khách, đánh đổi gì.
Chỉ nhắc các điểm có trong "lich_trinh" hoặc "khong_di_duoc". Không thêm điểm khác, không bịa giờ hay giá.
Trả JSON {"summary": "..."}"""


def write_summary(sched: dict, req: dict) -> tuple[str, dict]:
    pois = load_pois()
    final = [{"luc": f"{i['start']}-{i['end']}", "muc": pois[i["poi_id"]].name if i["poi_id"] else i["why"]}
             for i in sched["items"]]
    payload = json.dumps({"so_thich_khach": req.get("preferences", ""), "lich_trinh": final,
                          "khong_di_duoc": sched["dropped"]}, ensure_ascii=False)
    try:
        j, meta = chat_json(SUMMARY_SYSTEM, payload, max_tokens=250)
        text = str(j.get("summary", ""))
    except LLMError as e:
        return "", {"error": str(e)}
    # Lưới an toàn: bỏ câu nhắc tới điểm không có trong lịch và không nằm trong danh sách "không đi được"
    allowed = {i["poi_id"] for i in sched["items"] if i["poi_id"]} | mentioned_ids(" ".join(sched["dropped"]))
    kept = [s for s in re.split(r"(?<=[.!?])\s+", text) if mentioned_ids(s) <= allowed]
    return " ".join(kept), meta


def _needs_repair(sched: dict, req: dict) -> bool:
    """Chỉ sửa khi điểm bị bỏ vì không vừa giờ (không phải vì đóng cửa cả ngày – cái đó chỉ cần báo)."""
    return any(x.startswith(("Không xếp được", "Bỏ '")) for x in sched["dropped"])


def render_plan(res: dict, ctx: dict) -> str:
    pois = load_pois()
    d = date.fromisoformat(res["date"])
    plan, check = res["plan"], res["check"]
    lines = [f"Lịch trình {VN_DAYS[d.weekday()]} {d:%d/%m/%Y}:"]
    for it in plan.get("items", []):
        if it.get("kind") == "visit" and it.get("poi_id") in pois:
            p = pois[it["poi_id"]]
            lines.append(f"• {it['start']}-{it['end']}  {p.name} (mở {p.hours_text(d)}; vé: {p.ticket})")
        else:
            lines.append(f"• {it.get('start')}-{it.get('end')}  {it.get('why', 'nghỉ/ăn uống')}")
    if plan.get("summary"):
        lines.append(f"\n{plan['summary']}")
    if not check["ok"]:
        lines.append("\n⚠ Bộ kiểm tra vẫn còn lỗi sau khi sửa – KHÔNG nên dùng nguyên lịch này:")
        lines += [f"  - {v}" for v in check["violations"]]
    if check["warnings"]:
        lines.append("\nLưu ý:")
        lines += [f"  - {x}" for x in check["warnings"]]
    w, a = ctx["weather"], ctx["air"]
    lines.append(f"\nDữ liệu: thời tiết {w.get('source', '?')} lúc {str(w.get('model_time', '?'))[11:16]}; "
                 f"AQI {a.get('aqi')} ({a.get('level')}) – {a.get('source', '?')}; "
                 f"mức đông: {'BestTime' if any(c.get('available') for c in ctx['crowd'].values()) else 'chưa có nguồn (thiếu key BestTime)'}; "
                 f"thời gian di chuyển: ước lượng theo khoảng cách.")
    return "\n".join(lines)


# ---------- Điểm vào ----------
def handle(text: str, now: datetime | None = None, use_validator: bool = True) -> dict:
    now = now or now_hanoi()
    t0 = time.perf_counter()
    blocked = guard_input(text)
    if blocked:
        return {"answer": blocked, "intent": "blocked", "latency_s": 0.0, "llm_calls": []}
    try:
        req, m_intent = parse_request(text, now)
    except LLMError as e:
        return {"answer": f"Xin lỗi, hệ thống AI đang lỗi ({e}). Bạn thử lại sau.", "intent": "error",
                "latency_s": round(time.perf_counter() - t0, 2), "llm_calls": []}
    out: dict = {"req": req, "intent": req["intent"], "llm_calls": [m_intent]}
    if req["intent"] == "other":
        out["answer"] = "Mình chỉ hỗ trợ du lịch Hà Nội: giờ mở cửa, thời tiết, thông tin điểm tham quan và lên lịch trình."
    elif req["intent"] in ("hours", "info"):
        out["answer"] = answer_hours(req, now)
    else:
        ctx = gather(req)
        out["gather_s"] = ctx["gather_s"]
        if req["intent"] == "weather":
            out["answer"] = answer_weather(ctx, req)
        else:
            res = make_plan(req, ctx, now, use_validator=use_validator)
            out.update(plan=res["plan"], check=res["check"], repairs=res["repairs"])
            out["llm_calls"] += res["llm_calls"]
            out["answer"] = render_plan(res, ctx)
    out["latency_s"] = round(time.perf_counter() - t0, 2)
    return out


def news_check(poi_name: str) -> dict:
    """Tìm thông báo đóng cửa/sự kiện mới trong 7 ngày (dùng khi cần kiểm đột xuất)."""
    return web_search(f"{poi_name} Hà Nội thông báo đóng cửa mở cửa", days=7)
