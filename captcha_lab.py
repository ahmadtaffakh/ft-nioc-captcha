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
    predict_batch,
    predict_image,
    read_labels,
    save_labeled_sample,
    train_model,
)
from ft_browser import app_data_dir
from ft_runtime import (
    BrowserSession,
    DEFAULT_CAPTCHA_SELECTOR,
    RuntimeLog,
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


COLORS = {
    "background": "#F3F6FA",
    "surface": "#FFFFFF",
    "navy": "#17365D",
    "primary": "#245A92",
    "primary_active": "#1B4777",
    "text": "#1F2937",
    "muted": "#667085",
    "border": "#D0D5DD",
    "danger": "#B42318",
    "danger_active": "#912018",
    "log_background": "#111827",
    "log_text": "#E5E7EB",
}

AUTO_UPLOAD_INTERVAL_MS = 10_000
ENVIRONMENT_LABELS = {
    "": "انتخاب نشده",
    "thinclient": "تین‌کلاینت",
    "intranet": "اینترانت",
}
ENVIRONMENT_CODES = {label: code for code, label in ENVIRONMENT_LABELS.items()}


def add_tooltip(widget, text: str, delay_ms: int = 500) -> None:
    """Attach a keyboard- and mouse-accessible tooltip to one Tk widget."""
    tooltip = None
    pending = None
    widget._tooltip_text = text

    def hide(_event=None):
        nonlocal tooltip, pending
        if pending is not None:
            widget.after_cancel(pending)
            pending = None
        if tooltip is not None:
            tooltip.destroy()
            tooltip = None

    def show():
        nonlocal tooltip, pending
        pending = None
        if tooltip is not None or not widget.winfo_exists():
            return
        tooltip = tk.Toplevel(widget)
        tooltip.wm_overrideredirect(True)
        tooltip.wm_attributes("-topmost", True)
        x = widget.winfo_rootx() + widget.winfo_width() - 12
        y = widget.winfo_rooty() + widget.winfo_height() + 6
        tooltip.wm_geometry(f"+{x}+{y}")
        tk.Label(
            tooltip,
            text=text,
            justify="right",
            background="#FFF8D6",
            foreground=COLORS["text"],
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=5,
            font=("Tahoma", 9),
            wraplength=360,
        ).pack()

    def schedule(_event=None):
        nonlocal pending
        hide()
        pending = widget.after(delay_ms, show)

    widget.bind("<Enter>", schedule, add="+")
    widget.bind("<Leave>", hide, add="+")
    widget.bind("<FocusIn>", schedule, add="+")
    widget.bind("<FocusOut>", hide, add="+")
    widget.bind("<ButtonPress>", hide, add="+")


class CaptchaLabApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"آزمایشگاه CAPTCHA سامانه FT — نسخه {APP_VERSION}")
        self.root.geometry("980x720")
        self.root.minsize(860, 640)
        self.base_dir = app_data_dir()
        self.dataset_dir = self.base_dir / "captcha-data"
        self.batch_root = self.base_dir / "captcha-batches"
        self.model_dir = self.base_dir / "captcha-model"
        self.results = queue.Queue()
        self.callbacks = {}
        self.active_tasks = set()
        self.browser = BrowserSession(self.base_dir / "browser-profile", self.results)
        self.current_image = None
        self.preview_reference = None
        self.closing = False
        self.upload_task_id = None
        self.download_task_id = None
        self.auto_upload_suspended = False
        self.auto_upload_warning = None
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
        self.login_attempts = tk.IntVar(value=settings.login_attempts)
        self.watch_dir = tk.StringVar(value=settings.watch_dir)
        self.max_files_per_run = tk.IntVar(value=settings.max_files_per_run)
        self.max_file_size_mb = tk.DoubleVar(value=settings.max_file_size_mb)
        self.auto_upload_enabled = tk.BooleanVar(value=settings.auto_upload_enabled)
        self.environment_label = tk.StringVar(
            value=ENVIRONMENT_LABELS[settings.environment]
        )
        self.download_dir = tk.StringVar(value=settings.download_dir)
        self.remote_downloads = {}
        self.username = tk.StringVar(value=stored_username)
        self.password = tk.StringVar(value=stored_password)
        self.remember_credentials = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value="آماده")
        self.activity_status = tk.StringVar(value="آماده")
        self.sample_count = tk.StringVar()

        self._build_ui()
        for warning in self.startup_warnings:
            self._log("warning", warning)
        if stored_username:
            self._log("info", "اطلاعات ورود امن برای این حساب Windows بازیابی شد.")
        self._update_sample_count()
        self.root.after(100, self._poll_results)
        self.root.after(AUTO_UPLOAD_INTERVAL_MS, self._auto_upload_tick)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        self._configure_styles()
        container = ttk.Frame(self.root, padding=(18, 14, 18, 10))
        container.pack(fill="both", expand=True)

        header = ttk.Frame(container, style="Header.TFrame", padding=(16, 12))
        header.pack(fill="x", pady=(0, 12))
        title_group = ttk.Frame(header, style="Header.TFrame")
        title_group.pack(side="right", fill="x", expand=True)
        ttk.Label(
            title_group,
            text="سامانه سازمانی مدیریت CAPTCHA و انتقال فایل FT",
            style="HeaderTitle.TLabel",
            anchor="e",
        ).pack(fill="x")
        ttk.Label(
            title_group,
            text="محیط کنترل، آموزش و آزمون ورود به سامانه",
            style="HeaderSubtitle.TLabel",
            anchor="e",
        ).pack(fill="x", pady=(3, 0))
        ttk.Label(
            header,
            text=f"نسخه {APP_VERSION}",
            style="VersionBadge.TLabel",
        ).pack(side="left", padx=(0, 12))

        self.notebook = ttk.Notebook(container)
        self.notebook.pack(fill="both", expand=True)
        self.browser_tab = ttk.Frame(self.notebook, padding=16)
        self.data_tab = ttk.Frame(self.notebook, padding=16)
        self.login_tab = ttk.Frame(self.notebook, padding=16)
        self.upload_tab = ttk.Frame(self.notebook, padding=16)
        self.download_tab = ttk.Frame(self.notebook, padding=16)
        self.output_tab = ttk.Frame(self.notebook, padding=16)
        self.notebook.add(self.browser_tab, text="  اتصال و CAPTCHA  ")
        self.notebook.add(self.data_tab, text="  داده و آموزش  ")
        self.notebook.add(self.login_tab, text="  ورود سامانه  ")
        self.notebook.add(self.upload_tab, text="  آپلود فایل  ")
        self.notebook.add(self.download_tab, text="  دانلود فایل  ")
        self.notebook.add(self.output_tab, text="  خروجی و گزارش  ")

        self._build_browser_tab()
        self._build_data_tab()
        self._build_login_tab()
        self._build_upload_tab()
        self._build_download_tab()
        self._build_output_tab()

        status_bar = ttk.Frame(container, style="StatusBar.TFrame", padding=(10, 7))
        status_bar.pack(side="bottom", fill="x", pady=(10, 0))
        ttk.Label(
            status_bar, text="وضعیت اجرا:", style="StatusTitle.TLabel"
        ).pack(side="right")
        ttk.Label(
            status_bar,
            textvariable=self.activity_status,
            style="Activity.TLabel",
        ).pack(side="right", padx=(8, 4))
        self.status_progress = ttk.Progressbar(
            status_bar, mode="indeterminate", length=80
        )
        self.status_progress.pack(side="right", padx=(4, 14))
        ttk.Separator(status_bar, orient="vertical").pack(
            side="right", fill="y", padx=8
        )
        ttk.Label(
            status_bar, text="آخرین وضعیت:", style="StatusTitle.TLabel"
        ).pack(side="right")
        ttk.Label(
            status_bar,
            textvariable=self.status,
            style="Status.TLabel",
            anchor="e",
        ).pack(side="right", padx=8)
        ttk.Label(
            status_bar,
            text=f"FT Desktop Console — {APP_VERSION}",
            style="StatusMuted.TLabel",
        ).pack(side="left")

    def _configure_styles(self):
        self.root.configure(background=COLORS["background"])
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(".", font=("Tahoma", 10), foreground=COLORS["text"])
        style.configure("TFrame", background=COLORS["background"])
        style.configure("Card.TFrame", background=COLORS["surface"])
        style.configure("TLabel", background=COLORS["background"], foreground=COLORS["text"])
        style.configure("Header.TFrame", background=COLORS["navy"])
        style.configure(
            "HeaderTitle.TLabel",
            background=COLORS["navy"],
            foreground="#FFFFFF",
            font=("Tahoma", 13, "bold"),
        )
        style.configure(
            "HeaderSubtitle.TLabel",
            background=COLORS["navy"],
            foreground="#D7E3F1",
            font=("Tahoma", 9),
        )
        style.configure(
            "VersionBadge.TLabel",
            background="#DCE8F5",
            foreground=COLORS["navy"],
            font=("Tahoma", 9, "bold"),
            padding=(10, 5),
        )
        style.configure(
            "Card.TLabelframe",
            background=COLORS["surface"],
            bordercolor=COLORS["border"],
            relief="solid",
        )
        style.configure(
            "Card.TLabelframe.Label",
            background=COLORS["surface"],
            foreground=COLORS["navy"],
            font=("Tahoma", 10, "bold"),
        )
        style.configure("Card.TLabel", background=COLORS["surface"])
        style.configure(
            "MutedCard.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            font=("Tahoma", 9),
        )
        style.configure(
            "Primary.TButton",
            background=COLORS["primary"],
            foreground="#FFFFFF",
            font=("Tahoma", 9, "bold"),
            padding=(14, 8),
            borderwidth=0,
        )
        style.map(
            "Primary.TButton",
            background=[("pressed", COLORS["primary_active"]), ("active", COLORS["primary_active"])],
        )
        style.configure(
            "Secondary.TButton",
            background="#E8EEF5",
            foreground=COLORS["navy"],
            font=("Tahoma", 9, "bold"),
            padding=(12, 8),
            borderwidth=0,
        )
        style.map("Secondary.TButton", background=[("active", "#D8E3EF")])
        style.configure(
            "Danger.TButton",
            background="#FEE4E2",
            foreground=COLORS["danger"],
            font=("Tahoma", 9, "bold"),
            padding=(12, 8),
            borderwidth=0,
        )
        style.map("Danger.TButton", background=[("active", "#FECDCA")])
        style.configure("TNotebook", background=COLORS["background"], borderwidth=0)
        style.configure(
            "TNotebook.Tab",
            background="#E4EAF1",
            foreground=COLORS["muted"],
            font=("Tahoma", 10, "bold"),
            padding=(18, 10),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", COLORS["surface"]), ("active", "#DCE6F0")],
            foreground=[("selected", COLORS["navy"])],
        )
        style.configure("StatusBar.TFrame", background="#E8EEF5")
        style.configure(
            "StatusTitle.TLabel",
            background="#E8EEF5",
            foreground=COLORS["navy"],
            font=("Tahoma", 9, "bold"),
        )
        style.configure("Status.TLabel", background="#E8EEF5", foreground=COLORS["text"])
        style.configure(
            "Activity.TLabel",
            background="#E8EEF5",
            foreground=COLORS["primary"],
            font=("Tahoma", 9, "bold"),
        )
        style.configure(
            "StatusMuted.TLabel", background="#E8EEF5", foreground=COLORS["muted"]
        )

    def _button(self, parent, text, command, tooltip, style="Secondary.TButton"):
        button = ttk.Button(parent, text=text, command=command, style=style)
        add_tooltip(button, tooltip)
        return button

    def _build_browser_tab(self):
        connection = ttk.LabelFrame(
            self.browser_tab, text="اتصال به سامانه", style="Card.TLabelframe", padding=18
        )
        connection.pack(fill="x")
        ttk.Label(connection, text="Selector تصویر CAPTCHA", style="Card.TLabel").grid(
            row=0, column=0, sticky="e", padx=(10, 0)
        )
        selector_entry = ttk.Entry(connection, textvariable=self.selector, justify="left")
        selector_entry.grid(row=0, column=1, columnspan=3, sticky="ew", padx=10, pady=6)
        add_tooltip(selector_entry, "Selector عنصر تصویر CAPTCHA در صفحه ورود؛ در حالت عادی تغییر ندهید.")
        self._button(
            connection,
            "بازکردن مرورگر",
            self._open_browser,
            "مرورگر اختصاصی را با پروفایل ماندگار باز می‌کند.",
            "Primary.TButton",
        ).grid(row=1, column=1, padx=5, pady=10, sticky="ew")
        self._button(
            connection,
            "دریافت CAPTCHA",
            self._capture,
            "تصویر CAPTCHA فعلی را می‌خواند و در تب داده و آموزش نمایش می‌دهد.",
        ).grid(row=1, column=2, padx=5, pady=10, sticky="ew")
        self._button(
            connection,
            "تازه‌سازی صفحه",
            self._refresh,
            "صفحه ورود را Reload می‌کند تا CAPTCHA تازه ایجاد شود.",
        ).grid(row=1, column=3, padx=5, pady=10, sticky="ew")
        connection.columnconfigure(1, weight=2)
        connection.columnconfigure(2, weight=1)
        connection.columnconfigure(3, weight=1)

        batch = ttk.LabelFrame(
            self.browser_tab,
            text="دریافت خودکار CAPTCHA",
            style="Card.TLabelframe",
            padding=18,
        )
        batch.pack(fill="x", pady=(14, 0))
        ttk.Label(batch, text="تعداد تصاویر", style="Card.TLabel").grid(
            row=0, column=0, sticky="e", padx=8
        )
        count_input = ttk.Spinbox(
            batch, from_=1, to=10000, textvariable=self.batch_count, width=10, justify="center"
        )
        count_input.grid(row=0, column=1, sticky="w", padx=8, pady=8)
        add_tooltip(count_input, "تعداد CAPTCHAهایی که در Batch جاری دریافت می‌شوند.")
        ttk.Label(batch, text="تأخیر بعد از Reload (ثانیه)", style="Card.TLabel").grid(
            row=0, column=2, sticky="e", padx=8
        )
        delay_input = ttk.Spinbox(
            batch,
            from_=0,
            to=60,
            increment=0.5,
            textvariable=self.reload_delay_seconds,
            width=10,
            justify="center",
        )
        delay_input.grid(row=0, column=3, sticky="w", padx=8, pady=8)
        add_tooltip(delay_input, "فاصله بین Reload صفحه و خواندن CAPTCHA بعدی؛ مقدار مجاز صفر تا ۶۰ ثانیه.")
        self._button(
            batch,
            "شروع دریافت خودکار",
            self._collect_batch,
            "Batch جدید می‌سازد و CAPTCHAها را با تأخیر تعیین‌شده دریافت می‌کند.",
            "Primary.TButton",
        ).grid(row=1, column=2, padx=6, pady=10, sticky="ew")
        self._button(
            batch,
            "توقف عملیات",
            self.browser.cancel_batch,
            "دریافت خودکار را حتی در زمان انتظار بین Reloadها متوقف می‌کند.",
            "Danger.TButton",
        ).grid(row=1, column=3, padx=6, pady=10, sticky="ew")
        batch.columnconfigure(2, weight=1)
        batch.columnconfigure(3, weight=1)
        ttk.Label(
            self.browser_tab,
            text="مرورگر پس از بازشدن بین عملیات فعال می‌ماند و فقط هنگام خروج کامل بسته می‌شود.",
            style="MutedCard.TLabel",
        ).pack(anchor="e", pady=(12, 0))

    def _build_data_tab(self):
        labeling = ttk.LabelFrame(
            self.data_tab,
            text="پیش‌نمایش و برچسب‌گذاری",
            style="Card.TLabelframe",
            padding=18,
        )
        labeling.pack(fill="x")
        self.preview = ttk.Label(
            labeling,
            text="هنوز تصویری دریافت نشده است.",
            anchor="center",
            style="Card.TLabel",
        )
        self.preview.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(8, 18))
        ttk.Label(labeling, text="برچسب صحیح", style="Card.TLabel").grid(
            row=1, column=0, sticky="e", padx=8
        )
        label_entry = ttk.Entry(labeling, textvariable=self.label, width=20, justify="center")
        label_entry.grid(row=1, column=1, padx=8, pady=6)
        label_entry.bind("<Return>", lambda _event: self._save_sample())
        add_tooltip(label_entry, "عدد نمایش‌داده‌شده در CAPTCHA را با رقم فارسی یا انگلیسی وارد کنید.")
        ttk.Label(labeling, text="تعداد ارقام", style="Card.TLabel").grid(
            row=1, column=2, sticky="e", padx=8
        )
        length_input = ttk.Spinbox(
            labeling, from_=1, to=10, textvariable=self.sequence_length, width=8, justify="center"
        )
        length_input.grid(row=1, column=3, padx=8)
        add_tooltip(length_input, "طول دقیق رشته CAPTCHA در Dataset و مدل.")
        ttk.Label(labeling, textvariable=self.sample_count, style="Card.TLabel").grid(
            row=2, column=0, columnspan=4, sticky="e", padx=8, pady=(8, 2)
        )
        self.data_actions = ttk.Frame(labeling, style="Card.TFrame")
        self.data_actions.grid(
            row=3, column=0, columnspan=4, sticky="ew", pady=(8, 2)
        )
        self._button(
            self.data_actions,
            "ذخیره نمونه و برچسب",
            self._save_sample,
            "تصویر فعلی و برچسب معتبر را به Dataset اضافه می‌کند.",
            "Primary.TButton",
        ).grid(row=0, column=0, padx=5, sticky="ew")
        self._button(
            self.data_actions,
            "ورود Batch برچسب‌خورده",
            self._import_batch,
            "پوشه Batch تکمیل‌شده در Excel را اعتبارسنجی و وارد Dataset می‌کند.",
        ).grid(row=0, column=1, padx=5, sticky="ew")
        self._button(
            self.data_actions,
            "تست مدل روی Batch",
            self._predict_batch,
            "پیش‌بینی مدل را در ستون سوم labels.csv می‌نویسد و نرخ تطبیق را گزارش می‌کند.",
        ).grid(row=0, column=2, padx=5, sticky="ew")
        for column in range(3):
            self.data_actions.columnconfigure(column, weight=1, uniform="data_actions")
        labeling.columnconfigure(1, weight=1)
        labeling.columnconfigure(3, weight=1)

        training = ttk.LabelFrame(
            self.data_tab,
            text="آموزش و ارزیابی مدل",
            style="Card.TLabelframe",
            padding=18,
        )
        training.pack(fill="x", pady=(14, 0))
        ttk.Label(training, text="تعداد Epoch", style="Card.TLabel").grid(
            row=0, column=0, sticky="e", padx=8
        )
        epochs_input = ttk.Spinbox(
            training, from_=1, to=500, textvariable=self.epochs, width=10, justify="center"
        )
        epochs_input.grid(row=0, column=1, padx=8, pady=8)
        add_tooltip(epochs_input, "حداکثر دفعات آموزش؛ Early Stopping ممکن است زودتر آموزش را متوقف کند.")
        self.training_actions = ttk.Frame(training, style="Card.TFrame")
        self.training_actions.grid(
            row=1, column=0, columnspan=4, sticky="ew", pady=(10, 0)
        )
        self._button(
            self.training_actions,
            "آموزش مدل",
            self._train,
            "مدل را با Dataset فعلی آموزش می‌دهد و دقت اعتبارسنجی را گزارش می‌کند.",
            "Primary.TButton",
        ).grid(row=0, column=0, padx=5, sticky="ew")
        self._button(
            self.training_actions,
            "آزمون تصویر فعلی",
            self._predict_current,
            "مدل ذخیره‌شده را روی CAPTCHA فعلی اجرا می‌کند.",
        ).grid(row=0, column=1, padx=5, sticky="ew")
        for column in range(2):
            self.training_actions.columnconfigure(
                column, weight=1, uniform="training_actions"
            )
        training.columnconfigure(1, weight=1)

    def _build_login_tab(self):
        login = ttk.LabelFrame(
            self.login_tab,
            text="ورود به سامانه FT",
            style="Card.TLabelframe",
            padding=22,
        )
        login.pack(fill="x")
        ttk.Label(login, text="نام کاربری", style="Card.TLabel").grid(
            row=0, column=0, sticky="e", padx=8, pady=8
        )
        username_entry = ttk.Entry(login, textvariable=self.username, width=32, justify="left")
        username_entry.grid(
            row=0, column=1, columnspan=3, sticky="ew", padx=8, pady=8
        )
        add_tooltip(username_entry, "نام کاربری سامانه؛ پس از ورود خودکار موفق می‌تواند امن ذخیره شود.")
        ttk.Label(login, text="حداقل اطمینان مدل", style="Card.TLabel").grid(
            row=2, column=0, sticky="e", padx=8, pady=8
        )
        confidence_input = ttk.Spinbox(
            login,
            from_=0.0,
            to=1.0,
            increment=0.05,
            textvariable=self.minimum_confidence,
            width=10,
            justify="center",
        )
        confidence_input.grid(row=2, column=1, sticky="w", padx=8, pady=8)
        add_tooltip(confidence_input, "اگر اطمینان مدل کمتر از این مقدار باشد CAPTCHA Reload می‌شود.")
        ttk.Label(login, text="رمز عبور", style="Card.TLabel").grid(
            row=1, column=0, sticky="e", padx=8, pady=8
        )
        password_entry = ttk.Entry(
            login, textvariable=self.password, show="●", width=32, justify="left"
        )
        password_entry.grid(
            row=1, column=1, columnspan=3, sticky="ew", padx=8, pady=8
        )
        add_tooltip(password_entry, "رمز در Log نمایش داده نمی‌شود و با Windows DPAPI محافظت می‌شود.")
        ttk.Label(login, text="حداکثر تلاش ورود", style="Card.TLabel").grid(
            row=2, column=2, sticky="e", padx=8, pady=8
        )
        attempts_input = ttk.Spinbox(
            login,
            from_=1,
            to=20,
            textvariable=self.login_attempts,
            width=10,
            justify="center",
        )
        attempts_input.grid(row=2, column=3, sticky="w", padx=8, pady=8)
        add_tooltip(
            attempts_input,
            "حداکثر دفعات دریافت، تشخیص و ارسال CAPTCHA در ورود خودکار.",
        )
        remember = ttk.Checkbutton(
            login,
            text="ذخیره امن پس از ورود موفق",
            variable=self.remember_credentials,
        )
        remember.grid(row=3, column=0, columnspan=4, sticky="e", padx=8, pady=8)
        add_tooltip(remember, "Credential فقط پس از تأیید ورود خودکار و برای همین حساب Windows ذخیره می‌شود.")
        self.login_actions = ttk.Frame(login, style="Card.TFrame")
        self.login_actions.grid(
            row=4, column=0, columnspan=4, sticky="ew", pady=(10, 4)
        )
        self._button(
            self.login_actions,
            "ورود دستی",
            self._manual_login,
            "صفحه ورود را باز می‌کند تا اطلاعات و CAPTCHA را شخصاً وارد کنید.",
        ).grid(row=0, column=0, padx=5, sticky="ew")
        self._button(
            self.login_actions,
            "آزمون ورود خودکار",
            self._login,
            "مدل CAPTCHA را اجرا می‌کند و تا سقف تنظیم‌شده برای ورود تلاش می‌کند.",
            "Primary.TButton",
        ).grid(row=0, column=1, padx=5, sticky="ew")
        for column in range(2):
            self.login_actions.columnconfigure(
                column, weight=1, uniform="login_actions"
            )
        self.login_settings_actions = ttk.Frame(login, style="Card.TFrame")
        self.login_settings_actions.grid(
            row=5, column=0, columnspan=4, sticky="ew", pady=(4, 8)
        )
        self._button(
            self.login_settings_actions,
            "ذخیره تنظیمات",
            self._save_preferences,
            "تنظیمات غیرحساس رابط را در پروفایل کاربر ذخیره می‌کند.",
        ).grid(row=0, column=0, padx=5, sticky="ew")
        self._button(
            self.login_settings_actions,
            "پاک کردن اطلاعات ورود",
            self._forget_credentials,
            "Credential رمزگذاری‌شده را حذف و فیلدهای ورود را خالی می‌کند.",
            "Danger.TButton",
        ).grid(row=0, column=1, padx=5, sticky="ew")
        for column in range(2):
            self.login_settings_actions.columnconfigure(
                column, weight=1, uniform="login_settings_actions"
            )
        ttk.Label(
            login,
            text="اطلاعات ورود با Windows DPAPI محافظت می‌شوند و در خروجی برنامه نوشته نمی‌شوند.",
            style="MutedCard.TLabel",
            anchor="e",
        ).grid(row=6, column=0, columnspan=4, sticky="ew", padx=8, pady=(8, 2))
        login.columnconfigure(1, weight=1)
        login.columnconfigure(2, weight=1)
        login.columnconfigure(3, weight=1)

    def _build_output_tab(self):
        toolbar = ttk.Frame(self.output_tab)
        toolbar.pack(fill="x", pady=(0, 10))
        ttk.Label(
            toolbar,
            text="گزارش زمان‌دار عملیات",
            font=("Tahoma", 10, "bold"),
        ).pack(side="right")
        self._button(
            toolbar,
            "کپی کل خروجی",
            self._copy_log,
            "همه پیام‌ها را برای ارسال به پشتیبانی یا ایجنت در Clipboard کپی می‌کند.",
        ).pack(side="left")
        self._button(
            toolbar,
            "گزارش آپلودها",
            self._load_upload_history,
            "همه سوابق ثبت‌شده را با تفکیک وضعیت نمایش می‌دهد.",
        ).pack(side="left", padx=8)
        self._button(
            toolbar,
            "پاک کردن نمایش",
            self._clear_log,
            "فقط متن قابل‌مشاهده را پاک می‌کند و اثری بر فایل‌ها یا تنظیمات ندارد.",
            "Danger.TButton",
        ).pack(side="left", padx=8)
        self.output = scrolledtext.ScrolledText(
            self.output_tab,
            wrap="word",
            state="disabled",
            font=("Tahoma", 10),
            background=COLORS["log_background"],
            foreground=COLORS["log_text"],
            insertbackground="#FFFFFF",
            selectbackground="#365C8D",
            selectforeground="#FFFFFF",
            relief="flat",
            borderwidth=0,
            padx=14,
            pady=12,
        )
        self.output.pack(fill="both", expand=True)
        self.output.tag_configure("info", foreground="#7CC4FF")
        self.output.tag_configure("success", foreground="#6CE9A6")
        self.output.tag_configure("warning", foreground="#FEC84B")
        self.output.tag_configure("error", foreground="#FDA29B")
        add_tooltip(self.output, "متن خروجی قابل انتخاب و کپی است؛ رمز عبور هرگز در این بخش ثبت نمی‌شود.")
        self._log("info", "برنامه آماده است؛ مرورگر در تمام عملیات باز می‌ماند.")

    def _load_upload_history(self):
        self._submit(
            "upload_history",
            self.browser.upload_history,
            success=self._display_upload_history,
        )

    def _display_upload_history(self, records):
        labels = {
            "queued": "ارسال نهایی موفق",
            "uploaded": "در انتظار اسکن",
            "rejected": "ردشده",
            "unknown": "نتیجه نامشخص",
        }
        counts = {status: 0 for status in labels}
        for _, status, _ in records:
            counts[status] = counts.get(status, 0) + 1
        self._log(
            "info",
            "گزارش کل سوابق — "
            f"موفق: {counts['queued']}، در انتظار اسکن: {counts['uploaded']}، "
            f"ردشده: {counts['rejected']}، نامشخص: {counts['unknown']}",
        )
        if not records:
            self._log("info", "هنوز سابقه‌ای ثبت نشده است.")
        for source_path, status, updated_at in records:
            changed_at = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(updated_at)
            )
            self._log(
                "success" if status == "queued" else "warning",
                f"{changed_at} | {labels.get(status, status)} | {source_path}",
            )
        self.status.set(f"گزارش {len(records)} سابقه نمایش داده شد.")

    def _build_upload_tab(self):
        upload = ttk.LabelFrame(
            self.upload_tab,
            text="انتخاب و انتقال فایل‌ها",
            style="Card.TLabelframe",
            padding=22,
        )
        upload.pack(fill="x")
        ttk.Label(upload, text="محیط اجرا", style="Card.TLabel").grid(
            row=0, column=0, sticky="e", padx=8, pady=10
        )
        environment = ttk.Combobox(
            upload,
            textvariable=self.environment_label,
            values=list(ENVIRONMENT_LABELS.values()),
            state="readonly",
            justify="right",
        )
        environment.grid(row=0, column=1, columnspan=3, sticky="ew", padx=8, pady=10)
        environment.bind("<<ComboboxSelected>>", lambda _event: self._save_preferences(False))
        add_tooltip(environment, "محیط نامشخص اجازه آپلود یا دانلود نمی‌دهد.")
        ttk.Label(upload, text="پوشه ورودی", style="Card.TLabel").grid(
            row=1, column=0, sticky="e", padx=8, pady=10
        )
        folder_entry = ttk.Entry(upload, textvariable=self.watch_dir, justify="left")
        folder_entry.grid(row=1, column=1, columnspan=2, sticky="ew", padx=8, pady=10)
        add_tooltip(folder_entry, "فقط فایل‌های ریشه این پوشه بررسی می‌شوند؛ زیرپوشه‌ها نادیده گرفته می‌شوند.")
        self._button(
            upload,
            "انتخاب پوشه",
            self._choose_upload_folder,
            "پوشه حاوی فایل‌های آماده ارسال را انتخاب می‌کند.",
        ).grid(row=1, column=3, padx=6, pady=10, sticky="ew")
        ttk.Label(upload, text="حداکثر فایل در هر اجرا", style="Card.TLabel").grid(
            row=2, column=0, sticky="e", padx=8, pady=10
        )
        count_input = ttk.Spinbox(
            upload,
            from_=0,
            to=10000,
            textvariable=self.max_files_per_run,
            width=12,
            justify="center",
        )
        count_input.grid(row=2, column=1, sticky="w", padx=8, pady=10)
        add_tooltip(count_input, "صفر یعنی بدون محدودیت تعداد؛ فایل‌ها به ترتیب نام پردازش می‌شوند.")
        ttk.Label(upload, text="حداکثر حجم هر فایل (MB)", style="Card.TLabel").grid(
            row=2, column=2, sticky="e", padx=8, pady=10
        )
        size_input = ttk.Spinbox(
            upload,
            from_=0,
            to=100000,
            increment=1,
            textvariable=self.max_file_size_mb,
            width=12,
            justify="center",
        )
        size_input.grid(row=2, column=3, sticky="w", padx=8, pady=10)
        add_tooltip(size_input, "صفر یعنی بدون محدودیت حجم؛ فایل‌های بزرگ‌تر Skip و در خروجی گزارش می‌شوند.")
        self._button(
            upload,
            "آپلود فایل‌های پوشه",
            self._upload_folder,
            "فایل‌های مجاز را آپلود، نتیجه اسکن را بررسی و موارد موفق را در _uploaded بایگانی می‌کند.",
            "Primary.TButton",
        ).grid(row=3, column=2, columnspan=2, padx=6, pady=18, sticky="ew")
        auto_upload = ttk.Checkbutton(
            upload,
            text="آپلود خودکار هر ۱۰ ثانیه",
            variable=self.auto_upload_enabled,
            command=self._toggle_auto_upload,
        )
        auto_upload.grid(row=3, column=0, columnspan=2, padx=8, pady=18, sticky="e")
        add_tooltip(auto_upload, "هنگام اجرای Upload قبلی، اجرای جدید شروع نمی‌شود.")
        ttk.Label(
            upload,
            text="فایل‌های موقت نادیده گرفته می‌شوند. فقط نتیجه صریحاً سالم به مرحله نهایی ارسال می‌شود.",
            style="MutedCard.TLabel",
            anchor="e",
        ).grid(row=4, column=0, columnspan=4, sticky="ew", padx=8, pady=(8, 2))
        upload.columnconfigure(1, weight=1)
        upload.columnconfigure(2, weight=1)
        upload.columnconfigure(3, weight=1)

    def _build_download_tab(self):
        card = ttk.LabelFrame(
            self.download_tab,
            text="دانلود کنترل‌شده بین دو محیط",
            style="Card.TLabelframe",
            padding=18,
        )
        card.pack(fill="both", expand=True)
        ttk.Label(card, text="محیط اجرا", style="Card.TLabel").grid(
            row=0, column=0, sticky="e", padx=8, pady=8
        )
        environment = ttk.Combobox(
            card,
            textvariable=self.environment_label,
            values=list(ENVIRONMENT_LABELS.values()),
            state="readonly",
            justify="right",
        )
        environment.grid(row=0, column=1, sticky="ew", padx=8, pady=8)
        environment.bind("<<ComboboxSelected>>", lambda _event: self._save_preferences(False))
        ttk.Label(card, text="پوشه مقصد", style="Card.TLabel").grid(
            row=1, column=0, sticky="e", padx=8, pady=8
        )
        destination = ttk.Entry(card, textvariable=self.download_dir, justify="left")
        destination.grid(row=1, column=1, sticky="ew", padx=8, pady=8)
        self._button(
            card,
            "انتخاب پوشه",
            self._choose_download_folder,
            "پوشه محلی دریافت فایل‌ها را انتخاب می‌کند.",
        ).grid(row=1, column=2, padx=6, pady=8, sticky="ew")
        controls = ttk.Frame(card)
        controls.grid(row=2, column=0, columnspan=3, sticky="ew", padx=8, pady=8)
        self._button(
            controls,
            "بروزرسانی فهرست",
            self._refresh_downloads,
            "فقط فایل‌های دارای نشان محیط مقابل را نمایش می‌دهد.",
        ).pack(side="right", padx=4)
        self._button(
            controls,
            "دانلود انتخاب‌شده‌ها",
            self._download_selected,
            "فایل‌های انتخابی را بدون بازنویسی فایل موجود دانلود می‌کند.",
            "Primary.TButton",
        ).pack(side="right", padx=4)
        self.download_tree = ttk.Treeview(
            card,
            columns=("name", "size", "origin"),
            show="headings",
            selectmode="extended",
            height=12,
        )
        self.download_tree.heading("name", text="نام فایل")
        self.download_tree.heading("size", text="حجم")
        self.download_tree.heading("origin", text="منشأ")
        self.download_tree.column("name", width=480, anchor="e")
        self.download_tree.column("size", width=110, anchor="center")
        self.download_tree.column("origin", width=110, anchor="center")
        self.download_tree.grid(row=3, column=0, columnspan=3, sticky="nsew", padx=8, pady=8)
        ttk.Label(
            card,
            text="فایل بدون نشان منشأ یا فایل متعلق به همین محیط هرگز دانلود نمی‌شود.",
            style="MutedCard.TLabel",
            anchor="e",
        ).grid(row=4, column=0, columnspan=3, sticky="ew", padx=8, pady=6)
        card.columnconfigure(1, weight=1)
        card.rowconfigure(3, weight=1)

    def _environment_code(self) -> str:
        return ENVIRONMENT_CODES.get(self.environment_label.get(), "")

    def _choose_download_folder(self):
        selected = filedialog.askdirectory(
            title="پوشه مقصد دانلود را انتخاب کنید",
            initialdir=self.download_dir.get() or str(self.base_dir),
        )
        if selected:
            self.download_dir.set(selected)
            self._save_preferences(announce=False)

    def _refresh_downloads(self):
        environment = self._environment_code()
        if not environment:
            messagebox.showerror("خطا", "ابتدا محیط اجرا را مشخص کنید.")
            return
        if self.download_task_id is not None:
            return
        self.download_task_id = self._submit(
            "list_downloadable_files",
            self.browser.list_downloadable_files,
            environment,
            success=self._display_download_files,
        )

    def _display_download_files(self, records):
        self.remote_downloads = {record["remote_id"]: record for record in records}
        for item in self.download_tree.get_children():
            self.download_tree.delete(item)
        for record in records:
            size = f"{record['size'] / 1024:.1f} KB"
            origin = ENVIRONMENT_LABELS.get(record["origin"], record["origin"])
            self.download_tree.insert(
                "", "end", iid=record["remote_id"], values=(record["name"], size, origin)
            )
        self.status.set(f"{len(records)} فایل قابل دانلود پیدا شد.")

    def _download_selected(self):
        environment = self._environment_code()
        if not environment:
            messagebox.showerror("خطا", "ابتدا محیط اجرا را مشخص کنید.")
            return
        destination = Path(self.download_dir.get()).expanduser()
        if not destination.is_dir():
            messagebox.showerror("خطا", "ابتدا پوشه مقصد معتبر انتخاب کنید.")
            return
        selected = [
            self.remote_downloads[item]
            for item in self.download_tree.selection()
            if item in self.remote_downloads
        ]
        if not selected:
            messagebox.showerror("خطا", "حداقل یک فایل را انتخاب کنید.")
            return
        if self.download_task_id is not None:
            return
        self.download_task_id = self._submit(
            "download_files",
            self.browser.download_files,
            selected,
            destination,
            environment,
            Path(self.watch_dir.get()).expanduser() if self.watch_dir.get() else None,
            success=self._after_download,
        )

    def _after_download(self, result):
        message = (
            f"دانلود تمام شد؛ موفق: {result['downloaded']}، "
            f"تکراری: {result['skipped']}، خطا: {result['failed']}"
        )
        self.status.set(message)
        self._log("success" if not result["failed"] else "warning", message)

    def _choose_upload_folder(self):
        selected = filedialog.askdirectory(
            title="پوشه فایل‌های قابل آپلود را انتخاب کنید",
            initialdir=self.watch_dir.get() or str(self.base_dir),
        )
        if selected:
            self.watch_dir.set(selected)
            self.auto_upload_warning = None
            self._save_preferences(announce=False)

    def _toggle_auto_upload(self):
        self.auto_upload_suspended = False
        self.auto_upload_warning = None
        self._save_preferences(announce=False)
        state = "فعال" if self.auto_upload_enabled.get() else "غیرفعال"
        self._log("info", f"آپلود خودکار {state} شد.")

    def _auto_upload_tick(self):
        if self.closing:
            return
        if self.auto_upload_enabled.get() and not self.auto_upload_suspended:
            self._upload_folder(automatic=True)
        self.root.after(AUTO_UPLOAD_INTERVAL_MS, self._auto_upload_tick)

    def _upload_folder(self, automatic=False):
        if self.upload_task_id is not None:
            if not automatic:
                self._log("info", "یک عملیات آپلود در حال اجرا است؛ درخواست تکراری نادیده گرفته شد.")
            return
        try:
            folder = Path(self.watch_dir.get()).expanduser()
            max_files = self.max_files_per_run.get()
            max_size = self.max_file_size_mb.get()
        except tk.TclError:
            if not automatic:
                messagebox.showerror("خطا", "محدودیت تعداد و حجم باید عددی باشند.")
            return
        if not folder.is_dir():
            if automatic:
                warning = str(folder)
                if self.auto_upload_warning != warning:
                    self._log("warning", "آپلود خودکار منتظر انتخاب یک پوشه معتبر است.")
                    self.auto_upload_warning = warning
            else:
                messagebox.showerror("خطا", "ابتدا یک پوشه معتبر انتخاب کنید.")
            return
        if max_files < 0 or max_size < 0:
            if not automatic:
                messagebox.showerror("خطا", "محدودیت تعداد و حجم نباید منفی باشد.")
            return
        environment = self._environment_code()
        if not environment:
            if automatic:
                if self.auto_upload_warning != "environment":
                    self._log("warning", "آپلود خودکار تا تعیین محیط اجرا متوقف است.")
                    self.auto_upload_warning = "environment"
            else:
                messagebox.showerror("خطا", "ابتدا محیط اجرا را مشخص کنید.")
            return
        self.auto_upload_warning = None
        if not automatic:
            self.auto_upload_suspended = False
        self._save_preferences(announce=False)
        if not automatic:
            self.notebook.select(self.output_tab)
        operation = "auto_upload_folder" if automatic else "upload_folder"
        self.upload_task_id = self._submit(
            operation,
            self.browser.upload_folder,
            folder,
            max_files,
            max_size,
            environment,
            announce=not automatic,
        )

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
                login_attempts=self.login_attempts.get(),
                watch_dir=self.watch_dir.get(),
                max_files_per_run=self.max_files_per_run.get(),
                max_file_size_mb=self.max_file_size_mb.get(),
                auto_upload_enabled=self.auto_upload_enabled.get(),
                environment=self._environment_code(),
                download_dir=self.download_dir.get(),
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

    def _submit(self, operation_name, operation, *args, success=None, announce=True):
        if announce:
            self.status.set("عملیات در حال اجرا است...")
            self._log("info", f"شروع عملیات: {operation_name}")
        task_id = self.browser.submit(operation_name, operation, *args)
        self.callbacks[task_id] = success
        self._mark_task_started(task_id)
        return task_id

    def _mark_task_started(self, task_id):
        if not hasattr(self, "active_tasks"):
            self.active_tasks = set()
        self.active_tasks.add(task_id)
        self._sync_activity_status()

    def _sync_activity_status(self):
        if not hasattr(self, "activity_status"):
            return
        busy = bool(getattr(self, "active_tasks", ()))
        self.activity_status.set("در حال اجرا" if busy else "آماده")
        if not hasattr(self, "status_progress"):
            return
        if busy:
            self.status_progress.start(12)
        else:
            self.status_progress.stop()

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
        self.notebook.select(self.data_tab)

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
        self._mark_task_started("import_batch")
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
        self._mark_task_started("train")

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
        self._mark_task_started("predict")
        threading.Thread(target=work, daemon=True).start()

    def _predict_batch(self):
        selected = filedialog.askdirectory(
            title="پوشه Batch را انتخاب کنید",
            initialdir=str(self.batch_root),
        )
        if not selected:
            return

        def work():
            try:
                result = predict_batch(Path(selected), self.model_dir)
                self.results.put(
                    RuntimeResult("predict_batch", "predict_batch", True, result)
                )
            except Exception as error:
                self.results.put(
                    RuntimeResult("predict_batch", "predict_batch", False, str(error))
                )

        self.status.set("در حال تست مدل روی Batch...")
        self._mark_task_started("predict_batch")
        threading.Thread(target=work, daemon=True).start()

    def _login(self):
        try:
            minimum_confidence = self.minimum_confidence.get()
            login_attempts = self.login_attempts.get()
        except tk.TclError:
            messagebox.showerror("خطا", "اطمینان و تعداد تلاش ورود باید عددی باشند.")
            return
        if not 0 <= minimum_confidence <= 1:
            messagebox.showerror("خطا", "حداقل اطمینان باید بین صفر و یک باشد.")
            return
        if not 1 <= login_attempts <= 20:
            messagebox.showerror("خطا", "تعداد تلاش ورود باید بین ۱ و ۲۰ باشد.")
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
            login_attempts,
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
            if isinstance(result, RuntimeLog):
                self._log(result.level, result.message)
                continue
            task_id = result.task_id
            operation = result.operation
            succeeded = result.succeeded
            payload = result.payload
            callback = self.callbacks.pop(task_id, None)
            if hasattr(self, "active_tasks"):
                self.active_tasks.discard(task_id)
                self._sync_activity_status()
            if task_id == self.upload_task_id:
                self.upload_task_id = None
            if task_id == self.download_task_id:
                self.download_task_id = None
            if operation == "close":
                self.browser.executor.shutdown(wait=False, cancel_futures=True)
                self.root.destroy()
                return
            if not succeeded:
                self.status.set("عملیات ناموفق بود.")
                self._log("error", f"{operation}: {payload}")
                if operation != "auto_upload_folder":
                    messagebox.showerror("خطا", payload)
                continue
            if callback:
                callback(payload)
            if operation in {"list_downloadable_files", "download_files"}:
                pass
            elif operation == "collect_batch":
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
            elif operation == "predict_batch":
                if payload["exact_match"] is None:
                    comparison = "نرخ تطبیق قابل محاسبه نیست؛ ستون label خالی است"
                else:
                    comparison = (
                        f"تطبیق دقیق: {payload['matches']} از {payload['evaluated']} "
                        f"({payload['exact_match']:.1%})"
                    )
                message = (
                    f"تست Batch تمام شد؛ {payload['count']} تصویر — {comparison}"
                )
                self.status.set(message)
                self._log("success", message)
            elif operation == "upload_history":
                pass
            elif operation in {"upload_folder", "auto_upload_folder"}:
                if operation == "auto_upload_folder" and not any(payload.values()):
                    continue
                message = (
                    f"اجرای آپلود تمام شد؛ پردازش: {payload['processed']}، "
                    f"خطا: {payload['failed']}، عبور از سقف حجم: {payload['oversized']}"
                )
                if payload.get("stopped"):
                    message += " — توقف پس از دو خطای پیاپی"
                    self.auto_upload_suspended = True
                    message += "؛ پایش خودکار متوقف شد"
                self.status.set(message)
                self._log("success" if payload["failed"] == 0 else "warning", message)
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
