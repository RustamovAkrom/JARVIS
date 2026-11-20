#flight_finder.py
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from config import is_windows, is_mac, is_linux
from memory.config_manager import get_assistant_name

try:
    from core.paths import data_dir as _data_dir
except ImportError:          # a core older than core/paths.py
    _data_dir = lambda: Path(__file__).resolve().parent.parent


def _get_base_dir() -> Path:
    return _data_dir()


BASE_DIR        = _get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"


# ── Plugin settings (⚙ SETUP → PLUGIN SETTINGS) ─────────────────────────────
# All optional. They only fill in what the user did not say out loud.
_NS = "flight_finder"

PLUGIN_SETTINGS = {
    "namespace": _NS,
    "title": "✈️  FLIGHT FINDER",
    "fields": [
        {"key": "home_airport", "label": "Home airport / city", "type": "text",
         "placeholder": "IST   —   used when you say only where you are going"},
        {"key": "cabin", "label": "Preferred cabin", "type": "choice",
         "options": ["economy", "premium", "business", "first"],
         "default": "economy"},
        {"key": "save_reports", "label": "Always save the full report to the Desktop",
         "type": "toggle", "default": False},
    ],
}


def _setting(key: str, default=""):
    """One stored value, or `default`. Never raises: a plugin that cannot read
    its own config still has to run."""
    try:
        from memory.config_manager import get_plugin_setting
        val = get_plugin_setting(_NS, key, default)
    except Exception:
        return default
    return default if val in (None, "") else val


def _get_api_key() -> str:
    """The Gemini credential: the user's key, or a relay licence (core/gemini.py)."""
    from core import gemini
    return gemini.api_key()


_MONTH_MAP: dict[str, int] = {

    "january": 1, "february": 2, "march": 3,     "april": 4,
    "may": 5,     "june": 6,     "july": 7,       "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}

# English fast-path only — Gemini (below) normalizes date expressions in ANY
# language to YYYY-MM-DD, so no other language needs to be hardcoded here.
_RELATIVE_MAP_KEYS = {
    "today",
    "tomorrow",
}


def _parse_date(raw: str) -> str:

    raw   = raw.strip()
    lower = raw.lower()
    today = datetime.now()

    if re.match(r"\d{4}-\d{2}-\d{2}", raw):
        return raw
    for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%d.%m.%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass

    relative = {
        "today": today,
        "tomorrow": today + timedelta(days=1),
    }
    for key, val in relative.items():
        if key in lower:
            return val.strftime("%Y-%m-%d")

    try:
        from core import gemini
        result = gemini.text(
            f"Today is {today.strftime('%Y-%m-%d')}. "
            f"Convert this date expression to YYYY-MM-DD: '{raw}'. "
            f"Return ONLY the date string, nothing else.",
            tier=gemini.FAST,
        )
        if re.match(r"\d{4}-\d{2}-\d{2}", result):
            return result
    except Exception as e:
        print(f"[FlightFinder] ⚠️ Gemini date parse failed: {e}")

    for month_name, month_num in _MONTH_MAP.items():
        if month_name in lower:
            day_match = re.search(r"\d{1,2}", raw)
            if day_match:
                day  = int(day_match.group())
                year = today.year if month_num >= today.month else today.year + 1
                return f"{year}-{month_num:02d}-{day:02d}"

    # Last resort: today
    print(f"[FlightFinder] ⚠️ Could not parse date '{raw}' — using today.")
    return today.strftime("%Y-%m-%d")

_CABIN_CODE: dict[str, str] = {
    "economy":  "1",
    "premium":  "2",
    "business": "3",
    "first":    "4",
}


def _build_google_flights_url(
    origin:      str,
    destination: str,
    date:        str,
    return_date: str | None = None,
    passengers:  int        = 1,
    cabin:       str        = "economy",
) -> str:
    cabin_code = _CABIN_CODE.get(cabin.lower(), "1")
    base       = "https://www.google.com/travel/flights"

    # Google Flights accepts these query params for pre-filling
    if return_date:
        trip = f"Flights+from+{origin}+to+{destination}+on+{date}+returning+{return_date}"
    else:
        trip = f"Flights+from+{origin}+to+{destination}+on+{date}"

    # Only the trip in words: Google Flights reads it from `q`. (A fixed
    # `tfs` was left here once — an encoded IST->LHR search on one date in
    # 2025 — and could put that trip on screen instead of the one asked.)
    from urllib.parse import quote
    return (
        f"{base}"
        f"?q={quote(trip.replace('+', ' '))}"
        f"&cabin={cabin_code}"
        f"&adults={passengers}"
    )


def _reads_pages() -> bool:
    """Whether this copy reads the results page itself. The product does not:
    Google's terms do not allow a program to read its pages, so the product
    opens the search in the user's browser and the user reads it there."""
    try:
        from core import edition
        return not edition.is_product()
    except Exception:
        return True



def _search_flights_browser(
    origin:      str,
    destination: str,
    date:        str,
    return_date: str | None,
    passengers:  int,
    cabin:       str,
) -> tuple[str, str]:
    import time
    from actions.browser_control import browser_control

    url = _build_google_flights_url(
        origin, destination, date, return_date, passengers, cabin
    )

    print("[FlightFinder] 🌐 Opening the flight search")
    browser_control({"action": "go_to", "url": url})
    time.sleep(5)

    raw = browser_control({"action": "get_text"})
    return (raw or ""), url

def _parse_flights_with_gemini(
    raw_text:    str,
    origin:      str,
    destination: str,
    date:        str,
) -> list[dict]:
    from google import genai as _genai
    from google.genai import types

    prompt  = (
        f"Extract flight options from {origin} to {destination} on {date} "
        f"from this Google Flights page text:\n\n{raw_text[:12000]}\n\n"
        f"Return a JSON array of up to 5 flights:\n"
        f'[{{"airline":"...","departure":"HH:MM","arrival":"HH:MM",'
        f'"duration":"Xh Ym","stops":0,"price":"...","currency":"USD"}}]\n'
        f"If no flights found, return: []"
    )

    try:
        from core import gemini
        response = gemini.call(
            prompt,
            tier=gemini.SMART,
            timeout_ms=30_000,
            config=types.GenerateContentConfig(
                system_instruction=(
                    "You are a flight data extraction expert. "
                    "Extract flight information from raw webpage text. "
                    "Return ONLY valid JSON — no markdown, no explanation."
                )
            ),
        )
        text     = re.sub(r"```(?:json)?", "", response.text).strip().rstrip("`").strip()
        flights  = json.loads(text)
        return flights if isinstance(flights, list) else []
    except Exception as e:
        print(f"[FlightFinder] ⚠️ Gemini parse failed: {e}")
        return []

def _format_spoken(
    flights:     list[dict],
    origin:      str,
    destination: str,
    date:        str,
) -> str:
    if not flights:
        return (
            f"I couldn't find any flights from {origin} to {destination} "
            f"on {date}. The page may not have loaded correctly."
        )

    lines = [f"Here are the top flights from {origin} to {destination} on {date}."]

    for i, f in enumerate(flights[:5], 1):
        airline   = f.get("airline",   "Unknown airline")
        departure = f.get("departure", "--:--")
        arrival   = f.get("arrival",   "--:--")
        duration  = f.get("duration",  "")
        stops     = f.get("stops",     0)
        price     = f.get("price",     "")
        currency  = f.get("currency",  "")

        stop_str  = "non-stop" if stops == 0 else f"{stops} stop{'s' if stops > 1 else ''}"
        price_str = f"{price} {currency}".strip() if price else "price unavailable"
        dur_str   = f", {duration}" if duration else ""

        lines.append(
            f"Option {i}: {airline}, departing {departure}, "
            f"arriving {arrival}{dur_str}, {stop_str}, {price_str}."
        )

    # Cheapest — strip non-digits for comparison
    priced = [f for f in flights if f.get("price")]
    if priced:
        cheapest = min(
            priced,
            key=lambda x: int(re.sub(r"[^\d]", "", str(x["price"])) or "999999"),
        )
        lines.append(
            f"The cheapest option is {cheapest.get('airline')} "
            f"at {cheapest.get('price')} {cheapest.get('currency', '')}."
        )

    return " ".join(lines)


def _format_text_report(
    flights:     list[dict],
    origin:      str,
    destination: str,
    date:        str,
    return_date: str | None,
    page_url:    str,
) -> str:
    lines = [
        f"{get_assistant_name()} — Flight Search Results",
        "─" * 50,
        f"Route     : {origin} → {destination}",
        f"Date      : {date}",
    ]
    if return_date:
        lines.append(f"Return    : {return_date}")
    lines += [
        f"Searched  : {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"Source    : {page_url}",
        "─" * 50,
        "",
    ]

    if not flights:
        lines.append("No flights found.")
    else:
        for i, f in enumerate(flights, 1):
            stops    = f.get("stops", 0)
            stop_str = "Non-stop" if stops == 0 else f"{stops} stop(s)"
            lines += [
                f"Flight {i}:",
                f"  Airline   : {f.get('airline',   'N/A')}",
                f"  Departure : {f.get('departure', 'N/A')}",
                f"  Arrival   : {f.get('arrival',   'N/A')}",
                f"  Duration  : {f.get('duration',  'N/A')}",
                f"  Stops     : {stop_str}",
                f"  Price     : {f.get('price', 'N/A')} {f.get('currency', '')}",
                "",
            ]

    return "\n".join(lines)

def _save_to_desktop(content: str, origin: str, destination: str) -> str:
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"flights_{origin}_{destination}_{ts}.txt".replace(" ", "_")
    from core.user_dirs import folder
    desktop  = folder("desktop")      # the real one, never a made-up ~/Desktop
    filepath = desktop / filename

    filepath.write_text(content, encoding="utf-8")
    print(f"[FlightFinder] 💾 Saved: {filepath}")

    try:
        if is_windows():
            subprocess.Popen(["notepad.exe", str(filepath)])
        elif is_mac():
            subprocess.Popen(["open", "-t", str(filepath)])
        else:
            subprocess.Popen(["xdg-open", str(filepath)])
    except Exception as e:
        print(f"[FlightFinder] ⚠️ Could not open text editor: {e}")

    return str(filepath)


def flight_finder(parameters: dict, player=None, speak=None) -> str:
    params = parameters or {}

    origin      = params.get("origin",      "").strip()
    destination = params.get("destination", "").strip()
    date_raw    = params.get("date",        "").strip()
    return_raw  = (params.get("return_date") or "").strip()
    passengers  = max(1, int(params.get("passengers", 1)))
    cabin       = params.get("cabin", "").strip().lower()
    _save       = params.get("save")

    # Settings fill the gaps, never override a spoken instruction. "Find me a
    # flight to London" is a complete sentence for someone who always leaves
    # from the same airport, so a stored home airport makes it one here too.
    if not origin:
        origin = str(_setting("home_airport", "")).strip()
    if not cabin:
        cabin = str(_setting("cabin", "economy")).strip().lower()
    save = bool(_setting("save_reports", False)) if _save is None else bool(_save)

    if not origin or not destination:
        return "Please provide both origin and destination."
    if not date_raw:
        return "Please provide a departure date."

    # Normalise cabin value
    if cabin not in _CABIN_CODE:
        cabin = "economy"

    date        = _parse_date(date_raw)
    return_date = _parse_date(return_raw) if return_raw else None

    if player:
        player.write_log(f"[FlightFinder] {origin} → {destination} on {date}")

    if speak:
        speak(f"Searching flights from {origin} to {destination} on {date}.")

    print(
        f"[FlightFinder] ▶️ {origin} → {destination} | {date}"
        f"{' → ' + return_date if return_date else ''}"
        f" | {cabin} | {passengers} pax"
    )

    if not _reads_pages():
        import webbrowser
        url = _build_google_flights_url(origin, destination, date, return_date, passengers, cabin)
        try:
            webbrowser.open(url, new=2)
        except Exception as e:
            return f"Could not open the browser: {e}"
        return ("status=opened where=browser - the flight search is open in the user's "
                "browser; say so in one short sentence. You have not seen the results, "
                "so do not name flights or prices.")

    try:
        raw_text, page_url = _search_flights_browser(
            origin, destination, date, return_date, passengers, cabin
        )

        if not raw_text:
            return "Could not retrieve flight data. The page may not have loaded."

        if speak:
            speak("Analysing the results now.")

        flights = _parse_flights_with_gemini(raw_text, origin, destination, date)
        spoken  = _format_spoken(flights, origin, destination, date)

        if speak:
            speak(spoken)

        result = spoken

        if save and flights:
            report     = _format_text_report(flights, origin, destination, date, return_date, page_url)
            saved_path = _save_to_desktop(report, origin, destination)
            result    += f" Results saved to Desktop: {saved_path}"

        return result

    except Exception as e:
        print(f"[FlightFinder] ❌ {e}")
        return f"Flight search failed: {e}"


# ── Plugin entry point (auto-discovered by core/plugin_loader.py) ────────────

def _speaker(player):
    """The `speak` channel, plugin-side.

    As a built-in action this file was handed a `speak` callable by main.py.
    Plugins are handed `player.request_say` instead — main.py wires it up at
    startup and it sends the identical client content into the Live session —
    so this is the same channel under another name, not a downgrade. Returns
    None when no session is connected, which every `if speak:` below already
    copes with.
    """
    fn = getattr(player, "request_say", None) if player is not None else None
    if not callable(fn):
        return None

    def speak(text: str) -> None:
        try:
            fn(text)
        except Exception:
            pass

    return speak


def run(parameters: dict, player=None, session_memory=None) -> str:
    return flight_finder(parameters, player=player, speak=_speaker(player))


# ── Plugin declaration (auto-discovered by core/plugin_loader.py) ───────────
PLUGIN = {
    # For the plugin manager (core/plugin_loader): never sent to a model.
    "level": "basic",
    "label": "Flight finder",
    "summary": "Finds flights for the trip you describe.",
    "setup": [
        "In these settings, type your home airport or city (for example IST).",
        "Say: \"Find me a cheap flight to Berlin next Friday.\"",
    ],
    "name": "flight_finder",
    "description": "Searches Google Flights and speaks the best options.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "origin": {
                "type": "STRING",
                "description": "Departure city or airport code"
            },
            "destination": {
                "type": "STRING",
                "description": "Arrival city or airport code"
            },
            "date": {
                "type": "STRING",
                "description": "Departure date (any format)"
            },
            "return_date": {
                "type": "STRING",
                "description": "Return date for round trips"
            },
            "passengers": {
                "type": "INTEGER",
                "description": "Number of passengers (default: 1)"
            },
            "cabin": {
                "type": "STRING",
                "description": "economy | premium | business | first"
            },
            "save": {
                "type": "BOOLEAN",
                "description": "Save results to Notepad"
            }
        },
        "required": [
            "origin",
            "destination",
            "date"
        ]
    },
}
