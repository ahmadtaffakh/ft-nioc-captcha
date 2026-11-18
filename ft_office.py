"""Small office entry point for automatic login and FT file transfer."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import tkinter as tk

from captcha_inference import load_model, predict_image
from captcha_lab import CaptchaLabApp


def office_self_test() -> int:
    """Verify the lightweight model, Playwright driver and Tk runtime."""
    from PIL import Image
    from playwright.sync_api import sync_playwright

    image = Image.new("RGB", (160, 50), (120, 190, 140))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    model_dir = Path(getattr(sys, "_MEIPASS", ".")) / "captcha-model"
    text, confidence = predict_image(buffer.getvalue(), load_model(model_dir))
    if len(text) != 4 or not 0 <= confidence <= 1:
        raise RuntimeError("Lightweight CAPTCHA model self-test failed.")

    with sync_playwright() as playwright:
        if playwright.chromium is None:
            raise RuntimeError("Playwright Chromium driver is unavailable.")

    root = tk.Tk()
    root.withdraw()
    root.update_idletasks()
    root.destroy()
    return 0


def main() -> None:
    root = tk.Tk()
    CaptchaLabApp(root, office_mode=True)
    root.mainloop()


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        raise SystemExit(office_self_test())
    main()
