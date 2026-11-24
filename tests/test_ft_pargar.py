import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from ft_pargar import PargarClient, PargarError


class PargarClientTest(unittest.TestCase):
    def setUp(self):
        self.client = PargarClient("https://pargar.example/Fonix/", "user", "token")

    def test_rejects_missing_api_credentials(self):
        with self.assertRaises(PargarError):
            PargarClient("https://pargar.example/Fonix", "", "token")

    def test_send_wraps_non_zip_file_and_uses_original_name(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "نامه.pdf"
            source.write_bytes(b"sample document")
            captured = {}

            def capture(message, attachment, upload_name):
                captured.update(message=message, name=upload_name)
                with zipfile.ZipFile(attachment) as archive:
                    captured["members"] = archive.namelist()
                    captured["content"] = archive.read(source.name)
                return {"Succeeded": True, "MailId": "id"}

            sender = {"Contactid": "sender-id"}
            receiver = {"Contactid": "receiver-id"}
            with patch.object(self.client, "_send_multipart", side_effect=capture):
                result = self.client.send_file(source, sender, [receiver], "برای {filename}")

        self.assertEqual(result["MailId"], "id")
        self.assertEqual(captured["name"], "نامه.pdf.zip")
        self.assertEqual(captured["members"], ["نامه.pdf"])
        self.assertEqual(captured["content"], b"sample document")
        self.assertEqual(captured["message"]["Subject"], "نامه.pdf")
        self.assertEqual(captured["message"]["TextBody"], "برای نامه.pdf")
        self.assertEqual(captured["message"]["Sender"], {"ContactId": "sender-id"})
        self.assertEqual(captured["message"]["Receivers"], [{"ContactId": "receiver-id"}])

    def test_existing_zip_is_attached_without_nesting(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "already.zip"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("document.txt", "content")
            original_zip = source.read_bytes()
            captured = {}

            def capture(_message, attachment, upload_name):
                captured["path"] = attachment
                captured["bytes"] = attachment.read_bytes()
                captured["name"] = upload_name
                return {"Succeeded": True, "MailId": "id"}

            with patch.object(self.client, "_send_multipart", side_effect=capture):
                self.client.send_file(source, {"Contactid": "s"}, [{"Contactid": "r"}], "{filename}")

            self.assertNotEqual(captured["path"], source)
            self.assertEqual(captured["bytes"], original_zip)
            self.assertEqual(captured["name"], "already.zip")

    def test_temporary_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "partial.part"
            source.write_bytes(b"partial")
            with self.assertRaises(PargarError):
                self.client.send_file(source, {"Contactid": "s"}, [{"Contactid": "r"}], "{filename}")


if __name__ == "__main__":
    unittest.main()
