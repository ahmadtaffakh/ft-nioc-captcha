"""Parghar messaging API and isolated, fail-closed folder monitor."""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path


TEMP_SUFFIXES = (".tmp", ".part", ".crdownload")
DEFAULT_MESSAGE = "فایل «{filename}» جهت بررسی ارسال می‌شود."


class PargarError(RuntimeError):
    pass


class AmbiguousSendError(PargarError):
    """The request may have reached Pargar; automatic retry is unsafe."""


class PargarClient:
    def __init__(self, base_url: str, username: str, token: str, timeout: int = 30):
        self.base_url = base_url.strip().rstrip("/")
        self.username = username.strip()
        self.token = token.strip()
        self.timeout = timeout
        parsed = urllib.parse.urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise PargarError("نشانی پرگار باید با http:// یا https:// شروع شود.")
        if not self.username or not self.token:
            raise PargarError("نام کاربری و API Token پرگار را وارد کنید.")
        self._base_path = parsed.path.rstrip("/")
        if self._base_path.casefold().endswith("/mailbox.api"):
            self._base_path = self._base_path.rsplit("/", 1)[0]
        self._origin = f"{parsed.scheme}://{parsed.netloc}"
        raw = f"{self.username}:{self.token}".encode("utf-8")
        self._authorization = "Basic " + base64.b64encode(raw).decode("ascii")

    def _url(self, relative: str, params: dict | None = None) -> str:
        url = self._origin + self._base_path + "/" + relative.lstrip("/")
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return url

    def _get_json(self, relative: str, params: dict | None = None):
        request = urllib.request.Request(
            self._url(relative, params), headers={"Authorization": self._authorization, "Accept": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                payload = json.loads(raw.decode("utf-8-sig")) if raw else {}
        except urllib.error.HTTPError as error:
            if error.code == 401:
                raise PargarError("احراز هویت پرگار ناموفق بود؛ نام کاربری یا توکن را بررسی کنید.") from error
            endpoint = urllib.parse.urlsplit(request.full_url).path
            raise PargarError(f"پرگار برای مسیر {endpoint} پاسخ HTTP {error.code} برگرداند.") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise PargarError(f"ارتباط با پرگار برقرار نشد: {error}") from error
        except (UnicodeError, json.JSONDecodeError) as error:
            raise PargarError("پاسخ پرگار قابل‌خواندن نیست.") from error
        if isinstance(payload, dict) and payload.get("Succeeded") is False:
            raise PargarError(str(payload.get("Message") or "پرگار درخواست را نپذیرفت."))
        return payload

    def test_connection(self) -> None:
        self._get_json("inf.Api/v0/Authentication/Authenticate")

    def candidates(self, *, sender: bool = False, search: str = "") -> list[dict]:
        relative = (
            "Mailbox.api/v0/messages/GetCurrentUserSenderContacts"
            if sender else "Mailbox.api/v0/messages/MessageReceivingCandidates"
        )
        page = 0
        results = []
        while page < 100:
            payload = self._get_json(
                relative, {"searchText" if sender else "searchterm": search.strip(), "pageSize": 100, "pageNum": page}
            )
            entities = payload.get("Entities", []) if isinstance(payload, dict) else []
            if not isinstance(entities, list):
                raise PargarError("ساختار فهرست مخاطبین پرگار معتبر نیست.")
            results.extend(item for item in entities if isinstance(item, dict))
            if len(entities) < 100 or len(results) >= int(payload.get("TotalCount", len(results))):
                break
            page += 1
        return results

    @staticmethod
    def _participant(contact: dict) -> dict:
        contact_id = contact.get("Contactid") or contact.get("ContactId")
        if not contact_id:
            raise PargarError("یکی از مخاطبین شناسه ContactId ندارد؛ انتخاب را تازه‌سازی کنید.")
        return {"ContactId": str(contact_id)}

    def send_file(self, path: Path, sender: dict, receivers: list[dict], message_template: str,
                  expected_sha256: str | None = None) -> dict:
        path = Path(path)
        if not path.is_file() or path.suffix.lower() in TEMP_SUFFIXES:
            raise PargarError("فایل ورودی معتبر یا کامل نیست.")
        if not receivers:
            raise PargarError("حداقل یک گیرنده انتخاب کنید.")
        message = {
            "Subject": path.name,
            "TextBody": message_template.replace("{filename}", path.name),
            "RedirectLink": None,
            "Sender": self._participant(sender),
            "Receivers": [self._participant(item) for item in receivers],
            "CcReceivers": [], "BccReceivers": [], "Priority": 0,
            "Privacy": 0, "ReferenceNumber": None,
        }
        handle = tempfile.NamedTemporaryFile(prefix="pargar-", suffix=".zip", delete=False)
        temporary_zip = Path(handle.name)
        handle.close()
        attachment = temporary_zip
        try:
            if path.suffix.lower() == ".zip":
                shutil.copyfile(path, temporary_zip)
                if expected_sha256 and fingerprint(temporary_zip) != expected_sha256:
                    raise PargarError("محتوای فایل ZIP هنگام آماده‌سازی تغییر کرد؛ ارسال انجام نشد.")
            else:
                with zipfile.ZipFile(temporary_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    archive.write(path, arcname=path.name)
                if expected_sha256:
                    digest = hashlib.sha256()
                    with zipfile.ZipFile(temporary_zip) as archive, archive.open(path.name) as source:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            digest.update(chunk)
                    if digest.hexdigest() != expected_sha256:
                        raise PargarError("محتوای فایل هنگام آماده‌سازی تغییر کرد؛ ارسال انجام نشد.")
            zip_name = path.name if path.suffix.lower() == ".zip" else path.name + ".zip"
            return self._send_multipart(message, attachment, zip_name)
        finally:
            if temporary_zip:
                try:
                    temporary_zip.unlink(missing_ok=True)
                except OSError:
                    pass

    def _send_multipart(self, message: dict, attachment: Path, upload_name: str) -> dict:
        boundary = "----FTPargar" + os.urandom(18).hex()
        filename = upload_name
        mime = "application/zip"
        safe_filename = filename.encode("ascii", "replace").decode("ascii").replace('"', "_")
        encoded_filename = urllib.parse.quote(filename.encode("utf-8"), safe="")
        prefix = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"Message\"\r\n"
            "Content-Type: application/json; charset=utf-8\r\n\r\n"
        ).encode("ascii") + json.dumps(message, ensure_ascii=False).encode("utf-8") + (
            f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"Attachments\"; "
            f"filename=\"{safe_filename}\"; filename*=UTF-8''{encoded_filename}\r\n"
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("ascii")
        suffix = f"\r\n--{boundary}--\r\n".encode("ascii")
        parsed = urllib.parse.urlsplit(self._url("Mailbox.api/v0/messages/sendmail"))
        connection_type = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        connection = connection_type(parsed.hostname, parsed.port, timeout=self.timeout)
        request_started = False
        try:
            connection.putrequest("POST", urllib.parse.urlunsplit(("", "", parsed.path, parsed.query, "")))
            connection.putheader("Authorization", self._authorization)
            connection.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
            connection.putheader("Content-Length", str(len(prefix) + attachment.stat().st_size + len(suffix)))
            connection.putheader("Accept", "application/json")
            connection.endheaders()
            request_started = True
            connection.send(prefix)
            with attachment.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    connection.send(chunk)
            connection.send(suffix)
            response = connection.getresponse()
            raw = response.read()
            if response.status == 401:
                raise PargarError("احراز هویت پرگار ناموفق بود؛ نام کاربری یا توکن را بررسی کنید.")
            if response.status < 200 or response.status >= 300:
                raise AmbiguousSendError(f"پاسخ ارسال پرگار HTTP {response.status} بود؛ ارسال را بررسی کنید.")
            try:
                payload = json.loads(raw.decode("utf-8-sig"))
            except (UnicodeError, json.JSONDecodeError) as error:
                raise AmbiguousSendError("پاسخ ارسال پرگار نامعتبر است؛ از ارسال دوباره خودداری شد.") from error
            if not isinstance(payload, dict) or payload.get("Succeeded") is not True or not payload.get("MailId"):
                raise AmbiguousSendError("تأیید قطعی ارسال از پرگار دریافت نشد؛ از ارسال دوباره خودداری شد.")
            return payload
        except AmbiguousSendError:
            raise
        except PargarError:
            raise
        except Exception as error:
            if request_started:
                raise AmbiguousSendError("ارتباط هنگام ارسال قطع شد؛ ممکن است پیام ثبت شده باشد. ارسال خودکار تکرار نمی‌شود.") from error
            raise PargarError(f"ارسال به پرگار آغاز نشد: {error}") from error
        finally:
            connection.close()


def fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _state_db(path: Path):
    connection = sqlite3.connect(path, timeout=15)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS messages (hash TEXT PRIMARY KEY, source_path TEXT NOT NULL, "
        "status TEXT NOT NULL, mail_id TEXT, detail TEXT, updated_at REAL NOT NULL)"
    )
    connection.commit()
    return connection


class PargarFolderMonitor:
    """Serially watch one folder; no recursion and no automatic ambiguous retry."""

    def __init__(self, data_dir: Path, emit):
        self.data_dir = Path(data_dir)
        self.emit = emit
        self._stop = threading.Event()
        self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, *, folder: Path, client: PargarClient, sender: dict, receivers: list[dict],
              template: str, interval: int, process_existing: bool = False):
        if self.running:
            raise PargarError("پایش پوشه پرگار از قبل فعال است.")
        folder = Path(folder)
        if not folder.is_dir():
            raise PargarError("پوشه انتخاب‌شده وجود ندارد یا قابل‌دسترسی نیست.")
        if not receivers or not sender:
            raise PargarError("فرستنده و حداقل یک گیرنده را انتخاب کنید.")
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(folder, client, sender, receivers, template, max(5, int(interval)), process_existing),
            daemon=True, name="pargar-folder-monitor",
        )
        self._thread.start()

    def stop(self):
        self._stop.set()

    def send_test_zip(self, path, client, sender, receivers, message):
        if path.suffix.lower() != ".zip" or not zipfile.is_zipfile(path):
            raise PargarError("یک فایل ZIP معتبر انتخاب کنید.")
        digest = fingerprint(path)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        db = _state_db(self.data_dir / "pargar-state.sqlite3")
        try:
            with db:
                claim = db.execute(
                    "INSERT INTO messages(hash,source_path,status,updated_at) VALUES(?,?,?,?) "
                    "ON CONFLICT(hash) DO UPDATE SET source_path=excluded.source_path, "
                    "status=excluded.status, updated_at=excluded.updated_at WHERE messages.status='failed'",
                    (digest, str(path), "sending", time.time()),
                )
                if not claim.rowcount:
                    raise PargarError("این فایل قبلاً ارسال شده یا وضعیت ارسال قبلی آن نیاز به بررسی دارد.")
            try:
                result = client.send_file(path, sender, receivers, message, expected_sha256=digest)
            except Exception as error:
                status = "ambiguous" if isinstance(error, AmbiguousSendError) else "failed"
                with db:
                    db.execute("UPDATE messages SET status=?,updated_at=? WHERE hash=?",
                               (status, time.time(), digest))
                raise
            mail_id = str(result["MailId"])
            with db:
                db.execute("UPDATE messages SET status='sent',mail_id=?,updated_at=? WHERE hash=?",
                           (mail_id, time.time(), digest))
            return mail_id
        finally:
            db.close()

    def _run(self, folder, client, sender, receivers, template, interval, process_existing):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        db = _state_db(self.data_dir / "pargar-state.sqlite3")
        rows = list(db.execute("SELECT hash,status FROM messages"))
        known_hashes = {digest for digest, status in rows if status in {"sent", "ambiguous", "sending"}}
        retry_hashes = {digest for digest, status in rows if status == "failed"}
        baseline = {}
        if not process_existing:
            try:
                for path in folder.iterdir():
                    if path.is_file() and not path.is_symlink():
                        stat = path.stat()
                        baseline[str(path.resolve())] = (stat.st_size, stat.st_mtime_ns)
            except OSError as error:
                self.emit("error", f"خواندن پوشه پرگار ممکن نیست: {error}")
                db.close()
                return
        self.emit("info", f"پایش پرگار فعال شد: {folder}")
        stable = {}
        try:
            while not self._stop.is_set():
                try:
                    paths = list(folder.iterdir())
                except OSError as error:
                    self.emit("error", f"خطای دسترسی به پوشه؛ پایش متوقف شد: {error}")
                    return
                for path in paths:
                    if not path.is_file() or path.is_symlink() or path.name.lower().endswith(TEMP_SUFFIXES):
                        continue
                    try:
                        stat = path.stat()
                        signature = (stat.st_size, stat.st_mtime_ns)
                        key = str(path.resolve())
                        digest = None
                        if baseline.get(key) == signature and not process_existing:
                            digest = fingerprint(path)
                            if digest not in retry_hashes:
                                continue
                        prior = stable.get(key)
                        if prior and prior[0] == signature:
                            stable[key] = (signature, prior[1] + 1)
                        else:
                            stable[key] = (signature, 1)
                        if stable[key][1] < 2:
                            continue
                        digest = digest or fingerprint(path)
                        if digest in known_hashes:
                            stable.pop(key, None)
                            continue
                        db.execute(
                            "INSERT OR IGNORE INTO messages(hash,source_path,status,updated_at) VALUES(?,?,?,?)",
                            (digest, str(path), "sending", time.time()),
                        )
                        db.commit()
                        self.emit("info", f"در حال ارسال: {path.name}")
                        try:
                            result = client.send_file(path, sender, receivers, template, expected_sha256=digest)
                        except AmbiguousSendError as error:
                            status, detail, mail_id = "ambiguous", str(error), None
                        except Exception as error:
                            status, detail, mail_id = "failed", str(error), None
                        else:
                            status, detail, mail_id = "sent", "ارسال و شناسه پیام تأیید شد.", str(result["MailId"])
                        db.execute(
                            "UPDATE messages SET status=?,mail_id=?,detail=?,updated_at=? WHERE hash=?",
                            (status, mail_id, detail, time.time(), digest),
                        )
                        db.commit()
                        known_hashes.add(digest)
                        retry_hashes.discard(digest)
                        level = "success" if status == "sent" else "error"
                        suffix = f" (شناسه پیام: {mail_id})" if mail_id else ""
                        self.emit(level, f"{path.name}: {detail}{suffix}")
                        stable.pop(key, None)
                    except (OSError, PargarError) as error:
                        self.emit("error", f"{path.name}: {error}")
                        stable.pop(str(path), None)
                self._stop.wait(interval)
        finally:
            db.close()
            self.emit("info", "پایش پرگار متوقف شد.")

