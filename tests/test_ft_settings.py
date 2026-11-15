import base64
import json
import os
import tempfile
import unittest
from pathlib import Path

from ft_settings import (
    AppSettings,
    CREDENTIALS_FILENAME,
    delete_credentials,
    load_credentials,
    load_settings,
    save_credentials,
    save_settings,
)


class SettingsTest(unittest.TestCase):
    def test_missing_settings_use_documented_defaults(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = load_settings(Path(temporary))

        self.assertEqual(settings.reload_delay_seconds, 1.0)
        self.assertEqual(settings.batch_count, 1000)
        self.assertTrue(settings.auto_upload_enabled)

    def test_settings_round_trip_and_ignore_future_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            base_dir = Path(temporary)
            expected = AppSettings(
                selector="img.captcha",
                sequence_length=5,
                batch_count=25,
                reload_delay_seconds=2.5,
                epochs=12,
                minimum_confidence=0.8,
            )
            path = save_settings(base_dir, expected)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["future_option"] = True
            path.write_text(json.dumps(payload), encoding="utf-8")

            actual = load_settings(base_dir)

        self.assertEqual(actual, expected)

    def test_invalid_settings_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            base_dir = Path(temporary)
            path = save_settings(base_dir, AppSettings())
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["reload_delay_seconds"] = 61
            path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "مقادیر"):
                load_settings(base_dir)


@unittest.skipUnless(os.name == "nt", "Windows DPAPI is required")
class CredentialStoreTest(unittest.TestCase):
    def test_credentials_round_trip_without_plaintext_on_disk(self):
        with tempfile.TemporaryDirectory() as temporary:
            base_dir = Path(temporary)
            username = "کاربر-آزمایشی"
            password = "not-a-real-password-123"

            path = save_credentials(base_dir, username, password)
            stored = path.read_bytes()
            protected = base64.b64decode(stored, validate=True)

            self.assertNotIn(username.encode("utf-8"), stored)
            self.assertNotIn(password.encode("utf-8"), stored)
            self.assertNotIn(username.encode("utf-8"), protected)
            self.assertNotIn(password.encode("utf-8"), protected)
            self.assertEqual(load_credentials(base_dir), (username, password))

    def test_delete_credentials_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            base_dir = Path(temporary)
            save_credentials(base_dir, "user", "password")

            self.assertTrue(delete_credentials(base_dir))
            self.assertFalse((base_dir / CREDENTIALS_FILENAME).exists())
            self.assertFalse(delete_credentials(base_dir))


if __name__ == "__main__":
    unittest.main()
