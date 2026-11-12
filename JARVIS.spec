# PyInstaller onedir build consumed by Velopack. User config and data are
# intentionally excluded: all mutable state lives in LocalAppData/JARVIS.
from PyInstaller.utils.hooks import collect_submodules
from pathlib import Path

root = Path(SPECPATH)
datas = []
# Ship only runtime assets. Fonts are not registered or used by the UI, while
# the banner and desktop PNG are documentation/source artwork (the app icon is
# already embedded as config/jarvis.ico).
for folder in ("assets/icons", "assets/vendor", "dashboard", "actions"):
    candidate = root / folder
    if candidate.exists():
        datas.append((str(candidate), folder))

# These packages are discovered from source at runtime. Stage their files one
# by one so development-only modules are not shipped with the desktop app.
for folder in ("providers", "plugins"):
    candidate = root / folder
    if not candidate.exists():
        continue
    for path in candidate.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        if (folder, path.name) in {
            ("providers", "_tests.py"),
            ("plugins", "_template.py"),
        }:
            continue
        datas.append((str(path), str(path.relative_to(root).parent)))
datas.extend([
    (str(root / "core" / "promt.txt"), "core"),
    (str(root / "pyproject.toml"), "."),
    (str(root / "config" / "jarvis.ico"), "config"),
])

hiddenimports = []
for package in ("actions", "plugins", "providers", "google.genai", "sounddevice"):
    try:
        excluded = {
            "plugins": {"plugins._template"},
            "providers": {"providers._tests"},
        }.get(package, set())
        hiddenimports += collect_submodules(
            package, filter=lambda name, excluded=excluded: name not in excluded
        )
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
    icon=str(root / "config" / "jarvis.ico"),
    console=False,
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
