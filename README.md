# HanoiTravelBot – MVP

Agent lập và sửa lịch trình tham quan Hà Nội. **LLM quyết định** đi đâu, thứ tự, ở bao lâu, nghỉ ở đâu.
**Code quyết định** ngày tháng, giờ mở cửa, giờ giấc, thời gian di chuyển và kiểm lại toàn bộ lịch.

## Chạy

```powershell
cd C:\VISUAL\HanoiTravelBot
.venv\Scripts\Activate.ps1
$env:PYTHONIOENCODING = "utf-8"
python -m hanoibot.cli              # chat trên terminal
python -m pytest -q                 # test offline (không cần mạng)
python -m evals.run_evals           # 10 kịch bản, gọi API thật, so với baseline
```

## Luồng xử lý

```text
tin nhắn -> chặn injection (Day 11) -> LLM tách ý định thành JSON -> code tính ngày (dates.py)
  ├─ hỏi giờ mở cửa -> trả lời bằng code từ data/pois.json
  ├─ hỏi thời tiết  -> Open-Meteo thời tiết + AQI, lấy song song, cache 20-30 phút
  └─ lập lịch -> LLM chọn điểm, thứ tự, thời lượng -> scheduler.py tính giờ
                -> có điểm bị bỏ thì LLM đề xuất lại (tối đa 1 lần)
                -> LLM viết tóm tắt từ lịch cuối cùng -> validator.py kiểm lần cuối
```

## Nguồn dữ liệu

| Dữ liệu | Nguồn | Trạng thái |
|---|---|---|
| Giờ mở cửa, vé | `data/pois.json`, gom từ web ngày 21/11/2026, mỗi điểm có `confidence` và `sources` | 9 điểm; chỉ Bảo tàng Lịch sử ở mức `high` |
| Thời tiết | Open-Meteo, không cần key | Ước tính từ mô hình |
| AQI | Có `WAQI_TOKEN` thì dùng WAQI (số đo từ trạm); không có thì dùng Open-Meteo (ước tính) | Đang dùng Open-Meteo |
| Mức đông | Cần `BESTTIME_API_KEY_PRIVATE` | Chưa có key; bot báo "không có dữ liệu", không đoán |
| Di chuyển | Khoảng cách × 1,4 ở tốc độ 18 km/h, cộng 5 phút | Ước lượng; `TOMTOM_API_KEY` để dùng giao thông thật |
| Tin đóng cửa đột xuất | Tavily (`bot.news_check`) | Có tool nhưng chưa nối vào luồng lập lịch |
| LLM | OpenRouter `openai/gpt-4o-mini` | Key lấy từ repo Day 03 |

`.env` chứa key thật và đã nằm trong `.gitignore`. Không commit file này.
