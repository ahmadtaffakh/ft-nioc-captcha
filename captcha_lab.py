"""Tkinter UI for collecting, labeling, training, and using FT CAPTCHA data."""

from __future__ import annotations

import io
import queue
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from captcha_training import (
    decode_image_data_url,
    import_labeled_batch,
    load_model,
    predict_image,
    read_labels,
    save_labeled_sample,
    save_unlabeled_batch_sample,
    train_model,
)
from ft_browser import app_data_dir, launch_persistent_session


LOGIN_URL = "https://ft.nioc.ir/dargah/user/sign-in"
DEFAULT_CAPTCHA_SELECTOR = "img[src^='data:image/']"
USERNAME_SELECTOR = "div.itc-card__content > div > div:nth-of-type(1) input"
PASSWORD_SELECTOR = "div.itc-card__content > div > div:nth-of-type(2) input"
CAPTCHA_INPUT_SELECTOR = "div:nth-of-type(3) input"


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
    """Keep all synchronous Playwright access on one dedicated thread."""

    def __init__(self, profile_dir: Path, results: queue.Queue):
        self.profile_dir = Path(profile_dir)
        self.results = results
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ft-browser")
        self.playwright = None
        self.context = None
        self.page = None
        self.batch_cancelled = threading.Event()

    def submit(self, token: str, operation, *args) -> None:
        future = self.executor.submit(operation, *args)

        def completed(done):
            try:
                self.results.put((token, True, done.result()))
            except Exception as error:
                self.results.put((token, False, str(error)))

        future.add_done_callback(completed)

    def open(self):
        if self.context is None:
            from playwright.sync_api import sync_playwright

            self.playwright = sync_playwright().start()
            self.context, self.page = launch_persistent_session(
                self.playwright, self.profile_dir
            )
        self.page.goto(LOGIN_URL, wait_until="domcontentloaded")
        return "مرورگر باز شد."

    def capture(self, selector: str):
        if self.page is None:
            raise RuntimeError("ابتدا مرورگر را باز کنید.")
        return capture_captcha_bytes(self.page, selector)

    def refresh(self):
        if self.page is None:
            raise RuntimeError("ابتدا مرورگر را باز کنید.")
        self.page.reload(wait_until="domcontentloaded")
        return "CAPTCHA تازه دریافت شد."

    def collect_batch(self, selector: str, batch_dir: Path, count: int):
        if self.page is None:
            raise RuntimeError("ابتدا مرورگر را باز کنید.")
        if count < 1:
            raise ValueError("تعداد نمونه باید حداقل یک باشد.")
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

    def close(self):
        if self.context is not None:
            self.context.close()
            self.context = None
            self.page = None
        if self.playwright is not None:
            self.playwright.stop()
            self.playwright = None


class CaptchaLabApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("آزمایشگاه CAPTCHA سامانه FT")
        self.root.geometry("820x680")
        self.base_dir = app_data_dir()
        self.dataset_dir = self.base_dir / "captcha-data"
        self.batch_root = self.base_dir / "captcha-batches"
        self.model_dir = self.base_dir / "captcha-model"
        self.results = queue.Queue()
        self.callbacks = {}
        self.browser = BrowserSession(self.base_dir / "browser-profile", self.results)
        self.current_image = None
        self.preview_reference = None
        self.closing = False

        self.selector = tk.StringVar(value=DEFAULT_CAPTCHA_SELECTOR)
        self.label = tk.StringVar()
        self.sequence_length = tk.IntVar(value=5)
        self.batch_count = tk.IntVar(value=1000)
        self.epochs = tk.IntVar(value=40)
        self.minimum_confidence = tk.DoubleVar(value=0.75)
        self.username = tk.StringVar()
        self.password = tk.StringVar()
        self.status = tk.StringVar(value="آماده")
        self.sample_count = tk.StringVar()

        self._build_ui()
        self._update_sample_count()
        self.root.after(100, self._poll_results)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        container = ttk.Frame(self.root, padding=14)
        container.pack(fill="both", expand=True)

        browser = ttk.LabelFrame(container, text="۱) دریافت تصویر از سامانه", padding=10)
        browser.pack(fill="x")
        ttk.Label(browser, text="Selector تصویر:").grid(row=0, column=0, sticky="e")
        ttk.Entry(browser, textvariable=self.selector, width=70).grid(
            row=0, column=1, columnspan=3, sticky="ew", padx=6
        )
        ttk.Button(browser, text="بازکردن مرورگر", command=self._open_browser).grid(row=1, column=1, pady=8)
        ttk.Button(browser, text="دریافت CAPTCHA", command=self._capture).grid(row=1, column=2, pady=8)
        ttk.Button(browser, text="تازه‌سازی", command=self._refresh).grid(row=1, column=3, pady=8)
        ttk.Label(browser, text="تعداد Batch:").grid(row=2, column=0, sticky="e")
        ttk.Spinbox(browser, from_=1, to=10000, textvariable=self.batch_count, width=8).grid(
            row=2, column=1, sticky="w", padx=6
        )
        ttk.Button(browser, text="دریافت خودکار", command=self._collect_batch).grid(
            row=2, column=2, pady=4
        )
        ttk.Button(browser, text="توقف", command=self.browser.cancel_batch).grid(
            row=2, column=3, pady=4
        )
        browser.columnconfigure(1, weight=1)

        labeling = ttk.LabelFrame(container, text="۲) پیش‌نمایش و برچسب‌گذاری", padding=10)
        labeling.pack(fill="x", pady=10)
        self.preview = ttk.Label(labeling, text="هنوز تصویری دریافت نشده است.", anchor="center")
        self.preview.grid(row=0, column=0, columnspan=4, sticky="ew", pady=8)
        ttk.Label(labeling, text="برچسب صحیح:").grid(row=1, column=0, sticky="e")
        label_entry = ttk.Entry(labeling, textvariable=self.label, width=20, justify="center")
        label_entry.grid(row=1, column=1, padx=6)
        label_entry.bind("<Return>", lambda _event: self._save_sample())
        ttk.Label(labeling, text="تعداد ارقام:").grid(row=1, column=2, sticky="e")
        ttk.Spinbox(labeling, from_=1, to=10, textvariable=self.sequence_length, width=5).grid(row=1, column=3)
        ttk.Button(labeling, text="ذخیره نمونه و برچسب", command=self._save_sample).grid(
            row=2, column=1, pady=8
        )
        ttk.Label(labeling, textvariable=self.sample_count).grid(row=2, column=2, columnspan=2)
        ttk.Button(
            labeling, text="ورود Batch برچسب‌خورده", command=self._import_batch
        ).grid(row=3, column=1, columnspan=2, pady=4)
        labeling.columnconfigure(0, weight=1)
        labeling.columnconfigure(3, weight=1)

        training = ttk.LabelFrame(container, text="۳) آموزش و آزمون مدل", padding=10)
        training.pack(fill="x")
        ttk.Label(training, text="Epoch:").grid(row=0, column=0)
        ttk.Spinbox(training, from_=1, to=500, textvariable=self.epochs, width=7).grid(row=0, column=1, padx=5)
        ttk.Button(training, text="آموزش مدل", command=self._train).grid(row=0, column=2, padx=10)
        ttk.Button(training, text="آزمون روی تصویر فعلی", command=self._predict_current).grid(row=0, column=3)

        login = ttk.LabelFrame(container, text="۴) ورود آزمایشی با مدل آموزش‌دیده", padding=10)
        login.pack(fill="x", pady=10)
        ttk.Label(login, text="نام کاربری:").grid(row=0, column=0, sticky="e")
        ttk.Entry(login, textvariable=self.username, width=28).grid(row=0, column=1, padx=6)
        ttk.Label(login, text="رمز عبور:").grid(row=1, column=0, sticky="e")
        ttk.Entry(login, textvariable=self.password, show="●", width=28).grid(row=1, column=1, padx=6)
        ttk.Label(login, text="حداقل اطمینان:").grid(row=0, column=2, sticky="e")
        ttk.Spinbox(
            login, from_=0.0, to=1.0, increment=0.05,
            textvariable=self.minimum_confidence, width=7
        ).grid(row=0, column=3, padx=6)
        ttk.Button(login, text="ورود خودکار (حداکثر ۳ تلاش)", command=self._login).grid(
            row=1, column=2, columnspan=2, pady=6
        )

        ttk.Separator(container).pack(fill="x", pady=6)
        ttk.Label(container, textvariable=self.status, anchor="center").pack(fill="x")

    def _submit(self, token, operation, *args, success=None):
        self.status.set("در حال انجام...")
        self.callbacks[token] = success
        self.browser.submit(token, operation, *args)

    def _open_browser(self):
        self._submit("open", self.browser.open)

    def _capture(self):
        self._submit("capture", self.browser.capture, self.selector.get(), success=self._show_image)

    def _refresh(self):
        self._submit("refresh", self.browser.refresh)

    def _collect_batch(self):
        try:
            count = self.batch_count.get()
        except tk.TclError:
            messagebox.showerror("خطا", "تعداد Batch باید عدد صحیح باشد.")
            return
        if not 1 <= count <= 10000:
            messagebox.showerror("خطا", "تعداد Batch باید بین ۱ و ۱۰۰۰۰ باشد.")
            return
        batch_dir = self.batch_root / f"batch-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1000:03d}"
        self._submit(
            "collect_batch",
            self.browser.collect_batch,
            self.selector.get(),
            batch_dir,
            count,
        )

    def _show_image(self, image_bytes):
        from PIL import Image, ImageTk

        self.current_image = image_bytes
        with Image.open(io.BytesIO(image_bytes)) as image:
            preview = image.convert("RGB")
            preview.thumbnail((500, 120), Image.Resampling.LANCZOS)
            self.preview_reference = ImageTk.PhotoImage(preview)
        self.preview.configure(image=self.preview_reference, text="")
        self.label.set("")

    def _save_sample(self):
        try:
            path = save_labeled_sample(
                self.dataset_dir,
                self.current_image,
                self.label.get(),
                self.sequence_length.get(),
            )
        except Exception as error:
            messagebox.showerror("خطا", str(error))
            return
        self.status.set(f"نمونه ذخیره شد: {path.name}")
        self.current_image = None
        self.preview.configure(image="", text="نمونه ذخیره شد؛ CAPTCHA بعدی را دریافت کنید.")
        self.preview_reference = None
        self.label.set("")
        self._update_sample_count()

    def _update_sample_count(self):
        try:
            count = len(read_labels(self.dataset_dir))
        except Exception as error:
            self.sample_count.set(f"خطای dataset: {error}")
        else:
            self.sample_count.set(f"تعداد نمونه‌های برچسب‌خورده: {count}")

    def _import_batch(self):
        selected = filedialog.askdirectory(
            title="پوشه Batch برچسب‌خورده را انتخاب کنید",
            initialdir=str(self.batch_root),
        )
        if not selected:
            return
        try:
            sequence_length = self.sequence_length.get()
        except tk.TclError:
            messagebox.showerror("خطا", "تعداد ارقام باید عدد صحیح باشد.")
            return

        def work():
            try:
                count = import_labeled_batch(
                    Path(selected), self.dataset_dir, sequence_length
                )
                self.results.put(("import_batch", True, count))
            except Exception as error:
                self.results.put(("import_batch", False, str(error)))

        self.status.set("در حال ورود Batch...")
        threading.Thread(target=work, daemon=True).start()

    def _train(self):
        if not messagebox.askyesno(
            "آموزش مدل",
            "آموزش ممکن است چند دقیقه طول بکشد. ادامه می‌دهید؟",
        ):
            return
        try:
            sequence_length = self.sequence_length.get()
            epochs = self.epochs.get()
        except tk.TclError:
            messagebox.showerror("خطا", "تعداد ارقام و Epoch باید عدد صحیح باشند.")
            return
        self.status.set("مدل در حال آموزش است...")

        def work():
            try:
                result = train_model(
                    self.dataset_dir,
                    self.model_dir,
                    sequence_length,
                    epochs,
                )
                self.results.put(("train", True, result))
            except Exception as error:
                self.results.put(("train", False, str(error)))

        threading.Thread(target=work, daemon=True).start()

    def _predict_current(self):
        if not self.current_image:
            messagebox.showerror("خطا", "ابتدا یک CAPTCHA دریافت کنید.")
            return
        image_bytes = bytes(self.current_image)

        def work():
            try:
                prediction = predict_image(image_bytes, load_model(self.model_dir))
                self.results.put(("predict", True, prediction))
            except Exception as error:
                self.results.put(("predict", False, str(error)))

        self.status.set("در حال پیش‌بینی...")
        threading.Thread(target=work, daemon=True).start()

    def _login(self):
        try:
            minimum_confidence = self.minimum_confidence.get()
        except tk.TclError:
            messagebox.showerror("خطا", "حداقل اطمینان باید عددی بین صفر و یک باشد.")
            return
        if not 0 <= minimum_confidence <= 1:
            messagebox.showerror("خطا", "حداقل اطمینان باید بین صفر و یک باشد.")
            return
        self._submit(
            "login",
            self.browser.login,
            self.username.get(),
            self.password.get(),
            self.selector.get(),
            self.model_dir,
            minimum_confidence,
        )

    def _poll_results(self):
        while True:
            try:
                token, succeeded, payload = self.results.get_nowait()
            except queue.Empty:
                break
            callback = self.callbacks.pop(token, None)
            if token == "close":
                self.browser.executor.shutdown(wait=False, cancel_futures=True)
                self.root.destroy()
                return
            if not succeeded:
                self.status.set("عملیات ناموفق بود.")
                messagebox.showerror("خطا", payload)
                continue
            if callback:
                callback(payload)
            if token == "collect_batch":
                state = "کامل شد" if payload["count"] == payload["requested"] else "متوقف شد"
                self.status.set(
                    f"Batch {state}؛ {payload['count']} تصویر: {payload['batch_dir']}"
                )
            elif token == "import_batch":
                self._update_sample_count()
                self.status.set(f"{payload} نمونه از Batch وارد Dataset شد.")
            elif token == "train":
                self.status.set(
                    f"آموزش تمام شد؛ {payload['samples']} نمونه، "
                    f"دقت کامل اعتبارسنجی: {payload['exact_match']:.1%}"
                )
            elif token == "predict":
                text, confidence = payload
                self.status.set(f"تشخیص: {text} — حداقل اطمینان: {confidence:.1%}")
            elif isinstance(payload, str):
                self.status.set(payload)
            else:
                self.status.set("عملیات با موفقیت انجام شد.")
        self.root.after(100, self._poll_results)

    def _on_close(self):
        if self.closing:
            return
        self.closing = True
        self.browser.cancel_batch()
        self.status.set("در حال بستن مرورگر...")
        self.browser.submit("close", self.browser.close)


def main() -> None:
    root = tk.Tk()
    CaptchaLabApp(root)
    root.mainloop()


def portable_self_test() -> int:
    """Exercise frozen ML, Tk, and bundled Playwright dependencies."""
    import base64
    from io import BytesIO

    from PIL import Image
    from playwright.sync_api import sync_playwright

    from captcha_training import build_model

    model = build_model(5)
    if model.output_shape != (None, 5, 10):
        raise RuntimeError(f"Unexpected model output shape: {model.output_shape}")

    buffer = BytesIO()
    Image.new("RGB", (150, 40), (120, 190, 140)).save(buffer, format="PNG")
    expected = buffer.getvalue()
    data_url = "data:image/png;base64," + base64.b64encode(expected).decode("ascii")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chromium", headless=True)
        page = browser.new_page()
        page.set_content(
            f'<img class="captcha" width="150" height="40" src="{data_url}">'
        )
        actual = capture_captcha_bytes(page, "img.captcha")
        browser.close()
    if actual != expected:
        raise RuntimeError("Bundled browser did not return the original CAPTCHA bytes.")

    root = tk.Tk()
    root.withdraw()
    root.update_idletasks()
    root.destroy()
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        raise SystemExit(portable_self_test())
    main()
