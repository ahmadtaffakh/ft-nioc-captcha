"""Long-lived browser runtime for FT desktop operations."""

from __future__ import annotations

import hashlib
import itertools
import os
import queue
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from captcha_inference import load_model, predict_image
from captcha_training import decode_image_data_url, save_unlabeled_batch_sample
from ft_auto_uploader import (
    FILE_MANAGER_URL,
    downloaded_fingerprint_exists,
    downloaded_remote_id_exists,
    list_upload_history,
    list_candidate_files,
    opposite_environment,
    open_state,
    parse_transfer_name,
    process_file,
    save_download_state,
    select_files_for_run,
    validate_environment,
    wait_for_file_manager,
)
from ft_browser import launch_persistent_session
from ft_settings import load_credentials, load_settings


LOGIN_URL = "https://ft.nioc.ir/dargah/user/sign-in"
FILE_MANAGER_PREFIX = "https://ft.nioc.ir/filemanager/"
DEFAULT_CAPTCHA_SELECTOR = "img[src^='data:image/']"
USERNAME_SELECTOR = "div.itc-card__content > div > div:nth-of-type(1) input"
PASSWORD_SELECTOR = "div.itc-card__content > div > div:nth-of-type(2) input"
CAPTCHA_INPUT_SELECTOR = "div.itc-card__content > div > div:nth-of-type(3) input:visible"
FILE_MANAGER_API = "https://ft.nioc.ir/webapi/api/v1/"
GROUPS_URL = FILE_MANAGER_API + "Group/GetAllUserGroups"
FILES_URL = FILE_MANAGER_API + "File/GetFilesAndFolders"
DOWNLOAD_URL = FILE_MANAGER_API + "File/DownloadFile"


@dataclass(frozen=True)
class RuntimeResult:
    """Result of one serialized browser operation."""

    task_id: str
    operation: str
    succeeded: bool
    payload: Any


@dataclass(frozen=True)
class RuntimeStarted:
    task_id: str


@dataclass(frozen=True)
class RuntimeLog:
    """One live log entry emitted by a background operation."""

    level: str
    message: str


def _captcha_locator(page, preferred_selector: str):
    """Find a visible CAPTCHA image, preferring the configured selector."""
    if preferred_selector:
        preferred = page.locator(preferred_selector)
        for index in range(preferred.count()):
            candidate = preferred.nth(index)
            if candidate.is_visible():
                box = candidate.bounding_box()
                if box and box["width"] >= 60 and box["height"] >= 20:
                    return candidate

    candidates = page.locator("img")
    best = None
    best_score = -1
    for index in range(candidates.count()):
        candidate = candidates.nth(index)
        if not candidate.is_visible():
            continue
        box = candidate.bounding_box()
        if not box:
            continue
        width, height = box["width"], box["height"]
        if not (60 <= width <= 400 and 20 <= height <= 140):
            continue
        aspect = width / max(height, 1)
        if not 1.4 <= aspect <= 8:
            continue
        src = candidate.get_attribute("src") or ""
        score = 10 if src.startswith("data:image/") else 0
        score += 5 if "captcha" in src.lower() else 0
        score += aspect
        if score > best_score:
            best, best_score = candidate, score
    if best is None:
        raise RuntimeError("تصویر CAPTCHA پیدا نشد؛ selector را اصلاح کنید.")
    return best


def capture_captcha_bytes(page, selector: str) -> bytes:
    """Read original data-URL bytes, falling back to an element screenshot."""
    image = _captcha_locator(page, selector)
    image.wait_for(state="visible", timeout=15_000)
    src = image.get_attribute("src") or ""
    if src.startswith("data:image/"):
        try:
            return decode_image_data_url(src)
        except ValueError:
            pass
    return image.screenshot(type="png")


class BrowserSession:
    """Serialize all synchronous Playwright access on one browser thread."""

    def __init__(
        self, profile_dir: Path, results: queue.Queue[RuntimeResult | RuntimeLog | RuntimeStarted]
    ):
        self.profile_dir = Path(profile_dir)
        self.results = results
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ft-browser")
        self.playwright = None
        self.context = None
        self.context_closed = False
        self.page = None
        self.batch_cancelled = threading.Event()
        self._task_numbers = itertools.count(1)

    def submit(self, operation: str, function: Callable, *args) -> str:
        task_id = f"{operation}-{next(self._task_numbers)}"
        def run():
            self.results.put(RuntimeStarted(task_id))
            return function(*args)

        future = self.executor.submit(run)

        def completed(done):
            try:
                result = RuntimeResult(task_id, operation, True, done.result())
            except Exception as error:
                result = RuntimeResult(task_id, operation, False, str(error))
            self.results.put(result)

        future.add_done_callback(completed)
        return task_id

    def _emit(self, level: str, message: str) -> None:
        self.results.put(RuntimeLog(level, message))

    def _ensure_file_manager(self, login_timeout_minutes: float = 15.0, *, automatic_login=True) -> None:
        if self.page is None or self.context_closed or self.page.is_closed():
            self.open()

        def manual_login(upload_button, timeout_ms):
            if automatic_login:
                try:
                    settings = load_settings(self.profile_dir.parent)
                    credentials = load_credentials(self.profile_dir.parent)
                    if credentials:
                        self._emit("info", "نشست FT پایان یافته؛ ورود مجدد خودکار در حال انجام است.")
                        self.login(
                            *credentials, settings.selector,
                            self.profile_dir.parent / "captcha-model",
                            settings.minimum_confidence, settings.login_attempts,
                            settings.captcha_submit_delay_seconds,
                        )
                        upload_button.wait_for(state="visible", timeout=10_000)
                        self._emit("success", "ورود مجدد انجام شد؛ عملیات ادامه می‌یابد.")
                        return
                except Exception:
                    self._emit("warning", "ورود مجدد خودکار تکمیل نشد.")
            self._emit("warning", "ورود لازم است؛ ورود دستی را در مرورگر کامل کنید.")
            upload_button.wait_for(state="visible", timeout=timeout_ms)

        wait_for_file_manager(
            self.page,
            int(login_timeout_minutes * 60_000),
            manual_login,
        )

    def open(self):
        self._open_page()
        try:
            self.page.goto(LOGIN_URL, wait_until="domcontentloaded")
        except Exception:
            if not self.context_closed and not self.page.is_closed():
                raise
            self._open_page()
            self.page.goto(LOGIN_URL, wait_until="domcontentloaded")
        self._resume_file_manager()
        return "مرورگر باز شد و برای عملیات بعدی باز می‌ماند."

    def _resume_file_manager(self) -> bool:
        if self.page.url.split("?", 1)[0] == LOGIN_URL:
            return_to_portal = self.page.get_by_text("بازگشت به درگاه", exact=True)
            username = self.page.locator(USERNAME_SELECTOR)
            return_to_portal.or_(username).first.wait_for(state="visible", timeout=10_000)
            if return_to_portal.is_visible():
                self.page.goto(FILE_MANAGER_URL, wait_until="domcontentloaded")
                if not self.page.url.startswith(FILE_MANAGER_PREFIX):
                    raise RuntimeError("نشست فعال FT به مدیریت فایل منتقل نشد.")
        if not self.page.url.startswith(FILE_MANAGER_PREFIX):
            return False
        self.page.locator("button.mr-5").first.wait_for(state="visible", timeout=10_000)
        return self.page.url.startswith(FILE_MANAGER_PREFIX)

    def _open_page(self):
        if self.context_closed:
            self.close()
        if self.context is None:
            from playwright.sync_api import sync_playwright

            self.playwright = sync_playwright().start()
            try:
                self.context, self.page = launch_persistent_session(
                    self.playwright, self.profile_dir
                )
                self.context.on("close", self._on_context_closed)
            except Exception:
                # A failed launch can leave the Sync API's loop active on this worker.
                try:
                    self.close()
                except Exception as cleanup_error:
                    self._emit("warning", f"توقف Playwright ناموفق بود: {cleanup_error}")
                raise
        elif self.page.is_closed():
            try:
                self.page = self.context.new_page()
            except Exception:
                if not self.context_closed:
                    raise
                self.close()
                self._open_page()

    def _on_context_closed(self, _context):
        self.context_closed = True

    def manual_login(self):
        self.open()
        if self.page.url.startswith(FILE_MANAGER_PREFIX):
            return "نشست فعال FT بازیابی شد و مدیریت فایل باز است."
        return "صفحه ورود دستی باز شد؛ اطلاعات ورود و CAPTCHA را در مرورگر وارد کنید."

    def capture(self, selector: str):
        if self.page is None:
            raise RuntimeError("ابتدا مرورگر را باز کنید.")
        return capture_captcha_bytes(self.page, selector)

    def refresh(self):
        if self.page is None:
            raise RuntimeError("ابتدا مرورگر را باز کنید.")
        self.page.reload(wait_until="domcontentloaded")
        return "CAPTCHA تازه دریافت شد."

    def collect_batch(
        self,
        selector: str,
        batch_dir: Path,
        count: int,
        reload_delay_seconds: float = 0.0,
    ):
        if self.page is None:
            raise RuntimeError("ابتدا مرورگر را باز کنید.")
        if count < 1:
            raise ValueError("تعداد نمونه باید حداقل یک باشد.")
        if not 0 <= reload_delay_seconds <= 60:
            raise ValueError("تأخیر Reload باید بین صفر و ۶۰ ثانیه باشد.")
        self.batch_cancelled.clear()
        collected = 0
        for index in range(1, count + 1):
            if self.batch_cancelled.is_set():
                break
            image_bytes = capture_captcha_bytes(self.page, selector)
            save_unlabeled_batch_sample(batch_dir, image_bytes, index)
            collected += 1
            if index < count and not self.batch_cancelled.is_set():
                self.page.reload(wait_until="domcontentloaded")
                if self.batch_cancelled.wait(reload_delay_seconds):
                    break
        return {"count": collected, "requested": count, "batch_dir": str(batch_dir)}

    def cancel_batch(self):
        self.batch_cancelled.set()

    def start_session(self, username, password, selector, model_dir,
                      minimum_confidence, max_attempts, captcha_submit_delay_seconds=1.0):
        try:
            return self.login(username, password, selector, model_dir,
                              minimum_confidence, max_attempts, captcha_submit_delay_seconds)
        except Exception as error:
            self._emit("warning", f"ورود خودکار انجام نشد: {error}")
            self._emit("warning", "مرورگر برای ورود دستی باز است؛ ورود را در FT تکمیل کنید.")
            self._open_page()
            self._ensure_file_manager(automatic_login=False)
            return "ورود دستی تکمیل شد."

    def login(
        self,
        username: str,
        password: str,
        selector: str,
        model_dir: Path,
        minimum_confidence: float,
        max_attempts: int,
        captcha_submit_delay_seconds: float = 1.0,
    ):
        if not 1 <= max_attempts <= 20:
            raise ValueError("تعداد تلاش ورود باید بین ۱ و ۲۰ باشد.")
        if not 0 <= captcha_submit_delay_seconds <= 60:
            raise ValueError("تأخیر ارسال CAPTCHA باید بین صفر و ۶۰ ثانیه باشد.")
        if self.context is None or self.context_closed or self.page.is_closed():
            self.open()
        try:
            self.page.evaluate("() => true")
        except Exception:
            if not self.context_closed and not self.page.is_closed():
                raise
            self.open()
        if self.page.url.split("?", 1)[0] != LOGIN_URL and not self.page.url.startswith(FILE_MANAGER_PREFIX):
            self.open()
        if self._resume_file_manager():
            return "ورود قبلاً انجام شده است."
        if not username or not password:
            raise ValueError("نام کاربری و رمز عبور را وارد کنید.")
        model_bundle = load_model(model_dir)
        last_prediction = None
        for attempt in range(1, max_attempts + 1):
            if attempt > 1 and self._resume_file_manager():
                return "ورود قبلاً انجام شده است."
            self.page.locator(USERNAME_SELECTOR).fill(username)
            self.page.locator(PASSWORD_SELECTOR).fill(password)
            image_bytes = capture_captcha_bytes(self.page, selector)
            prediction, confidence = predict_image(image_bytes, model_bundle)
            last_prediction = f"{prediction} ({confidence:.1%})"
            captcha_input = self.page.locator(CAPTCHA_INPUT_SELECTOR)
            captcha_input.fill(prediction)
            if confidence < minimum_confidence:
                self._emit("warning", f"تلاش {attempt} از {max_attempts}: اطمینان CAPTCHA پایین بود؛ فرم ارسال نشد.")
                if attempt < max_attempts:
                    self.page.reload(wait_until="domcontentloaded")
                continue
            if captcha_submit_delay_seconds:
                self.page.wait_for_timeout(captcha_submit_delay_seconds * 1000)
            captcha_input.press("Enter")
            try:
                self.page.wait_for_url("https://ft.nioc.ir/filemanager/**", timeout=10_000)
                return f"ورود موفق؛ CAPTCHA: {last_prediction}"
            except Exception:
                if attempt < max_attempts:
                    self.page.goto(LOGIN_URL, wait_until="domcontentloaded")
        raise RuntimeError(
            f"ورود پس از {max_attempts} تلاش موفق نشد؛ "
            f"آخرین تشخیص: {last_prediction}"
        )

    def upload_folder(
        self,
        folder: Path,
        max_files: int,
        max_file_size_mb: float,
        environment: str = "",
        scan_timeout_minutes: float = 30.0,
        login_timeout_minutes: float = 15.0,
    ):
        folder = Path(folder)
        if not folder.is_dir():
            raise ValueError("پوشه انتخاب‌شده وجود ندارد.")
        if max_files < 0 or max_file_size_mb < 0:
            raise ValueError("محدودیت تعداد و حجم نباید منفی باشد.")
        selected, oversized = select_files_for_run(
            list_candidate_files(folder),
            max_files,
            int(max_file_size_mb * 1024 * 1024),
        )
        for path in oversized:
            self._emit("warning", f"به‌دلیل عبور از سقف حجم رد شد: {path.name}")
        if not selected:
            return {"processed": 0, "failed": 0, "oversized": len(oversized)}

        validate_environment(environment)
        self._ensure_file_manager(login_timeout_minutes)

        connection = open_state(self.profile_dir.parent / "state.db")
        failed = 0
        processed = 0
        consecutive_failures = 0
        stopped = False
        try:
            for path in selected:
                processed += 1
                try:
                    process_file(
                        connection,
                        self.context,
                        self.page,
                        path,
                        folder / "_uploaded",
                        scan_timeout_minutes * 60,
                        self._emit,
                        environment,
                    )
                    consecutive_failures = 0
                except Exception as error:
                    failed += 1
                    consecutive_failures += 1
                    self._emit("error", f"خطا برای {path.name}: {error}")
                    if consecutive_failures == 2:
                        stopped = True
                        self._emit(
                            "error",
                            "دو ردیف پیاپی خطا دادند؛ ادامه ثبت متوقف شد.",
                        )
                        break
        finally:
            connection.close()
        result = {
            "processed": processed,
            "failed": failed,
            "oversized": len(oversized),
        }
        if stopped:
            result["stopped"] = True
        return result

    @staticmethod
    def _response_data(response, operation: str):
        if 300 <= response.status < 400:
            raise RuntimeError(
                f"{operation} به مسیر دیگری هدایت شد (HTTP {response.status}). "
                "وضعیت ورود به مدیریت فایل FT را بررسی کنید."
            )
        if not response.ok:
            raise RuntimeError(f"{operation} با HTTP {response.status} ناموفق بود.")
        try:
            payload = response.json()
        except ValueError:
            content_type = response.headers.get("content-type", "").lower()
            detail = "صفحهٔ HTML" if content_type.startswith("text/html") else "پاسخ خالی یا نامعتبر"
            raise RuntimeError(
                f"{operation} به‌جای JSON، {detail} برگرداند (HTTP {response.status}). "
                "وضعیت ورود و مسیر API مدیریت فایل FT را بررسی کنید."
            ) from None
        if not isinstance(payload, dict):
            raise RuntimeError(f"ساختار پاسخ JSON برای {operation} معتبر نیست.")
        if payload.get("isSuccess") is not True:
            raise RuntimeError(payload.get("message") or f"{operation} ناموفق بود.")
        return payload.get("data")

    def _file_manager_headers(self) -> dict[str, str]:
        role = self.page.evaluate(
            """() => location.origin === 'https://ft.nioc.ir'
                ? sessionStorage.getItem('unitRoleCode') : null"""
        )
        return {"X-Role": role} if isinstance(role, str) and role else {}

    def _default_group_id(self, headers: dict[str, str]) -> str:
        groups = self._response_data(
            self.context.request.get(GROUPS_URL, headers=headers, max_redirects=0),
            "دریافت گروه‌های فایل",
        )
        groups = groups if isinstance(groups, list) else []
        enabled = [group for group in groups if group.get("isDisabled") is not True]
        selected = next((group for group in enabled if group.get("isDefault") is True), None)
        if selected is None and len(enabled) == 1:
            selected = enabled[0]
        if not selected or not selected.get("id"):
            raise RuntimeError("گروه پیش‌فرض و یکتای مدیریت فایل مشخص نیست.")
        return str(selected["id"])

    def list_downloadable_files(
        self,
        environment: str,
        login_timeout_minutes: float = 15.0,
        allow_same_origin: bool = False,
    ) -> list[dict]:
        expected_origin = opposite_environment(environment)
        allowed_origins = {expected_origin}
        if allow_same_origin and environment == "thinclient":
            allowed_origins.add(environment)
        self._ensure_file_manager(login_timeout_minutes)
        headers = self._file_manager_headers()
        group_id = self._default_group_id(headers)
        page_size = 100
        page_index = 1
        records = []
        while True:
            data = self._response_data(
                self.context.request.put(
                    FILES_URL,
                    headers=headers,
                    max_redirects=0,
                    data={
                        "pageSize": page_size,
                        "pageIndex": page_index,
                        "WithThumbnail": False,
                        "groupId": group_id,
                        "SortBy": 0,
                        "SortDirection": 0,
                    },
                ),
                "دریافت فهرست فایل‌ها",
            )
            if not isinstance(data, dict):
                raise RuntimeError("پاسخ فهرست فایل‌ها معتبر نیست.")
            items = data.get("items") or []
            for item in items:
                if item.get("directory") is True:
                    continue
                remote_name = f"{item.get('name') or ''}{item.get('extension') or ''}"
                parsed = parse_transfer_name(remote_name)
                if not parsed or parsed[0] not in allowed_origins:
                    continue
                remote_id = item.get("id")
                item_group_id = item.get("groupId") or group_id
                if not remote_id or not item_group_id:
                    continue
                records.append(
                    {
                        "remote_id": str(remote_id),
                        "group_id": str(item_group_id),
                        "remote_name": remote_name,
                        "name": parsed[1],
                        "size": int(item.get("size") or 0),
                        "origin": parsed[0],
                    }
                )
            total_count = int(data.get("totalCount") or 0)
            if page_index * page_size >= total_count:
                break
            page_index += 1
        return records

    @staticmethod
    def _unused_destination(folder: Path, name: str) -> Path:
        destination = folder / name
        counter = 1
        while destination.exists():
            destination = folder / f"{Path(name).stem}.{counter}{Path(name).suffix}"
            counter += 1
        return destination

    def download_files(
        self,
        records: list[dict],
        destination_dir: Path,
        environment: str,
        upload_dir: Path | None = None,
        allow_same_origin: bool = False,
        cancelled: threading.Event | None = None,
    ) -> dict:
        expected_origin = opposite_environment(environment)
        allowed_origins = {expected_origin}
        if allow_same_origin and environment == "thinclient":
            allowed_origins.add(environment)
        destination_dir = Path(destination_dir).resolve()
        destination_dir.mkdir(parents=True, exist_ok=True)
        if upload_dir and Path(upload_dir).resolve() == destination_dir:
            raise ValueError("پوشه دانلود نباید همان پوشه ورودی آپلود باشد.")
        self._ensure_file_manager()
        headers = self._file_manager_headers()
        connection = open_state(self.profile_dir.parent / "state.db")
        downloaded = skipped = failed = 0
        try:
            for record in records:
                if cancelled is not None and cancelled.is_set():
                    break
                try:
                    parsed = parse_transfer_name(str(record.get("remote_name") or ""))
                    if not parsed or parsed[0] not in allowed_origins:
                        raise ValueError("منشأ فایل با محیط مقابل تطابق ندارد.")
                    if parsed[1] != record.get("name"):
                        raise ValueError("نام فایل دانلودی معتبر نیست.")
                    remote_id = str(record.get("remote_id") or "")
                    group_id = str(record.get("group_id") or "")
                    if not remote_id or not group_id:
                        raise ValueError("شناسه فایل دانلودی ناقص است.")
                    if downloaded_remote_id_exists(connection, remote_id):
                        skipped += 1
                        self._emit("info", f"قبلاً دانلود شده و نادیده گرفته شد: {parsed[1]}")
                        continue
                    response = self.context.request.get(
                        DOWNLOAD_URL,
                        headers=headers,
                        max_redirects=0,
                        params={"fileId": remote_id, "groupId": group_id},
                        timeout=120_000,
                    )
                    if not response.ok:
                        raise RuntimeError(f"دانلود با HTTP {response.status} ناموفق بود.")
                    content = response.body()
                    file_hash = hashlib.sha256(content).hexdigest()
                    if downloaded_fingerprint_exists(connection, file_hash):
                        skipped += 1
                        self._emit(
                            "info", f"محتوای تکراری دانلود نشد: {parsed[1]}"
                        )
                        continue
                    destination = self._unused_destination(destination_dir, parsed[1])
                    temporary = None
                    try:
                        with tempfile.NamedTemporaryFile(
                            dir=destination_dir, suffix=".part", delete=False
                        ) as stream:
                            temporary = Path(stream.name)
                            stream.write(content)
                        os.replace(temporary, destination)
                        temporary = None
                    finally:
                        if temporary is not None:
                            temporary.unlink(missing_ok=True)
                    save_download_state(
                        connection,
                        remote_id,
                        file_hash,
                        destination,
                        parsed[0],
                    )
                    downloaded += 1
                    self._emit("success", f"دانلود شد: {destination.name}")
                except Exception as error:
                    failed += 1
                    self._emit("error", f"دانلود ناموفق بود: {error}")
        finally:
            connection.close()
        return {"downloaded": downloaded, "skipped": skipped, "failed": failed}

    def download_available_files(
        self,
        environment: str,
        destination_dir: Path,
        upload_dir: Path | None = None,
        allow_same_origin: bool = False,
        cancelled: threading.Event | None = None,
    ) -> dict:
        empty = {"downloaded": 0, "skipped": 0, "failed": 0}
        if cancelled is not None and cancelled.is_set():
            return empty
        if allow_same_origin:
            records = self.list_downloadable_files(
                environment, allow_same_origin=True
            )
        else:
            records = self.list_downloadable_files(environment)
        if not records or (cancelled is not None and cancelled.is_set()):
            return empty
        if cancelled is not None:
            return self.download_files(
                records, destination_dir, environment, upload_dir,
                allow_same_origin=allow_same_origin, cancelled=cancelled,
            )
        if allow_same_origin:
            return self.download_files(
                records,
                destination_dir,
                environment,
                upload_dir,
                allow_same_origin=True,
            )
        return self.download_files(records, destination_dir, environment, upload_dir)

    def upload_history(self):
        connection = open_state(self.profile_dir.parent / "state.db")
        try:
            return list_upload_history(connection)
        finally:
            connection.close()

    def close(self):
        context, self.context = self.context, None
        playwright, self.playwright = self.playwright, None
        self.page = None
        try:
            if context is not None and not self.context_closed:
                context.close()
        finally:
            self.context_closed = False
            if playwright is not None:
                playwright.stop()

