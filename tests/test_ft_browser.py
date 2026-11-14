import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ft_browser import app_data_dir, launch_persistent_session


class AppDataDirectoryTest(unittest.TestCase):
    def test_uses_local_app_data_when_available(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict("os.environ", {"LOCALAPPDATA": temporary}):
                self.assertEqual(
                    app_data_dir(),
                    Path(temporary) / "ft-nioc-uploader",
                )


class PersistentSessionTest(unittest.TestCase):
    def test_reuses_first_existing_page(self):
        playwright = Mock()
        context = playwright.chromium.launch_persistent_context.return_value
        existing_page = Mock()
        context.pages = [existing_page]

        with tempfile.TemporaryDirectory() as temporary:
            profile_dir = Path(temporary) / "browser-profile"
            actual_context, actual_page = launch_persistent_session(
                playwright, profile_dir
            )

        playwright.chromium.launch_persistent_context.assert_called_once_with(
            str(profile_dir), headless=False
        )
        self.assertIs(actual_context, context)
        self.assertIs(actual_page, existing_page)
        context.new_page.assert_not_called()

    def test_creates_page_when_context_has_none(self):
        playwright = Mock()
        context = playwright.chromium.launch_persistent_context.return_value
        context.pages = []

        with tempfile.TemporaryDirectory() as temporary:
            profile_dir = Path(temporary) / "browser-profile"
            _, actual_page = launch_persistent_session(playwright, profile_dir)

        self.assertIs(actual_page, context.new_page.return_value)
        context.new_page.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
