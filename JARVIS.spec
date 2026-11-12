# PyInstaller onedir build consumed by Velopack. User config and data are
# intentionally excluded: all mutable state lives in LocalAppData/JARVIS.
from PyInstaller.utils.hooks import collect_submodules
from pathlib import Path

root = Path(SPECPATH)
datas = []
for folder in ("assets", "dashboard", "providers", "actions", "plugins"):
    candidate = root / folder
    if candidate.exists():
        datas.append((str(candidate), folder))
datas.extend([
    (str(root / "core" / "promt.txt"), "core"),
    (str(root / "pyproject.toml"), "."),
    (str(root / "config" / "jarvis.ico"), "config"),
])

hiddenimports = []
for package in ("actions", "plugins", "providers", "google.genai", "sounddevice"):
    try:
        hiddenimports += collect_submodules(package)
    except (ImportError, ModuleNotFoundError):
        pass

a = Analysis(
    [str(root / "main.py")],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tests", "tkinter"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="JARVIS",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="JARVIS",
)
