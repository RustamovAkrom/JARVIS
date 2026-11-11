"""Install JARVIS Python dependencies and browser runtimes."""

import platform
import subprocess
import sys
from pathlib import Path

OS = platform.system()  # "Windows" | "Darwin" | "Linux"
HERE = Path(__file__).resolve().parent
REQUIREMENTS_FILE = HERE / "requirements.txt"
PLATFORM_NAMES = {
    "Windows": "Windows",
    "Linux": "Linux",
    "Darwin": "macOS",
}

MIN_PY = (3, 14)  # required by pyproject.toml and uv.lock
MAX_PY = (3, 14)  # highest version currently tested


def _run(label: str, args: list[str]) -> None:
    print(f"\n{label}")
    subprocess.run(args, check=True, cwd=HERE)


def _platform_name(system: str | None = None) -> str:
    detected = system or OS
    try:
        return PLATFORM_NAMES[detected]
    except KeyError as exc:
        supported = ", ".join(PLATFORM_NAMES.values())
        raise RuntimeError(
            f"Unsupported operating system '{detected}'. Supported systems: {supported}."
        ) from exc


def _check_python() -> None:
    """Fail immediately and clearly rather than deep inside a pip resolver.

    A wrong interpreter is the single most common way this install goes sideways,
    and the error it produces on its own names a wheel, not the real problem.
    """
    v = sys.version_info[:2]
    if v > MAX_PY:
        # Newer is a warning, not a wall. Turning away someone who installed
        # today's Python is a worse first impression than a version that
        # turns out to work fine, and if a wheel really is missing pip says
        # so plainly.
        print(
            f"\nWARNING: Python {v[0]}.{v[1]} is newer than the "
            f"{MAX_PY[0]}.{MAX_PY[1]} this is tested on. Continuing; if a "
            f"package has no wheel yet, install Python "
            f"{MAX_PY[0]}.{MAX_PY[1]} and run setup with that."
        )
        return
    if v < MIN_PY:
        print(
            f"\nERROR: Python {v[0]}.{v[1]} detected. JARVIS needs at "
            f"least Python {MIN_PY[0]}.{MIN_PY[1]}."
        )
        print("   Install a supported version and run setup with it, e.g.:")
        print(f"     py -{MIN_PY[0]}.{MIN_PY[1]} setup.py        (Windows)")
        print(f"     python{MIN_PY[0]}.{MIN_PY[1]} setup.py      (macOS / Linux)")
        sys.exit(1)


def main() -> None:
    os_name = _platform_name()
    print(
        f"JARVIS setup - detected OS: {os_name}, "
        f"Python {sys.version_info[0]}.{sys.version_info[1]}"
    )
    _check_python()
    if not REQUIREMENTS_FILE.is_file():
        raise FileNotFoundError(
            f"Dependency manifest not found: {REQUIREMENTS_FILE}")

    _run(
        f"Installing Python dependencies for {os_name}...",
        [sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS_FILE)],
    )

    # Chromium covers Chrome/Edge/Opera/Brave/Vivaldi; Firefox for Firefox.
    # (Safari automation additionally needs: python -m playwright install webkit)
    # Not fatal: these are a few hundred megabytes from a CDN that a corporate
    # network or a flaky connection can refuse, and everything except browser
    # automation works without them. Failing the whole install there would send
    # a user away from a working app.
    try:
        _run(
            "Installing Playwright browsers (chromium + firefox)...",
            [sys.executable, "-m", "playwright", "install", "chromium", "firefox"],
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"\nWARNING: Playwright browsers were not installed ({e}).")
        print("    Everything except browser automation works. Retry later with:")
        print(f"    {sys.executable} -m playwright install chromium firefox")

    # OS-specific post-install notes.
    if OS == "Windows":
        try:
            import win32com.client  # noqa: F401
        except ImportError:
            postinstall = (
                Path(sys.executable).parent / "Scripts" / "pywin32_postinstall.py"
            )
            print(
                "\nWARNING: pywin32 did not register correctly; desktop-shortcut "
                "creation will use a slower fallback. To fix it, run:\n"
                f'    "{sys.executable}" -m pip install --force-reinstall pywin32\n'
                f'    "{sys.executable}" "{postinstall}" -install'
            )
    elif OS == "Linux":
        print(
            "\nLinux note: a few voice-controlled OS actions use native tools. "
            "Install the ones you'll use via your package manager:\n"
            "    - volume: pulseaudio-utils (pactl)\n"
            "    - brightness: brightnessctl\n"
            "    - reminders: systemd (systemd-run) or 'at'\n"
            "    - open URLs: xdg-utils (xdg-open)"
        )
    elif OS == "Darwin":
        print(
            "\nmacOS note: volume, brightness and reminders use the built-in "
            "'osascript' / LaunchAgents, so no extra tools are required.\n"
            "    For Safari automation only: python -m playwright install webkit"
        )

    print("\nSetup complete.")
    print("   1) Launch it:  python main.py")
    print("   2) Paste your free Gemini API key when the setup screen appears.")
    print("   3) (Optional) Enable 'Hey Jarvis' from Setup > Wake Word.")


if __name__ == "__main__":
    main()
