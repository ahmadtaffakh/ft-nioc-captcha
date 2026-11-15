"""Tkinter UI for collecting, labeling, training, and using FT CAPTCHA data."""

from __future__ import annotations

import io
import queue
import sys
import threading
import time
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from captcha_training import (
    import_labeled_batch,
    load_model,
    predict_image,
    read_labels,
    save_labeled_sample,
    train_model,
)
from ft_browser import app_data_dir
from ft_runtime import (
    BrowserSession,
    DEFAULT_CAPTCHA_SELECTOR,
    RuntimeResult,
    capture_captcha_bytes,
)
from ft_settings import (
    APP_VERSION,
    AppSettings,
    delete_credentials,
    load_credentials,
    load_settings,
    save_credentials,
    save_settings,
)


class CaptchaLabApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"آزمایشگاه CAPTCHA سامانه FT — نسخه {APP_VERSION}")
        self.root.geometry("920x860")
        self.root.minsize(820, 720)
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
        self.startup_warnings = []

        try:
            settings = load_settings(self.base_dir)
        except ValueError as error:
            settings = AppSettings()
            self.startup_warnings.append(str(error))

        stored_username = ""
        stored_password = ""
        try:
            credentials = load_credentials(self.base_dir)
        except ValueError as error:
            self.startup_warnings.append(str(error))
        else:
            if credentials:
                stored_username, stored_password = credentials

        self.selector = tk.StringVar(value=settings.selector or DEFAULT_CAPTCHA_SELECTOR)
        self.label = tk.StringVar()
        self.sequence_length = tk.IntVar(value=settings.sequence_length)
        self.batch_count = tk.IntVar(value=settings.batch_count)
        self.reload_delay_seconds = tk.DoubleVar(value=settings.reload_delay_seconds)
        self.epochs = tk.IntVar(value=settings.epochs)
        self.minimum_confidence = tk.DoubleVar(value=settings.minimum_confidence)
        self.username = tk.StringVar(value=stored_username)
        self.password = tk.StringVar(value=stored_password)
        self.remember_credentials = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value="آماده")
        self.sample_count = tk.StringVar()

        self._build_ui()
        for warning in self.startup_warnings:
            self._log("warning", warning)
        if stored_username:
            self._log("info", "اطلاعات ورود امن برای این حساب Windows بازیابی شد.")
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
        ttk.Label(browser, text="تأخیر بعد از Reload (ثانیه):").grid(
            row=2, column=2, sticky="e"
        )
        ttk.Spinbox(
            browser,
            from_=0,
            to=60,
            increment=0.5,
            textvariable=self.reload_delay_seconds,
            width=8,
        ).grid(row=2, column=3, sticky="w", padx=6)
        ttk.Button(browser, text="دریافت خودکار", command=self._collect_batch).grid(
            row=3, column=1, pady=4
        )
        ttk.Button(browser, text="توقف", command=self.browser.cancel_batch).grid(
            row=3, column=2, pady=4
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
        ttk.Button(login, text="ورود دستی", command=self._manual_login).grid(
            row=1, column=2, pady=6, padx=4
        )
        ttk.Button(login, text="ورود خودکار (حداکثر ۳ تلاش)", command=self._login).grid(
            row=1, column=3, pady=6, padx=4
        )
        ttk.Checkbutton(
            login,
            text="ذخیره امن پس از ورود موفق",
            variable=self.remember_credentials,
        ).grid(row=2, column=0, columnspan=2, sticky="e", pady=4)
        ttk.Button(login, text="ذخیره تنظیمات", command=self._save_preferences).grid(
            row=2, column=2, pady=4, padx=4
        )
        ttk.Button(login, text="پاک کردن اطلاعات ورود", command=self._forget_credentials).grid(
            row=2, column=3, pady=4, padx=4
        )
        ttk.Label(
            login,
            text="رمز با Windows DPAPI محافظت می‌شود و در خروجی نمایش داده نمی‌شود.",
        ).grid(row=3, column=0, columnspan=4, pady=(2, 0))

        ttk.Separator(container).pack(fill="x", pady=6)
        ttk.Label(container, textvariable=self.status, anchor="center").pack(fill="x")

        output_header = ttk.Frame(container)
        output_header.pack(fill="x", pady=(8, 4))
        ttk.Label(output_header, text="خروجی عملیات").pack(side="right")
        ttk.Button(output_header, text="کپی کل", command=self._copy_log).pack(side="left")
        ttk.Button(output_header, text="پاک کردن", command=self._clear_log).pack(
            side="left", padx=6
        )
        self.output = scrolledtext.ScrolledText(
            container,
            height=10,
            wrap="word",
            state="disabled",
            font=("Cascadia Mono", 9),
        )
        self.output.pack(fill="both", expand=True)
        self.output.tag_configure("info", foreground="#1565C0")
        self.output.tag_configure("success", foreground="#2E7D32")
        self.output.tag_configure("warning", foreground="#9A6700")
        self.output.tag_configure("error", foreground="#C62828")
        self._log("info", "برنامه آماده است؛ مرورگر در تمام عملیات باز می‌ماند.")
        ttk.Label(container, text=f"نسخه {APP_VERSION}", anchor="w").pack(fill="x", pady=(4, 0))

    def _log(self, level, message):
        timestamp = time.strftime("%H:%M:%S")
        self.output.configure(state="normal")
        self.output.insert("end", f"[{timestamp}] {message}\n", level)
        self.output.configure(state="disabled")
        self.output.see("end")

    def _copy_log(self):
        text = self.output.get("1.0", "end-1c")
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status.set("کل خروجی کپی شد.")

    def _clear_log(self):
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.configure(state="disabled")

    def _current_settings(self):
        try:
            settings = AppSettings(
                selector=self.selector.get(),
                sequence_length=self.sequence_length.get(),
                batch_count=self.batch_count.get(),
                reload_delay_seconds=self.reload_delay_seconds.get(),
                epochs=self.epochs.get(),
                minimum_confidence=self.minimum_confidence.get(),
            )
            settings.validate()
        except (tk.TclError, ValueError) as error:
            raise ValueError(str(error) or "مقادیر تنظیمات معتبر نیستند.") from error
        return settings

    def _save_preferences(self, announce=True):
        try:
            save_settings(self.base_dir, self._current_settings())
        except (OSError, ValueError) as error:
            if announce:
                self._log("error", f"ذخیره تنظیمات ناموفق بود: {error}")
                messagebox.showerror("خطا", str(error))
            return False
        if announce:
            self.status.set("تنظیمات ذخیره شد.")
            self._log("success", "تنظیمات غیرحساس ذخیره شد.")
        return True

    def _forget_credentials(self):
        if not messagebox.askyesno(
            "پاک کردن اطلاعات ورود",
            "نام کاربری و رمز ذخیره‌شده از این حساب Windows پاک شود؟",
        ):
            return
        try:
            removed = delete_credentials(self.base_dir)
        except OSError as error:
            self._log("error", f"پاک کردن اطلاعات ورود ناموفق بود: {error}")
            messagebox.showerror("خطا", str(error))
            return
        self.username.set("")
        self.password.set("")
        message = "اطلاعات ورود ذخیره‌شده پاک شد." if removed else "اطلاعات ورودی ذخیره نشده بود."
        self.status.set(message)
        self._log("warning" if removed else "info", message)

    def _submit(self, operation_name, operation, *args, success=None):
        self.status.set("در حال انجام...")
        self._log("info", f"شروع عملیات: {operation_name}")
        task_id = self.browser.submit(operation_name, operation, *args)
        self.callbacks[task_id] = success

    def _open_browser(self):
        self._submit("open", self.browser.open)

    def _capture(self):
        self._submit("capture", self.browser.capture, self.selector.get(), success=self._show_image)

    def _refresh(self):
        self._submit("refresh", self.browser.refresh)

    def _manual_login(self):
        self._submit("manual_login", self.browser.manual_login)

    def _collect_batch(self):
        try:
            count = self.batch_count.get()
            reload_delay = self.reload_delay_seconds.get()
        except tk.TclError:
            messagebox.showerror("خطا", "تعداد Batch و تأخیر Reload باید عددی باشند.")
            return
        if not 1 <= count <= 10000:
            messagebox.showerror("خطا", "تعداد Batch باید بین ۱ و ۱۰۰۰۰ باشد.")
            return
        if not 0 <= reload_delay <= 60:
            messagebox.showerror("خطا", "تأخیر Reload باید بین صفر و ۶۰ ثانیه باشد.")
            return
        self._save_preferences(announce=False)
        batch_dir = self.batch_root / f"batch-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1000:03d}"
        self._submit(
            "collect_batch",
            self.browser.collect_batch,
            self.selector.get(),
            batch_dir,
            count,
            reload_delay,
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
        self._log("success", f"نمونه ذخیره شد: {path.name}")
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
                self.results.put(RuntimeResult("import_batch", "import_batch", True, count))
            except Exception as error:
                self.results.put(
                    RuntimeResult("import_batch", "import_batch", False, str(error))
                )

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
                self.results.put(RuntimeResult("train", "train", True, result))
            except Exception as error:
                self.results.put(RuntimeResult("train", "train", False, str(error)))

        threading.Thread(target=work, daemon=True).start()

    def _predict_current(self):
        if not self.current_image:
            messagebox.showerror("خطا", "ابتدا یک CAPTCHA دریافت کنید.")
            return
        image_bytes = bytes(self.current_image)

        def work():
            try:
                prediction = predict_image(image_bytes, load_model(self.model_dir))
                self.results.put(RuntimeResult("predict", "predict", True, prediction))
            except Exception as error:
                self.results.put(RuntimeResult("predict", "predict", False, str(error)))

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
        self._save_preferences(announce=False)
        self._submit(
            "login",
            self.browser.login,
            self.username.get(),
            self.password.get(),
            self.selector.get(),
            self.model_dir,
            minimum_confidence,
            success=self._after_login,
        )

    def _after_login(self, message):
        if not self.remember_credentials.get() or not message.startswith("ورود موفق"):
            return
        try:
            save_credentials(self.base_dir, self.username.get(), self.password.get())
        except (OSError, RuntimeError, ValueError) as error:
            self._log("error", f"ذخیره امن اطلاعات ورود ناموفق بود: {error}")
            messagebox.showerror("خطا", str(error))
            return
        self._log("success", "نام کاربری و رمز به‌صورت امن برای این حساب Windows ذخیره شد.")

    def _poll_results(self):
        while True:
            try:
                result = self.results.get_nowait()
            except queue.Empty:
                break
            task_id = result.task_id
            operation = result.operation
            succeeded = result.succeeded
            payload = result.payload
            callback = self.callbacks.pop(task_id, None)
            if operation == "close":
                self.browser.executor.shutdown(wait=False, cancel_futures=True)
                self.root.destroy()
                return
            if not succeeded:
                self.status.set("عملیات ناموفق بود.")
                self._log("error", f"{operation}: {payload}")
                messagebox.showerror("خطا", payload)
                continue
            if callback:
                callback(payload)
            if operation == "collect_batch":
                state = "کامل شد" if payload["count"] == payload["requested"] else "متوقف شد"
                message = f"Batch {state}؛ {payload['count']} تصویر: {payload['batch_dir']}"
                self.status.set(message)
                self._log("success" if state == "کامل شد" else "warning", message)
            elif operation == "import_batch":
                self._update_sample_count()
                message = f"{payload} نمونه از Batch وارد Dataset شد."
                self.status.set(message)
                self._log("success", message)
            elif operation == "train":
                message = (
                    f"آموزش تمام شد؛ {payload['samples']} نمونه، "
                    f"دقت کامل اعتبارسنجی: {payload['exact_match']:.1%}"
                )
                self.status.set(message)
                self._log("success", message)
            elif operation == "predict":
                text, confidence = payload
                message = f"تشخیص: {text} — حداقل اطمینان: {confidence:.1%}"
                self.status.set(message)
                self._log("success", message)
            elif isinstance(payload, str):
                self.status.set(payload)
                self._log("success", payload)
            else:
                self.status.set("عملیات با موفقیت انجام شد.")
                self._log("success", f"عملیات {operation} با موفقیت انجام شد.")
        self.root.after(100, self._poll_results)

    def _on_close(self):
        if self.closing:
            return
        if not messagebox.askyesno(
            "خروج از برنامه",
            "مرورگر بین عملیات باز می‌ماند. با خروج کامل از برنامه، مرورگر هم بسته می‌شود. ادامه می‌دهید؟",
        ):
            return
        self._save_preferences(announce=False)
        self.closing = True
        self.browser.cancel_batch()
        self.status.set("در حال بستن مرورگر...")
        self._log("warning", "در حال بستن برنامه و مرورگر...")
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
