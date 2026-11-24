"""Persistent non-secret settings and Windows-protected FT credentials."""

from __future__ import annotations

import base64
import ctypes
import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from ctypes import wintypes


APP_VERSION = "0.7.1"
OFFICE_VERSION = "0.7.2"
SETTINGS_FILENAME = "settings.json"
CREDENTIALS_FILENAME = "credentials.dat"
PARGAR_CREDENTIALS_FILENAME = "pargar-credentials.dat"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


@dataclass(frozen=True)
class AppSettings:
    selector: str = "img[src^='data:image/']"
    sequence_length: int = 5
    batch_count: int = 1000
    reload_delay_seconds: float = 1.0
    epochs: int = 40
    minimum_confidence: float = 0.30
    login_attempts: int = 5
    captcha_submit_delay_seconds: float = 1.0
    watch_dir: str = ""
    max_files_per_run: int = 10
    max_file_size_mb: float = 100.0
    auto_upload_enabled: bool = True
    environment: str = ""
    download_dir: str = ""
    auto_download_enabled: bool = False
    thinclient_test_download: bool = False
    download_interval_seconds: int = 10
    pargar_base_url: str = ""
    pargar_watch_dir: str = ""
    pargar_message_template: str = "فایل «{filename}» جهت بررسی ارسال می‌شود."
    pargar_poll_seconds: int = 10
    pargar_sender_json: str = ""
    pargar_recipients_json: str = "[]"
    pargar_process_existing: bool = False

    def validate(self) -> None:
        if not 1 <= self.sequence_length <= 10:
            raise ValueError("تعداد ارقام باید بین ۱ و ۱۰ باشد.")
        if not 1 <= self.batch_count <= 10000:
            raise ValueError("تعداد Batch باید بین ۱ و ۱۰۰۰۰ باشد.")
        if not 0 <= self.reload_delay_seconds <= 60:
            raise ValueError("تأخیر Reload باید بین صفر و ۶۰ ثانیه باشد.")
        if not 1 <= self.epochs <= 500:
            raise ValueError("Epoch باید بین ۱ و ۵۰۰ باشد.")
        if not 0 <= self.minimum_confidence <= 1:
            raise ValueError("حداقل اطمینان باید بین صفر و یک باشد.")
        if not 1 <= self.login_attempts <= 20:
            raise ValueError("تعداد تلاش ورود باید بین ۱ و ۲۰ باشد.")
        if not 0 <= self.captcha_submit_delay_seconds <= 60:
            raise ValueError("تأخیر ارسال CAPTCHA باید بین صفر و ۶۰ ثانیه باشد.")
        if not 0 <= self.max_files_per_run <= 10000:
            raise ValueError("سقف تعداد فایل باید بین صفر و ۱۰۰۰۰ باشد.")
        if not 0 <= self.max_file_size_mb <= 100000:
            raise ValueError("سقف حجم فایل باید بین صفر و ۱۰۰۰۰۰ مگابایت باشد.")
        if not isinstance(self.auto_upload_enabled, bool):
            raise ValueError("وضعیت آپلود خودکار معتبر نیست.")
        if not isinstance(self.auto_download_enabled, bool):
            raise ValueError("وضعیت دانلود خودکار معتبر نیست.")
        if not isinstance(self.thinclient_test_download, bool):
            raise ValueError("وضعیت حالت تست دانلود معتبر نیست.")
        if not 5 <= self.download_interval_seconds <= 3600:
            raise ValueError("بازه دانلود خودکار باید بین ۵ و ۳۶۰۰ ثانیه باشد.")
        if self.environment not in {"", "thinclient", "intranet"}:
            raise ValueError("محیط اجرا معتبر نیست.")


        if not 5 <= self.pargar_poll_seconds <= 3600:
            raise ValueError("فاصله پایش پوشه باید بین ۵ تا ۳۶۰۰ ثانیه باشد.")
        if not isinstance(self.pargar_process_existing, bool):
            raise ValueError("گزینه فایل‌های موجود پرگار معتبر نیست.")
        if "{filename}" not in self.pargar_message_template:
            raise ValueError("قالب متن پیام باید شامل {filename} باشد.")
        try:
            recipients = json.loads(self.pargar_recipients_json)
            sender = json.loads(self.pargar_sender_json) if self.pargar_sender_json else None
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("تنظیمات مخاطبین پرگار معتبر نیست.") from error
        if not isinstance(recipients, list) or (sender is not None and not isinstance(sender, dict)):
            raise ValueError("تنظیمات مخاطبین پرگار معتبر نیست.")


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(wintypes.BYTE)),
    ]


def load_settings(base_dir: Path) -> AppSettings:
    path = Path(base_dir) / SETTINGS_FILENAME
    if not path.exists():
        return AppSettings()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("فایل تنظیمات معتبر نیست.") from error
    if not isinstance(payload, dict):
        raise ValueError("ساختار فایل تنظیمات معتبر نیست.")
    allowed = {field.name for field in fields(AppSettings)}
    try:
        settings = AppSettings(**{key: value for key, value in payload.items() if key in allowed})
        settings.validate()
    except (TypeError, ValueError) as error:
        raise ValueError("مقادیر فایل تنظیمات معتبر نیست.") from error
    return settings


def save_settings(base_dir: Path, settings: AppSettings) -> Path:
    settings.validate()
    base_dir = Path(base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    path = base_dir / SETTINGS_FILENAME
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(asdict(settings), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path


def _crypt32():
    if os.name != "nt":
        raise RuntimeError("ذخیره امن رمز فقط در Windows پشتیبانی می‌شود.")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    return crypt32, kernel32


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    return (
        _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(wintypes.BYTE))),
        buffer,
    )


def protect_secret(data: bytes) -> bytes:
    crypt32, kernel32 = _crypt32()
    source, source_buffer = _blob(data)
    protected = _DataBlob()
    try:
        succeeded = crypt32.CryptProtectData(
            ctypes.byref(source),
            "FT NIOC Uploader",
            None,
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(protected),
        )
        if not succeeded:
            raise ctypes.WinError()
        return ctypes.string_at(protected.pbData, protected.cbData)
    finally:
        ctypes.memset(source_buffer, 0, len(source_buffer))
        if protected.pbData:
            kernel32.LocalFree(protected.pbData)


def unprotect_secret(data: bytes) -> bytes:
    crypt32, kernel32 = _crypt32()
    source, source_buffer = _blob(data)
    clear = _DataBlob()
    try:
        succeeded = crypt32.CryptUnprotectData(
            ctypes.byref(source),
            None,
            None,
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(clear),
        )
        if not succeeded:
            raise ValueError("اطلاعات ورود ذخیره‌شده قابل بازیابی نیست.")
        return ctypes.string_at(clear.pbData, clear.cbData)
    finally:
        ctypes.memset(source_buffer, 0, len(source_buffer))
        if clear.pbData:
            kernel32.LocalFree(clear.pbData)


def save_credentials(base_dir: Path, username: str, password: str) -> Path:
    if not username or not password:
        raise ValueError("نام کاربری و رمز عبور نباید خالی باشند.")
    payload = json.dumps(
        {"username": username, "password": password},
        ensure_ascii=False,
    ).encode("utf-8")
    protected = protect_secret(payload)
    encoded = base64.b64encode(protected)
    base_dir = Path(base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    path = base_dir / CREDENTIALS_FILENAME
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(encoded)
    os.replace(temporary, path)
    return path


def load_credentials(base_dir: Path) -> tuple[str, str] | None:
    path = Path(base_dir) / CREDENTIALS_FILENAME
    if not path.exists():
        return None
    try:
        protected = base64.b64decode(path.read_bytes(), validate=True)
        payload = json.loads(unprotect_secret(protected).decode("utf-8"))
        username = payload["username"]
        password = payload["password"]
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError("اطلاعات ورود ذخیره‌شده معتبر نیست.") from error
    if not isinstance(username, str) or not isinstance(password, str):
        raise ValueError("اطلاعات ورود ذخیره‌شده معتبر نیست.")
    if not username or not password:
        raise ValueError("اطلاعات ورود ذخیره‌شده خالی است.")
    return username, password


def delete_credentials(base_dir: Path) -> bool:
    path = Path(base_dir) / CREDENTIALS_FILENAME
    if not path.exists():
        return False
    path.unlink()
    return True


def save_pargar_credentials(base_dir: Path, username: str, token: str) -> Path:
    if not username.strip() or not token.strip():
        raise ValueError("نام کاربری و API Token پرگار را وارد کنید.")
    payload = json.dumps(
        {"username": username.strip(), "token": token.strip()}, ensure_ascii=False
    ).encode("utf-8")
    encoded = base64.b64encode(protect_secret(payload))
    base_dir = Path(base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    path = base_dir / PARGAR_CREDENTIALS_FILENAME
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(encoded)
    os.replace(temporary, path)
    return path


def load_pargar_credentials(base_dir: Path) -> tuple[str, str] | None:
    path = Path(base_dir) / PARGAR_CREDENTIALS_FILENAME
    if not path.exists():
        return None
    try:
        protected = base64.b64decode(path.read_bytes(), validate=True)
        payload = json.loads(unprotect_secret(protected).decode("utf-8"))
        username, token = payload["username"], payload["token"]
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError("اطلاعات ورود پرگار قابل‌خواندن نیست.") from error
    if not isinstance(username, str) or not isinstance(token, str) or not username or not token:
        raise ValueError("اطلاعات ورود پرگار ناقص است.")
    return username, token


def delete_pargar_credentials(base_dir: Path) -> bool:
    path = Path(base_dir) / PARGAR_CREDENTIALS_FILENAME
    if not path.exists():
        return False
    path.unlink()
    return True

