import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, patch

from ft_auto_uploader import (
    _build_api_console_script,
    _build_upload_console_script,
    _multipart_file_field,
    _write_network_debug,
    find_ready_files,
    list_upload_history,
    open_state,
    process_file,
    UnsupportedUploadError,
    parse_args,
    parse_transfer_name,
    scan_result,
    select_files_for_run,
    staged_transfer_path,
    tagged_transfer_name,
    upload_file,
    wait_for_file_manager,
)


class NetworkDebugTest(unittest.TestCase):
    def test_upload_script_uses_session_without_storing_sensitive_data(self):
        script = _build_upload_console_script("https://ft.nioc.ir/upload", "upload")

        self.assertIn("credentials: 'include'", script)
        self.assertIn('body.append("upload", file, file.name)', script)
        self.assertIn("response.status", script)
        self.assertNotIn("authorization", script.lower())
        self.assertNotIn("cookie", script.lower())

    def test_queue_script_contains_required_payload(self):
        script = _build_api_console_script(
            "Queue", "https://ft.nioc.ir/queue", "POST", {"groupId": "G1", "scanFileId": "S1"}
        )

        self.assertIn('"groupId":"G1"', script)
        self.assertIn('"scanFileId":"S1"', script)
        self.assertIn("responseText", script)

    def test_extracts_file_field_without_copying_body(self):
        request = Mock()
        request.post_data_buffer = (
            b'Content-Disposition: form-data; name="document"; '
            b'filename="report.pdf"\r\n\r\nprivate bytes'
        )

        self.assertEqual(_multipart_file_field(request), "document")

    def test_debug_file_is_reset_then_appended(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "ft_auto_uploader.app_data_dir", return_value=Path(temp_dir)
        ):
            _write_network_debug("first", reset=True)
            _write_network_debug("second")
            text = (Path(temp_dir) / "last-upload-network.console.txt").read_text(
                encoding="utf-8"
            )

        self.assertEqual(text, "first\n\nsecond\n\n")


class ReadyFilesTest(unittest.TestCase):
    def test_only_unchanged_complete_files_become_ready(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            complete = folder / "report.pdf"
            incomplete = folder / "download.crdownload"
            complete.write_bytes(b"complete")
            incomplete.write_bytes(b"partial")
            observations = {}

            self.assertEqual(find_ready_files(folder, observations, 5, now=10), [])
            self.assertEqual(
                find_ready_files(folder, observations, 5, now=15), [complete]
            )

    def test_count_and_size_limits_are_applied(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            small = folder / "a.pdf"
            medium = folder / "b.pdf"
            large = folder / "c.pdf"
            small.write_bytes(b"a" * 10)
            medium.write_bytes(b"b" * 20)
            large.write_bytes(b"c" * 30)

            selected, oversized = select_files_for_run(
                [small, medium, large], max_files=1, max_size_bytes=25
            )

            self.assertEqual(selected, [small])
            self.assertEqual(oversized, [large])

    def test_cli_accepts_upload_limits_and_run_once(self):
        with patch(
            "sys.argv",
            [
                "ft_auto_uploader.py",
                "--watch-dir",
                "C:\\FT-Test",
                "--environment",
                "thinclient",
                "--max-files-per-run",
                "5",
                "--max-file-size-mb",
                "12.5",
                "--run-once",
            ],
        ):
            args = parse_args()

        self.assertEqual(args.max_files_per_run, 5)
        self.assertEqual(args.max_file_size_mb, 12.5)
        self.assertTrue(args.run_once)

    def test_transfer_names_are_explicit_and_staging_preserves_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "report.pdf"
            source.write_bytes(b"content")

            with staged_transfer_path(source, "thinclient") as staged:
                self.assertEqual(staged.name, "__FT_TC__report.pdf")
                self.assertEqual(staged.read_bytes(), b"content")
            self.assertFalse(staged.exists())

        self.assertEqual(
            parse_transfer_name(tagged_transfer_name("report.pdf", "intranet")),
            ("intranet", "report.pdf"),
        )
        self.assertIsNone(parse_transfer_name("report.pdf"))


class UploadHistoryTest(unittest.TestCase):
    def test_history_is_newest_first(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = open_state(Path(temp_dir) / "state.db")
            connection.executemany(
                "INSERT INTO uploads VALUES (?, ?, ?, ?, ?)",
                [
                    ("a", "old.pdf", "1", "queued", 10),
                    ("b", "new.pdf", "2", "uploaded", 20),
                ],
            )
            rows = list_upload_history(connection)
            connection.close()

        self.assertEqual(rows, [("new.pdf", "uploaded", 20), ("old.pdf", "queued", 10)])


class ScanResultTest(unittest.TestCase):
    def test_only_explicit_clean_result_is_accepted(self):
        clean = {
            "fileScanState": "Finished",
            "isFinished": True,
            "isInfected": False,
            "isRejected": False,
            "avResults": [{"wasInfected": False}, {"wasInfected": False}],
        }
        unknown = {**clean, "isInfected": None}
        infected = {**clean, "avResults": [{"wasInfected": True}]}

        self.assertEqual(scan_result(clean), "clean")
        self.assertEqual(scan_result(unknown), "unknown")
        self.assertEqual(scan_result(infected), "rejected")


class UploadInteractionTest(unittest.TestCase):
    def test_ambiguous_upload_is_not_retried_after_restart_and_rename(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            source = folder / "first.xlsx"
            source.write_bytes(b"same contents")
            database = folder / "state.db"
            with (
                patch("ft_auto_uploader._write_network_debug"),
                patch("ft_auto_uploader.upload_file", side_effect=RuntimeError("no scan ID")) as upload,
                patch("ft_auto_uploader.wait_for_scan") as scan,
            ):
                connection = open_state(database)
                try:
                    with self.assertRaisesRegex(RuntimeError, "no scan ID"):
                        process_file(connection, Mock(), Mock(), source, folder / "_uploaded", 30, Mock(), "intranet")
                finally:
                    connection.close()
                renamed = source.rename(folder / "renamed.xlsx")
                connection = open_state(database)
                try:
                    outcome = process_file(connection, Mock(), Mock(), renamed, folder / "_uploaded", 30, Mock(), "intranet")
                finally:
                    connection.close()
                self.assertEqual(outcome, "upload_pending")
                upload.assert_called_once()
                scan.assert_not_called()
                self.assertTrue(renamed.exists())

    def test_unsupported_extension_is_skipped_without_repeating_upload(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            source = folder / "document.json"
            source.write_bytes(b"{}")
            connection = open_state(folder / "state.db")
            try:
                with (
                    patch("ft_auto_uploader._write_network_debug"),
                    patch("ft_auto_uploader.upload_file", side_effect=UnsupportedUploadError("unsupported")) as upload,
                    patch("ft_auto_uploader.wait_for_scan") as scan,
                ):
                    for _ in range(2):
                        result = process_file(connection, Mock(), Mock(), source,
                                              folder / "_uploaded", 30, Mock(), "thinclient")
                        self.assertEqual(result, "unsupported")
                    upload.assert_called_once()
                    scan.assert_not_called()
                    self.assertTrue(source.exists())
            finally:
                connection.close()
    def test_file_chooser_and_visible_confirmation_are_handled(self):
        page = Mock()
        upload_button = Mock()
        confirmation_button = Mock()
        page.locator.side_effect = [upload_button, confirmation_button]

        chooser_context = MagicMock()
        chooser_event = Mock()
        chooser = Mock()
        chooser_event.value = chooser
        chooser_context.__enter__.return_value = chooser_event
        page.expect_file_chooser.return_value = chooser_context

        response_context = MagicMock()
        response_event = Mock()
        response = Mock(ok=True)
        response.url = "https://ft.nioc.ir/sarp-api/api/Scan/UploadFile"
        response.request.method = "POST"
        response.request.url = "https://ft.nioc.ir/sarp-api/api/Scan/UploadFile"
        response.json.return_value = {"scanFileId": "scan-1"}
        response_event.value = response
        response_context.__enter__.return_value = response_event
        page.expect_response.return_value = response_context
        page.on.side_effect = lambda _event, handler: handler(response)

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "document.pdf"
            source.write_bytes(b"document")
            with (
                patch("ft_auto_uploader._multipart_file_field", return_value="file"),
                patch("ft_auto_uploader._write_network_debug"),
            ):
                result = upload_file(page, source, "thinclient")
            staged_path = Path(chooser.set_files.call_args.args[0])

        page.expect_file_chooser.assert_called_once_with(timeout=10_000)
        upload_button.first.click.assert_called_once_with()
        self.assertEqual(staged_path.name, "__FT_TC__document.pdf")
        self.assertFalse(staged_path.exists())
        confirmation_button.get_by_role.assert_called_once_with("button", name="اسکن", exact=True)
        confirmation_button.get_by_role.return_value.click.assert_called_once_with()
        self.assertEqual(result, "scan-1")

    def test_failed_upload_cancels_visible_dialog(self):
        page = Mock()
        cancel = page.locator.return_value.get_by_role.return_value
        cancel.count.return_value = 1
        cancel.is_visible.return_value = True
        with patch("ft_auto_uploader._upload_file", side_effect=RuntimeError("upload rejected")):
            with self.assertRaisesRegex(RuntimeError, "upload rejected"):
                upload_file(page, Path("document.json"), "thinclient")
        cancel.click.assert_called_once_with(timeout=5_000)
        cancel.wait_for.assert_called_once_with(state="hidden", timeout=5_000)

    def test_extension_rejection_cancels_without_waiting_for_api(self):
        page = Mock()
        chooser_context = MagicMock()
        page.expect_file_chooser.return_value = chooser_context
        cancel = page.locator.return_value.get_by_role.return_value
        cancel.count.return_value = 1
        cancel.is_visible.return_value = True
        page.get_by_text.return_value.first.is_visible.return_value = True
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "document.json"
            source.write_bytes(b"{}")
            with self.assertRaisesRegex(RuntimeError, "پسوند"):
                upload_file(page, source, "thinclient")
        cancel.click.assert_any_call(timeout=5_000)
        page.wait_for_timeout.assert_not_called()
        page.remove_listener.assert_called_once()


class FileManagerLoginTest(unittest.TestCase):
    def test_ready_file_manager_does_not_call_login_handler(self):
        page = Mock()
        page.url = "https://ft.nioc.ir/filemanager/app/files/root"
        upload_button = page.locator.return_value.first
        login_handler = Mock()

        wait_for_file_manager(page, 60_000, login_handler)

        page.goto.assert_not_called()
        upload_button.wait_for.assert_called_once_with(
            state="visible", timeout=10_000
        )
        login_handler.assert_not_called()

    def test_non_file_manager_page_opens_file_manager_before_waiting(self):
        page = Mock()
        page.url = "https://ft.nioc.ir/dargah/systems?grp=all"
        upload_button = page.locator.return_value.first
        login_handler = Mock()

        wait_for_file_manager(page, 60_000, login_handler)

        page.goto.assert_called_once_with(
            "https://ft.nioc.ir/filemanager/app/files/00000000-0000-0000-0000-000000000000",
            wait_until="domcontentloaded",
        )
        login_handler.assert_not_called()

    def test_login_handler_runs_after_initial_wait_fails(self):
        page = Mock()
        page.url = "https://ft.nioc.ir/filemanager/app/files/root"
        upload_button = page.locator.return_value.first
        upload_button.wait_for.side_effect = TimeoutError
        login_handler = Mock()

        wait_for_file_manager(page, 60_000, login_handler)

        login_handler.assert_called_once_with(upload_button, 60_000)

    def test_default_handler_preserves_manual_login_wait(self):
        page = Mock()
        page.url = "https://ft.nioc.ir/filemanager/app/files/root"
        upload_button = page.locator.return_value.first
        upload_button.wait_for.side_effect = [TimeoutError, None]

        with patch("builtins.print") as print_mock:
            wait_for_file_manager(page, 60_000)

        self.assertEqual(
            upload_button.wait_for.call_args_list,
            [
                call(state="visible", timeout=10_000),
                call(state="visible", timeout=60_000),
            ],
        )
        print_mock.assert_called_once_with(
            "ورود لازم است؛ لطفاً در مرورگر وارد شوید و CAPTCHA را دستی وارد کنید."
        )


if __name__ == "__main__":
    unittest.main()
