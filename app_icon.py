"""Shared window icon for source, portable, and frozen FT applications."""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path


def icon_directory() -> Path:
    """Find bundled assets in PyInstaller builds or beside source files."""
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root) / "assets"
    return Path(__file__).resolve().parent / "assets"


def apply_app_icon(root: tk.Tk) -> None:
    """Set a Tk window icon while retaining the PhotoImage reference."""
    assets = icon_directory()
    ico = assets / "ft-legal-transfer.ico"
    png = assets / "ft-legal-transfer.png"
    if ico.is_file():
        try:
            root.iconbitmap(default=str(ico))
        except tk.TclError:
            pass
    if png.is_file():
        try:
            image = tk.PhotoImage(file=str(png))
            root.iconphoto(True, image)
            root._ft_icon_image = image
        except tk.TclError:
            pass
