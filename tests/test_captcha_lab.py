import base64
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from unittest.mock import Mock

from captcha_lab import BrowserSession, capture_captcha_bytes


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
    def test_collects_requested_images_and_refreshes_between_them(self):
        with tempfile.TemporaryDirectory() as temporary:
            session = BrowserSession(Path(temporary) / "profile", queue.Queue())
            session.page = Mock()
            batch_dir = Path(temporary) / "batch"
            try:
                with patch("captcha_lab.capture_captcha_bytes", return_value=b"png") as capture:
                    result = session.collect_batch("img.captcha", batch_dir, 3)
            finally:
                session.executor.shutdown(wait=False, cancel_futures=True)

            self.assertEqual(result["count"], 3)
            self.assertEqual(capture.call_count, 3)
            self.assertEqual(session.page.reload.call_count, 2)
            self.assertEqual(len(list((batch_dir / "images").glob("*.png"))), 3)


if __name__ == "__main__":
    unittest.main()
