# HanoiTravelBot

Agent lập và sửa lịch trình tham quan Hà Nội, trả lời tiếng Việt và tiếng Anh.

- **LLM quyết định:** đi điểm nào, theo thứ tự nào, ở mỗi điểm bao lâu, nghỉ ăn ở đâu, và viết lời giải thích.
- **Code quyết định:** ngày tháng, giờ mở cửa, giờ giấc, thời gian di chuyển, chi phí, quãng đi bộ. Code cũng kiểm lại toàn bộ lịch trước khi trả lời.

## Chạy

```powershell
cd C:\VISUAL\HanoiTravelBot
.venv\Scripts\Activate.ps1
$env:PYTHONIOENCODING = "utf-8"
python -m hanoibot.server --port 8000   # ứng dụng web nhiều tab: http://127.0.0.1:8000/
python -m hanoibot.cli                  # chat trên terminal
python -m pytest -q                     # 43 test offline, không cần mạng
python -m evals.run_evals               # 16 kịch bản, gọi API thật, so với baseline chỉ dùng LLM
python -m evals.stability --k 3         # độ ổn định pass@k / pass^k
python -m evals.user_qa                 # 15 ca theo hành vi người dùng qua HTTP: nhiều lượt chat, ngày sai, ngân sách nhóm, hỏi tiếp thời tiết...
python -m scripts.check_hours           # đối chiếu giờ mở cửa với BestTime
```

Lần cài đặt đầu tiên: `py -3.12 -m venv .venv`, rồi `pip install -r requirements.txt`. Chép `.env.example` thành `.env` và điền key.

## Ứng dụng nhiều tab (đỡ việc cho chatbot)

| Tab | Làm gì | AI? |
|---|---|---|
| Trò chuyện | Hỏi tự nhiên; câu trả lời có nút mở tab liên quan với dữ liệu điền sẵn | Có, khi cần |
| Điểm tham quan | 9 điểm: giờ mở theo ngày, đang mở/chưa mở/hết giờ, vé, độ tin cậy, nguồn, biểu đồ mức đông theo giờ; lọc mở ngày này / trong nhà / miễn phí | Không |
| Thời tiết | 30 quận/huyện Hà Nội (gõ "Ha" là gợi ý Hai Bà Trưng, Hà Đông…) + ngày (tối đa 7 ngày), bảng theo giờ; dùng chung bộ nhớ thời tiết với chat | Không |
| Tự xếp lịch | Khách chọn điểm và thứ tự; code xếp giờ, tính chi phí, báo lỗi (~0,7 s). Nút "Nhờ AI chọn điểm" bỏ qua bước LLM tách ý định | Không (nút AI: có) |
| Giới thiệu | Tính năng, nguồn dữ liệu, giới hạn | Không |

- **Một nguồn mô tả ứng dụng:** `hanoibot/app_manifest.py`. Giao diện dựng tab từ `GET /api/app`; chatbot đọc cùng bảng để trả lời "ứng dụng làm được gì", "làm sao để tự xếp lịch" bằng code, không gọi LLM.
- **Câu hỏi về dữ liệu có sẵn** ("điểm nào miễn phí", "thứ Hai điểm nào mở", "chỗ nào trong nhà", "vé rẻ nhất") trả lời bằng code từ `data/pois.json` (`hanoibot/catalog.py`).
- **Thời tiết nhớ theo phiên:** đã lấy cho một nơi thì câu hỏi tiếp ("còn ngày mai?", "chiều ở đó có mưa không") dùng lại dữ liệu trong 1 giờ (AQI 30 phút), không gọi LLM, không gọi API; câu trả lời ghi rõ là dùng lại. Lập lịch sau khi hỏi thời tiết Hoàn Kiếm cũng dùng lại.
- API của tab: xem đầu file `hanoibot/api.py`.

## Luồng xử lý

```text
tin nhắn -> chặn injection (luật ViGuard có chuẩn hoá: bỏ dấu, tách chữ, chữ giả dạng...), đoán ngôn ngữ vi/en
  ├─ hỏi về ứng dụng / danh mục điểm -> app_manifest.py / catalog.py, không gọi LLM
  ├─ hỏi tiếp thời tiết cùng nơi -> dùng lại dữ liệu của phiên, không gọi LLM/API
  ├─ câu tra cứu rõ ràng (giờ mở cửa, giá vé, thời tiết) -> rule nhận diện, không gọi LLM (~0 s)
  ├─ câu quá mơ hồ ("đi chơi") -> hỏi lại ngày, số người, ngân sách, sở thích
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
| Giờ mở cửa, vé, lưu ý tham quan | `data/pois.json`: 9 điểm, kiểm lại 22/11/2026 theo trang chính thức (Codex tra, có link); mỗi điểm có `confidence`, `sources`, `tips` | Bảng tự soạn, cần kiểm định kỳ |
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
| QA theo hành vi người dùng qua HTTP (sau review Codex #7) | 12/12 (trước khi sửa: 7/12) |
| Thời gian lập một lịch trình | ~12 s, 3–5 lần gọi LLM |
| Thời gian trả lời câu tra cứu giờ mở cửa / thời tiết (khi đã có cache) | ~0 s |

## Giới hạn

- Ngữ cảnh nhiều lượt chat lưu ở `runs/cache/sessions.json` (không đưa lên git), khởi động lại máy chủ vẫn còn, hết hạn sau 2 giờ. Bot hiểu được câu chỉnh sửa (bỏ/thêm điểm, thêm người, đổi ngân sách), nhưng chưa hiểu câu tham chiếu phức tạp kiểu "cái thứ hai".
- Ngân sách: code bỏ dần mục tốn tiền nhất cho tới khi vừa. Nếu vẫn không đủ, bot nói rõ ngân sách không đủ.
- Thời tiết: chỉ cho khu vực thuộc Hà Nội (30 quận/huyện trong data/hanoi_areas.json, toạ độ trung tâm gần đúng; tên khác trong Hà Nội tìm qua Open-Meteo Geocoding), tối đa 7 ngày (tính cả hôm nay). AQI chỉ có cho hôm nay.

- Lăng Bác có giờ theo mùa (mùa nóng 01/04–31/10 mở 07:30) và độ tin cậy **trung bình**: lịch Ban Quản lý Lăng + bản tin VTV 03/11/2026; ngày lễ chưa được mô hình riêng. Chưa xác nhận từ đơn vị quản lý: giá tour đêm và audio guide Hỏa Lò, mốc tháng giờ mùa hè phố đi bộ, thông báo riêng mùa đông 2026 của Văn Miếu.
- Giá vé theo `ticket_rules` (tuổi, thẻ HSSV, chiều cao): chỉ miễn/giảm khi khách nói tuổi hoặc có thẻ; không đoán chiều cao, chưa biết tuổi thì tính như người lớn và nêu điều kiện.
- Bộ eval nhỏ, mỗi kịch bản chạy 1 lần. Chưa đo độ dao động giữa các lần chạy bằng pass^k.
- Chi phí taxi và ăn uống là ước tính theo công thức, không phải giá thật.

`.env` chứa key thật và đã nằm trong `.gitignore`.
