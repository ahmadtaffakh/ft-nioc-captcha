import base64
import queue
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

import tkinter as tk
from tkinter import ttk

from captcha_lab import AUTO_UPLOAD_INTERVAL_MS, CaptchaLabApp
from ft_auto_uploader import open_state, process_file
from ft_runtime import BrowserSession, capture_captcha_bytes


class CaptchaCaptureTest(unittest.TestCase):
    def test_reads_original_data_url_without_screenshot(self):
        image_bytes = b"original-image"
        data_url = "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii")
        page = Mock()
        image = page.locator.return_value.nth.return_value
        page.locator.return_value.count.return_value = 1
        image.is_visible.return_value = True
        image.bounding_box.return_value = {"width": 150, "height": 40}
        image.get_attribute.return_value = data_url

        self.assertEqual(capture_captcha_bytes(page, "img.captcha"), image_bytes)
        image.screenshot.assert_not_called()

    def test_uses_element_screenshot_for_non_data_url(self):
        page = Mock()
        image = page.locator.return_value.nth.return_value
        page.locator.return_value.count.return_value = 1
        image.is_visible.return_value = True
        image.bounding_box.return_value = {"width": 150, "height": 40}
        image.get_attribute.return_value = "/captcha/image"
        image.screenshot.return_value = b"screenshot"

        self.assertEqual(capture_captcha_bytes(page, "img.captcha"), b"screenshot")
        image.screenshot.assert_called_once_with(type="png")


class BatchCaptureTest(unittest.TestCase):
    def test_upload_history_reports_each_status(self):
        app = CaptchaLabApp.__new__(CaptchaLabApp)
        app._log = Mock()
        app.status = Mock()

        app._display_upload_history(
            [("report.pdf", "queued", 10), ("pending.pdf", "uploaded", 20)]
        )

        messages = [call.args[1] for call in app._log.call_args_list]
        self.assertIn("موفق: 1", messages[0])
        self.assertTrue(any("ارسال نهایی موفق" in message for message in messages))
        self.assertTrue(any("در انتظار اسکن" in message for message in messages))

    def test_auto_upload_ticks_every_ten_seconds_and_skips_overlap(self):
        app = CaptchaLabApp.__new__(CaptchaLabApp)
        app.root = Mock()
        app.closing = False
        app.auto_upload_suspended = False
        app.auto_upload_enabled = Mock()
        app.auto_upload_enabled.get.return_value = True
        app._upload_folder = Mock()

        app._auto_upload_tick()

        app._upload_folder.assert_called_once_with(automatic=True)
        app.root.after.assert_called_once_with(
            AUTO_UPLOAD_INTERVAL_MS, app._auto_upload_tick
        )

        app.upload_task_id = "upload-1"
        app._log = Mock()
        app.browser = Mock()
        app._upload_folder = CaptchaLabApp._upload_folder.__get__(app)
        app._upload_folder(automatic=True)
        app.browser.upload_folder.assert_not_called()

    def test_collects_requested_images_and_refreshes_between_them(self):
        with tempfile.TemporaryDirectory() as temporary:
            session = BrowserSession(Path(temporary) / "profile", queue.Queue())
            session.page = Mock()
            batch_dir = Path(temporary) / "batch"
            try:
                with patch("ft_runtime.capture_captcha_bytes", return_value=b"png") as capture:
                    result = session.collect_batch("img.captcha", batch_dir, 3, 0)
            finally:
                session.executor.shutdown(wait=False, cancel_futures=True)

            self.assertEqual(result["count"], 3)
            self.assertEqual(capture.call_count, 3)
            self.assertEqual(session.page.reload.call_count, 2)
            self.assertEqual(len(list((batch_dir / "images").glob("*.png"))), 3)

    def test_waits_configured_delay_after_each_reload(self):
        with tempfile.TemporaryDirectory() as temporary:
            session = BrowserSession(Path(temporary) / "profile", queue.Queue())
            session.page = Mock()
            batch_dir = Path(temporary) / "batch"
            try:
                with (
                    patch("ft_runtime.capture_captcha_bytes", return_value=b"png"),
                    patch.object(session.batch_cancelled, "wait", return_value=False) as wait,
                ):
                    session.collect_batch("img.captcha", batch_dir, 3, 2.5)
            finally:
                session.executor.shutdown(wait=False, cancel_futures=True)

            self.assertEqual(wait.call_args_list, [call(2.5)] * 2)

    def test_stop_during_reload_delay_prevents_next_captcha_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            session = BrowserSession(Path(temporary) / "profile", queue.Queue())
            session.page = Mock()
            batch_dir = Path(temporary) / "batch"
            try:
                with (
                    patch("ft_runtime.capture_captcha_bytes", return_value=b"png") as capture,
                    patch.object(session.batch_cancelled, "wait", return_value=True),
                ):
                    result = session.collect_batch("img.captcha", batch_dir, 3, 5)
            finally:
                session.executor.shutdown(wait=False, cancel_futures=True)

            self.assertEqual(result["count"], 1)
            self.assertEqual(capture.call_count, 1)
            session.page.reload.assert_called_once_with(wait_until="domcontentloaded")

    def test_repeated_open_reuses_one_browser_session(self):
        session = BrowserSession(Path("profile"), queue.Queue())
        context = Mock()
        page = Mock()
        playwright = Mock()
        starter = Mock()
        starter.start.return_value = playwright
        playwright_module = types.ModuleType("playwright")
        sync_api_module = types.ModuleType("playwright.sync_api")
        sync_api_module.sync_playwright = Mock(return_value=starter)
        playwright_module.sync_api = sync_api_module
        try:
            with (
                patch.dict(
                    sys.modules,
                    {
                        "playwright": playwright_module,
                        "playwright.sync_api": sync_api_module,
                    },
                ),
                patch(
                    "ft_runtime.launch_persistent_session",
                    return_value=(context, page),
                ) as launch,
            ):
                session.open()
                session.open()
        finally:
            session.close()
            session.executor.shutdown(wait=False, cancel_futures=True)

        sync_api_module.sync_playwright.assert_called_once_with()
        launch.assert_called_once_with(playwright, Path("profile"))
        self.assertEqual(page.goto.call_count, 2)

    def test_manual_login_reuses_open_browser_page(self):
        session = BrowserSession(Path("profile"), queue.Queue())
        session.page = Mock()
        try:
            message = session.manual_login()
        finally:
            session.executor.shutdown(wait=False, cancel_futures=True)

        session.page.goto.assert_called_once_with(
            "https://ft.nioc.ir/dargah/user/sign-in",
            wait_until="domcontentloaded",
        )
        self.assertIn("ورود دستی", message)

    def test_empty_upload_folder_finishes_without_database_work(self):
        with tempfile.TemporaryDirectory() as temporary:
            session = BrowserSession(Path(temporary) / "profile", queue.Queue())
            session.page = Mock()
            session.context = Mock()
            try:
                with patch("ft_runtime.wait_for_file_manager") as wait:
                    result = session.upload_folder(Path(temporary), 5, 10)
            finally:
                session.executor.shutdown(wait=False, cancel_futures=True)

            wait.assert_not_called()
            self.assertEqual(result, {"processed": 0, "failed": 0, "oversized": 0})

    def test_upload_stops_only_after_two_consecutive_errors(self):
        scenarios = (
            ([Exception("first"), None, Exception("third"), None], 4, False),
            ([Exception("first"), Exception("second"), None], 2, True),
        )
        for side_effects, expected_calls, should_stop in scenarios:
            with self.subTest(should_stop=should_stop), tempfile.TemporaryDirectory() as temporary:
                folder = Path(temporary) / "inbox"
                folder.mkdir()
                for index in range(len(side_effects)):
                    (folder / f"{index}.pdf").write_bytes(b"data")
                results = queue.Queue()
                session = BrowserSession(Path(temporary) / "profile", results)
                session.page = Mock()
                session.context = Mock()
                try:
                    with (
                        patch("ft_runtime.wait_for_file_manager"),
                        patch("ft_runtime.process_file", side_effect=side_effects) as process,
                    ):
                        result = session.upload_folder(folder, 0, 0, "thinclient")
                finally:
                    session.executor.shutdown(wait=False, cancel_futures=True)

                self.assertEqual(process.call_count, expected_calls)
                self.assertEqual(result.get("stopped", False), should_stop)
                messages = [item.message for item in list(results.queue)]
                self.assertEqual(
                    any("دو ردیف پیاپی" in message for message in messages), should_stop
                )

    def test_downloads_only_opposite_origin_and_blocks_reverse_upload(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            session = BrowserSession(base / "browser-profile", queue.Queue())
            session.page = Mock()
            session.context = Mock()
            groups = Mock(ok=True, status=200)
            groups.json.return_value = {
                "isSuccess": True,
                "data": [{"id": "group-1", "isDefault": True, "isDisabled": False}],
            }
            listing = Mock(ok=True, status=200)
            listing.json.return_value = {
                "isSuccess": True,
                "data": {
                    "totalCount": 3,
                    "items": [
                        {
                            "id": "remote-1",
                            "groupId": "group-1",
                            "name": "__FT_TC__report",
                            "extension": ".pdf",
                            "size": 7,
                            "directory": False,
                        },
                        {
                            "id": "remote-2",
                            "groupId": "group-1",
                            "name": "__FT_IN__local",
                            "extension": ".pdf",
                            "size": 7,
                            "directory": False,
                        },
                        {
                            "id": "remote-3",
                            "groupId": "group-1",
                            "name": "untagged",
                            "extension": ".pdf",
                            "size": 7,
                            "directory": False,
                        },
                    ],
                },
            }
            download = Mock(ok=True, status=200)
            download.body.return_value = b"payload"
            session.context.request.get.side_effect = [groups, download]
            session.context.request.put.return_value = listing
            destination = base / "downloads"
            upload_dir = base / "uploads"
            upload_dir.mkdir()
            try:
                with patch.object(session, "_ensure_file_manager"):
                    records = session.list_downloadable_files("intranet")
                    result = session.download_files(
                        records, destination, "intranet", upload_dir
                    )
                connection = open_state(base / "state.db")
                try:
                    with patch("ft_auto_uploader._write_network_debug"):
                        reverse = process_file(
                            connection,
                            Mock(),
                            Mock(),
                            destination / "report.pdf",
                            upload_dir / "_uploaded",
                            1,
                            environment="intranet",
                        )
                finally:
                    connection.close()
            finally:
                session.executor.shutdown(wait=False, cancel_futures=True)

        self.assertEqual([record["name"] for record in records], ["report.pdf"])
        self.assertEqual(result, {"downloaded": 1, "skipped": 0, "failed": 0})
        self.assertEqual(reverse, "downloaded")


class CredentialPersistenceTest(unittest.TestCase):
    def test_successful_automatic_login_saves_credentials(self):
        app = CaptchaLabApp.__new__(CaptchaLabApp)
        app.remember_credentials = Mock()
        app.remember_credentials.get.return_value = True
        app.base_dir = Path("app-data")
        app.username = Mock()
        app.username.get.return_value = "user"
        app.password = Mock()
        app.password.get.return_value = "password"
        app._log = Mock()

        with patch("captcha_lab.save_credentials") as save:
            app._after_login("ورود موفق؛ CAPTCHA: 12345")

        save.assert_called_once_with(Path("app-data"), "user", "password")
        logged_messages = [call.args[1] for call in app._log.call_args_list]
        self.assertNotIn("password", " ".join(logged_messages))

    def test_unverified_existing_session_does_not_save_credentials(self):
        app = CaptchaLabApp.__new__(CaptchaLabApp)
        app.remember_credentials = Mock()
        app.remember_credentials.get.return_value = True

        with patch("captcha_lab.save_credentials") as save:
            app._after_login("ورود قبلاً انجام شده است.")

        save.assert_not_called()


class OrganizationalUiTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = tk.Tk()
        self.root.withdraw()
        with patch("captcha_lab.app_data_dir", return_value=Path(self.temporary.name)):
            self.app = CaptchaLabApp(self.root)
        self.root.update_idletasks()

    def tearDown(self):
        self.app.browser.executor.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()
        self.temporary.cleanup()

    def test_workflow_is_separated_into_named_tabs(self):
        names = [self.app.notebook.tab(tab, "text").strip() for tab in self.app.notebook.tabs()]
        self.assertEqual(
            names,
            [
                "اتصال و CAPTCHA",
                "داده و آموزش",
                "ورود سامانه",
                "آپلود فایل",
                "خروجی و گزارش",
            ],
        )

    def test_every_action_button_has_a_tooltip(self):
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)

        buttons = [
            widget
            for widget in descendants(self.root)
            if isinstance(widget, ttk.Button)
        ]
        self.assertGreaterEqual(len(buttons), 15)
        self.assertTrue(all(getattr(button, "_tooltip_text", "") for button in buttons))

    def test_log_uses_professional_dark_palette(self):
        self.assertEqual(self.app.output.cget("background"), "#111827")
        self.assertEqual(self.app.output.tag_cget("success", "foreground"), "#6CE9A6")


if __name__ == "__main__":
    unittest.main()
