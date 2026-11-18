import os
from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata


model_path = Path(os.environ.get("FT_OFFICE_MODEL_PATH", ""))
if not model_path.is_file():
    raise SystemExit("FT_OFFICE_MODEL_PATH must point to captcha_model.ftmodel")

datas = copy_metadata("playwright")
datas.append((str(model_path), "captcha-model"))

a = Analysis(
    ["ft_office.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[str(Path("tools") / "pyinstaller_hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tensorflow",
        "keras",
        "numpy",
        "pandas",
        "h5py",
        "scipy",
        "sklearn",
        "matplotlib",
        "googleapiclient",
        "grpc",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FT-Office",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="FT-Office-Lite",
)
