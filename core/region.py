"""Where the user lives, as the operating system knows it.

One place for every OS (Windows' home location, macOS's locale, POSIX
variables and locale files), so the shop assistant and the opening briefing
read the same answer.
"""
from __future__ import annotations

import locale
import os
import re
import sys


def home_country() -> str:
    """ISO country code of where the user LIVES, by each OS's own means.

    Not the display language: measured on the author's Windows machine, the
    locale is en-US while the home location is Turkey — reading the locale sent
    the first search to American shops and threw away every price in lira.
    """
    if sys.platform.startswith("win"):
        try:
            import ctypes
            k = ctypes.windll.kernel32
            buf = ctypes.create_unicode_buffer(16)
            try:
                if k.GetUserDefaultGeoName(buf, 16) and re.fullmatch(r"[A-Z]{2}", buf.value):
                    return buf.value
            except AttributeError:
                pass                                   # Windows before 10 1709
            geo = k.GetUserGeoID(16)                   # GEOCLASS_NATION
            if k.GetGeoInfoW(geo, 4, buf, 16, 0) and re.fullmatch(r"[A-Z]{2}", buf.value):
                return buf.value                       # GEO_ISO2
        except Exception:
            pass
    if sys.platform == "darwin":
        try:
            import subprocess
            out = subprocess.run(["defaults", "read", "-g", "AppleLocale"], capture_output=True,
                                 text=True, timeout=2).stdout.strip()
            m = re.search(r"_([A-Z]{2})", out)
            if m:
                return m.group(1)
        except Exception:
            pass
    # POSIX order: LC_ALL overrides every category; then where-you-live's own
    # (address, money); LANG last. A start outside the login session (autostart)
    # may have none of them: the system's own locale file then says.
    names = ("LC_ALL", "LC_ADDRESS", "LC_MONETARY", "LANG")
    for var in names:
        m = re.match(r"[a-z]{2,3}_([A-Z]{2})", os.environ.get(var, ""))
        if m:
            return m.group(1)
    for path in ("/etc/locale.conf", "/etc/default/locale"):
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for var in names:
            m = re.search(rf'^\s*{var}="?[a-z]{{2,3}}_([A-Z]{{2}})', text, re.M)
            if m:
                return m.group(1)
    try:
        m = re.match(r"[a-z]{2,3}_([A-Z]{2})", locale.getlocale()[0] or "")
        if m:
            return m.group(1)
    except Exception:
        pass
    return ""
