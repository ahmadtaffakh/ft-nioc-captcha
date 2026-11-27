import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from ft_pargar import (
    AmbiguousSendError,
    CMF_TRUNCATION_MARKER,
    MAX_PARGAR_BODY_CHARS,
    PargarClient,
    PargarError,
    PargarFolderMonitor,
    _prepare_pargar_candidate,
    _state_db,
    inspect_pargar_folder,
    list_pargar_history,
)


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

    def test_cmf_subject_and_body_are_sent_without_template_expansion(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "notice.zip"
            source.write_bytes(b"zip bytes")
            captured = {}

            def capture(message, _attachment, _upload_name):
                captured.update(message)
                return {"Succeeded": True, "MailId": "id"}

            with patch.object(self.client, "_send_multipart", side_effect=capture):
                self.client.send_file(
                    source, {"Contactid": "s"}, [{"Contactid": "r"}], "ignored {filename}",
                    subject="موضوع CMF", body="متن CMF شامل {filename}",
                )

        self.assertEqual(captured["Subject"], "موضوع CMF")
        self.assertEqual(captured["TextBody"], "متن CMF شامل {filename}")


class PargarCmfTest(unittest.TestCase):
    def _write_pair(self, directory, *, name="notice.zip", intent="pargar_auto", **changes):
        package = Path(directory) / name
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("notice.txt", "valid package")
        digest = hashlib.sha256(package.read_bytes()).hexdigest()
        manifest = {
            "delivery_intent": intent,
            "notice_id": "NOTICE-1",
            "notice_no": "1405222366652",
            "web_datetime": "1405/03/05-09:30",
            "issuer_unit_name": "دادگاه صلح قزوین",
            "subject_type": "ابلاغیه تبادل لوایح",
            "parties": [{"name": "اسم طرف"}],
            "package": {"filename": name, "size": package.stat().st_size, "sha256": digest},
            "consumers": {"pargar": {"attachment": name, "subject": "موضوع", "body": "متن"}},
        }
        for key, value in changes.items():
            section, field = key.split("__", 1)
            manifest[section][field] = value
        package.with_suffix(".cmf").write_text(json.dumps(manifest), encoding="utf-8")
        return package

    def test_prepares_valid_pargar_delivery_from_cmf(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self._write_pair(directory)
            delivery = _prepare_pargar_candidate(package)
            expected_digest = hashlib.sha256(package.read_bytes()).hexdigest()
            expected_body = package.with_suffix(".cmf").read_text(encoding="utf-8")

        self.assertEqual(delivery["notice_id"], "NOTICE-1")
        self.assertEqual(
            delivery["subject"],
            "ابلاغیه شماره 1405222366652 با تاریخ ثبت 1405/03/05 "
            "مرجع دادگاه صلح قزوین موضوع ابلاغیه تبادل لوایح طرف : اسم طرف",
        )
        self.assertEqual(delivery["body"], expected_body)
        self.assertEqual(delivery["sha256"], expected_digest)

    def test_large_cmf_body_is_explicitly_truncated(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self._write_pair(directory)
            manifest_path = package.with_suffix(".cmf")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["padding"] = "x" * (MAX_PARGAR_BODY_CHARS + 500)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            delivery = _prepare_pargar_candidate(package)

        self.assertEqual(len(delivery["body"]), MAX_PARGAR_BODY_CHARS)
        self.assertTrue(delivery["body"].endswith(CMF_TRUNCATION_MARKER))

    def test_subject_requires_notice_metadata_and_party_names(self):
        cases = (
            {"notice_no": ""},
            {"web_datetime": None},
            {"issuer_unit_name": ""},
            {"subject_type": ""},
            {"parties": []},
        )
        for changes in cases:
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as directory:
                package = self._write_pair(directory)
                manifest_path = package.with_suffix(".cmf")
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest.update(changes)
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                with self.assertRaises(PargarError):
                    _prepare_pargar_candidate(package)

    def test_rejects_zip_without_same_named_cmf(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "notice.zip"
            package.write_bytes(b"zip")
            with self.assertRaisesRegex(PargarError, "CMF"):
                _prepare_pargar_candidate(package)

    def test_rejects_catalog_request_and_response_files(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ("CAT-REQ--one.req", "CAT-RSP--one.rsp", "partial.part"):
                path = Path(directory) / name
                path.write_bytes(b"not a package")
                with self.subTest(name=name), self.assertRaises(PargarError):
                    _prepare_pargar_candidate(path)

    def test_rejects_catalog_and_missing_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = self._write_pair(directory, name="CAT-PKG--one.zip")
            with self.assertRaisesRegex(PargarError, "کاتالوگ"):
                _prepare_pargar_candidate(catalog)

            missing = self._write_pair(directory, name="missing.zip", intent=None)
            with self.assertRaisesRegex(PargarError, "pargar_auto"):
                _prepare_pargar_candidate(missing)

            response = self._write_pair(directory, name="response.zip", intent="catalog_response")
            with self.assertRaisesRegex(PargarError, "پاسخ کاتالوگ"):
                _prepare_pargar_candidate(response)

    def test_rejects_attachment_size_and_hash_mismatches(self):
        cases = (
            {"consumers__pargar": {"attachment": "other.zip", "subject": "موضوع", "body": "متن"}},
            {"package__size": 999},
            {"package__sha256": "0" * 64},
        )
        for changes in cases:
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as directory:
                package = self._write_pair(directory, **changes)
                with self.assertRaises(PargarError):
                    _prepare_pargar_candidate(package)

    def test_state_records_notice_id_separately_from_ft_state(self):
        with tempfile.TemporaryDirectory() as directory:
            db = _state_db(Path(directory) / "pargar.sqlite3")
            columns = {row[1] for row in db.execute("PRAGMA table_info(messages)")}
            indexes = {row[1] for row in db.execute("PRAGMA index_list(messages)")}
            db.close()

        self.assertIn("notice_id", columns)
        self.assertIn("messages_notice_hash", indexes)

    def test_folder_inspection_reports_ready_and_rejected_without_state(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            self._write_pair(folder, name="ready.zip")
            self._write_pair(folder, name="missing-intent.zip", intent=None)
            self._write_pair(folder, name="catalog-response.zip", intent="catalog_response")
            (folder / "ignored.txt").write_text("ignored", encoding="utf-8")

            summary = inspect_pargar_folder(folder)
            state_created = (folder / "pargar-state.sqlite3").exists()

        self.assertEqual(summary["zip_files"], 3)
        self.assertEqual(summary["ready"], 1)
        self.assertEqual(summary["rejected"], 2)
        self.assertEqual(sum(summary["reasons"].values()), 2)
        self.assertFalse(state_created)

    def test_monitor_uses_ui_contacts_and_cmf_message(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "incoming"
            data_dir = Path(directory) / "state"
            folder.mkdir()
            package = self._write_pair(folder)
            events = []
            captured = {}
            monitor = PargarFolderMonitor(data_dir, lambda level, message: events.append((level, message)))
            sender = {"Contactid": "sender-from-ui"}
            receivers = [{"Contactid": "receiver-from-ui"}]

            class Client:
                def send_file(self, path, sent_sender, sent_receivers, template, **options):
                    captured.update(
                        path=path, sender=sent_sender, receivers=sent_receivers,
                        template=template, options=options,
                    )
                    monitor.stop()
                    return {"Succeeded": True, "MailId": "MAIL-1"}

            with patch.object(monitor._stop, "wait", return_value=False):
                monitor._run(folder, Client(), sender, receivers, "UI template", 5, True)

            db = _state_db(data_dir / "pargar-state.sqlite3")
            row = db.execute(
                "SELECT notice_id,status,mail_id FROM messages WHERE hash=?",
                (hashlib.sha256(package.read_bytes()).hexdigest(),),
            ).fetchone()
            db.close()

        self.assertEqual(captured["path"], package)
        self.assertIs(captured["sender"], sender)
        self.assertIs(captured["receivers"], receivers)
        self.assertIn("ابلاغیه شماره 1405222366652", captured["options"]["subject"])
        self.assertIn('"notice_id": "NOTICE-1"', captured["options"]["body"])
        self.assertEqual(row, ("NOTICE-1", "sent", "MAIL-1"))
        self.assertTrue(any(level == "success" for level, _message in events))

    def test_monitor_notifies_only_after_confirmed_send(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "incoming"
            data_dir = Path(directory) / "state"
            folder.mkdir()
            package = self._write_pair(folder)
            notifications = []
            monitor = PargarFolderMonitor(
                data_dir,
                lambda *_args: None,
                on_sent=lambda path, mail_id: notifications.append((path, mail_id)),
            )

            class Client:
                def send_file(self, *_args, **_options):
                    monitor.stop()
                    return {"Succeeded": True, "MailId": "MAIL-NOTIFY"}

            with patch.object(monitor._stop, "wait", return_value=False):
                monitor._run(folder, Client(), {}, [{}], "template", 5, True)

        self.assertEqual(notifications, [(package, "MAIL-NOTIFY")])

    def test_monitor_does_not_notify_after_failed_or_ambiguous_send(self):
        for error in (PargarError("failed"), AmbiguousSendError("ambiguous")):
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as directory:
                folder = Path(directory) / "incoming"
                folder.mkdir()
                self._write_pair(folder)
                notifications = []
                monitor = PargarFolderMonitor(
                    Path(directory) / "state",
                    lambda *_args: None,
                    on_sent=lambda *args: notifications.append(args),
                )

                class Client:
                    def send_file(self, *_args, **_options):
                        monitor.stop()
                        raise error

                with patch.object(monitor._stop, "wait", return_value=False):
                    monitor._run(folder, Client(), {}, [{}], "template", 5, True)

            self.assertEqual(notifications, [])

    def test_manual_test_send_also_requires_and_uses_cmf(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "incoming"
            folder.mkdir()
            package = self._write_pair(folder)
            captured = {}

            class Client:
                def send_file(self, path, sender, receivers, template, **options):
                    captured.update(path=path, sender=sender, receivers=receivers, options=options)
                    return {"Succeeded": True, "MailId": "MAIL-TEST"}

            notifications = []
            monitor = PargarFolderMonitor(
                Path(directory) / "state",
                lambda *_args: None,
                on_sent=lambda path, mail_id: notifications.append((path, mail_id)),
            )
            sender = {"Contactid": "sender-from-ui"}
            receivers = [{"Contactid": "receiver-from-ui"}]
            mail_id = monitor.send_test_zip(package, Client(), sender, receivers, "ignored")

        self.assertEqual(mail_id, "MAIL-TEST")
        self.assertIs(captured["sender"], sender)
        self.assertIs(captured["receivers"], receivers)
        self.assertIn("ابلاغیه شماره 1405222366652", captured["options"]["subject"])
        self.assertIn('"notice_id": "NOTICE-1"', captured["options"]["body"])
        self.assertEqual(notifications, [(package, "MAIL-TEST")])

    def test_lists_only_pargar_history_newest_first(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            db = _state_db(data_dir / "pargar-state.sqlite3")
            db.execute(
                "INSERT INTO messages(hash,source_path,status,mail_id,detail,updated_at,notice_id) "
                "VALUES(?,?,?,?,?,?,?)",
                ("a", "old.zip", "failed", None, "خطا", 10, "N-1"),
            )
            db.execute(
                "INSERT INTO messages(hash,source_path,status,mail_id,detail,updated_at,notice_id) "
                "VALUES(?,?,?,?,?,?,?)",
                ("b", "new.zip", "sent", "MAIL-2", "موفق", 20, "N-2"),
            )
            db.commit()
            db.close()

            records = list_pargar_history(data_dir)

        self.assertEqual([record[0] for record in records], ["N-2", "N-1"])
        self.assertEqual(records[0][2:5], ("sent", "MAIL-2", "موفق"))


if __name__ == "__main__":
    unittest.main()
