"""Build and package a Windows JARVIS release using PyInstaller and Velopack."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], *, cwd: Path = ROOT) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "release")
    parser.add_argument("--repo-url", default="https://github.com/RustamovAkrom/JARVIS")
    parser.add_argument("--channel", default="stable")
    args = parser.parse_args()

    if sys.platform != "win32":
        raise SystemExit("The production Windows installer must be built on Windows.")
    version = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"][
        "version"
    ]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise SystemExit(f"Release version must be MAJOR.MINOR.PATCH, got {version!r}")
    if not shutil.which("vpk"):
        raise SystemExit("Install Velopack CLI first: dotnet tool install -g vpk")

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    dist = ROOT / "dist" / "JARVIS"
    if dist.exists():
        raise SystemExit(
            f"Build output already exists at {dist}. Inspect or remove it before rebuilding."
        )
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "JARVIS.spec"])
    smoke = dist / "JARVIS.exe"
    run([str(smoke), "--smoke-test"])
    run(
        [
            "vpk",
            "pack",
            "--packId",
            "JARVIS",
            "--packVersion",
            version,
            "--packDir",
            str(dist),
            "--mainExe",
            "JARVIS.exe",
            "--packTitle",
            "JARVIS",
            "--channel",
            args.channel,
            "--icon",
            str(ROOT / "config" / "jarvis.ico"),
            "--outputDir",
            str(output),
            "--packAuthors",
            "JARVIS",
        ]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
