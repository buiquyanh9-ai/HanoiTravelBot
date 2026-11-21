"""Test offline cho: song ngữ, chi phí/đi bộ, định tuyến nhanh, máy chủ web."""
import json
import threading
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer

from hanoibot.bot import fast_route
from hanoibot.config import HANOI_TZ
from hanoibot.costs import plan_totals
from hanoibot.i18n import detect_lang, en_text
from hanoibot.validator import validate

NOW = datetime(2026, 11, 22, 9, 0, tzinfo=HANOI_TZ)  # Chủ nhật


def _req(**kw):
    base = {"date": "2026-11-22", "start": "08:00", "end": "21:00", "avoid": [], "must_include": [], "people": 1}
    return {**base, **kw}


def test_detect_lang():
    assert detect_lang("Lăng Bác mấy giờ mở?") == "vi"
    assert detect_lang("lang bac may gio mo cua") == "vi"
    assert detect_lang("What time does the Temple of Literature open today?") == "en"
    assert detect_lang("Plan my day in Hanoi please") == "en"


def test_en_text_translates_code_warnings():
    s = en_text("Giờ mở cửa Lăng Chủ tịch Hồ Chí Minh có độ tin cậy THẤP, nên gọi xác nhận trước.")
    assert s.startswith("Opening hours of Ho Chi Minh Mausoleum")
    s = en_text("Không xếp được Lăng Chủ tịch Hồ Chí Minh: nghỉ cố định ngày này trong tuần vào 23/11.")
    assert "regular weekly closing day" in s and "Ho Chi Minh Mausoleum" in s


PLAN = [
    {"kind": "visit", "poi_id": "van_mieu", "start": "08:00", "end": "09:15"},
    {"kind": "visit", "poi_id": "hoa_lo", "start": "09:40", "end": "10:55"},
    {"kind": "meal", "poi_id": None, "start": "11:00", "end": "12:00", "why": "Ăn trưa – phố cổ"},
    {"kind": "visit", "poi_id": "ho_guom", "start": "12:10", "end": "13:10"},
]


def test_plan_totals_counts_tickets_taxi_food_walk():
    t1 = plan_totals(PLAN, _req())
    assert t1["tickets_vnd"] == 30000 + 50000
    assert t1["food_vnd"] == 60000
    assert t1["transport_vnd"] > 0 and t1["walk_km"] > 1.0
    t2 = plan_totals(PLAN, _req(people=2))
    assert t2["tickets_vnd"] == 2 * t1["tickets_vnd"] and t2["food_vnd"] == 120000
    assert t2["transport_vnd"] == t1["transport_vnd"]  # cùng 1 xe


def test_validator_budget_and_walk_limits():
    r = validate({"items": PLAN}, _req(budget_vnd=100000, max_walk_km=1.0))
    text = " ".join(r["violations"])
    assert "vượt ngân sách" in text and "Tổng đi bộ" in text
    r2 = validate({"items": PLAN}, _req(budget_vnd=1000000, max_walk_km=10))
    assert r2["ok"], r2["violations"]


def test_fast_route_skips_llm_for_clear_lookups():
    r = fast_route("Lăng Bác hôm nay mở mấy giờ?", NOW, "vi")
    assert r and r["intent"] == "hours" and r["poi_ids"] == ["lang_bac"]
    r = fast_route("Bây giờ ở Hồ Gươm có mưa không?", NOW, "vi")
    assert r and r["intent"] == "weather"
    assert fast_route("Xếp lịch trình đi Văn Miếu giúp mình", NOW, "vi") is None
    assert fast_route("Chủ nhật đi đâu chơi?", NOW, "vi") is None


def test_web_server_health_and_chat(monkeypatch):
    import hanoibot.server as server
    monkeypatch.setattr(server, "handle", lambda msg: {"answer": f"echo:{msg}", "intent": "hours", "lang": "vi",
                                                       "latency_s": 0.01, "llm_calls": []})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        assert json.load(urllib.request.urlopen(base + "/health"))["ok"] is True
        html = urllib.request.urlopen(base + "/").read().decode("utf-8")
        assert "Trợ lý du lịch Hà Nội" in html
        req = urllib.request.Request(base + "/api/chat", data=json.dumps({"message": "xin chào"}).encode(),
                                     headers={"Content-Type": "application/json"})
        out = json.load(urllib.request.urlopen(req))
        assert out["answer"] == "echo:xin chào"
    finally:
        srv.shutdown()


def test_plan_words_cover_english_suggestions():
    import re
    from hanoibot.bot import PLAN_WORDS
    from hanoibot.textutil import fold
    assert re.search(PLAN_WORDS, fold("Is it going to rain? Suggest indoor places from 14:00 to 18:00 today."))
    assert re.search(PLAN_WORDS, fold("Chiều nay đi tham quan đâu?"))
    assert not re.search(PLAN_WORDS, fold("Lăng Bác mấy giờ mở cửa?"))


def test_english_dates():
    from datetime import date
    from hanoibot.dates import resolve_date
    assert resolve_date("What time does it open on Monday?", NOW) == date(2026, 11, 23)
    assert resolve_date("plan my day tomorrow", NOW) == date(2026, 11, 23)
    assert resolve_date("this afternoon", NOW) == date(2026, 11, 22)


def test_crowd_warning_translated():
    s = en_text("Di tích Nhà tù Hỏa Lò lúc 14h thường rất đông (~95% so với giờ đông nhất tuần).")
    assert s == "Hoa Lo Prison Relic at 14:00 is usually very busy (~95% of the weekly peak)."


def test_redact_hides_keys_in_error_text():
    from hanoibot.tools._http import redact
    fake_bt = "pri" + "_" + "0123456789abcdef" * 2  # key giả, ghép lúc chạy để repo không chứa chuỗi giống key
    fake_or = "sk-or" + "-v1-" + "0123456789abcdef" * 2
    s = redact(f"Client error for url 'https://x/api?api_key_private={fake_bt}&venue_name=A'")
    assert fake_bt not in s and "api_key_private=***" in s
    assert fake_or not in redact(f"bad {fake_or}")
