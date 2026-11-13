"""Standalone EVNHES signal-loss monitor.

This module intentionally has no imports from the parent TSVH application.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import tkinter as tk
from tkinter import messagebox, ttk


APP_DIR = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
CONFIG_FILE = APP_DIR / "monitor_config.json"
STATION_NAMES = (
    "Trạm 110kV Ba Khe",
    "Trạm 110kV Bảo Hưng",
    "Trạm 110kV Bảo Yên",
    "Trạm 110kV Bắc Quang",
    "Trạm 110kV Cẩm Khê",
    "Trạm 110kV Lục Yên",
    "Trạm 110kV Nghĩa Lộ",
    "Trạm 110kV Phù Yên",
    "Trạm 110kV Văn Yên",
    "Trạm 110kV Yên Bái",
    "Trạm 110kV Yên Bái 2",
    "Trạm 110kV Yên Thế",
)
STATION_XPATHS = {
    name: f'//*[@id="formMain:dtlstTBA_data"]/tr[{index}]/td[2]'
    for index, name in enumerate(STATION_NAMES, start=1)
}
ENERGY_TOLERANCE_PERCENT = Decimal("3")
CURRENT_IMBALANCE_PERCENT = Decimal("5")
MIN_AVERAGE_POWER_FACTOR = Decimal("0.90")
DETAIL_SNAPSHOT_SCRIPT = """
const body = document.getElementById('formMain:dtDetail_data');
const pager = document.getElementById('formMain:dtDetail_paginator_top');
const next = pager?.querySelector('.ui-paginator-next');
return {
    rows: body ? Array.from(body.rows, row => ({
        cells: Array.from(row.cells, cell => cell.innerText.trim()),
        status: row.querySelector('[id$=":btnStatus"] span')?.className || ''
    })) : [],
    page: pager?.querySelector('.ui-paginator-current')?.innerText.trim() || '',
    has_next: Boolean(next && !next.classList.contains('ui-state-disabled'))
};
"""
DEFAULT_CONFIG = {
    "web_url": "https://evnhes.npc.com.vn/evnhes/tmsLogin.jsf",
    "web_user": "",
    "web_pass": "",
    "save_password": False,
    "show_browser": False,
    "bot_token": "",
    "chat_id": "",
    "monitor_minute": 15,
    "monitor_stations": {},
}


def parse_reading(text: str) -> Decimal | None:
    try:
        value = text.strip()
        if "," in value:
            if not re.fullmatch(r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?", value):
                return None
            value = value.replace(",", "")
        number = Decimal(value)
        return number if number.is_finite() else None
    except (InvalidOperation, AttributeError):
        return None


def valid_telegram_token(token: str) -> bool:
    return bool(re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token.strip()))


def parse_chat_ids(raw: str) -> list[str]:
    chat_ids = [part.strip() for part in raw.split(",") if part.strip()]
    if raw.strip() and not chat_ids:
        raise ValueError("Cần nhập ít nhất một Telegram chat ID dạng số.")
    if any(not re.fullmatch(r"-?\d+", chat_id) for chat_id in chat_ids):
        raise ValueError("Telegram chat ID phải là số; ngăn cách nhiều ID bằng dấu phẩy.")
    return list(dict.fromkeys(chat_ids))


def evaluate_readings(cells: list[str]) -> tuple[list[str], list[str]]:
    if len(cells) < 26:
        return [], ["thiếu cột dữ liệu"]

    alerts: list[str] = []
    missing: list[str] = []
    for channel, indices in (("giao", (7, 8, 9, 10)), ("nhận", (12, 13, 14, 15))):
        readings = [parse_reading(cells[index]) for index in indices]
        if any(value is None for value in readings):
            missing.append(f"kênh {channel}")
            continue
        total, tariff_1, tariff_2, tariff_3 = readings
        tariff_sum = tariff_1 + tariff_2 + tariff_3
        difference = abs(total - tariff_sum)
        if total == 0:
            if difference != 0:
                alerts.append(f"Tổng kênh {channel}=0 nhưng 3 biểu={tariff_sum}")
        elif difference * 100 > abs(total) * ENERGY_TOLERANCE_PERCENT:
            deviation = difference / abs(total) * 100
            alerts.append(f"Tổng kênh {channel} lệch 3 biểu {deviation:.2f}%")

    currents = [parse_reading(cells[index]) for index in (20, 21, 22)]
    if any(value is None for value in currents):
        missing.append("dòng pha A/B/C")
    else:
        mean_current = sum(currents) / 3
        if mean_current > 0:
            deviation = max(abs(value - mean_current) for value in currents) / mean_current * 100
            if deviation > CURRENT_IMBALANCE_PERCENT:
                alerts.append(f"dòng pha A/B/C lệch {deviation:.2f}% so với trung bình")
        elif any(value != 0 for value in currents):
            missing.append("dòng pha A/B/C không hợp lệ")

    power_factors = [parse_reading(cells[index]) for index in (23, 24, 25)]
    if any(value is None for value in power_factors):
        missing.append("Cosφ pha A/B/C")
    elif not all(value == 0 for value in currents):
        mean_power_factor = sum(power_factors) / 3
        if mean_power_factor < MIN_AVERAGE_POWER_FACTOR:
            alerts.append(f"Cosφ trung bình 3 pha={mean_power_factor:.3f} dưới 0,90")

    return alerts, missing


def detail_snapshot(driver) -> dict:
    return driver.execute_script(DETAIL_SNAPSHOT_SCRIPT)


def station_detail_ready(driver, station: str) -> dict | bool:
    snapshot = detail_snapshot(driver)
    rows = snapshot["rows"]
    if rows and len(rows[0]["cells"]) > 2 and rows[0]["cells"][2] == station:
        return snapshot
    if snapshot["page"].startswith("0-0"):
        return snapshot
    return False


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return dict(DEFAULT_CONFIG)
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return {**DEFAULT_CONFIG, **data} if isinstance(data, dict) else dict(DEFAULT_CONFIG)
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_CONFIG)


def write_config(config: dict) -> None:
    temp_file = CONFIG_FILE.with_suffix(".tmp")
    temp_file.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp_file, CONFIG_FILE)


class SignalMonitorApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.config = load_config()
        self.running = False
        self.stop_event = threading.Event()
        self.last_schedule_key = ""
        self.run_config: dict | None = None
        self.debug_driver = None

        root.title("Theo dõi mất tín hiệu EVNHES")
        root.geometry("1080x760")
        root.minsize(900, 650)
        root.configure(bg="#edf2f7")
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._configure_style()
        self._build_ui()
        self.root.after(15000, self._check_schedule)

    def _configure_style(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Header.TLabel", background="#123047", foreground="white", font=("Segoe UI", 19, "bold"))
        style.configure("Subheader.TLabel", background="#123047", foreground="#cbd9e5", font=("Segoe UI", 10))
        style.configure("Section.TLabelframe", background="#edf2f7", font=("Segoe UI", 10, "bold"))
        style.configure("Section.TLabelframe.Label", background="#edf2f7", foreground="#123047")
        style.configure("Treeview", rowheight=28, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"), background="#d9e5ec")
        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"), padding=(12, 7), background="#087e8b", foreground="white")
        style.map("Primary.TButton", background=[("active", "#056672")])
        style.configure("Danger.TButton", font=("Segoe UI", 10, "bold"), padding=(12, 7), background="#b23a48", foreground="white")
        style.map("Danger.TButton", background=[("active", "#8d2d38")])

    def _build_ui(self) -> None:
        header = tk.Frame(self.root, bg="#123047", padx=22, pady=16)
        header.pack(fill="x")
        ttk.Label(header, text="THEO DÕI MẤT TÍN HIỆU", style="Header.TLabel").pack(anchor="w")
        ttk.Label(header, text="EVNHES · kiểm tra theo lịch · cảnh báo Telegram", style="Subheader.TLabel").pack(anchor="w", pady=(3, 0))

        body = tk.Frame(self.root, bg="#edf2f7", padx=16, pady=14)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(2, weight=1)

        connection = ttk.LabelFrame(body, text="Kết nối EVNHES", style="Section.TLabelframe", padding=12)
        connection.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=(0, 10))
        connection.columnconfigure(1, weight=1)
        self.url_var = tk.StringVar(value=self.config["web_url"])
        self.user_var = tk.StringVar(value=self.config["web_user"])
        self.password_var = tk.StringVar(value=self.config["web_pass"] if self.config.get("save_password") else "")
        self.save_password_var = tk.BooleanVar(value=bool(self.config.get("save_password")))
        self.show_browser_var = tk.BooleanVar(value=bool(self.config.get("show_browser")))
        self._field(connection, 0, "Đường dẫn đăng nhập", self.url_var)
        self._field(connection, 1, "Tên đăng nhập", self.user_var)
        self._field(connection, 2, "Mật khẩu", self.password_var, show="*")
        ttk.Checkbutton(connection, text="Lưu mật khẩu cục bộ để chạy tự động", variable=self.save_password_var).grid(row=3, column=1, sticky="w", pady=(4, 0))
        ttk.Label(connection, text="Nếu không chọn, mật khẩu chỉ tồn tại khi ứng dụng đang mở.", foreground="#5d6872", wraplength=360).grid(row=4, column=1, sticky="w", pady=(2, 0))
        ttk.Checkbutton(connection, text="Hiện Chrome khi kiểm tra (bỏ chọn để chạy ngầm)", variable=self.show_browser_var).grid(
            row=5, column=1, sticky="w", pady=(6, 0)
        )

        alert = ttk.LabelFrame(body, text="Cảnh báo và lịch", style="Section.TLabelframe", padding=12)
        alert.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=(0, 10))
        alert.columnconfigure(1, weight=1)
        self.token_var = tk.StringVar(value=self.config["bot_token"])
        self.chat_id_rows: list[tuple[ttk.Frame, tk.StringVar]] = []
        self.minute_var = tk.StringVar(value=str(self.config["monitor_minute"]))
        self.schedule_var = tk.BooleanVar(value=True)
        self._field(alert, 0, "Telegram bot token", self.token_var, show="*")
        ttk.Label(alert, text="Telegram chat IDs").grid(row=1, column=0, sticky="nw", padx=(0, 10), pady=5)
        self.chat_ids_frame = ttk.Frame(alert)
        self.chat_ids_frame.grid(row=1, column=1, sticky="ew", pady=5)
        saved_chat_ids = [item.strip() for item in self.config["chat_id"].split(",") if item.strip()]
        for index, chat_id in enumerate(saved_chat_ids or [""]):
            self._add_chat_id_row(chat_id, first=index == 0)
        ttk.Label(alert, text="Phút kiểm tra mỗi giờ").grid(row=2, column=0, sticky="w", pady=5)
        ttk.Spinbox(alert, from_=0, to=59, width=7, textvariable=self.minute_var).grid(row=2, column=1, sticky="w", pady=5)
        ttk.Checkbutton(alert, text="Bật kiểm tra theo lịch", variable=self.schedule_var).grid(row=3, column=1, sticky="w", pady=(2, 0))
        ttk.Label(alert, text="Mỗi người nhận cần mở bot và bấm Start.", foreground="#5d6872", wraplength=360).grid(row=4, column=1, sticky="w", pady=(2, 0))

        stations = ttk.LabelFrame(body, text="Danh sách trạm cần theo dõi", style="Section.TLabelframe", padding=12)
        stations.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        stations.columnconfigure(1, weight=1)
        self.station_name_var = tk.StringVar()
        ttk.Label(stations, text="Tên trạm").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=5)
        ttk.Combobox(stations, textvariable=self.station_name_var, values=STATION_NAMES, state="readonly").grid(
            row=0, column=1, sticky="ew", pady=5
        )
        buttons = tk.Frame(stations, bg="#edf2f7")
        buttons.grid(row=1, column=1, sticky="w", pady=(5, 0))
        ttk.Button(buttons, text="Lưu / cập nhật trạm", command=self.save_station).pack(side="left")
        ttk.Button(buttons, text="Sửa trạm chọn", command=self.edit_station).pack(side="left", padx=7)
        ttk.Button(buttons, text="Xóa trạm chọn", command=self.remove_station).pack(side="left")

        table_frame = tk.Frame(body, bg="#edf2f7")
        table_frame.grid(row=2, column=0, columnspan=2, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        self.station_tree = ttk.Treeview(table_frame, columns=("station",), show="headings", selectmode="browse")
        self.station_tree.heading("station", text="Tên trạm")
        self.station_tree.column("station", width=960, minwidth=160, stretch=True)
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.station_tree.yview)
        self.station_tree.configure(yscrollcommand=scrollbar.set)
        self.station_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.refresh_stations()

        actions = tk.Frame(body, bg="#edf2f7")
        actions.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 8))
        self.run_button = ttk.Button(actions, text="KIỂM TRA NGAY", command=self.start_check, style="Primary.TButton")
        self.run_button.pack(side="left")
        ttk.Button(actions, text="LƯU CẤU HÌNH", command=self.save_settings).pack(side="left", padx=8)
        self.test_telegram_button = ttk.Button(actions, text="GỬI THỬ TELEGRAM", command=self.test_telegram)
        self.test_telegram_button.pack(side="left", padx=(0, 8))
        self.close_browser_button = ttk.Button(actions, text="Đóng Chrome", command=self.close_debug_browser, state="disabled")
        self.close_browser_button.pack(side="left")
        self.status_var = tk.StringVar(value="Sẵn sàng")
        ttk.Label(actions, textvariable=self.status_var, foreground="#24506b", font=("Segoe UI", 10, "bold")).pack(side="right")

        log_box = ttk.LabelFrame(body, text="Nhật ký hoạt động", style="Section.TLabelframe", padding=8)
        log_box.grid(row=4, column=0, columnspan=2, sticky="nsew")
        self.log_text = tk.Text(log_box, height=7, wrap="word", font=("Consolas", 9), background="#101b25", foreground="#dce7ed", insertbackground="white", state="disabled")
        self.log_text.pack(fill="both", expand=True)

    @staticmethod
    def _field(frame: tk.Misc, row: int, label: str, variable: tk.StringVar, show: str | None = None) -> None:
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=5)
        ttk.Entry(frame, textvariable=variable, show=show or "").grid(row=row, column=1, sticky="ew", pady=5)

    def _add_chat_id_row(self, value: str = "", first: bool = False) -> None:
        row = ttk.Frame(self.chat_ids_frame)
        row.pack(fill="x", pady=(0, 4))
        row.columnconfigure(0, weight=1)
        variable = tk.StringVar(value=value)
        ttk.Entry(row, textvariable=variable).grid(row=0, column=0, sticky="ew")
        if first:
            self.add_chat_id_button = ttk.Button(row, text="+", width=3, command=self._add_chat_id_row)
            self.add_chat_id_button.grid(row=0, column=1, padx=(5, 0))
        else:
            ttk.Button(row, text="−", width=3, command=lambda: self._remove_chat_id_row(row)).grid(
                row=0, column=1, padx=(5, 0)
            )
        self.chat_id_rows.append((row, variable))

    def _remove_chat_id_row(self, row: ttk.Frame) -> None:
        self.chat_id_rows = [(item, variable) for item, variable in self.chat_id_rows if item != row]
        row.destroy()

    def _chat_ids_from_ui(self) -> str:
        return ", ".join(variable.get().strip() for _, variable in self.chat_id_rows if variable.get().strip())

    def log(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.root.after(0, self._append_log, f"[{stamp}] {text}")

    def _append_log(self, line: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def set_status(self, text: str) -> None:
        self.root.after(0, self.status_var.set, text)

    def settings_from_ui(self, include_stations: bool = True) -> dict:
        if not self.url_var.get().strip().lower().startswith("https://"):
            raise ValueError("Đường dẫn đăng nhập EVNHES phải dùng HTTPS.")
        try:
            minute = int(self.minute_var.get().strip())
            if not 0 <= minute <= 59:
                raise ValueError
        except ValueError:
            raise ValueError("Phút kiểm tra phải là số từ 0 đến 59.")
        chat_ids = parse_chat_ids(self._chat_ids_from_ui())
        password = self.password_var.get() if self.save_password_var.get() else ""
        data = {
            "web_url": self.url_var.get().strip(), "web_user": self.user_var.get().strip(),
            "web_pass": password, "save_password": self.save_password_var.get(),
            "show_browser": self.show_browser_var.get(),
            "bot_token": self.token_var.get().strip(), "chat_id": ", ".join(chat_ids),
            "monitor_minute": minute,
        }
        if include_stations:
            data["monitor_stations"] = dict(self.config.get("monitor_stations", {}))
        return data

    def save_settings(self, quiet: bool = False) -> bool:
        try:
            self.config = self.settings_from_ui()
            write_config(self.config)
            if not quiet:
                self.log("Đã lưu cấu hình cục bộ.")
                self.status_var.set("Đã lưu cấu hình")
            return True
        except (OSError, ValueError) as exc:
            messagebox.showerror("Không thể lưu cấu hình", str(exc), parent=self.root)
            return False

    def refresh_stations(self) -> None:
        for item in self.station_tree.get_children():
            self.station_tree.delete(item)
        for name in self.config.get("monitor_stations", {}):
            self.station_tree.insert("", "end", values=(name,))

    def save_station(self) -> None:
        name = self.station_name_var.get().strip()
        if name not in STATION_XPATHS:
            messagebox.showwarning("Chọn trạm", "Hãy chọn tên trạm trong danh sách.", parent=self.root)
            return
        if not self.save_settings(quiet=True):
            return
        self.config.setdefault("monitor_stations", {})[name] = STATION_XPATHS[name]
        write_config(self.config)
        self.refresh_stations()
        self.log(f"Đã lưu trạm: {name}")

    def edit_station(self) -> None:
        selected = self.station_tree.selection()
        if not selected:
            messagebox.showinfo("Chọn trạm", "Hãy chọn một trạm trong bảng.", parent=self.root)
            return
        name = self.station_tree.item(selected[0], "values")[0]
        self.station_name_var.set(name)

    def remove_station(self) -> None:
        selected = self.station_tree.selection()
        if not selected:
            return
        name = self.station_tree.item(selected[0], "values")[0]
        if not messagebox.askyesno("Xóa trạm", f"Xóa trạm '{name}' khỏi danh sách?", parent=self.root):
            return
        self.config.get("monitor_stations", {}).pop(name, None)
        write_config(self.config)
        self.refresh_stations()
        self.log(f"Đã xóa trạm: {name}")

    def start_check(self) -> None:
        if self.running:
            self.log("Lượt kiểm tra trước vẫn đang chạy.")
            return
        if not self.save_settings(quiet=True):
            return
        if not self.config["web_url"] or not self.config["web_user"] or not self.password_var.get():
            messagebox.showwarning("Thiếu thông tin", "Nhập đường dẫn EVNHES, tên đăng nhập và mật khẩu trước khi kiểm tra.", parent=self.root)
            return
        if not self.config.get("monitor_stations"):
            messagebox.showwarning("Chưa có trạm", "Thêm ít nhất một trạm cần theo dõi.", parent=self.root)
            return
        self.close_debug_browser()
        self.run_config = {
            **self.config,
            "web_pass": self.password_var.get(),
            "monitor_stations": dict(self.config["monitor_stations"]),
        }
        if not self.run_config["bot_token"] or not self.run_config["chat_id"]:
            self.log("Telegram chưa cấu hình đủ bot token và chat ID; cảnh báo chỉ ghi vào nhật ký.")
        elif not valid_telegram_token(self.run_config["bot_token"]):
            self.log("Bot token Telegram không đúng định dạng. Dán token API do BotFather cấp, không phải tên bot; cảnh báo chỉ ghi vào nhật ký.")
            self.run_config["bot_token"] = ""
        self.running = True
        self.root.after(0, lambda: self.run_button.configure(state="disabled"))
        self.status_var.set("Đang kiểm tra...")
        threading.Thread(target=self.monitor_once, daemon=True, name="signal-monitor-run").start()

    def _check_schedule(self) -> None:
        if self.stop_event.is_set():
            return
        if self.schedule_var.get() and not self.running:
            try:
                minute = int(self.minute_var.get())
            except ValueError:
                minute = -1
            now = datetime.now()
            key = now.strftime("%Y-%m-%d-%H:%M")
            if now.minute == minute and key != self.last_schedule_key:
                self.last_schedule_key = key
                self.log("Đến lịch kiểm tra.")
                self.start_check()
        self.root.after(15000, self._check_schedule)

    def send_telegram(self, message: str, settings: dict | None = None) -> bool:
        settings = settings or self.run_config or self.config
        token = settings.get("bot_token", "")
        if not token or not settings.get("chat_id"):
            return False
        if not valid_telegram_token(token):
            self.log("Không gửi được Telegram: bot token không đúng định dạng; cần token API do BotFather cấp.")
            return False
        try:
            chat_ids = parse_chat_ids(settings["chat_id"])
        except ValueError as exc:
            self.log(f"Không gửi được Telegram: {exc}")
            return False
        all_sent = True
        for chat_id in chat_ids:
            try:
                payload = urlencode({"chat_id": chat_id, "text": message}).encode("utf-8")
                request = Request(f"https://api.telegram.org/bot{token}/sendMessage", data=payload, method="POST")
                with urlopen(request, timeout=20) as response:
                    if not 200 <= response.status < 300:
                        raise RuntimeError(f"Telegram HTTP {response.status}")
                    result = json.load(response)
                    if not result.get("ok"):
                        raise RuntimeError(result.get("description", "Telegram không xác nhận gửi thành công"))
            except HTTPError as exc:
                all_sent = False
                if exc.code == 404:
                    self.log("Không gửi được Telegram: bot token không hợp lệ hoặc đã bị thu hồi (HTTP 404).")
                    break
                self.log(f"Không gửi được Telegram tới {chat_id}: HTTP {exc.code}. Kiểm tra chat ID và quyền gửi tin.")
            except Exception as exc:
                all_sent = False
                detail = str(exc).replace(token, "<ẩn token>")
                self.log(f"Không gửi được Telegram tới {chat_id}: {detail}")
        return all_sent

    def test_telegram(self) -> None:
        token, chat_id = self.token_var.get().strip(), self._chat_ids_from_ui()
        if not token or not chat_id:
            messagebox.showwarning("Thiếu Telegram", "Nhập bot token và chat ID trước khi gửi thử.", parent=self.root)
            return
        if not valid_telegram_token(token):
            messagebox.showwarning("Bot token không hợp lệ", "Dán token API do BotFather cấp vào ô bot token, không nhập tên bot.", parent=self.root)
            return
        try:
            parse_chat_ids(chat_id)
        except ValueError as exc:
            messagebox.showwarning("Chat ID không hợp lệ", str(exc), parent=self.root)
            return
        self.test_telegram_button.configure(state="disabled")
        settings = {"bot_token": token, "chat_id": chat_id}

        def send_test() -> None:
            if self.send_telegram("Thử kết nối từ công cụ theo dõi mất tín hiệu EVNHES.", settings):
                self.log("Đã gửi tin nhắn thử Telegram thành công.")
            self.root.after(0, lambda: self.test_telegram_button.configure(state="normal"))

        threading.Thread(target=send_test, daemon=True, name="telegram-test").start()

    def close_debug_browser(self) -> None:
        driver = self.debug_driver
        if driver is None:
            return
        self.debug_driver = None
        self.close_browser_button.configure(state="disabled")
        threading.Thread(target=driver.quit, daemon=True, name="close-debug-browser").start()

    def monitor_once(self) -> None:
        driver = None
        settings = self.run_config or dict(self.config)
        step = "khởi tạo trình duyệt"
        keep_browser_open = False
        try:
            from selenium import webdriver
            from selenium.common.exceptions import TimeoutException
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.support.ui import WebDriverWait

            options = Options()
            if not settings.get("show_browser"):
                options.add_argument("--headless=new")
            options.add_argument("--window-size=1920,1080")
            options.add_argument("--disable-gpu")
            options.add_argument("--disable-notifications")
            driver = webdriver.Chrome(options=options)
            wait = WebDriverWait(driver, 30)
            step = "mở trang EVNHES"
            driver.get(settings["web_url"])
            step = "tìm mẫu đăng nhập"
            username_input = wait.until(EC.element_to_be_clickable((By.ID, "formMain:userName")))
            username_input.clear()
            username_input.send_keys(settings["web_user"])
            step = "điền mật khẩu"
            password_input = wait.until(EC.element_to_be_clickable((By.ID, "formMain:password")))
            password_input.clear()
            password_input.send_keys(settings["web_pass"])
            if password_input.get_property("value") != settings["web_pass"]:
                raise RuntimeError("Ô mật khẩu không nhận đủ dữ liệu.")
            step = "gửi biểu mẫu đăng nhập"
            wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "#formMain button[type='submit']"))).click()
            step = "mở menu EVNHES sau đăng nhập"
            wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="formMain:mnuMain"]/ul/li[4]/a/span[1]'))).click()
            step = "mở danh sách trạm"
            wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="formMain:mnuMain"]/ul/li[4]/ul/li/a'))).click()
            offline_count = 0
            abnormal_count = 0
            incomplete_count = 0
            point_count = 0
            for station, xpath in settings["monitor_stations"].items():
                xpath = STATION_XPATHS.get(station, xpath)
                step = f"chọn trạm {station}"
                old_rows = driver.find_elements(By.CSS_SELECTOR, '[id="formMain:dtDetail_data"] > tr')
                wait.until(EC.element_to_be_clickable((By.XPATH, xpath))).click()
                step = f"đọc điểm đo trạm {station}"
                if old_rows:
                    wait.until(EC.staleness_of(old_rows[0]))
                snapshot = wait.until(lambda browser: station_detail_ready(browser, station))
                seen_pages: set[str] = set()
                while True:
                    page = snapshot["page"]
                    if page in seen_pages:
                        raise RuntimeError(f"Bảng trạm {station} lặp lại trang {page}.")
                    seen_pages.add(page)
                    if not snapshot["rows"] or len(snapshot["rows"][0]["cells"]) < 7:
                        self.log(f"{station}: không tìm thấy điểm đo.")
                    for row in snapshot["rows"]:
                        cells = row["cells"]
                        if len(cells) < 7:
                            continue
                        point_count += 1
                        station_label = cells[2] or station
                        point = cells[3] or "Điểm đo"
                        label = f"{station_label} - {point}"
                        status = f"{cells[6]} {row['status']}".lower()
                        state = "offline" if "offline" in status else "online" if "online" in status else "unknown"
                        self.log(f"{label}: {state}")
                        if state == "offline":
                            offline_count += 1
                            self.send_telegram(f"{label} đang bị mất tín hiệu. Đề nghị kiểm tra lại đường dẫn.")
                        elif state == "online":
                            alerts, missing = evaluate_readings(cells)
                            if alerts:
                                abnormal_count += len(alerts)
                                message = f"{label}: {'; '.join(alerts)}"
                                self.log(message)
                                self.send_telegram(message)
                            if missing:
                                incomplete_count += 1
                                self.log(f"{label}: không đủ dữ liệu để kiểm tra {', '.join(missing)}.")
                    if not snapshot["has_next"]:
                        break
                    step = f"đọc trang tiếp theo của trạm {station}"
                    old_rows = driver.find_elements(By.CSS_SELECTOR, '[id="formMain:dtDetail_data"] > tr')
                    wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, '[id="formMain:dtDetail_paginator_top"] .ui-paginator-next'))).click()
                    if old_rows:
                        wait.until(EC.staleness_of(old_rows[0]))
                    snapshot = wait.until(lambda browser: station_detail_ready(browser, station))
            self.log(
                f"Hoàn tất {point_count} điểm đo: {offline_count} mất tín hiệu, "
                f"{abnormal_count} bất thường số đo, {incomplete_count} điểm thiếu dữ liệu."
            )
            self.set_status(f"Hoàn tất · {offline_count} mất tín hiệu · {abnormal_count} bất thường")
        except TimeoutException:
            self.log(f"Hết thời gian chờ 30 giây khi {step}. Kiểm tra trang EVNHES và thử lại.")
            self.send_telegram(f"Không hoàn tất kiểm tra EVNHES khi {step}. Xem nhật ký ứng dụng để biết chi tiết.")
            self.set_status("Kiểm tra thất bại")
            keep_browser_open = bool(settings.get("show_browser") and driver is not None)
        except Exception as exc:
            detail = str(exc).split("Stacktrace:", 1)[0].strip() or type(exc).__name__
            self.log(f"Không hoàn tất kiểm tra khi {step}: {detail}")
            self.send_telegram(f"Không hoàn tất kiểm tra EVNHES khi {step}. Xem nhật ký ứng dụng để biết chi tiết.")
            self.set_status("Kiểm tra thất bại")
            keep_browser_open = bool(settings.get("show_browser") and driver is not None)
        finally:
            if driver is not None:
                if keep_browser_open:
                    self.debug_driver = driver
                    self.log("Đã giữ Chrome ở bước xảy ra lỗi. Bấm 'Đóng Chrome' sau khi xem xong.")
                    self.root.after(0, lambda: self.close_browser_button.configure(state="normal"))
                else:
                    try:
                        driver.quit()
                    except Exception:
                        pass
            self.running = False
            self.root.after(0, lambda: self.run_button.configure(state="normal"))

    def close(self) -> None:
        self.stop_event.set()
        if self.debug_driver is not None:
            try:
                self.debug_driver.quit()
            except Exception:
                pass
        self.root.destroy()


if __name__ == "__main__":
    if sys.version_info < (3, 10):
        raise SystemExit("Cần Python 3.10 hoặc mới hơn.")
    window = tk.Tk()
    SignalMonitorApp(window)
    window.mainloop()
