"""Long-lived browser runtime for FT desktop operations."""

from __future__ import annotations

import itertools
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from captcha_training import (
    decode_image_data_url,
    load_model,
    predict_image,
    save_unlabeled_batch_sample,
)
from ft_auto_uploader import (
    list_upload_history,
    list_candidate_files,
    open_state,
    process_file,
    select_files_for_run,
    wait_for_file_manager,
)
from ft_browser import launch_persistent_session


LOGIN_URL = "https://ft.nioc.ir/dargah/user/sign-in"
DEFAULT_CAPTCHA_SELECTOR = "img[src^='data:image/']"
USERNAME_SELECTOR = "div.itc-card__content > div > div:nth-of-type(1) input"
PASSWORD_SELECTOR = "div.itc-card__content > div > div:nth-of-type(2) input"
CAPTCHA_INPUT_SELECTOR = "div:nth-of-type(3) input"


@dataclass(frozen=True)
class RuntimeResult:
    """Result of one serialized browser operation."""

    task_id: str
    operation: str
    succeeded: bool
    payload: Any


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
        self, profile_dir: Path, results: queue.Queue[RuntimeResult | RuntimeLog]
    ):
        self.profile_dir = Path(profile_dir)
        self.results = results
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ft-browser")
        self.playwright = None
        self.context = None
        self.page = None
        self.batch_cancelled = threading.Event()
        self._task_numbers = itertools.count(1)

    def submit(self, operation: str, function: Callable, *args) -> str:
        task_id = f"{operation}-{next(self._task_numbers)}"
        future = self.executor.submit(function, *args)

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

    def open(self):
        if self.context is None:
            from playwright.sync_api import sync_playwright

            self.playwright = sync_playwright().start()
            self.context, self.page = launch_persistent_session(
                self.playwright, self.profile_dir
            )
        self.page.goto(LOGIN_URL, wait_until="domcontentloaded")
        return "مرورگر باز شد و برای عملیات بعدی باز می‌ماند."

    def manual_login(self):
        if self.page is None:
            self.open()
        else:
            self.page.goto(LOGIN_URL, wait_until="domcontentloaded")
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

    def login(
        self,
        username: str,
        password: str,
        selector: str,
        model_dir: Path,
        minimum_confidence: float,
    ):
        if not username or not password:
            raise ValueError("نام کاربری و رمز عبور را وارد کنید.")
        if self.page is None:
            self.open()
        model_bundle = load_model(model_dir)
        last_prediction = None
        for attempt in range(1, 4):
            if "/filemanager/" in self.page.url:
                return "ورود قبلاً انجام شده است."
            self.page.locator(USERNAME_SELECTOR).fill(username)
            self.page.locator(PASSWORD_SELECTOR).fill(password)
            image_bytes = capture_captcha_bytes(self.page, selector)
            prediction, confidence = predict_image(image_bytes, model_bundle)
            last_prediction = f"{prediction} ({confidence:.1%})"
            if confidence < minimum_confidence:
                self.page.reload(wait_until="domcontentloaded")
                continue
            captcha_input = self.page.locator(CAPTCHA_INPUT_SELECTOR)
            captcha_input.fill(prediction)
            captcha_input.press("Enter")
            try:
                self.page.wait_for_url("**/filemanager/**", timeout=10_000)
                return f"ورود موفق؛ CAPTCHA: {last_prediction}"
            except Exception:
                if attempt < 3:
                    self.page.goto(LOGIN_URL, wait_until="domcontentloaded")
        raise RuntimeError(f"ورود پس از ۳ تلاش موفق نشد؛ آخرین تشخیص: {last_prediction}")

    def upload_folder(
        self,
        folder: Path,
        max_files: int,
        max_file_size_mb: float,
        scan_timeout_minutes: float = 30.0,
        login_timeout_minutes: float = 15.0,
    ):
        folder = Path(folder)
        if not folder.is_dir():
            raise ValueError("پوشه انتخاب‌شده وجود ندارد.")
        if max_files < 0 or max_file_size_mb < 0:
            raise ValueError("محدودیت تعداد و حجم نباید منفی باشد.")
        if self.page is None:
            self.open()

        def manual_login(upload_button, timeout_ms):
            self._emit("warning", "ورود لازم است؛ ورود دستی را در مرورگر کامل کنید.")
            upload_button.wait_for(state="visible", timeout=timeout_ms)

        wait_for_file_manager(
            self.page,
            int(login_timeout_minutes * 60_000),
            manual_login,
        )
        selected, oversized = select_files_for_run(
            list_candidate_files(folder),
            max_files,
            int(max_file_size_mb * 1024 * 1024),
        )
        for path in oversized:
            self._emit("warning", f"به‌دلیل عبور از سقف حجم رد شد: {path.name}")
        if not selected:
            return {"processed": 0, "failed": 0, "oversized": len(oversized)}

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

    def upload_history(self):
        connection = open_state(self.profile_dir.parent / "state.db")
        try:
            return list_upload_history(connection)
        finally:
            connection.close()

    def close(self):
        if self.context is not None:
            self.context.close()
            self.context = None
            self.page = None
        if self.playwright is not None:
            self.playwright.stop()
            self.playwright = None

