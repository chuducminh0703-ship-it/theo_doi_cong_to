import unittest
import tkinter as tk
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs

from selenium.common.exceptions import TimeoutException

from monitor_signal_tool import DEFAULT_CONFIG, SignalMonitorApp, evaluate_readings, parse_chat_ids, parse_reading, valid_telegram_token


class ConfigSafetyTests(unittest.TestCase):
    def test_default_login_url_uses_https(self):
        self.assertTrue(DEFAULT_CONFIG["web_url"].startswith("https://"))

    def test_rejects_http_login_url(self):
        app = object.__new__(SignalMonitorApp)
        app.url_var = Mock()
        app.url_var.get.return_value = "http://evnhes.npc.com.vn/evnhes/tmsLogin.jsf"
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            app.settings_from_ui()


class MonitorErrorTests(unittest.TestCase):
    def run_timeout(self, show_browser):
        app = object.__new__(SignalMonitorApp)
        messages = []
        app.run_config = {
            "web_url": "http://example.invalid", "web_user": "test", "web_pass": "test",
            "monitor_stations": {}, "show_browser": show_browser,
        }
        app.config = app.run_config
        app.debug_driver = None
        app.log = messages.append
        app.send_telegram = lambda message: None
        app.set_status = lambda status: None
        app.root = SimpleNamespace(after=lambda delay, callback: callback())
        app.run_button = SimpleNamespace(configure=lambda **kwargs: None)
        app.close_browser_button = Mock()
        driver = SimpleNamespace(get=lambda url: None, quit=Mock())
        wait = SimpleNamespace(until=lambda condition: (_ for _ in ()).throw(TimeoutException()))

        with patch("selenium.webdriver.Chrome", return_value=driver), patch(
            "selenium.webdriver.support.ui.WebDriverWait", return_value=wait
        ), patch("selenium.webdriver.chrome.options.Options") as options_class:
            app.monitor_once()
        return app, driver, options_class.return_value, messages

    def test_timeout_identifies_waiting_step_without_stacktrace(self):
        app, driver, options, messages = self.run_timeout(False)

        self.assertTrue(any("mẫu đăng nhập" in message and "30" in message for message in messages))
        self.assertFalse(any("Stacktrace" in message or "Không truy cập được EVNHES: Message:" in message for message in messages))
        options.add_argument.assert_any_call("--headless=new")
        driver.quit.assert_called_once()
        self.assertIsNone(app.debug_driver)

    def test_visible_chrome_remains_open_on_timeout(self):
        app, driver, options, messages = self.run_timeout(True)

        self.assertNotIn("--headless=new", [call.args[0] for call in options.add_argument.call_args_list])
        driver.quit.assert_not_called()
        self.assertIs(app.debug_driver, driver)
        app.close_browser_button.configure.assert_called_with(state="normal")
        self.assertTrue(any("Đã giữ Chrome" in message for message in messages))

    def test_login_enters_password_before_clicking_submit(self):
        app = object.__new__(SignalMonitorApp)
        app.run_config = {
            "web_url": "http://example.invalid", "web_user": "test", "web_pass": "secret",
            "monitor_stations": {}, "show_browser": False,
        }
        app.config = app.run_config
        app.debug_driver = None
        app.log = lambda message: None
        app.send_telegram = lambda message: None
        app.set_status = lambda status: None
        app.root = SimpleNamespace(after=lambda delay, callback: callback())
        app.run_button = SimpleNamespace(configure=lambda **kwargs: None)
        username = Mock()
        password = Mock()
        password.get_property.return_value = "secret"
        submit = Mock()
        driver = Mock()
        wait = Mock()
        wait.until.side_effect = [username, password, submit, TimeoutException()]

        with patch("selenium.webdriver.Chrome", return_value=driver), patch(
            "selenium.webdriver.support.ui.WebDriverWait", return_value=wait
        ):
            app.monitor_once()

        password.send_keys.assert_called_once_with("secret")
        submit.click.assert_called_once()


class ReadingChecksTests(unittest.TestCase):
    def test_evnhes_thousands_separator(self):
        self.assertEqual(str(parse_reading("29,613.069")), "29613.069")
        self.assertIsNone(parse_reading("12,34.567"))

    def make_cells(self):
        cells = [""] * 26
        cells[7:11] = ["100", "40", "30", "30"]
        cells[12:16] = ["100", "40", "30", "30"]
        cells[20:23] = ["10", "10", "10"]
        cells[23:26] = ["0.95", "0.95", "0.95"]
        return cells

    def test_energy_checks_both_channels_with_three_percent_limit(self):
        cells = self.make_cells()
        cells[10] = "33"
        cells[15] = "33.001"
        alerts, missing = evaluate_readings(cells)
        self.assertFalse(any("giao" in alert for alert in alerts))
        self.assertTrue(any("nhận" in alert for alert in alerts))
        self.assertFalse(missing)

    def test_exact_current_and_power_factor_limits_are_accepted(self):
        cells = self.make_cells()
        cells[20:23] = ["10.5", "10", "9.5"]
        cells[23:26] = ["0.90", "0.90", "0.90"]
        alerts, missing = evaluate_readings(cells)
        self.assertFalse(alerts)
        self.assertFalse(missing)

    def test_phase_current_and_average_power_factor(self):
        cells = self.make_cells()
        cells[20:23] = ["10.6", "10", "9.4"]
        cells[23:26] = ["0.89", "0.90", "0.90"]
        alerts, missing = evaluate_readings(cells)
        self.assertTrue(any("dòng pha" in alert for alert in alerts))
        self.assertTrue(any("Cosφ" in alert for alert in alerts))
        self.assertFalse(missing)

    def test_zero_load_and_missing_readings_do_not_make_false_alerts(self):
        cells = self.make_cells()
        cells[7:11] = ["0", "0", "0", "0"]
        cells[20:23] = ["0", "0", "0"]
        cells[23:26] = ["0", "0", "0"]
        cells[12] = ""
        alerts, missing = evaluate_readings(cells)
        self.assertFalse(alerts)
        self.assertTrue(any("nhận" in item for item in missing))


class TelegramTests(unittest.TestCase):
    def test_parse_multiple_chat_ids_deduplicates_and_validates(self):
        self.assertEqual(parse_chat_ids("123, -456, 123"), ["123", "-456"])
        with self.assertRaises(ValueError):
            parse_chat_ids("123, @someone")
        with self.assertRaises(ValueError):
            parse_chat_ids(" , ")

    def test_bot_username_is_not_an_api_token(self):
        self.assertFalse(valid_telegram_token("@example_bot"))
        self.assertFalse(valid_telegram_token("example_bot"))
        self.assertTrue(valid_telegram_token("123456789:AbCdEf0123_-"))

    def test_send_telegram_uses_configured_chat_and_checks_api_result(self):
        app = object.__new__(SignalMonitorApp)
        app.config = {"bot_token": "123456789:AbCdEf0123_-", "chat_id": "123"}
        app.run_config = None
        app.log = Mock()
        response = BytesIO(b'{"ok":true,"result":{}}')
        response.status = 200

        with patch("monitor_signal_tool.urlopen", return_value=response) as opened:
            self.assertTrue(app.send_telegram("Cảnh báo"))

        request = opened.call_args.args[0]
        self.assertEqual(parse_qs(request.data.decode("utf-8")), {"chat_id": ["123"], "text": ["Cảnh báo"]})
        self.assertEqual(request.full_url, "https://api.telegram.org/bot123456789:AbCdEf0123_-/sendMessage")
        app.log.assert_not_called()

    def test_send_telegram_reports_rejected_api_response(self):
        app = object.__new__(SignalMonitorApp)
        app.config = {"bot_token": "123456789:AbCdEf0123_-", "chat_id": "123"}
        app.run_config = None
        app.log = Mock()
        response = BytesIO(b'{"ok":false,"description":"chat not found"}')
        response.status = 200

        with patch("monitor_signal_tool.urlopen", return_value=response):
            self.assertFalse(app.send_telegram("Cảnh báo"))

        app.log.assert_called_once_with("Không gửi được Telegram tới 123: chat not found")

    def test_sends_to_each_recipient_even_when_one_fails(self):
        app = object.__new__(SignalMonitorApp)
        app.config = {"bot_token": "123456789:AbCdEf0123_-", "chat_id": "111, 222, 111"}
        app.run_config = None
        app.log = Mock()
        success = BytesIO(b'{"ok":true,"result":{}}')
        success.status = 200
        with patch("monitor_signal_tool.urlopen", side_effect=[HTTPError("https://api.telegram.org", 400, "Bad Request", None, None), success]) as opened:
            self.assertFalse(app.send_telegram("Cảnh báo"))
        self.assertEqual(opened.call_count, 2)
        self.assertEqual([parse_qs(call.args[0].data.decode("utf-8"))["chat_id"][0] for call in opened.call_args_list], ["111", "222"])
        self.assertIn("111", app.log.call_args.args[0])

    def test_invalid_token_is_rejected_before_network_call(self):
        app = object.__new__(SignalMonitorApp)
        app.config = {"bot_token": "example_bot", "chat_id": "123"}
        app.run_config = None
        app.log = Mock()
        with patch("monitor_signal_tool.urlopen") as opened:
            self.assertFalse(app.send_telegram("Cảnh báo"))
        opened.assert_not_called()
        self.assertIn("token", app.log.call_args.args[0])

    def test_revoked_token_404_has_clear_message(self):
        app = object.__new__(SignalMonitorApp)
        app.config = {"bot_token": "123456789:AbCdEf0123_-", "chat_id": "123"}
        app.run_config = None
        app.log = Mock()
        error = HTTPError("https://api.telegram.org", 404, "Not Found", None, None)
        with patch("monitor_signal_tool.urlopen", side_effect=error):
            self.assertFalse(app.send_telegram("Cảnh báo"))
        self.assertIn("đã bị thu hồi", app.log.call_args.args[0])


class ChatIdUiTests(unittest.TestCase):
    def test_add_remove_and_reload_chat_id_rows(self):
        root = tk.Tk()
        root.withdraw()
        try:
            config = {**DEFAULT_CONFIG, "chat_id": "111, 222"}
            with patch("monitor_signal_tool.load_config", return_value=config):
                app = SignalMonitorApp(root)
            self.assertEqual([variable.get() for _, variable in app.chat_id_rows], ["111", "222"])
            app.add_chat_id_button.invoke()
            app.chat_id_rows[-1][1].set("333")
            self.assertEqual(app.settings_from_ui()["chat_id"], "111, 222, 333")
            app._remove_chat_id_row(app.chat_id_rows[1][0])
            self.assertEqual(app.settings_from_ui()["chat_id"], "111, 333")
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
