# Giao diện Hanoi, slowly

Trang đầu tràn viền, dùng assets/hanoi-lake.mp4 ở tốc độ gốc, chỉ chạy xuôi, không hòa mờ hoặc đảo chiều. Clip khoảng 4 giây và có thay đổi góc quay nên điểm lặp vẫn có thể thấy; cần footage có đầu/cuối khớp để lặp hoàn toàn liền mạch. Nguồn Pexels được ghi trong assets/credits.json. Có nút tạm dừng/phát; khi bật giảm chuyển động hoặc tiết kiệm dữ liệu, giao diện giữ ảnh tĩnh.

Giao diện độc lập, không thay đổi file backend hay giao diện cũ. Không cần npm hoặc thư viện Python bổ sung.

## Chạy nhanh

Từ thư mục gốc dự án:

```powershell
powershell -ExecutionPolicy Bypass -File frontend-hanoi/start.ps1
```

Hoặc chạy trong terminal để xem log và dừng bằng Ctrl+C:

```powershell
.venv/Scripts/python.exe frontend-hanoi/serve.py --start-backend
```

Giao diện http://127.0.0.1:8081, backend preview riêng 8090. Nếu đã mở trang, dùng Ctrl+F5 để nạp bản mới.

Chạy backend hiện có ở terminal thứ nhất, tại thư mục gốc:

```powershell
python -m hanoibot.server --port 8000
```

Chạy giao diện ở terminal thứ hai, cũng từ thư mục gốc:

```powershell
python frontend-hanoi/serve.py
```

Mở http://127.0.0.1:8081 . Không mở index.html trực tiếp vì các chức năng cần máy chủ chuyển tiếp API.

## Kết nối

- Khám phá, lọc và thêm địa điểm: GET /api/places.
- Thời tiết: GET /api/weather.
- Tự xếp lịch theo thứ tự đã chọn: POST /api/schedule.
- AI chọn lịch theo form: POST /api/plan.
- Hỏi đáp và chỉnh lịch trong hội thoại: POST /api/chat.
- Chi tiết, lịch mở 7 ngày, ưu đãi vé, lưu ý, mức đông và điểm gần: GET /api/place/<id>.
- Ẩm thực, sự kiện và thông tin: GET /api/content?type=food|events|info.

serve.py phục vụ giao diện và chuyển tiếp /api/* đến backend, không thay đổi code backend. Mặc định dùng cổng 8000; --start-backend dùng 8090. Đổi cổng bằng --port, địa chỉ backend bằng --backend. Backend mới đồng bộ lịch tạo qua form vào session để chatbot sửa tiếp; bản lưu trên thiết bị cần tạo lại trong phiên hiện tại trước khi chỉnh qua chat.

## Nội dung và lưu trữ

portal.js/portal.css bổ sung giao diện khám phá, tìm không dấu, lọc, yêu thích, chi tiết, bản đồ, ẩm thực, cẩm nang, sự kiện, biểu mẫu gia đình và timeline. app.js/style.css giữ phần chat/thời tiết hiện có.

below-fold.js/below-fold.css trình bày lại phần dưới trang: thẻ khám phá theo chủ đề, danh mục gọn, khối lập lịch với ảnh nền, cuộn ngang ẩm thực và thẻ sự kiện nổi bật. Phần hero được giữ nguyên. Các nút theo chủ đề dùng bộ lọc thực tế, thanh ẩm thực cuộn được bằng nút và thao tác chạm.

Giờ, giá vé và lưu ý lấy trực tiếp từ backend. catalog.json chỉ lưu cấu hình ảnh/link, không sao chép bảng giá hay giờ. Nội dung ẩm thực/sự kiện đọc data/content.json qua API, được Claude phụ trách cập nhật. Có link nguồn HanoiGO/đơn vị quản lý; các giá chưa xác nhận được ghi rõ. Đây là nội dung tham khảo đã chọn lọc, không có kết nối đồng bộ toàn bộ dữ liệu HanoiGO.

Yêu thích, điểm đã chọn và lịch lưu chỉ nằm trong localStorage của trình duyệt; session hội thoại dùng sessionStorage. Không có đăng nhập/tài khoản. Có xuất văn bản và in/lưu PDF. Lưu trú, tour và các sự kiện khác mở trang nguồn HanoiGO.

Ảnh/video lưu tại assets. Nguồn gốc tại assets/credits.json, assets/hanoigo-credits.json và cấu hình ảnh; ảnh tham khảo HanoiGO giữ quyền của tác giả nguồn, không mặc định giấy phép mở. Ảnh Bảo tàng Lịch sử: Alistair Morrenger, public domain, Wikimedia Commons. Google Fonts có font hệ thống dự phòng. Bản đồ dùng OpenStreetMap, cần mạng.

Đã kiểm tra Chrome desktop và viewport mobile: tìm kiếm, chi tiết qua API, yêu thích, xếp lịch, lưu/xuất, bản đồ, chatbot và tràn ngang. Công cụ kiểm tra cục bộ trong .media-tools được gitignore và không cần để chạy website.
