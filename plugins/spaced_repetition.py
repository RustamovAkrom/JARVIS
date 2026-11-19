"""
Plugin — Spaced repetition flashcards (Anki-style SM-2).

Turns JARVIS into a memory coach. The user builds decks by voice — add a
card ("'ephemeral' means 'short-lived'"), or have Gemini generate a batch
("make me 15 Spanish food-vocabulary cards") — then reviews them in short
daily sessions. Each card is scheduled with the SM-2 algorithm, so items
you find hard come back sooner and easy ones drift further out.

The whole deck lives in memory/srs_cards.json and persists forever, which
is exactly what Gemini alone can't do: it can't remember, across sessions,
which of your 200 cards are due today. That scheduling *is* the product.

Review flow (each step is one voice turn):
  1. "review"  → JARVIS asks the front of the next due card
  2. "reveal"  → JARVIS gives the answer (the back)
  3. "grade" with quality again/hard/good/easy → schedules it and moves on

Pure Python for the scheduling; Gemini (already configured) only for the
optional card-generation. Cross-OS, all languages.
"""

import json
import threading
from datetime import date, timedelta
from pathlib import Path

try:
    from core.paths import data_dir as _data_dir
except ImportError:          # a core older than core/paths.py
    _data_dir = lambda: Path(__file__).resolve().parent.parent

STATE_FILE = _data_dir() / "memory" / "srs_cards.json"

PLUGIN = {
    # For the plugin manager (core/plugin_loader): never sent to a model.
    "level": "basic",
    "label": "Flashcards",
    "summary": "Flashcards that come back just before you would forget them.",
    "setup": [
        "Open a document and say \"Make flashcards from this\", or \"Make flashcards about Spanish verbs.\"",
        "Say: \"Quiz my flashcards\" — it brings back each card just before you would forget it.",
    ],
    "name": "spaced_repetition",
    "description": (
        "A spaced-repetition FLASHCARD trainer (Anki-style) for memorising "
        "vocabulary, definitions, facts, formulas — anything. It remembers the "
        "user's whole deck across sessions and schedules each card so hard ones "
        "return sooner. Use for: 'add a flashcard', 'make me 15 flashcards "
        "about the solar system', 'start my review', 'show the answer', "
        "'I got it / that was hard / I couldn't recall it' (grading a card), "
        "'how many cards are due'. Use "
        "this instead of quiz tools for anything the user wants to MEMORISE over "
        "time."
    ),
    # Used in place of `description` only when a small model is on a tool
    # budget - Gemini still gets the full text. See providers/schema.py.
    "brief": (
        "Flashcards that come back before they are forgotten: adds or writes cards, runs the "
        "review, grades the answers, deck stats."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "'add' create one card, 'generate' write a batch on the topic, "
                    "'review' ask the next due card, 'reveal' show its answer, "
                    "'grade' score the shown card, 'stats' deck summary."
                ),
            },
            "front": {"type": "STRING", "description": "For 'add': the question / prompt side."},
            "back": {"type": "STRING", "description": "For 'add': the answer side."},
            "deck": {"type": "STRING", "description": "Optional deck name (default 'default')."},
            "topic": {"type": "STRING", "description": "For 'generate': what the cards should be about."},
            "count": {"type": "INTEGER", "description": "For 'generate': how many cards (default 10)."},
            "language": {"type": "STRING", "description": "For 'generate': language/format, e.g. 'English→Turkish vocab'."},
            "quality": {
                "type": "STRING",
                "description": "For 'grade': how well the user recalled — 'again', 'hard', 'good', or 'easy'.",
            },
        },
        "required": [],
    },
}

# see core/gemini.py — model, timeout and fallback ladder live there
_TIER = "smart"

# module-level: which card id is currently being reviewed this session
_lock = threading.Lock()
_current = {"id": None}


# ── persistence ──────────────────────────────────────────────────────────────

def _load() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    data.setdefault("cards", [])
    data.setdefault("next_id", 1)
    return data


def _save(data: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def _today() -> str:
    return date.today().isoformat()


def _new_card(data: dict, front: str, back: str, deck: str) -> dict:
    card = {
        "id": data["next_id"], "front": front, "back": back, "deck": deck,
        "ease": 2.5, "interval": 0, "reps": 0, "due": _today(),
    }
    data["next_id"] += 1
    data["cards"].append(card)
    return card


def _due(data: dict) -> list:
    today = _today()
    return [c for c in data["cards"] if c.get("due", today) <= today]


# ── SM-2 scheduling ───────────────────────────────────────────────────────────

# Canonical grade tokens (defined in the parameter schema) plus the raw SM-2
# 0-5 scale, so Gemini can pass either. No free-text matching of user speech.
_QMAP = {"again": 1, "hard": 3, "good": 4, "easy": 5,
         "0": 1, "1": 1, "2": 3, "3": 3, "4": 4, "5": 5}


def _schedule(card: dict, quality: int) -> None:
    """Classic SM-2. quality < 3 → lapse (see again today)."""
    ease = card.get("ease", 2.5)
    if quality < 3:
        card["reps"] = 0
        card["interval"] = 0
        card["due"] = _today()  # stays due this session
    else:
        reps = card.get("reps", 0) + 1
        if reps == 1:
            interval = 1
        elif reps == 2:
            interval = 6
        else:
            interval = max(1, round(card.get("interval", 1) * ease))
        card["reps"] = reps
        card["interval"] = interval
        card["due"] = (date.today() + timedelta(days=interval)).isoformat()
    # ease update (bounded)
    ease = ease + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    card["ease"] = max(1.3, round(ease, 3))


# ── Gemini batch generation ───────────────────────────────────────────────────

def _generate_cards(topic: str, count: int, language: str) -> list:
    """Returns a list of {'front','back'} dicts. Raises on failure."""
    from core import gemini
    api_key = gemini.api_key()
    if not api_key:
        raise RuntimeError("no API key configured")

    from google import genai
    fmt = f" Format/language: {language}." if language else ""
    prompt = (
        f"Create exactly {count} spaced-repetition flashcards about: {topic}.{fmt}\n"
        "Return ONLY a minified JSON array, no markdown, each element "
        '{"front": "...", "back": "..."} where front is a short prompt/question '
        "and back is the concise answer. Keep both sides short."
    )
    from core import gemini
    resp = gemini.call(prompt, tier=_TIER, timeout_ms=30_000, key=api_key)
    if resp is None:
        raise RuntimeError("every Gemini model on the ladder failed")
    text = (resp.text or "").strip()
    if "[" in text and "]" in text:
        text = text[text.find("["): text.rfind("]") + 1]
    cards = json.loads(text)
    out = []
    for c in cards:
        f, b = str(c.get("front", "")).strip(), str(c.get("back", "")).strip()
        if f and b:
            out.append({"front": f, "back": b})
    return out


# ── HUD helper ─────────────────────────────────────────────────────────────────

def _panel(player, title: str, text: str) -> None:
    if player:
        try:
            player.show_content(title, text)
        except Exception:
            pass


# ── entry point ───────────────────────────────────────────────────────────────

def run(parameters: dict, player=None, session_memory=None) -> str:
    action = (parameters.get("action") or "").strip().lower()
    data = _load()

    # -------- ADD --------
    if action == "add":
        front = (parameters.get("front") or "").strip()
        back = (parameters.get("back") or "").strip()
        if not front or not back:
            return "To add a card I need both a front (prompt) and a back (answer)."
        deck = (parameters.get("deck") or "default").strip() or "default"
        _new_card(data, front, back, deck)
        _save(data)
        return (f"Added the card to your '{deck}' deck. "
                f"You now have {len(data['cards'])} cards, {len(_due(data))} due today.")

    # -------- GENERATE --------
    if action == "generate":
        topic = (parameters.get("topic") or "").strip()
        if not topic:
            return "Tell me what topic the flashcards should cover."
        try:
            count = max(1, min(50, int(parameters.get("count") or 10)))
        except (TypeError, ValueError):
            count = 10
        deck = (parameters.get("deck") or topic[:30]).strip() or "default"
        language = (parameters.get("language") or "").strip()
        try:
            pairs = _generate_cards(topic, count, language)
        except Exception as e:
            return f"I couldn't generate the cards: {e}"
        if not pairs:
            return "I couldn't produce any cards for that topic — try rephrasing it."
        for pr in pairs:
            _new_card(data, pr["front"], pr["back"], deck)
        _save(data)
        return (f"Generated {len(pairs)} cards into your '{deck}' deck. "
                f"You now have {len(data['cards'])} cards total, {len(_due(data))} due today. "
                f"Say 'start my review' whenever you're ready.")

    # -------- REVIEW (ask next due card) --------
    if action in ("review", "next"):
        due = _due(data)
        if not due:
            upcoming = sorted(c.get("due", _today()) for c in data["cards"])
            tail = f" Next card is due {upcoming[0]}." if upcoming else ""
            return f"You're all caught up — no cards are due right now.{tail}"
        with _lock:
            prev = _current["id"]
            pick = next((c for c in due if c["id"] != prev), due[0])
            _current["id"] = pick["id"]
        _panel(player, "🎴 FLASHCARD REVIEW",
               f"Q: {pick['front']}\n\n({len(due)} due · deck: {pick['deck']})")
        return (f"Card {len(due)} due. Here's the prompt: {pick['front']}. "
                f"Say 'show the answer' when you're ready.")

    # -------- REVEAL --------
    if action in ("reveal", "answer", "show"):
        with _lock:
            cid = _current["id"]
        card = next((c for c in data["cards"] if c["id"] == cid), None)
        if not card:
            return "There's no card in review right now. Say 'start my review' first."
        _panel(player, "🎴 FLASHCARD REVIEW",
               f"Q: {card['front']}\n\nA: {card['back']}\n\n"
               f"(rate it: again / hard / good / easy)")
        return (f"The answer is: {card['back']}. "
                f"How did you do — again, hard, good, or easy?")

    # -------- GRADE --------
    if action in ("grade", "rate"):
        with _lock:
            cid = _current["id"]
        card = next((c for c in data["cards"] if c["id"] == cid), None)
        if not card:
            return "There's no card to grade yet. Say 'start my review' first."
        q = _QMAP.get((parameters.get("quality") or "").strip().lower())
        if q is None:
            return "Tell me how you did: again, hard, good, or easy."
        _schedule(card, q)
        _save(data)
        with _lock:
            _current["id"] = None
        due = _due(data)
        when = "today" if card["interval"] == 0 else (
            "tomorrow" if card["interval"] == 1 else f"in {card['interval']} days")
        if not due:
            return f"Got it — you'll see that card again {when}. That's all your due cards, nicely done!"
        # auto-advance to the next card
        with _lock:
            pick = due[0]
            _current["id"] = pick["id"]
        _panel(player, "🎴 FLASHCARD REVIEW",
               f"Q: {pick['front']}\n\n({len(due)} left · deck: {pick['deck']})")
        return (f"Scheduled — next review {when}. {len(due)} to go. "
                f"Next prompt: {pick['front']}. Say 'show the answer' when ready.")

    # -------- STATS --------
    if action in ("stats", "status", ""):
        total = len(data["cards"])
        if total == 0:
            return ("Your flashcard deck is empty. Add a card or ask me to "
                    "generate some on any topic.")
        due = len(_due(data))
        decks = {}
        for c in data["cards"]:
            decks[c["deck"]] = decks.get(c["deck"], 0) + 1
        deck_str = ", ".join(f"{k} ({v})" for k, v in
                             sorted(decks.items(), key=lambda kv: -kv[1])[:4])
        return (f"You have {total} flashcards across {len(decks)} "
                f"{'deck' if len(decks) == 1 else 'decks'} — {deck_str}. "
                f"{due} {'card is' if due == 1 else 'cards are'} due for review today.")

    return "I can add cards, generate a batch, run a review, or show your deck stats."
