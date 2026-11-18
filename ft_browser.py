"""Shared paths and persistent-browser startup for FT tools."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def app_data_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ft-nioc-uploader"


def launch_persistent_session(playwright, profile_dir: Path):
    profile_dir = Path(profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    chromium = playwright.chromium
    bundled_chrome = Path(getattr(sys, "_MEIPASS", "")) / "portable-chrome" / "chrome.exe"
    if getattr(sys, "frozen", False) and bundled_chrome.is_file():
        context = chromium.launch_persistent_context(
            str(profile_dir), headless=False, executable_path=str(bundled_chrome)
        )
    elif os.name == "nt" and not Path(chromium.executable_path).is_file():
        # Office builds use an installed browser instead of shipping Chromium.
        last_error = None
        for channel in ("chrome", "msedge"):
            try:
                context = chromium.launch_persistent_context(
                    str(profile_dir), headless=False, channel=channel
                )
                break
            except Exception as error:
                last_error = error
        else:
            raise RuntimeError(
                "Microsoft Edge یا Google Chrome نصب‌شده پیدا نشد."
            ) from last_error
    else:
        context = chromium.launch_persistent_context(str(profile_dir), headless=False)
    page = context.pages[0] if context.pages else context.new_page()
    return context, page
