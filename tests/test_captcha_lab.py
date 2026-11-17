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
from ft_runtime import BrowserSession, RuntimeResult, capture_captcha_bytes
from ft_settings import load_settings


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
    def test_download_api_rejects_html_and_redirects(self):
        html = Mock(ok=True, status=200, headers={"content-type": "text/html"})
        html.json.side_effect = ValueError("Expecting value")
        redirect = Mock(ok=False, status=302)

        for response, expected in [(html, "HTML"), (redirect, "HTTP 302")]:
            with self.subTest(status=response.status):
                with self.assertRaisesRegex(RuntimeError, expected):
                    BrowserSession._response_data(response, "دریافت گروه‌های فایل")

        redirect.json.assert_not_called()

    def test_batch_prediction_result_displays_exact_match_rate(self):
        app = CaptchaLabApp.__new__(CaptchaLabApp)
        app.results = queue.Queue()
        app.results.put(
            RuntimeResult(
                "predict_batch",
                "predict_batch",
                True,
                {"count": 2, "evaluated": 2, "matches": 1, "exact_match": 0.5},
            )
        )
        app.callbacks = {}
        app.upload_task_id = None
        app.download_task_id = None
        app.status = Mock()
        app._log = Mock()
        app.root = Mock()

        app._poll_results()

        message = "تست Batch تمام شد؛ 2 تصویر — تطبیق دقیق: 1 از 2 (50.0%)"
        app.status.set.assert_called_once_with(message)
        app._log.assert_called_once_with("success", message)

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

    def test_auto_download_uses_configured_interval_and_skips_overlap(self):
        app = CaptchaLabApp.__new__(CaptchaLabApp)
        app.root = Mock()
        app.closing = False
        app.auto_download_suspended = False
        app.auto_download_enabled = Mock()
        app.auto_download_enabled.get.return_value = True
        app.download_interval_seconds = Mock()
        app.download_interval_seconds.get.return_value = 30
        app._download_available = Mock()

        app._auto_download_tick()

        app._download_available.assert_called_once_with(automatic=True)
        app.root.after.assert_called_once_with(30_000, app._auto_download_tick)

        app.download_task_id = "download-1"
        app._environment_code = Mock(return_value="intranet")
        app.download_dir = Mock()
        app.browser = Mock()
        app._download_available = CaptchaLabApp._download_available.__get__(app)
        app._download_available(automatic=True)
        app.browser.download_available_files.assert_not_called()

    def test_thinclient_test_mode_starts_and_repeats_downloads(self):
        app = CaptchaLabApp.__new__(CaptchaLabApp)
        app.root = Mock()
        app.closing = False
        app.auto_download_suspended = True
        app.auto_download_enabled = Mock()
        app.auto_download_enabled.get.return_value = False
        app.thinclient_test_download = Mock()
        app.thinclient_test_download.get.return_value = True
        app._environment_code = Mock(return_value="thinclient")
        app.download_dir = Mock()
        app.watch_dir = Mock()
        app.watch_dir.get.return_value = ""
        app.download_task_id = None
        app._download_interval_ms = Mock(return_value=10_000)
        app._log = Mock()
        app._save_preferences = Mock()
        app.browser = Mock()
        app._submit = Mock(return_value="download-1")
        destination = Path("downloads")

        with patch("captcha_lab._existing_directory", return_value=destination):
            app._toggle_test_download()
            app._auto_download_tick()
            app._submit.assert_called_once_with(
                "auto_download_files", app.browser.download_available_files,
                "thinclient", destination, None, True, app.download_cancelled,
                success=app._after_auto_download, announce=False,
            )
            app.download_task_id = None
            app._auto_download_tick()
            self.assertEqual(app._submit.call_count, 2)
            app.download_task_id = None
            app.thinclient_test_download.get.return_value = False
            app._auto_download_tick()
            self.assertEqual(app._submit.call_count, 2)

    def test_download_available_combines_listing_and_download(self):
        session = BrowserSession(Path("profile"), queue.Queue())
        records = [{"remote_id": "file-1"}]
        expected = {"downloaded": 1, "skipped": 0, "failed": 0}
        session.list_downloadable_files = Mock(return_value=records)
        session.download_files = Mock(return_value=expected)
        try:
            actual = session.download_available_files(
                "intranet", Path("downloads"), Path("uploads")
            )
        finally:
            session.executor.shutdown(wait=False, cancel_futures=True)

        self.assertEqual(actual, expected)
        session.list_downloadable_files.assert_called_once_with("intranet")
        session.download_files.assert_called_once_with(
            records, Path("downloads"), "intranet", Path("uploads")
        )

    def test_stopping_queued_download_skips_listing(self):
        session = BrowserSession(Path("profile"), queue.Queue())
        session.list_downloadable_files = Mock()
        cancelled = Mock()
        cancelled.is_set.return_value = True
        with session.executor:
            result = session.download_available_files(
                "thinclient", Path("downloads"), cancelled=cancelled,
            )
        session.list_downloadable_files.assert_not_called()
        self.assertEqual(result, {"downloaded": 0, "skipped": 0, "failed": 0})

    def test_only_confirmed_auto_login_is_saved(self):
        app = CaptchaLabApp.__new__(CaptchaLabApp)
        app.base_dir = Path("profile")
        app.credential_status = Mock()
        app._log = Mock()
        with patch("captcha_lab.save_credentials") as save:
            app._after_login("ورود قبلاً انجام شده است.", "user", "password")
            save.assert_not_called()
            app._after_login("ورود موفق", "user", "password")
            save.assert_called_once_with(app.base_dir, "user", "password")

    def test_save_entered_credentials_works_before_login(self):
        app = CaptchaLabApp.__new__(CaptchaLabApp)
        app.base_dir = Path("profile")
        app.username = Mock()
        app.username.get.return_value = "user"
        app.password = Mock()
        app.password.get.return_value = "password"
        app.credential_status = Mock()
        app._log = Mock()
        with patch("captcha_lab.save_credentials") as save:
            app._save_entered_credentials()
        save.assert_called_once_with(app.base_dir, "user", "password")
        app.credential_status.set.assert_called_once()

    def test_refresh_downloads_enables_thinclient_loopback_only_when_selected(self):
        app = CaptchaLabApp.__new__(CaptchaLabApp)
        app.download_task_id = None
        app.browser = Mock()
        app._submit = Mock(return_value="download-1")
        app._environment_code = Mock(return_value="thinclient")
        app.thinclient_test_download = Mock()
        app.thinclient_test_download.get.return_value = True

        app._refresh_downloads()

        app._submit.assert_called_once_with(
            "list_downloadable_files",
            app.browser.list_downloadable_files,
            "thinclient",
            15.0,
            True,
            success=app._display_download_files,
        )

    def test_download_folder_picker_falls_back_from_invalid_saved_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = CaptchaLabApp.__new__(CaptchaLabApp)
            app.base_dir = Path(temporary)
            app.download_dir = Mock()
            app.download_dir.get.return_value = str(Path(temporary) / "missing")
            app._save_preferences = Mock()

            with patch("captcha_lab.filedialog.askdirectory", return_value="") as choose:
                app._choose_download_folder()

        choose.assert_called_once_with(
            title="پوشه مقصد دانلود را انتخاب کنید",
            initialdir=temporary,
        )
        app.download_dir.set.assert_not_called()

    def test_empty_upload_folder_is_not_treated_as_current_directory(self):
        app = CaptchaLabApp.__new__(CaptchaLabApp)
        app.upload_task_id = None
        app.watch_dir = Mock()
        app.watch_dir.get.return_value = ""
        app.max_files_per_run = Mock()
        app.max_files_per_run.get.return_value = 10
        app.max_file_size_mb = Mock()
        app.max_file_size_mb.get.return_value = 100
        app.browser = Mock()

        with patch("captcha_lab.messagebox.showerror") as showerror:
            app._upload_folder()

        showerror.assert_called_once_with("خطا", "ابتدا یک پوشه معتبر انتخاب کنید.")
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
        page.is_closed.return_value = False
        page.url = "about:blank"
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

    def test_failed_browser_launch_stops_playwright_before_retry(self):
        session = BrowserSession(Path("profile"), queue.Queue())
        failed_playwright, retry_playwright = Mock(), Mock()
        sync_api = types.ModuleType("playwright.sync_api")
        starter = Mock()
        starter.start.side_effect = [failed_playwright, retry_playwright]
        sync_api.sync_playwright = Mock(return_value=starter)
        context, page = Mock(), Mock()
        page.url = "about:blank"

        with (
            patch.dict(sys.modules, {"playwright.sync_api": sync_api}),
            patch(
                "ft_runtime.launch_persistent_session",
                side_effect=[RuntimeError("Chromium launch failed"), (context, page)],
            ) as launch,
            session.executor as worker,
        ):
            try:
                with self.assertRaisesRegex(RuntimeError, "Chromium launch failed"):
                    worker.submit(session.open).result()
                failed_playwright.stop.assert_called_once_with()
                self.assertIsNone(session.playwright)
                self.assertIsNone(session.page)

                worker.submit(session.open).result()
                launch.assert_called_with(retry_playwright, Path("profile"))
                self.assertIs(session.page, page)
            finally:
                worker.submit(session.close).result()

    def test_reopens_browser_after_context_close(self):
        session = BrowserSession(Path("profile"), queue.Queue())
        old_driver, new_driver = Mock(), Mock()
        new_context, new_page = Mock(), Mock()
        new_page.url = "about:blank"
        session.playwright = old_driver
        session.context = Mock()
        session.page = Mock()
        session._on_context_closed(session.context)
        sync_api = types.ModuleType("playwright.sync_api")
        sync_api.sync_playwright = Mock()
        sync_api.sync_playwright.return_value.start.return_value = new_driver
        with (
            patch.dict(sys.modules, {"playwright.sync_api": sync_api}),
            patch("ft_runtime.launch_persistent_session", return_value=(new_context, new_page)),
            session.executor as worker,
        ):
            try:
                worker.submit(session.open).result()
                old_driver.stop.assert_called_once_with()
                self.assertIs(session.page, new_page)
                new_page.goto.assert_called_once()
            finally:
                worker.submit(session.close).result()

    def test_start_resumes_existing_ft_session_from_sign_in_prompt(self):
        session = BrowserSession(Path("profile"), queue.Queue())
        session.context, session.page = Mock(), Mock()
        page = session.page
        page.is_closed.return_value = False
        page.url = "https://ft.nioc.ir/dargah/user/sign-in"
        page.get_by_text.return_value.is_visible.return_value = True
        page.goto.side_effect = lambda url, **_kwargs: setattr(page, "url", url)
        with (
            session.executor,
            patch("ft_runtime.load_model") as load,
        ):
            result = session.login("", "", "img", Path("model"), 0.3, 1)
        self.assertEqual(result, "ورود قبلاً انجام شده است.")
        self.assertTrue(page.goto.call_args.args[0].startswith("https://ft.nioc.ir/filemanager/"))
        page.goto.assert_called_once()
        load.assert_not_called()

    def test_manual_login_reuses_open_browser_page(self):
        session = BrowserSession(Path("profile"), queue.Queue())
        session.context = Mock()
        session.page = Mock()
        session.page.is_closed.return_value = False
        session.page.url = "https://ft.nioc.ir/dargah/user/sign-in"
        session.page.get_by_text.return_value.is_visible.return_value = False
        try:
            message = session.manual_login()
        finally:
            session.executor.shutdown(wait=False, cancel_futures=True)

        session.page.goto.assert_called_once_with(
            "https://ft.nioc.ir/dargah/user/sign-in",
            wait_until="domcontentloaded",
        )
        self.assertIn("ورود دستی", message)

    def test_automatic_login_uses_configured_attempt_limit(self):
        session = BrowserSession(Path("profile"), queue.Queue())
        session.context = Mock()
        session.page = Mock()
        session.page.is_closed.return_value = False
        session.page.url = "https://ft.nioc.ir/dargah/user/sign-in"
        session.page.get_by_text.return_value.is_visible.return_value = False
        session.page.wait_for_url.side_effect = RuntimeError("wrong captcha")
        try:
            with (
                patch("ft_runtime.load_model", return_value=object()),
                patch("ft_runtime.capture_captcha_bytes", return_value=b"captcha"),
                patch("ft_runtime.predict_image", return_value=("1234", 0.9)) as predict,
            ):
                with self.assertRaisesRegex(RuntimeError, "پس از 5 تلاش"):
                    session.login(
                        "username",
                        "password",
                        "img.captcha",
                        Path("model"),
                        0.3,
                        5,
                        2.5,
                    )
        finally:
            session.executor.shutdown(wait=False, cancel_futures=True)

        self.assertEqual(predict.call_count, 5)
        self.assertEqual(session.page.goto.call_count, 4)
        self.assertEqual(session.page.wait_for_timeout.call_args_list, [call(2500)] * 5)

    def test_low_confidence_captcha_is_filled_without_submission(self):
        session = BrowserSession(Path("profile"), queue.Queue())
        session.context, session.page = Mock(), Mock()
        session.page.is_closed.return_value = False
        session.page.url = "https://ft.nioc.ir/dargah/user/sign-in"
        session.page.get_by_text.return_value.is_visible.return_value = False
        captcha_input = Mock()
        session.page.locator.side_effect = [Mock(), Mock(), Mock(), captcha_input]
        with (
            session.executor,
            patch("ft_runtime.load_model"),
            patch("ft_runtime.capture_captcha_bytes"),
            patch("ft_runtime.predict_image", return_value=("0123", 0.2)),
        ):
            with self.assertRaisesRegex(RuntimeError, "پس از 1 تلاش"):
                session.login("username", "password", "img", Path("model"), 0.3, 1)
        captcha_input.fill.assert_called_once_with("0123")
        captcha_input.press.assert_not_called()
        session.page.wait_for_timeout.assert_not_called()
        session.page.reload.assert_not_called()

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
            session.page.evaluate.return_value = "test-role"
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
        self.assertEqual(
            session.context.request.get.call_args_list,
            [
                call("https://ft.nioc.ir/webapi/api/v1/Group/GetAllUserGroups",
                     headers={"X-Role": "test-role"}, max_redirects=0),
                call("https://ft.nioc.ir/webapi/api/v1/File/DownloadFile",
                     headers={"X-Role": "test-role"}, max_redirects=0,
                     params={"fileId": "remote-1", "groupId": "group-1"},
                     timeout=120_000),
            ],
        )
        listing_url, = session.context.request.put.call_args.args
        self.assertEqual(listing_url, "https://ft.nioc.ir/webapi/api/v1/File/GetFilesAndFolders")
        self.assertEqual(session.context.request.put.call_args.kwargs["headers"], {"X-Role": "test-role"})
        self.assertEqual(result, {"downloaded": 1, "skipped": 0, "failed": 0})
        self.assertEqual(reverse, "downloaded")

    def test_thinclient_loopback_downloads_its_own_uploaded_file_for_testing(self):
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
                    "totalCount": 1,
                    "items": [
                        {
                            "id": "remote-1",
                            "groupId": "group-1",
                            "name": "__FT_TC__report",
                            "extension": ".pdf",
                            "size": 7,
                            "directory": False,
                        }
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
                    records = session.list_downloadable_files(
                        "thinclient", allow_same_origin=True
                    )
                    result = session.download_files(
                        records,
                        destination,
                        "thinclient",
                        upload_dir,
                        allow_same_origin=True,
                    )
                    downloaded_content = (destination / "report.pdf").read_bytes()
            finally:
                session.executor.shutdown(wait=False, cancel_futures=True)

        self.assertEqual([record["name"] for record in records], ["report.pdf"])
        self.assertEqual(result, {"downloaded": 1, "skipped": 0, "failed": 0})
        self.assertEqual(downloaded_content, b"payload")


class CredentialPersistenceTest(unittest.TestCase):
    def test_successful_automatic_login_saves_credentials(self):
        app = CaptchaLabApp.__new__(CaptchaLabApp)
        app.base_dir = Path("app-data")
        app.credential_status = Mock()
        app._log = Mock()

        with patch("captcha_lab.save_credentials") as save:
            app._after_login("ورود موفق؛ CAPTCHA: 12345", "user", "password")

        save.assert_called_once_with(Path("app-data"), "user", "password")
        logged_messages = [call.args[1] for call in app._log.call_args_list]
        self.assertNotIn("password", " ".join(logged_messages))

    def test_unverified_existing_session_does_not_save_credentials(self):
        app = CaptchaLabApp.__new__(CaptchaLabApp)
        with patch("captcha_lab.save_credentials") as save:
            app._after_login("ورود قبلاً انجام شده است.", "user", "password")

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
        for callback in self.root.tk.splitlist(self.root.tk.call("after", "info")):
            self.root.after_cancel(callback)
        self.root.destroy()
        self.temporary.cleanup()

    def test_workflow_is_separated_into_named_tabs(self):
        names = [self.app.notebook.tab(tab, "text").strip() for tab in self.app.notebook.tabs()]
        self.assertEqual(
            names,
            [
                "اطلاعات ورود",
                "آپلود و دانلود",
                "اتصال و CAPTCHA",
                "داده و آموزش",
                "خروجی و گزارش",
            ],
        )
        nested = self.app.transfer_notebook
        self.assertEqual(
            [nested.tab(tab, "text").strip() for tab in nested.tabs()],
            ["آپلود فایل", "دانلود فایل"],
        )

    def test_valid_form_changes_are_saved_automatically(self):
        self.assertEqual(load_settings(self.app.base_dir).captcha_submit_delay_seconds, 1.0)
        self.app.watch_dir.set("upload-folder")
        self.app.download_dir.set("download-folder")
        self.app.captcha_submit_delay_seconds.set(3.5)
        self.app.thinclient_test_download.set(True)
        saved = tk.BooleanVar(self.root, value=False)
        self.root.after(400, lambda: saved.set(True))
        self.root.wait_variable(saved)
        settings = load_settings(self.app.base_dir)
        self.assertEqual(settings.watch_dir, "upload-folder")
        self.assertEqual(settings.download_dir, "download-folder")
        self.assertEqual(settings.captcha_submit_delay_seconds, 3.5)
        self.assertTrue(settings.thinclient_test_download)

    def test_status_bar_distinguishes_running_from_last_status(self):
        self.assertEqual(self.app.activity_status.get(), "آماده")
        self.assertEqual(self.app.status.get(), "آماده")
        self.assertEqual(self.app.status_progress.master.grid_size()[1], 3)

        task_id = "auto_download_files-1"
        self.app.queued_browser_tasks.add(task_id)
        self.app._mark_task_started(task_id)
        self.assertEqual(self.app.activity_status.get(), "در حال اجرا")
        self.assertEqual(self.app.current_work.get(), "1 کار در صف")

        self.app.queued_browser_tasks.remove(task_id)
        self.app._sync_activity_status()
        self.assertEqual(self.app.current_work.get(), "دانلود خودکار")
        self.assertEqual(self.app.status.get(), "آماده")

        self.app.active_tasks.clear()
        self.app._sync_activity_status()
        self.assertEqual(self.app.activity_status.get(), "آماده")

    def test_login_and_training_actions_are_grouped_in_single_rows(self):
        expected_counts = {
            self.app.data_actions: 3,
            self.app.training_actions: 2,
            self.app.login_actions: 2,
            self.app.login_settings_actions: 3,
        }
        for frame, expected_count in expected_counts.items():
            with self.subTest(frame=str(frame)):
                buttons = [
                    child
                    for child in frame.winfo_children()
                    if isinstance(child, ttk.Button)
                ]
                self.assertEqual(len(buttons), expected_count)
                self.assertEqual({button.grid_info()["row"] for button in buttons}, {0})

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
