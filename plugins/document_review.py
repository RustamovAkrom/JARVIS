"""
JARVIS plugin — Explain this document.

A lease, an insurance policy, a phone contract, a loan agreement, terms of
service, a job offer, a consent form, a government letter. Drop it in, put it on
screen, or hold it up to the camera, and JARVIS lays out what it actually says
and what is worth knowing before signing.

WHY THIS PLUGIN CONTAINS NO LEGAL KNOWLEDGE
-------------------------------------------
Because any it contained would be wrong somewhere. A list of "red flag" clauses,
a table of terms per language, a template per document type — each of those
works in the country and legal system it was written for and quietly misleads
everywhere else. A tenancy clause that is standard in one place is unlawful in
another, and neither of them is in this file.

So the reading is done by the model that is already in the conversation, in the
document's own language, and this plugin lays the result out. It receives
findings; it never decides what a finding is. That is what lets the same code
serve a Turkish rental contract, a Brazilian phone plan and a Japanese offer
letter, without a line of it knowing any of them exist.

WHERE THE DOCUMENT COMES FROM IS NOT THIS PLUGIN'S PROBLEM EITHER
-----------------------------------------------------------------
An uploaded PDF, a page on screen, a photograph of a paper letter, pasted text —
all four already reach the assistant through machinery that exists. By taking
findings rather than a file, this plugin works with all of them and knows about
none of them.

WHY IT IS SHOWN, NOT SPOKEN
---------------------------
Nobody can hold eight clauses of a contract in their head from audio. The value
is a page you can re-read, scroll back through and copy from, which is why the
findings render into the panel below the HUD, ordered by how much they matter.
JARVIS still says the short version out loud.

FOR EVERYONE: no keys, no setup, no network calls, nothing beyond the standard
library. Same on Windows, macOS and Linux, in any language and any alphabet.
"""
from __future__ import annotations

# Severity is an ordinal, not a category — the only thing this plugin claims to
# know about a finding is how far up the page it belongs. Three levels is what a
# person can act on: something that changes their decision, something to be
# aware of, and context. Anything finer would be a judgement this file is in no
# position to make.
_LEVELS = ("serious", "caution", "note")
_RANK = {name: i for i, name in enumerate(_LEVELS)}

_MAX_FINDINGS = 30
_MAX_QUOTE = 400
_MAX_TEXT = 1200

PLUGIN = {
    "name": "document_review",
    "description": (
        "Lays out a document's contents on screen: what it is, what agreeing to it "
        "commits the user to, and each point worth knowing before they sign, ordered "
        "by how much it matters. Use for any document a person is expected to accept "
        "or act on - rental and employment contracts, insurance policies, loan and "
        "subscription agreements, terms of service, warranties, consent forms, "
        "official letters and forms. Trigger phrases include 'explain this contract', "
        "'what am I signing', 'is there anything I should watch out for here', 'read "
        "this policy for me', 'what does this letter want from me'. "
        "FIRST get the text - read the uploaded file, look at the screen, or use the "
        "photo they showed you - THEN call this with what you found. "
        "YOU decide what matters: this tool holds no legal knowledge and applies no "
        "checklist, because what counts as a warning depends on the document, the "
        "country and the person. Write every field in the user's own language, quote "
        "the document's real wording so they can find the clause, and mark severity "
        "by what would actually change their decision - not everything unusual is "
        "serious. Say plainly in `unclear` whatever the document does not settle "
        "rather than guessing. "
        "You are not a lawyer and this is not advice; explain what the text says and "
        "what it would mean for them, and suggest getting professional help when the "
        "stakes justify it."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "title": {
                "type": "STRING",
                "description": "What this document is, in the user's language - "
                               "'Rental agreement', 'Health insurance policy'.",
            },
            "summary": {
                "type": "STRING",
                "description": "One or two sentences, in the user's language: what the "
                               "document is for and what accepting it commits them to. "
                               "The thing they would want to know if they read nothing "
                               "else.",
            },
            "findings": {
                "type": "ARRAY",
                "description": "The points worth knowing, most important first. Leave "
                               "out anything routine - a long list of the ordinary "
                               "buries the one clause that matters.",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "heading": {
                            "type": "STRING",
                            "description": "A few words naming the point, in the user's "
                                           "language - 'Automatic renewal', 'Deposit is "
                                           "non-refundable'.",
                        },
                        "detail": {
                            "type": "STRING",
                            "description": "What it means for them in plain language, in "
                                           "their own words, not the document's. Include "
                                           "the numbers, dates and deadlines that matter.",
                        },
                        "severity": {
                            "type": "STRING",
                            "enum": ["serious", "caution", "note"],
                            "description": "'serious' if it could change whether they "
                                           "sign or cost them real money, 'caution' if "
                                           "they should be aware of it, 'note' for useful "
                                           "context. Use 'serious' sparingly - if "
                                           "everything is serious, nothing is.",
                        },
                        "quote": {
                            "type": "STRING",
                            "description": "The document's own wording for this point, so "
                                           "they can find the clause. Keep it short and "
                                           "copy it exactly, in the document's language.",
                        },
                        "suggestion": {
                            "type": "STRING",
                            "description": "Optional - what they could actually do about "
                                           "it: a question to ask, a term to negotiate, a "
                                           "deadline to diarise.",
                        },
                    },
                    "required": ["heading", "detail", "severity"],
                },
            },
            "unclear": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": "Anything the document leaves unanswered or you could not "
                               "determine from the part you saw - a missing figure, an "
                               "unreadable page, a term it refers to but does not "
                               "define. Say so rather than filling the gap.",
            },
        },
        "required": ["title", "findings"],
    },
}


def _clip(s, limit: int) -> str:
    s = str(s or "").strip()
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def clean_findings(raw) -> list:
    """The findings come from a language model, so treat them as untrusted:
    drop what cannot be rendered, and sort by severity so the thing that could
    cost someone money is not below a note about office hours."""
    out = []
    for f in (raw or []):
        if not isinstance(f, dict):
            continue
        heading = _clip(f.get("heading"), 120)
        detail = _clip(f.get("detail"), _MAX_TEXT)
        if not heading and not detail:
            continue
        sev = str(f.get("severity") or "").strip().lower()
        if sev not in _RANK:
            sev = "note"
        out.append({
            "heading": heading or detail[:60],
            "detail": detail,
            "severity": sev,
            "quote": _clip(f.get("quote"), _MAX_QUOTE),
            "suggestion": _clip(f.get("suggestion"), 300),
        })
    out.sort(key=lambda f: _RANK[f["severity"]])       # stable: keeps model order within a level
    return out[:_MAX_FINDINGS]


def tally(findings: list) -> dict:
    """How many of each level. Counting is the only judgement this file makes."""
    return {lvl: sum(1 for f in findings if f["severity"] == lvl) for lvl in _LEVELS}


def _spoken(title: str, findings: list, unclear: list) -> str:
    """The short version, for JARVIS to say while the page is on screen. Kept
    deliberately thin — the detail is readable, and reading it aloud would be
    the one thing this plugin exists to avoid."""
    counts = tally(findings)
    n = len(findings)
    if not n:
        return ("Nothing in " + (title or "that document") + " stood out as worth "
                "flagging. It's on screen if you want to look through it.")
    bits = [str(counts[lvl]) + " " + lvl for lvl in _LEVELS if counts[lvl]]
    line = (str(n) + " point" + ("s" if n != 1 else "") + " from "
            + (title or "the document") + " on screen"
            + (" — " + ", ".join(bits) if bits else "") + ".")
    if counts["serious"]:
        line += " Tell them the serious ones first, briefly, in their own language."
    else:
        line += " Give them the gist in a sentence; the detail is there to read."
    if unclear:
        line += (" " + str(len(unclear)) + " thing"
                 + ("s" if len(unclear) != 1 else "") + " the document doesn't settle.")
    return line


def run(parameters: dict, player=None, session_memory=None) -> str:
    try:
        title = _clip(parameters.get("title"), 120)
        summary = _clip(parameters.get("summary"), _MAX_TEXT)
        findings = clean_findings(parameters.get("findings"))
        unclear = [_clip(u, 300) for u in (parameters.get("unclear") or [])
                   if str(u or "").strip()][:10]

        if not findings and not summary:
            return ("I need the document read first — look at the file or the screen, "
                    "then call document_review with what you found.")

        shown = False
        if player:
            try:
                player.show_review(title, summary, findings, unclear)
                shown = True
            except Exception:
                shown = False

        if not shown:
            # No rich panel available (an older UI, or a headless run): fall back
            # to flat text rather than losing the review.
            lines = []
            if summary:
                lines += [summary, ""]
            for f in findings:
                lines.append("[" + f["severity"].upper() + "] " + f["heading"])
                lines.append("    " + f["detail"])
                if f["quote"]:
                    lines.append('    "' + f["quote"] + '"')
                if f["suggestion"]:
                    lines.append("    → " + f["suggestion"])
                lines.append("")
            if unclear:
                lines.append("Not settled by the document:")
                lines += ["  · " + u for u in unclear]
            if player:
                try:
                    player.show_content(title.upper() or "DOCUMENT", "\n".join(lines))
                except Exception:
                    pass

        return _spoken(title, findings, unclear)
    except Exception as e:
        return "Sir, the document review failed: " + str(e)
