"""Build and package a Windows JARVIS release plus an optional clap launcher."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"


def run(command: list[str], *, cwd: Path = ROOT) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def remove_path(path: Path) -> None:
    """Remove a stale build output without assuming it is a file or directory."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "release")
    parser.add_argument("--repo-url", default="https://github.com/RustamovAkrom/JARVIS")
    parser.add_argument("--channel", default="stable")
    args = parser.parse_args()

    if sys.platform != "win32":
        raise SystemExit("The production Windows installer must be built on Windows.")
    version = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise SystemExit(f"Release version must be MAJOR.MINOR.PATCH, got {version!r}")
    if not shutil.which("vpk"):
        raise SystemExit("Install Velopack CLI first: dotnet tool install -g vpk")

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    app_dist = DIST / "JARVIS"
    launcher_exe = DIST / "JARVISClapLauncher.exe"

    # Remove both historical launcher layouts. Older builds/specs may have
    # left a one-folder output with the same name.
    for stale in (app_dist, launcher_exe, DIST / "JARVISClapLauncher"):
        remove_path(stale)

    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "JARVIS.spec"])
    app_exe = app_dist / "JARVIS.exe"
    if not app_exe.is_file():
        raise SystemExit(f"Main application build did not produce {app_exe}")
    run([str(app_exe), "--smoke-test"])

    # Companion launcher is intentionally one-file and is not included in the
    # main Velopack app directory. Its canonical PyInstaller output is dist/*.exe.
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "JARVISClapLauncher.spec"])
    if not launcher_exe.is_file():
        legacy_launcher = DIST / "JARVISClapLauncher" / "JARVISClapLauncher.exe"
        if legacy_launcher.is_file():
            launcher_exe.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(legacy_launcher, launcher_exe)
        else:
            raise SystemExit(
                "Clap launcher build did not produce the expected one-file executable: "
                f"{launcher_exe}"
            )
    if launcher_exe.stat().st_size < 1_000_000:
        raise SystemExit(
            f"Clap launcher executable is unexpectedly small: "
            f"{launcher_exe.stat().st_size} bytes"
        )
    run([str(launcher_exe), "--smoke-test"])
    shutil.copy2(launcher_exe, output / launcher_exe.name)

    run([
        "vpk", "pack",
        "--packId", "JARVIS",
        "--packVersion", version,
        "--packDir", str(app_dist),
        "--mainExe", "JARVIS.exe",
        "--packTitle", "JARVIS",
        "--channel", args.channel,
        "--icon", str(ROOT / "config" / "jarvis.ico"),
        "--outputDir", str(output),
        "--packAuthors", "JARVIS",
    ])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
