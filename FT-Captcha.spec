from PyInstaller.utils.hooks import (
    collect_data_files,
    copy_metadata,
)


datas = [("assets/ft-legal-transfer.png", "assets")]
for package in ("playwright", "keras"):
    datas += collect_data_files(package)
    datas += copy_metadata(package)

a = Analysis(
    ["captcha_lab.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FT-Captcha",
    icon="assets/ft-legal-transfer.ico",
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
    name="FT-Captcha-Portable",
)
