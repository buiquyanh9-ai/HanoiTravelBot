"""API cho các tab của ứng dụng. Phần lớn KHÔNG gọi LLM – đó là cách ứng dụng đỡ việc cho chatbot:

GET  /api/app        cấu trúc ứng dụng (app_manifest) – giao diện dựng tab, chatbot đọc cùng nguồn
GET  /api/places     danh mục điểm theo ngày: giờ mở, trạng thái, vé, độ tin cậy, mức đông (chỉ đọc cache)  – 0 LLM
GET  /api/place/<id> chi tiết một điểm: giờ cả tuần theo mùa, vé + miễn/giảm, lưu ý, nguồn, mức đông, điểm gần  – 0 LLM
GET  /api/content    ẩm thực / sự kiện / thông tin hữu ích có nguồn (data/content.json)                       – 0 LLM
GET  /api/areas      gợi ý quận/huyện Hà Nội khi gõ ("Ha" -> Hai Bà Trưng...)                                  – 0 LLM
GET  /api/weather    thời tiết quận/huyện Hà Nội + ngày (tối đa 7 ngày), có cấu trúc (chung bộ nhớ với chat)   – 0 LLM
POST /api/schedule   khách tự chọn điểm + thứ tự -> code xếp giờ, tính chi phí, kiểm lỗi                   – 0 LLM
POST /api/plan       "Nhờ AI chọn điểm": form có cấu trúc -> bỏ qua bước LLM tách ý định                     – 2-4 LLM
"""
from __future__ import annotations

from datetime import date, datetime

from . import areas, bot
from .app_manifest import manifest
from .i18n import DAYS, t
from .pois import load_pois
from .scheduler import schedule
from .tools._http import peek
from .tools.air import get_air
from .tools.crowd import pct_at
from .tools.weather import FORECAST_DAYS, daily_for_date, get_weather, hourly_for_date
from .validator import validate

MAX_STOPS = 10


class BadRequest(ValueError):
    """Lỗi đầu vào: trả 400 kèm thông điệp cho người dùng."""


def app_info(lang: str) -> dict:
    return manifest(_lang(lang))


def _lang(lang: str | None) -> str:
    return "en" if lang == "en" else "vi"


def _date(s: str | None, now: datetime) -> date:
    if not s:
        return now.date()
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        raise BadRequest("Ngày không hợp lệ (cần YYYY-MM-DD).") from None


def _crowd_cached(pid: str, d: date) -> dict:
    """Mức đông theo giờ từ cache BestTime – không gọi API (tab mở ra là hiện ngay, không tốn credit)."""
    fc = peek(f"bt_forecast:{pid}") or {}
    if not fc.get("available"):
        return {}
    return {"available": True, "hourly": fc.get("by_day", {}).get(str(d.weekday()), {})}


def places(params: dict, now: datetime) -> dict:
    lang = _lang(params.get("lang"))
    d = _date(params.get("date"), now)
    out = []
    for p in load_pois().values():
        cr = _crowd_cached(p.id, d)
        out.append({
            "id": p.id, "name": p.display(lang), "district": p.district, "indoor": p.indoor,
            "hours": p.hours_text(d, lang), "open_day": bool(p.intervals_on(d)),
            "status": bot._open_status(p, now, lang).strip(" –") if d == now.date() else "",
            "open_now": d == now.date() and p.is_open_at(now),
            "ticket": p.ticket_text(lang), "ticket_vnd": p.ticket_vnd, "free": p.ticket_vnd == 0,
            "confidence": p.confidence, "notes": p.notes if lang == "vi" else "", "sources": p.sources[:3],
            "visit_minutes": p.visit_minutes, "walk_km_onsite": p.walk_km_onsite,
            "crowd": {h: v for h, v in (cr.get("hourly") or {}).items() if 7 <= int(h) <= 21} if cr else {},
            "address": p.address, "lat": p.lat, "lon": p.lon, "name_vi": p.name,
        })
    return {"date": d.isoformat(), "day": DAYS[lang][d.weekday()], "places": out}


def _rules_public(p, lang: str) -> list[dict]:
    out = []
    for r in p.ticket_rules:
        cond = ("age_max" if "max_age" in r else "card" if "card" in r else "height_max_m" if "max_height_m" in r else "")
        out.append({"label": r["label"] if lang == "vi" else r.get("label_en", r["label"]), "price_vnd": r["price"],
                    "condition": cond, "value": r.get("max_age", r.get("card", r.get("max_height_m"))),
                    "proof": r.get("proof") if lang == "vi" else None})
    return out


CONTENT_TYPES = ("food", "events", "info")


def content(params: dict, now: datetime) -> dict:
    """Ẩm thực / sự kiện / thông tin hữu ích từ data/content.json (mục nào cũng có nguồn). Sự kiện đã kết thúc thì ẩn."""
    import json as _json
    from .config import ROOT
    kind = params.get("type") or "food"
    if kind not in CONTENT_TYPES:
        raise BadRequest(f"type phải là một trong {', '.join(CONTENT_TYPES)}.")
    raw = _json.loads((ROOT / "data" / "content.json").read_text(encoding="utf-8"))
    items = [x for x in raw.get(kind, []) if x.get("sources")]  # không nguồn thì không hiện
    if kind == "events":
        today = now.date().isoformat()
        items = sorted((x for x in items if str(x.get("end") or x.get("start") or "") >= today), key=lambda x: x.get("start", ""))
    return {"type": kind, "items": items, "count": len(items)}


def place_detail(pid: str, params: dict, now: datetime) -> dict:
    """Trang chi tiết một điểm (kiểu trang địa điểm của cổng du lịch): mọi dữ liệu đã kiểm chứng + nguồn,
    tính bằng code từ data/pois.json – giao diện không cần chép dữ liệu ra file riêng. 0 LLM."""
    from .tools.travel import leg
    pois = load_pois()
    if pid not in pois:
        raise BadRequest("Không có địa điểm này trong danh mục.")
    lang = _lang(params.get("lang"))
    d = _date(params.get("date"), now)
    p = pois[pid]
    week = []
    for i in range(7):
        day = date.fromordinal(d.toordinal() + i)
        week.append({"date": day.isoformat(), "day": DAYS[lang][day.weekday()], "hours": p.hours_text(day, lang),
                     "open": bool(p.intervals_on(day))})
    cr = _crowd_cached(pid, d)
    hourly = {int(h): v for h, v in (cr.get("hourly") or {}).items() if v} if cr else {}
    open_h = {h for a, b in p.intervals_on(d) for h in range(a.hour, b.hour)}
    hourly = {h: v for h, v in hourly.items() if h in open_h}
    nearby = []
    for q in pois.values():
        if q.id == pid:
            continue
        lg = leg(p, q)
        nearby.append({"id": q.id, "name": q.display(lang), "km": lg.km, "mode": lg.mode, "minutes": lg.minutes,
                       "free": q.free, "hours": q.hours_text(d, lang)})
    nearby.sort(key=lambda x: x["km"])
    return {
        "id": p.id, "name": p.display(lang), "name_vi": p.name, "district": p.district, "address": p.address,
        "lat": p.lat, "lon": p.lon, "indoor": p.indoor, "visit_minutes": p.visit_minutes,
        "walk_km_onsite": p.walk_km_onsite, "date": d.isoformat(), "day": DAYS[lang][d.weekday()],
        "hours": p.hours_text(d, lang), "open_day": bool(p.intervals_on(d)),
        "status": bot._open_status(p, now, lang).strip(" –") if d == now.date() else "",
        "open_now": d == now.date() and p.is_open_at(now), "week": week,
        "seasonal_hours": [{"from": s["from"], "to": s["to"], "label": s.get("label", "")} for s in p.seasonal_hours],
        "free": p.free, "ticket": p.ticket_text(lang), "ticket_vnd": p.ticket_vnd, "ticket_rules": _rules_public(p, lang),
        "tips": bot.visit_tips(p, d, lang), "confidence": p.confidence, "notes": p.notes if lang == "vi" else "",
        "sources": p.sources, "crowd": {str(h): v for h, v in sorted(hourly.items())},
        "quietest_hour": min(hourly, key=hourly.get) if hourly else None,
        "nearby": nearby[:4],
        "ask_bot": (f"Cho tôi biết về {p.name}, giờ mở cửa và lưu ý khi tham quan." if lang == "vi"
                    else f"Tell me about {p.display('en')}: opening hours and visiting tips."),
    }


def area_suggest(params: dict) -> dict:
    """Gợi ý quận/huyện khi gõ (không dấu vẫn khớp): "Ha" -> Hai Bà Trưng, Hà Đông..."""
    limit = 40 if params.get("all") else 8
    return {"areas": [a.public() for a in areas.search(str(params.get("q") or "")[:40], limit)],
            "max_days": FORECAST_DAYS}


def weather(params: dict, now: datetime, session: dict | None) -> dict:
    """Thời tiết theo quận/huyện Hà Nội + ngày (tối đa FORECAST_DAYS ngày). Trả cả câu chữ (cho chat/CLI)
    lẫn dữ liệu có cấu trúc để giao diện tự trình bày gọn."""
    lang = _lang(params.get("lang"))
    d = _date(params.get("date"), now)
    if d < now.date():
        raise BadRequest(t("past_date", lang, d=d.strftime("%d/%m/%Y")))
    place = str(params.get("place") or "").strip()[:80]
    area = areas.resolve(place) if place else None
    if area:
        place = area.name  # "hai ba" -> "Hai Bà Trưng": tìm đúng khu vực ở Hà Nội
    is_today = d == now.date()
    req = {"intent": "weather", "lang": lang, "date": d.isoformat(), "place": place or None,
           "start": bot._next_slot(now) if is_today else "06:00", "end": "23:59"}
    session = session if session is not None else {}
    w = bot.weather_data(req, now, session)
    if "error" in w:
        return {"ok": False, "text": w["error"], "date": d.isoformat(), "max_days": FORECAST_DAYS}
    wx, air, loc = w["weather"], w["air"] or {}, w["loc"] or {}
    if "hourly" not in wx:
        return {"ok": False, "text": bot.answer_weather({"weather": wx, "air": air}, req, now, loc), "date": d.isoformat()}
    hours = hourly_for_date(wx, d.isoformat())
    if is_today:
        hours = [h for h in hours if h["time"][11:16] >= req["start"][:2] + ":00"]
    rows = [{"h": h["time"][11:13], "temp": round(h["temp"]) if h["temp"] is not None else None,
             "rain_prob": h["rain_prob"], "desc": h["desc"]} for h in hours]
    out = {"ok": True, "date": d.isoformat(), "day": DAYS[lang][d.weekday()], "is_today": is_today,
           "place": area.public() if area else {"name": loc.get("name") or place or "Hoàn Kiếm", "label": loc.get("name")},
           "source": "Open-Meteo (ước tính mô hình)" if lang == "vi" else "Open-Meteo (model estimate)",
           "model_time": str(wx.get("model_time", ""))[11:16], "stale": bool(wx.get("_stale")),
           "reused": w["reused"], "hourly": rows, "max_days": FORECAST_DAYS,
           "text": bot.answer_weather({"weather": wx, "air": air}, req, now, loc)}
    if is_today:
        c = wx["current"]
        out["now"] = {"temp": c["temp"], "feels_like": c["feels_like"], "humidity": c["humidity"],
                      "wind_kmh": c["wind_kmh"], "rain_mm": c["rain_mm"], "desc": c["desc"]}
        if air.get("aqi") is not None:
            out["air"] = {"aqi": air["aqi"], "level": air.get("level"), "pm25": air.get("pm25"),
                          "measured": bool(air.get("measured")), "time": str(air.get("obs_time") or "")[11:16]}
    else:
        day = daily_for_date(wx, d.isoformat())
        if day:
            out["day_summary"] = day
    return out


def build_req(body: dict, now: datetime) -> dict:
    """Form có cấu trúc -> req giống bot (cùng bước chuẩn hoá _defaults), KHÔNG cần LLM đọc câu chữ."""
    if not isinstance(body, dict):
        raise BadRequest("Dữ liệu không hợp lệ.")
    lang = _lang(body.get("lang"))
    pois = load_pois()
    req = {"intent": "plan", "date": str(body.get("date") or now.date().isoformat()),
           "start": body.get("start"), "end": body.get("end"), "people": body.get("people"),
           "children": body.get("children"), "child_ages": body.get("child_ages"),
           "students": body.get("students"), "student_level": body.get("student_level"),
           "budget_vnd": body.get("budget_vnd"),
           "max_walk_km": body.get("max_walk_km"), "preferences": body.get("preferences"),
           "avoid": body.get("avoid") or []}
    req = bot._defaults(req, "", now, lang)
    req["must_include"] = [p for p in (body.get("must_include") or []) if isinstance(p, str) and p in pois][:6]
    req["avoid"] = [p for p in req["avoid"] if p not in req["must_include"]]
    req["_text"] = req["preferences"]
    if req.get("_error"):
        key, kw = req["_error"]
        raise BadRequest(t(key, lang, **kw))
    return req


def _stops(body: dict) -> list[dict]:
    pois = load_pois()
    raw = body.get("stops")
    if not isinstance(raw, list) or not raw:
        raise BadRequest("Chưa có điểm nào trong lịch.")
    if len(raw) > MAX_STOPS:
        raise BadRequest(f"Tối đa {MAX_STOPS} mục.")
    out = []
    for s in raw:
        if not isinstance(s, dict):
            raise BadRequest("Mục lịch không hợp lệ.")
        try:
            minutes = int(s.get("minutes") or 60)
        except (TypeError, ValueError):
            raise BadRequest("Số phút không hợp lệ.") from None
        if s.get("kind") == "meal":
            out.append({"kind": "meal", "minutes": max(15, min(120, minutes)), "why": ""})
        elif s.get("poi_id") in pois:
            out.append({"kind": "visit", "poi_id": s["poi_id"], "minutes": max(15, min(300, minutes)), "why": ""})
        else:
            raise BadRequest(f"Không có điểm '{s.get('poi_id')}' trong danh mục.")
    return out


def _remember(session: dict | None, req: dict, items: list[dict]) -> None:
    """Lịch tạo bằng form -> ngữ cảnh chat: nhắn tiếp "bỏ Hỏa Lò", "thêm 2 người" sẽ sửa đúng lịch này."""
    if session is None:
        return
    visits = [i["poi_id"] for i in items if i.get("poi_id")]
    keep = {k: v for k, v in req.items() if not k.startswith("_")}
    keep.update(intent="plan", must_include=visits, poi_ids=visits, from_form=True)
    session["last_req"] = keep


def manual_schedule(body: dict, now: datetime, session: dict | None = None) -> dict:
    """Khách tự chọn: code xếp giờ (chờ mở cửa, thời gian đi), tính chi phí, kiểm ràng buộc. Không gọi LLM."""
    req = build_req(body, now)
    req["must_include"] = []  # khách tự xếp: điểm bị bỏ đã có lý do trong 'dropped'
    stops = _stops(body)
    sched = schedule(stops, req)
    w = get_weather(*bot.HANOI_CENTER)  # cache 20 phút; chỉ dùng cho cảnh báo mưa ở điểm ngoài trời
    air = get_air(*bot.HANOI_CENTER)  # cache 30 phút
    check = validate({"items": sched["items"]}, req, hourly_for_date(w, req["date"]), air, now)
    crowd = {p: _crowd_cached(p, date.fromisoformat(req["date"])) for p in load_pois()}
    pois = load_pois()
    for it in sched["items"]:
        if it.get("poi_id"):
            pct = pct_at(crowd.get(it["poi_id"], {}), int(it["start"][:2]))
            if pct and pct >= 85:
                check["warnings"].append(
                    f"{pois[it['poi_id']].name} lúc {it['start'][:2]}h thường rất đông (~{pct}% so với giờ đông nhất tuần).")
    check["warnings"] = sched["dropped"] + check["warnings"]
    res = {"plan": {"items": sched["items"], "summary": ""}, "check": check, "date": req["date"],
           "dropped": sched["dropped"], "manual": True}
    ctx = {"crowd": crowd, "weather": w, "air": air, "news": {}, "traffic": {"available": False}}
    _remember(session, req, sched["items"])
    return {"text": bot.render_plan(res, ctx, req), "items": sched["items"], "totals": check["totals"],
            "ok": check["ok"], "violations": check["violations"], "warnings": check["warnings"], "llm_calls": 0}


def ai_plan(body: dict, now: datetime, session: dict | None) -> dict:
    """Nhờ AI chọn điểm theo form. Bỏ qua bước LLM tách ý định (form đã có cấu trúc)."""
    req = build_req(body, now)
    ctx = bot.gather(req, now, bot._fresh_wx(session, bot.HK_KEY))
    res = bot.make_plan(req, ctx, now)
    stops = bot._actions(req, res)
    _remember(session, req, res["plan"]["items"])
    return {"text": bot.render_plan(res, ctx, req), "items": res["plan"]["items"], "ok": res["check"]["ok"],
            "stops": stops[0]["plan"]["stops"] if stops else [], "llm_calls": len(res["llm_calls"])}
