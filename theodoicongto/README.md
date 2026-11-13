# Công cụ theo dõi mất tín hiệu EVNHES

Ứng dụng Python/Tkinter đọc dữ liệu điểm đo EVNHES và gửi cảnh báo Telegram cho các chat ID đã cấu hình.

## Chạy ứng dụng

- Cần Python 3.10+, Google Chrome và quyền truy cập EVNHES.
- Chạy `run_monitor.bat` để cài Selenium và mở ứng dụng.
- Điền URL EVNHES HTTPS, tài khoản, mật khẩu, bot token, chat ID và các trạm cần theo dõi.
- Bấm `Gửi thử Telegram` trước khi bật kiểm tra theo lịch.
- `build_portable_exe.bat` tạo bản một file trong `dist/`; đặt file EXE trong thư mục mà người dùng có quyền ghi.

## Dữ liệu và an toàn thông tin

- Ứng dụng tạo `monitor_config.json` cạnh mã nguồn hoặc cạnh file EXE. File này chứa bot token dạng rõ và có thể chứa mật khẩu EVNHES dạng rõ nếu người dùng chọn lưu mật khẩu. **Không đưa file này lên GitHub hoặc gửi cho bên đánh giá.**
- `.gitignore` loại file cấu hình và tệp sinh ra khỏi `git add` thông thường. Vẫn cần kiểm tra `git status` trước khi push. Nếu bí mật từng được commit, cần thu hồi/đổi bí mật; xóa file ở commit mới không xóa nó khỏi lịch sử Git.
- Đăng nhập EVNHES chỉ chấp nhận HTTPS. Đội an toàn thông tin cần phê duyệt việc gửi tên trạm, điểm đo và cảnh báo vận hành tới Telegram trước khi triển khai rộng.
- `run_monitor.bat` cài gói từ PyPI; `build_portable_exe.bat` nâng cấp PyInstaller/Selenium từ PyPI. Đội an toàn thông tin cần rà soát quy trình cung ứng và đóng gói.

## Kiểm thử

Chạy `python -B -m unittest test_monitor_signal_tool -q`.
