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
        # Use Playwright's supported Chrome channel when the browser download is absent.
        context = chromium.launch_persistent_context(
            str(profile_dir), headless=False, channel="chrome"
        )
    else:
        context = chromium.launch_persistent_context(str(profile_dir), headless=False)
    page = context.pages[0] if context.pages else context.new_page()
    return context, page
