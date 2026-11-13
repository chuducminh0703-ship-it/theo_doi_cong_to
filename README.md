# Công cụ theo dõi mất tín hiệu

Công cụ độc lập để đăng nhập EVNHES, đọc trạng thái các điểm đo theo trạm đã cấu hình và gửi cảnh báo Telegram khi phát hiện trạng thái `offline`.



Selenium Manager tự lấy ChromeDriver tương thích trong các bản Selenium mới. Máy chạy cần truy cập được EVNHES (thường cần VPN nội bộ) và Internet để gửi Telegram.

Để bàn giao bản không cần cài Python, chạy `build_portable_exe.bat` trên một máy đã cài Python. Gửi file `dist\\TheoDoiMatTinHieu.exe` cùng `README.md` cho máy nhận. Máy nhận vẫn cần Google Chrome và quyền truy cập EVNHES/VPN.

## Dữ liệu cấu hình và an toàn thông tin

- Sau lần lưu đầu tiên, công cụ tạo `monitor_config.json` ngay trong thư mục này. File này bị loại khỏi gói mẫu và không được phát hành kèm cấu hình thật.
- Mật khẩu EVNHES **không được lưu mặc định**. Chọn `Lưu mật khẩu cục bộ` chỉ khi máy là máy quản trị được bảo vệ. Khi chọn, mật khẩu sẽ nằm dạng rõ trong `monitor_config.json`; đây là đánh đổi để công cụ có thể chạy tự động không cần người nhập.
- Telegram bot token có quyền gửi tin nhắn; chỉ cấp token dành riêng cho công cụ và thu hồi/đổi token khi chia sẻ nhầm.
- Công cụ chỉ đọc trạng thái từ EVNHES và gửi cảnh báo Telegram; không ghi hoặc thay đổi dữ liệu trên EVNHES.

## Thêm trạm

Mỗi trạm gồm tên nhận diện và XPath tới dòng/đường dẫn trạm trên trang EVNHES. Chọn một trạm trong bảng, bấm `Sửa trạm` để nạp lại thông tin; lưu sẽ cập nhật trạm cùng tên.

## Lịch chạy

Bật lịch trong giao diện để kiểm tra tại phút đã đặt của mỗi giờ. Cửa sổ phải được mở. Nút `Kiểm tra ngay` chạy nền để giao diện vẫn dùng được.
