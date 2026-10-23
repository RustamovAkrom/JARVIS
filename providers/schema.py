"""
Tool declarations, translated — and trimmed to what a small model can hold.

WHY THIS IS ITS OWN FILE, AND WHY IT WAS WRITTEN FIRST
    Everything this assistant does that is not talking happens through a
    function call. Swap the model behind the conversation and the audio path is
    the obvious worry, but the audio path is only latency; the thing that
    silently stops WORKING is tool calling. So this is the piece the rest of the
    folder rests on, and it is the one piece that depends on nothing — no
    network, no key, no model, no audio. It can be run against this app's real
    declarations and checked by eye before a single provider exists.

THE THREE DIALECTS
    Gemini      {"name", "description", "parameters": {"type": "OBJECT", ...}}
    OpenAI      {"type": "function",
                 "function": {"name", "description",
                              "parameters": {"type": "object", ...}}}
    Anthropic   {"name", "description", "input_schema": {"type": "object", ...}}

    The differences are shallow and there are exactly three of them: where the
    schema hangs, what the wrapper looks like, and the case of the type names.
    Gemini's own protobuf enum is upper case ("OBJECT", "STRING", "ARRAY");
    everyone else is plain JSON Schema and wants lower case. Sending "OBJECT" to
    an OpenAI-compatible endpoint is not a soft failure — llama.cpp and vLLM
    reject the request, and Ollama accepts it and then ignores the schema, which
    is worse because it looks like it worked.

WHAT DOES NOT SURVIVE, AND WHY THAT IS FINE
    `behavior: NON_BLOCKING` and `scheduling` (see core/action_loader.py) exist
    only in the Live API. No other vendor has the concept. They are dropped
    here rather than faked, and the engine reports `caps["non_blocking"]=False`
    so nothing downstream expects otherwise. The tools still run — they just
    hold the turn. A slower assistant, not a broken one.

THE PART THAT MATTERS MORE THAN THE TRANSLATION
    Measured here, with no plugins enabled: 8 inline tools plus 12 bundled
    actions is 20 declarations and 18,039 characters. Enabling plugins only adds
    to it.

    Gemini carries that. A local 8B model does not: given forty tools it picks
    the wrong one, or emits JSON that will not parse, and it spends a large part
    of a small context window on declarations before the user has said anything.

    So an engine may declare a budget. An engine with no budget — Gemini — gets
    every tool, in the original order, unchanged. That is deliberate: today's
    behaviour has to stay bit-for-bit what it is, or this file has made the app
    worse for the people who never asked for any of this.

WHY THERE ARE TWO BUDGETS AND NOT ONE
    A count budget alone was measured and it does not do the job. Cutting 20
    tools to 8 — sixty percent fewer — took the payload from 18,039 characters
    to 9,050. Only half, because the tools worth keeping are the fat ones:
    `file_processor` alone is 3,024 characters, `browser_control` 2,050.

    A model's problem is really two problems — how many things it must choose
    between, and how much of its context the choosing costs — so `budget()`
    takes a limit for each and applies whichever bites first.
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence

# Gemini-only declaration keys. Dropped for every other vendor — see the module
# docstring; faking them would be worse than losing them.
_LIVE_ONLY = ("behavior", "scheduling")

# The tools that make this assistant itself rather than a chat box, in the order
# they should survive a tight budget.
#
# Chosen by what breaks without them, not by taste: opening things, changing the
# machine, touching files, the web, the eyes, and the four that make it feel like
# it has a memory and a way back. Everything else is a bonus a bigger model can
# afford.
#
# A TOOL dict may override its own place by declaring "tier": "core" | "extra".
# This list is the fallback so that nothing has to be edited for the budget to
# behave sensibly on day one.
#
# Names verified against the live registry rather than written from memory —
# the first draft of this list said "set_reminder" and the tool is called
# "reminder", which would have quietly demoted it to an extra.
CORE_TOOLS: tuple[str, ...] = (
    "open_app",
    "computer_settings",
    "computer_control",
    "file_controller",
    "browser_control",
    "web_search",
    "screen_process",
    "save_memory",
    "recall_memory",
    "undo",
    "reminder",
    "video_player",
    "close_camera",
)


# ─────────────────────────────────────────────────────────────────────────────
#  Schema conversion
# ─────────────────────────────────────────────────────────────────────────────
def _json_schema(node: Any) -> Any:
    """Gemini's schema -> plain JSON Schema.

    Recursive because `properties`, `items` and the arrays inside them nest, and
    a type left upper case three levels down fails exactly as loudly as one at
    the top. Anything that is not a dict or list is returned untouched, so
    enums, descriptions, defaults and numbers pass through as they are.
    """
    if isinstance(node, list):
        return [_json_schema(v) for v in node]
    if not isinstance(node, dict):
        return node

    out: dict[str, Any] = {}
    for k, v in node.items():
        if k == "type" and isinstance(v, str):
            out[k] = v.lower()
        elif k in ("properties", "items", "any_of", "anyOf", "$defs"):
            out[k] = _json_schema(v)
        elif isinstance(v, (dict, list)):
            out[k] = _json_schema(v)
        else:
            out[k] = v

    # An OBJECT with no properties is legal in Gemini and several
    # OpenAI-compatible servers reject it, so give it the empty map it wants.
    if out.get("type") == "object" and "properties" not in out:
        out["properties"] = {}
    return out


def _parts(decl: Any) -> tuple[str, str, dict]:
    """(name, description, parameters) out of a declaration.

    Accepts a plain dict — which is what actions/*.py and plugins/*.py produce —
    and also an SDK FunctionDeclaration object, because main.py assembles both
    into the same list and this should not care which arrived.
    """
    if isinstance(decl, dict):
        name = decl.get("name", "")
        desc = decl.get("description", "")
        params = decl.get("parameters") or {"type": "OBJECT", "properties": {}}
    else:
        name = getattr(decl, "name", "") or ""
        desc = getattr(decl, "description", "") or ""
        params = getattr(decl, "parameters", None) or {"type": "OBJECT",
                                                       "properties": {}}
        if hasattr(params, "to_json_dict"):
            params = params.to_json_dict()
    if not isinstance(params, dict):
        params = {"type": "OBJECT", "properties": {}}
    return name, desc, params


def to_openai(decls: Iterable[Any]) -> list[dict]:
    """The shape OpenAI, Groq, DeepSeek, Mistral, xAI, OpenRouter, Together,
    Ollama, LM Studio, llama.cpp and vLLM all accept."""
    out = []
    for d in decls:
        name, desc, params = _parts(d)
        if not name:
            continue
        out.append({
            "type": "function",
            "function": {
                "name": name,
                "description": desc,
                "parameters": _json_schema(params),
            },
        })
    return out


def to_anthropic(decls: Iterable[Any]) -> list[dict]:
    """Anthropic's shape: no wrapper, and the schema is called input_schema."""
    out = []
    for d in decls:
        name, desc, params = _parts(d)
        if not name:
            continue
        out.append({
            "name": name,
            "description": desc,
            "input_schema": _json_schema(params),
        })
    return out


def to_gemini(decls: Iterable[Any]) -> list[Any]:
    """Identity. It exists so a caller can pick a converter by capability name
    without special-casing the one vendor that needs no conversion — and so that
    `tool_format: "gemini"` is a real value rather than a None to test for."""
    return list(decls)


def to_prompt(decls: Iterable[Any]) -> str:
    """For models with no native tool calling at all.

    The declarations become part of the system prompt and the model is asked to
    answer with one JSON object. This is genuinely less reliable than a native
    tool API — it is here because it is the difference between a small local
    model being usable and being a toy, and an engine that uses it says so
    through `caps["tool_format"] == "prompt"` rather than pretending otherwise.
    """
    lines = [
        "You can call tools. To call one, reply with ONE JSON object and "
        "nothing else:",
        '  {"tool": "<name>", "args": {<arguments>}}',
        "To answer the user instead, reply with ordinary text and no JSON.",
        "",
        "Available tools:",
    ]
    for d in decls:
        name, desc, params = _parts(d)
        if not name:
            continue
        props = (params.get("properties") or {})
        required = set(params.get("required") or ())
        args = ", ".join(
            f"{k}{'' if k in required else '?'}: "
            f"{str((v or {}).get('type', 'string')).lower()}"
            for k, v in props.items()
        ) or "no arguments"
        one_line = " ".join(str(desc).split())
        if len(one_line) > 160:
            one_line = one_line[:157].rstrip() + "..."
        lines.append(f"- {name}({args}) — {one_line}")
    return "\n".join(lines)


CONVERTERS = {
    "gemini": to_gemini,
    "openai": to_openai,
    "anthropic": to_anthropic,
    "prompt": to_prompt,
}


def convert(decls: Iterable[Any], fmt: str | None):
    """Declarations in whatever dialect `fmt` names. `None` means the engine
    takes no tools, and gets none — not an empty list that looks like a bug."""
    if not fmt:
        return None
    conv = CONVERTERS.get(fmt)
    if conv is None:
        raise ValueError(f"unknown tool_format {fmt!r}; "
                         f"expected one of {tuple(CONVERTERS)}")
    return conv(decls)


# ─────────────────────────────────────────────────────────────────────────────
#  Budget
# ─────────────────────────────────────────────────────────────────────────────
def _tier_of(decl: Any, core_names: Sequence[str]) -> int:
    """0 for core, 1 for extra. A declaration's own "tier" key wins; the
    fallback list decides for everything that has not declared one, so no
    existing file has to be edited for this to behave."""
    if isinstance(decl, dict):
        declared = str(decl.get("tier", "")).strip().lower()
        name = decl.get("name", "")
    else:
        declared = str(getattr(decl, "tier", "") or "").strip().lower()
        name = getattr(decl, "name", "") or ""
    if declared == "core":
        return 0
    if declared == "extra":
        return 1
    return 0 if name in core_names else 1


def budget(decls: Sequence[Any], max_tools: int | None = None,
           max_chars: int | None = None,
           core_names: Sequence[str] = CORE_TOOLS) -> list[Any]:
    """The declarations an engine should actually be given.

    Both limits `None` returns the input unchanged — same objects, same order.
    That is the Gemini path, and it has to stay identical to what ships today.

    Otherwise core tools are kept first, in the order of `core_names` so the
    most load-bearing survive a very tight budget, and extras fill whatever is
    left in their original order. Whichever limit bites first stops the filling.

    The result is returned in the ORIGINAL order, not in ranked order: ranking
    decides what survives, and a model reads a list that has been reshuffled
    every time a budget changes as a different list.
    """
    # NO budget at all is the Gemini path, and it must return the very objects
    # it was given — same order, same text, nothing rebuilt. That is the
    # property the previous release verified byte for byte.
    if not max_tools and not max_chars:
        return list(decls)

    # A budget too generous to cut anything still means a model that ASKED for
    # a budget, so the short descriptions still apply. Returning early here
    # without compacting was handing a local engine the full text whenever its
    # limit happened to exceed the tool count — the exact model that cannot
    # afford it, spared nothing because it was not being cut.
    if max_tools and max_tools >= len(decls) and not max_chars:
        return [compact(d) for d in decls]

    order = {n: i for i, n in enumerate(core_names)}

    def sort_key(item):
        i, d = item
        name = (d.get("name", "") if isinstance(d, dict)
                else (getattr(d, "name", "") or ""))
        return (_tier_of(d, core_names), order.get(name, 10_000), i)

    # Compact BEFORE fitting, so a brief that halves a declaration buys room
    # for another tool rather than just making the same set smaller.
    compacted = [compact(d) for d in decls]

    kept: list[tuple[int, Any]] = []
    used = 0
    for i, _d in sorted(enumerate(decls), key=sort_key):
        if max_tools and len(kept) >= max_tools:
            break
        candidate = compacted[i]
        cost = payload_size([candidate])
        if max_chars and kept and used + cost > max_chars:
            # Do not stop at the first tool that will not fit — a single fat
            # declaration in the middle would otherwise throw away every smaller
            # one behind it, and the small ones are cheap to keep.
            continue
        kept.append((i, candidate))
        used += cost

    return [d for _, d in sorted(kept, key=lambda t: t[0])]


# The canonical definition lives in core/action_loader.py — see the note there
# on why main.py must be able to strip these without importing this package.
from core.action_loader import META_KEYS as _META_ONLY, strip_meta   # noqa: E402


def compact(decl: Any) -> Any:
    """A declaration with its `brief` swapped in for its `description`.

    WHY BRIEFS EXIST RATHER THAN SHORTER DESCRIPTIONS
        The fat declarations in this project are fat on purpose. whatsapp_guard
        spends three thousand characters spelling out that "reddet ve müsait
        olmadığımı söyle" is action='decline_message' with the sentence in
        'message', in two languages, with counter-examples — and that is what
        makes the model get it right. Cutting it to save tokens would be paying
        for a smaller prompt with a worse assistant.

        But the model that needs those three thousand characters is not the
        model that cannot afford them. Gemini reads the long version and uses
        it; an 8B model served by Ollama is drowning in it, and the budget it
        gets exists precisely because 45 tools and 55,000 characters is more
        than it can hold.

        So a declaration may carry BOTH: `description` for a model with room,
        and `brief` for one without. Nothing is deleted; the same file answers
        two different questions.

    WHEN IT APPLIES
        Only through `budget()`, and `budget()` only does anything when an
        engine declares a limit. Gemini declares none, so it goes on receiving
        the full text, byte for byte — the property the previous release
        verified side by side and this must not quietly break.

    A declaration with no `brief` comes back untouched, so this costs nothing
    to adopt and nothing to ignore.
    """
    if not isinstance(decl, dict):
        # SDK FunctionDeclaration objects have no place to put a brief, and
        # rebuilding one here would mean guessing at a constructor. They are
        # already the small, hand-written built-ins.
        return decl
    brief = decl.get("brief")
    if not brief or not str(brief).strip():
        return decl
    slim = {k: v for k, v in decl.items() if k != "brief"}
    slim["description"] = str(brief).strip()
    return slim


def payload_size(decls: Iterable[Any]) -> int:
    """Characters a set of declarations costs on the wire. Used by the settings
    panel so a budget is a number the user can see rather than a promise."""
    import json
    total = 0
    for d in decls:
        name, desc, params = _parts(d)
        try:
            total += len(name) + len(desc) + len(json.dumps(params))
        except Exception:
            total += len(name) + len(desc)
    return total


# ─────────────────────────────────────────────────────────────────────────────
#  Run this file to check the translation against the app's real declarations.
#      python -m providers.schema
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    decls: list[Any] = []

    # The inline ones, read out of main.py's source rather than by importing it:
    # importing main.py starts an assistant.
    import ast
    src = Path(__file__).resolve().parent.parent / "main.py"
    tree = ast.parse(src.read_text(encoding="utf-8", errors="ignore"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == "TOOL_DECLARATIONS" for t in node.targets):
            decls.extend(ast.literal_eval(node.value))
            break

    # The bundled actions, through the real loader — the same call main.py makes.
    root = Path(__file__).resolve().parent.parent
    try:
        from core.action_loader import discover_actions
        reg = discover_actions(root / "actions", logger=lambda m: None)
        decls.extend(reg.get_tool_declarations())
    except Exception as e:
        print(f"(actions not loaded: {type(e).__name__}: {e})")

    print(f"\n{len(decls)} declarations, {payload_size(decls):,} characters\n")

    openai = to_openai(decls)
    anthropic = to_anthropic(decls)

    # The one thing that must be true: no upper-case type survives anywhere, at
    # any depth. A single one is a request llama.cpp rejects.
    blob = json.dumps(openai) + json.dumps(anthropic)
    import re
    bad = sorted(set(re.findall(r'"type":\s*"([A-Z_]+)"', blob)))
    print(f"OpenAI     {len(openai)} tools, {len(json.dumps(openai)):,} chars")
    print(f"Anthropic  {len(anthropic)} tools, {len(json.dumps(anthropic)):,} chars")
    print(f"upper-case types left behind: {bad or 'none'}")

    names = [d.get('name') for d in decls if isinstance(d, dict)]
    print(f"\nnames preserved: {names == [t['function']['name'] for t in openai]}")

    for cap in (None, 12, 8):
        kept = budget(decls, cap)
        core = [d.get('name') for d in kept if _tier_of(d, CORE_TOOLS) == 0]
        print(f"\nmax_tools={str(cap):<5} -> {len(kept):>2} tools, "
              f"{payload_size(kept):>6,} chars   core kept: {len(core)}")
        if cap:
            print(f"   {', '.join(d.get('name', '?') for d in kept)}")

    print("\n--- one converted declaration, in full ---")
    sample = next((t for t in openai
                   if t["function"]["name"] == "screen_process"), openai[0])
    print(json.dumps(sample, indent=2)[:1200])

    print("\n--- prompt-mode fallback, first 6 lines ---")
    print("\n".join(to_prompt(budget(decls, 12)).splitlines()[:10]))
