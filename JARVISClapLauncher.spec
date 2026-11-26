# PyInstaller one-file companion executable for double-clap activation.
# This executable is optional and runs independently of JARVIS.
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH)
hiddenimports = []
for package in (
    "core",
    "memory",
    "sounddevice",
    "PyQt6.QtNetwork",
    "PyQt6.QtCore",
    "PyQt6.QtWidgets",
):
    try:
        hiddenimports += collect_submodules(package)
    except (ImportError, ModuleNotFoundError):
        # Optional/platform-specific modules are handled by the launcher at
        # runtime. Do not make a release build fail only because a package
        # has no importable submodules on this platform.
        pass

a = Analysis(
    [str(root / "clap_launcher.pyw")],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(root / "config" / "jarvis.ico"), "config")],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tests", "tkinter"],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

# Keep this as a true one-file executable. PyInstaller writes the executable
# directly to dist/JARVISClapLauncher.exe; there is no COLLECT directory.
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="JARVISClapLauncher",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(root / "config" / "jarvis.ico"),
)
