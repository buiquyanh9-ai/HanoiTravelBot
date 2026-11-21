# HanoiTravelBot

Agent lập và sửa lịch trình tham quan Hà Nội, trả lời tiếng Việt và tiếng Anh.

- **LLM quyết định:** đi điểm nào, theo thứ tự nào, ở mỗi điểm bao lâu, nghỉ ăn ở đâu, và viết lời giải thích.
- **Code quyết định:** ngày tháng, giờ mở cửa, giờ giấc, thời gian di chuyển, chi phí, quãng đi bộ. Code cũng kiểm lại toàn bộ lịch trước khi trả lời.

## Chạy

```powershell
cd C:\VISUAL\HanoiTravelBot
.venv\Scripts\Activate.ps1
$env:PYTHONIOENCODING = "utf-8"
python -m hanoibot.server --port 8000   # giao diện web: http://127.0.0.1:8000/
python -m hanoibot.cli                  # chat trên terminal
python -m pytest -q                     # 24 test offline, không cần mạng
python -m evals.run_evals               # 16 kịch bản, gọi API thật, so với baseline chỉ dùng LLM
python -m evals.stability --k 3         # độ ổn định pass@k / pass^k
python -m scripts.check_hours           # đối chiếu giờ mở cửa với BestTime
```

Lần cài đặt đầu tiên: `py -3.12 -m venv .venv`, rồi `pip install -r requirements.txt`. Chép `.env.example` thành `.env` và điền key.

## Luồng xử lý

```text
tin nhắn -> chặn injection (luật ViGuard có chuẩn hoá: bỏ dấu, tách chữ, chữ giả dạng...), đoán ngôn ngữ vi/en
  ├─ câu tra cứu rõ ràng (giờ mở cửa, thời tiết) -> rule nhận diện, không gọi LLM (~0 s)
  └─ còn lại -> LLM tách ý định thành JSON; code tính ngày (dates.py) và chỉ ép "phải đi" với điểm khách gọi đúng tên
       ├─ hours/info -> bảng giờ data/pois.json + tin mới (Tavily) liên quan đúng địa điểm
       ├─ weather    -> Open-Meteo + AQI WAQI
       └─ plan -> lấy song song: thời tiết, AQI, ma trận TomTom, BestTime cho từng điểm, tin mới
                 -> LLM chọn điểm, thứ tự, thời lượng
                 -> scheduler.py tính giờ (chờ điểm mở cửa, cộng thời gian đi, bỏ điểm đóng cửa kèm lý do)
                 -> bị bỏ điểm, vượt ngân sách, đi bộ quá giới hạn, hoặc khách ngại đông mà điểm rất đông
                    -> LLM đề xuất lại (tối đa 2 lần)
                 -> LLM viết tóm tắt từ lịch cuối; code bỏ câu nhắc tới điểm không có trong lịch
                 -> validator.py kiểm lần cuối và tính tổng chi phí, quãng đi bộ
```

## Nguồn dữ liệu

| Dữ liệu | Nguồn | Loại |
|---|---|---|
| Giờ mở cửa, vé | `data/pois.json`: 9 điểm, gom từ web ngày 21/11/2026; mỗi điểm có `confidence` và `sources` | Bảng tự soạn, cần kiểm định kỳ |
| Thời tiết | Open-Meteo | Ước tính từ mô hình |
| AQI | WAQI, không có key thì dùng Open-Meteo | Đo thật tại trạm |
| Mức đông | BestTime: dự báo theo giờ, cộng mức đông hiện tại nếu điểm đang mở | Dự báo; không có dữ liệu thì bot báo không có, không đoán |
| Thời gian đi ô tô | TomTom Matrix Routing v2: 1 lần gọi cho mọi cặp điểm; hôm nay lấy giao thông hiện tại, ngày khác lấy dự báo | Thật hoặc dự báo |
| Đi bộ, chi phí taxi, ăn uống | Công thức trong `tools/travel.py` và `costs.py` | Ước tính, có ghi rõ trong câu trả lời |
| Tin đóng cửa, sự kiện | Tavily, 14 ngày gần nhất, chỉ giữ tin có tên đúng địa điểm; tin có dấu hiệu injection bị bỏ | Người dùng tự kiểm |
| LLM | OpenRouter `gpt-4o-mini`; lỗi thì chuyển sang OpenRouter key 2, rồi Gemini | |

## Kết quả eval gần nhất (22/11/2026, 16 kịch bản, API thật)

| Chỉ số | Kết quả |
|---|---|
| Hiểu đúng loại câu hỏi | 16/16 |
| Câu trả lời có đủ thông tin kỳ vọng (ngày, đóng cửa, tiếng Anh, ngân sách ≤ 350k, đi bộ ≤ 2 km) | 16/16 |
| Lịch trình đúng ràng buộc: **bot** | **10/10** |
| Lịch trình đúng ràng buộc: LLM tự viết giờ (baseline) | 1–2/10, tổng 21–23 vi phạm |
| Độ ổn định: pass^3 (mỗi kịch bản chạy 3 lần, đạt cả 3) | 16/16 |
| Thời gian lập một lịch trình | ~12 s, 3–5 lần gọi LLM |
| Thời gian trả lời câu tra cứu giờ mở cửa / thời tiết (khi đã có cache) | ~0 s |

## Giới hạn

- Giờ mở cửa của Lăng Bác và Văn Miếu có độ tin cậy **thấp**, vì các nguồn mâu thuẫn nhau. Bot có cảnh báo; cần gọi điện xác nhận.
- Bộ eval nhỏ, mỗi kịch bản chạy 1 lần. Chưa đo độ dao động giữa các lần chạy bằng pass^k.
- Chi phí taxi và ăn uống là ước tính theo công thức, không phải giá thật.

`.env` chứa key thật và đã nằm trong `.gitignore`.
