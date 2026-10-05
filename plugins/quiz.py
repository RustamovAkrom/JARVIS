"""
JARVIS plugin — Quiz me.

"Test me on the French subjunctive", "ask me five questions about what's in
this PDF", "quiz me on yesterday's chemistry chapter". JARVIS puts the questions
on screen, you answer them at your own pace, and it tells you how you did.

WHO WRITES THE QUESTIONS — and why it matters
---------------------------------------------
JARVIS does, in the same breath it calls this tool. The questions arrive as the
`questions` argument, already written by the model that is mid-conversation with
you. This plugin never calls an API of its own.

That is a deliberate choice, not a shortcut. Generating questions with a
separate text model would spend a request per quiz against a daily quota, add
seconds of latency, and throw away everything the assistant already knows — your
level, your language, the file you just dropped in, what you got wrong last
week. Passing them through as tool arguments costs nothing extra and keeps all
of it.

IT DOES NOT BLOCK
-----------------
`start` puts the questions on the board and returns immediately. A tool call
that waited for a human to finish a quiz would sit there for minutes and time
out. When you finish, the UI hands the results back to JARVIS as a new message,
exactly the way a dropped file does — so the marking, the encouragement, and the
decision to remember any of it happen in normal conversation, in your language.

IT KEEPS NO STORE OF ITS OWN
----------------------------
No quiz_history.json, no per-plugin database. What is worth carrying between
sessions goes where everything else about you goes — long-term memory, through
the same save_memory JARVIS uses for anything else, and only when there is real
continuity to carry: you are working through a language, you keep losing the
same tense. A score from one Tuesday is not that.

That is a rule about the whole app, not about this plugin. Long-term memory is
what makes one person's JARVIS different from another's, and it takes its shape
from what they actually do. If every plugin opened a private file beside it, the
interesting part would be scattered across a dozen of them and JARVIS would know
less about you, not more.

FOR EVERYONE: no keys, no setup, no dependencies beyond the standard library,
same on Windows, macOS and Linux. Language-agnostic — answers are compared after
Unicode normalisation rather than through any per-language table, and anything
the comparison is unsure about is handed to JARVIS to judge rather than being
marked wrong.
"""
from __future__ import annotations

import unicodedata

_MAX_QUESTIONS = 20
_MAX_TEXT = 600        # a question the panel can still show without scrolling away
_MAX_OPTION = 200      # a button, not a paragraph
_TYPES = ("multiple_choice", "true_false", "fill_blank", "open")

PLUGIN = {
    "name": "quiz",
    "description": (
        "Puts a quiz on screen for the user to answer. Use whenever they want to be "
        "tested, drilled, revised or challenged on ANY topic - a language, an exam "
        "subject, a document they just uploaded, general knowledge. Trigger phrases "
        "include 'test me', 'quiz me', 'ask me questions', 'let us revise', 'see if "
        "I know this'. "
        "Writing a set of questions takes you several seconds, and the user hears "
        "silence for all of them. Say one short sentence naming what you are "
        "about to test them on FIRST, then write the questions. "
        "YOU WRITE THE QUESTIONS and pass them in `questions`. Write them in the "
        "user's own language, pitched at the level the conversation suggests, and mix "
        "the types - a page of identical multiple-choice is a worse test than a "
        "varied one. If a file was uploaded, read it first and base the questions on "
        "it. "
        "This returns as soon as the questions are on the board; it does NOT wait for "
        "answers. Results come back to you later as a [QUIZ_DONE] message - mark any "
        "open questions then and tell them how they did. "
        "Use action='stop' to clear a quiz they want to abandon. "
        "If they ask how they have been doing over time, answer from what you "
        "remember about them - this tool keeps no records of its own."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["start", "stop"],
                "description": "'start' puts a new quiz on screen (needs `questions`); "
                               "'stop' clears one they want to abandon.",
            },
            "topic": {
                "type": "STRING",
                "description": "Short name of what is being tested, in the user's own "
                               "language - 'French subjunctive', 'Ottoman reforms'. Used "
                               "as the on-screen title and to track progress over time.",
            },
            "questions": {
                "type": "ARRAY",
                "description": "The questions, in the order they should be asked. 1-20.",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "type": {
                            "type": "STRING",
                            "enum": ["multiple_choice", "true_false", "fill_blank", "open"],
                            "description": "'multiple_choice' (give 3-5 options), "
                                           "'true_false', 'fill_blank' (write the sentence "
                                           "with ___ where the gap is), or 'open' (a short "
                                           "written answer you will mark yourself when the "
                                           "results come back).",
                        },
                        "question": {"type": "STRING", "description": "The question itself."},
                        "options": {
                            "type": "ARRAY",
                            "items": {"type": "STRING"},
                            "description": "The choices. For multiple_choice, 3-5 of them. For "
                                           "true_false, give exactly 2 - the words for true and "
                                           "false IN THE USER'S OWN LANGUAGE, so the buttons "
                                           "read the same way the question does.",
                        },
                        "answer": {
                            "type": "STRING",
                            "description": "The correct answer, written exactly as it appears in "
                                           "`options` when there are options. For fill_blank, the "
                                           "missing word. For open, a model answer to mark "
                                           "against.",
                        },
                        "note": {
                            "type": "STRING",
                            "description": "Optional one-line explanation shown once they have "
                                           "answered. Worth adding - it is where the learning "
                                           "actually happens.",
                        },
                    },
                    "required": ["type", "question", "answer"],
                },
            },
        },
        "required": ["action"],
    },
}


# ── answer comparison ────────────────────────────────────────────────────────
# Normalise, do not pattern-match. Stripping accents and case makes "café",
# "CAFE" and "cafe" agree without a rule per language, so the same code path
# serves Turkish, Greek and Vietnamese alike. Whatever this cannot decide is
# handed to JARVIS to mark instead of being called wrong.

def _norm(s) -> str:
    s = unicodedata.normalize("NFD", str(s or "").strip().casefold())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = "".join(c if (c.isalnum() or c.isspace()) else " " for c in s)
    return " ".join(s.split())


def _truthy(s):
    """A true/false answer, whatever it was displayed as."""
    t = _norm(s)
    if t in ("true", "t", "1", "yes"):
        return True
    if t in ("false", "f", "0", "no"):
        return False
    return None


def grade(question: dict, given: str):
    """True = right, False = wrong, None = JARVIS should mark this one."""
    qtype = str(question.get("type") or "").strip()
    correct = str(question.get("answer") or "")
    opts = question.get("options") or []
    if not str(given or "").strip():
        return False
    if qtype == "open":
        return None
    # An answer key that matches none of the buttons cannot mark anything. It
    # happens: a model asked for the option's text sometimes replies with a
    # letter, and `clean_questions` resolves the ones it can. Marking every
    # answer wrong because the key is unusable is the worst outcome available —
    # the person answered correctly and is told they did not. Hand it over.
    if opts and not any(_norm(correct) == _norm(o) for o in opts):
        return None
    # Compare the words first, whatever they are. A true/false question shown in
    # the user's own language sends back "Doğru" or "Σωστό", not "true", and
    # those match here without anything needing to know which language it was.
    if _norm(given) == _norm(correct):
        return True
    if qtype == "true_false":
        a, b = _truthy(given), _truthy(correct)
        if a is not None and b is not None:
            return a == b
        return False        # two shown labels that did not match: a real miss
    if qtype == "multiple_choice":
        # The UI sends back the option's own text, so a mismatch is a real miss.
        return False
    # fill_blank: near misses are common and often still right ("l'hotel" for
    # "hotel", a different but valid conjugation). Say so rather than inventing
    # a similarity threshold that would be wrong in some language.
    return None


# ── validation ───────────────────────────────────────────────────────────────
# The questions come from a language model, so treat them as untrusted input:
# drop anything unusable rather than putting a broken question on screen.

def _resolve_answer(answer: str, opts: list) -> str:
    """Point the answer key at one of the buttons.

    The model is asked for the option's own text and usually gives it, but a
    multiple-choice answer is habitually written as a letter or a number, and
    "A" matches no button. Left alone, the person clicks the right option and is
    told they were wrong — a silent scoring bug that looks like the quiz being
    broken. Resolving the letter or the number here is a positional lookup, not
    a language rule, so it holds in every alphabet.
    """
    if not opts:
        return answer
    for o in opts:
        if _norm(answer) == _norm(o):
            # Snap to the button's own wording. When the person gets it wrong
            # the panel shows them the right answer, and it should read exactly
            # as the button they did not press.
            return o
    token = _norm(answer)                     # "a)" and "A." both reduce to "a"
    if len(token) == 1 and "a" <= token <= "z":
        i = ord(token) - ord("a")
        if i < len(opts):
            return opts[i]
    if token.isdigit():
        i = int(token) - 1                    # people and models both count from 1
        if 0 <= i < len(opts):
            return opts[i]
    return answer                             # unusable — grade() defers to JARVIS


def clean_questions(raw) -> list:
    out = []
    for q in (raw or [])[:_MAX_QUESTIONS]:
        if not isinstance(q, dict):
            continue
        text = str(q.get("question") or "").strip()[:_MAX_TEXT]
        answer = str(q.get("answer") or "").strip()[:_MAX_TEXT]
        if not text or not answer:
            continue
        qtype = str(q.get("type") or "").strip()
        if qtype not in _TYPES:
            qtype = "multiple_choice" if q.get("options") else "open"
        # Deduplicate: two identical buttons are two ways to give one answer,
        # and one of them will look wrong whichever the person presses.
        opts, seen = [], set()
        for o in (q.get("options") or []):
            o = str(o).strip()[:_MAX_OPTION]
            if o and _norm(o) not in seen:
                seen.add(_norm(o))
                opts.append(o)
        if qtype == "multiple_choice" and len(opts) < 2:
            qtype = "open"            # a choice of one is not a question
        if qtype == "true_false" and len(opts) != 2:
            # Only fall back to English when the model did not label the two
            # buttons itself. Hard-coding a pair per language is the thing this
            # project does not do, and a Turkish question above two buttons
            # reading True and False is exactly what that would look like.
            opts = ["True", "False"]
        shown = opts if qtype in ("multiple_choice", "true_false") else []
        out.append({
            "type": qtype,
            "question": text,
            "options": shown,
            "answer": _resolve_answer(answer, shown),
            "note": str(q.get("note") or "").strip()[:_MAX_TEXT],
        })
    return out


# ── entry point ──────────────────────────────────────────────────────────────

def run(parameters: dict, player=None, session_memory=None) -> str:
    try:
        action = str(parameters.get("action") or "start").strip().lower()
        topic = str(parameters.get("topic") or "").strip()

        if action == "stop":
            if player:
                try:
                    player.hide_quiz()
                except Exception:
                    pass
            return "Quiz cleared."

        questions = clean_questions(parameters.get("questions"))
        if not questions:
            return ("I need the questions themselves before I can put a quiz on "
                    "screen - write them and call quiz again with them.")

        shown = False
        if player:
            try:
                player.show_quiz(topic, questions, grade)
                shown = True
            except Exception:
                shown = False

        if not shown:
            # No interactive panel (an older UI, or a headless run): read them
            # out rather than losing the quiz.
            lines = []
            for i, q in enumerate(questions, 1):
                lines.append(str(i) + ". " + q["question"])
                for j, o in enumerate(q["options"]):
                    lines.append("     " + chr(65 + j) + ") " + o)
            if player:
                try:
                    player.show_content(topic.upper() or "QUIZ", "\n".join(lines))
                except Exception:
                    pass
            return (str(len(questions)) + " questions ready"
                    + ((" on " + topic) if topic else "")
                    + " - I'll read them out; answer whenever you're ready.")

        n = len(questions)
        return (str(n) + " question" + ("s" if n != 1 else "") + " on the board"
                + ((" - " + topic + ".") if topic else ".")
                + " Tell them to take their time, and that you'll go through the"
                  " results together when they finish.")
    except Exception as e:
        return "Sir, the quiz plugin failed: " + str(e)
