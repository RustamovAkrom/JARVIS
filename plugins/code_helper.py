import subprocess
import sys
import json
import re
import time
from pathlib import Path
from core.paths import APP_ID


try:
    from core.paths import data_dir as _data_dir
except ImportError:          # a core older than core/paths.py
    _data_dir = lambda: Path(__file__).resolve().parent.parent


def get_base_dir() -> Path:
    return _data_dir()

BASE_DIR           = get_base_dir()
API_CONFIG_PATH    = BASE_DIR / "config" / "api_keys.json"
from core.user_dirs import folder as _user_folder
DESKTOP            = _user_folder("desktop")   # the real one: OneDrive, a translated name (core/user_dirs)
MAX_BUILD_ATTEMPTS = 3
# Model choice lives in core/gemini.py, and so does the timeout and the
# fallback ladder. Writing a model name here is what left this file hanging
# forever whenever that one alias was unwell.
from core import gemini


# ── Plugin settings (⚙ SETUP → PLUGIN SETTINGS) ─────────────────────────────
_NS = "code_helper"

PLUGIN_SETTINGS = {
    "namespace": _NS,
    "title": "⌨️  CODE HELPER",
    "fields": [
        {"key": "save_dir", "label": "Where to save code", "type": "text",
         "placeholder": str(DESKTOP)},
        {"key": "build_attempts",
         "label": "Build: how many times to try fixing its own errors",
         "type": "choice", "options": ["1", "2", "3", "5", "8"], "default": "3"},
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


def _save_dir() -> Path:
    """Where a file with no path of its own lands. Falls back to the Desktop if
    the configured folder cannot be created — a build must not fail because of
    a typo in a settings box."""
    raw = str(_setting("save_dir", "")).strip()
    if not raw:
        return DESKTOP
    try:
        p = Path(raw).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        return p
    except Exception as e:
        print(f"[Code] ⚠️ save_dir {raw!r} unusable ({e}) — using {DESKTOP}")
        return DESKTOP


def _max_build_attempts() -> int:
    try:
        return max(1, int(str(_setting("build_attempts", MAX_BUILD_ATTEMPTS)).strip()))
    except Exception:
        return MAX_BUILD_ATTEMPTS


def _get_api_key() -> str:
    """The Gemini credential: the user's key, or a relay licence (core/gemini.py)."""
    from core import gemini
    return gemini.api_key()


def _get_gemini(tier: str = gemini.SMART):
    """Writing and fixing code is the reasoning tier; a 60s deadline because a
    whole file can come back."""
    class _W:
        def generate_content(self, contents):
            resp = gemini.call(contents, tier=tier, timeout_ms=60000)
            if resp is None:
                raise RuntimeError("every Gemini model on the ladder failed")
            return resp

    return _W()


def _clean_code(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    return text.strip()


def _resolve_save_path(output_path: str, language: str) -> Path:
    ext_map = {
        "python": ".py", "py": ".py",
        "javascript": ".js", "js": ".js",
        "typescript": ".ts", "ts": ".ts",
        "html": ".html", "css": ".css",
        "java": ".java", "cpp": ".cpp", "c": ".c",
        "bash": ".sh", "shell": ".sh", "powershell": ".ps1",
        "sql": ".sql", "json": ".json", "rust": ".rs", "go": ".go",
    }
    base = _save_dir()
    if output_path:
        p = Path(output_path)
        return p if p.is_absolute() else base / p
    ext = ext_map.get((language or "python").lower(), ".py")
    return base / f"{APP_ID.lower()}_code{ext}"


def _read_file(file_path: str) -> tuple[str, str]:
    if not file_path:
        return "", "No file path provided."
    p = Path(file_path)
    if not p.exists():
        return "", f"File not found: {file_path}"
    try:
        return p.read_text(encoding="utf-8"), ""
    except Exception as e:
        return "", f"Could not read file: {e}"


def _save_file(path: Path, content: str) -> str:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"Saved to: {path}"
    except Exception as e:
        return f"Could not save: {e}"


def _preview(code: str, lines: int = 10) -> str:
    all_lines = code.splitlines()
    preview   = "\n".join(all_lines[:lines])
    suffix    = f"\n... ({len(all_lines) - lines} more lines)" if len(all_lines) > lines else ""
    return preview + suffix


def _has_error(output: str) -> bool:
    error_signals = ["error", "exception", "traceback", "syntaxerror",
                     "nameerror", "typeerror", "stderr", "failed", "crash"]
    return any(s in output.lower() for s in error_signals)


def _take_screenshot() -> bytes | None:
    """The screen as PNG bytes, in memory: a picture of the screen is never
    written to disk (the privacy notice promises it is not kept)."""
    try:
        import io
        import pyautogui
        buf = io.BytesIO()
        pyautogui.screenshot().save(buf, format="PNG")
        print("[Code] 📸 Screenshot taken (in memory)")
        return buf.getvalue()
    except Exception as e:
        print(f"[Code] ⚠️ Screenshot failed: {e}")
        return None


_VALID_INTENTS = {"write", "edit", "explain", "run", "build", "screen_debug", "optimize"}


def _detect_intent(description: str, file_path: str, code: str) -> str:
    """
    Language-independent intent detection — NO fixed keyword list.
    Whatever language the user speaks, the description is classified by
    Gemini. If the API is unreachable, it falls back to language-agnostic
    structural hints (does the file exist on disk, was code provided).
    """
    desc        = (description or "").strip()
    file_exists = bool(file_path) and Path(file_path).exists()

    if desc:
        try:
            ctx = []
            if file_path:
                ctx.append(f"a file path is provided (exists on disk: {file_exists})")
            if code:
                ctx.append("an inline code snippet is provided")
            prompt = (
                "Classify a coding assistant request into exactly ONE intent word.\n"
                "The request may be written in ANY language.\n\n"
                f"Request: {desc}\n"
                + (f"Context: {'; '.join(ctx)}\n" if ctx else "")
                + "\nIntents:\n"
                "  write        = create new code from scratch\n"
                "  edit         = modify an existing file\n"
                "  explain      = describe what given code/file does\n"
                "  run          = execute an existing file\n"
                "  build        = write code, run it, and iterate until it works\n"
                "  screen_debug = analyze an error currently visible on the user's screen\n"
                "  optimize     = refactor / clean up / speed up existing code\n\n"
                "Reply with ONLY the intent word, nothing else."
            )
            ans = _get_gemini().generate_content(prompt).text.strip().lower()
            ans = ans.strip("`'\". \n")
            if ans in _VALID_INTENTS:
                return ans
        except Exception as e:
            print(f"[Code] Intent classification failed ({e}) — structural fallback")

    # Structural fallback — not tied to any language
    if file_exists:
        return "edit" if desc else "explain"
    if code:
        return "explain"
    return "write"

def _write(description: str, language: str, output_path: str, player=None) -> tuple[str, Path]:
    lang  = language or "python"
    model = _get_gemini()

    prompt = f"""You are an expert {lang} developer.
Write clean, working, well-commented {lang} code for the description below.

Rules:
- Output ONLY the code. No explanation, no markdown, no backticks.
- Add helpful inline comments.
- Handle errors and edge cases properly.
- Use modern best practices.

Description: {description}

Code:"""

    response = model.generate_content(prompt)
    code     = _clean_code(response.text)
    path     = _resolve_save_path(output_path, lang)
    _save_file(path, code)
    return code, path


def _fix_code(code: str, error_output: str, description: str) -> str:
    model  = _get_gemini()
    prompt = f"""You are an expert debugger.
The code below failed with the following error. Fix it.
Return ONLY the corrected code — no explanation, no markdown, no backticks.

Original goal: {description}

Error:
{error_output[:2000]}

Broken code:
{code}

Fixed code:"""

    response = model.generate_content(prompt)
    return _clean_code(response.text)


def _user_python():
    """The Python that runs the USER's code — never the assistant's own .exe.

    In a packaged build `sys.executable` IS the assistant: "python app.py"
    through it would start a second assistant, and "-m pip" would too.
    """
    try:
        from core.paths import user_python
    except ImportError:          # a core older than core.paths.user_python
        return sys.executable
    return user_python()


def _run_file(path: Path, args: list, timeout: int) -> str:
    python = _user_python()
    if path.suffix.lower() == ".py" and not python:
        return "Python is not installed on this computer (python.org), so this cannot run."
    interpreters = {
        ".py":  [python],
        ".js":  ["node"],
        ".ts":  ["ts-node"],
        ".sh":  ["bash"],
        ".ps1": ["powershell", "-File"],
        ".rb":  ["ruby"],
        ".php": ["php"],
    }
    interp = interpreters.get(path.suffix.lower())
    if not interp:
        return f"No interpreter for {path.suffix}."

    try:
        result = subprocess.run(
            interp + [str(path)] + (args or []),
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout, cwd=str(path.parent)
        )
        output = result.stdout.strip()
        error  = result.stderr.strip()
        parts  = []
        if output: parts.append(f"Output:\n{output}")
        if error:  parts.append(f"Stderr:\n{error}")
        return "\n\n".join(parts) if parts else "Executed with no output."

    except subprocess.TimeoutExpired:
        return f"Timed out after {timeout}s."
    except FileNotFoundError:
        return f"Interpreter not found: {interp[0]}."
    except Exception as e:
        return f"Execution error: {e}"


def _build(description, language, output_path, args, timeout, speak=None, player=None) -> str:
    if not description:
        return "Please describe what you want me to build."

    if player:
        player.write_log("[Code] Build started...")

    lang = language or "python"

    try:
        code, path = _write(description, lang, output_path, player)
        print(f"[Code] ✅ Written: {path}")
    except Exception as e:
        msg = f"Could not write initial code: {e}"
        if speak: speak(msg)
        return msg

    last_output = ""
    budget = _max_build_attempts()
    for attempt in range(1, budget + 1):
        print(f"[Code] 🔄 Attempt {attempt}/{budget}")
        if player:
            player.write_log(f"[Code] Attempt {attempt}...")

        last_output = _run_file(path, args, timeout)

        if not _has_error(last_output):
            msg = (
                f"Build complete. "
                f"The code is working after {attempt} attempt{'s' if attempt > 1 else ''}. "
                f"Saved to {path}."
            )
            if speak: speak(msg)
            return f"{msg}\n\nOutput:\n{last_output}"

        print(f"[Code] ⚠️ Error on attempt {attempt}, fixing...")
        if player:
            player.write_log(f"[Code] Fixing (attempt {attempt})...")

        try:
            code = _fix_code(code, last_output, description)
            _save_file(path, code)
        except Exception as e:
            msg = f"Could not fix code on attempt {attempt}: {e}"
            if speak: speak(msg)
            return msg

    msg = (
        f"I was unable to build a working version after {budget} attempts. "
        f"The last error was: {last_output[:200]}"
    )
    if speak: speak(msg)
    return f"{msg}\n\nLast code saved to: {path}"

def _write_action(description, language, output_path, player) -> str:
    if not description:
        return "Please describe what you want me to write."
    if player:
        player.write_log("[Code] Writing code...")
    try:
        code, path = _write(description, language, output_path, player)
        print(f"[Code] ✅ Written: {path}")
        return f"Code written. Saved to: {path}\n\nPreview:\n{_preview(code)}"
    except Exception as e:
        return f"Could not generate code: {e}"


def _edit_action(file_path, instruction, player) -> str:
    if not file_path:
        return "Please provide a file path to edit."
    if not instruction:
        return "Please describe what change to make."

    content, err = _read_file(file_path)
    if err:
        return err

    if player:
        player.write_log("[Code] Editing file...")

    model  = _get_gemini()
    prompt = f"""You are an expert code editor.
Apply the following change to the code below.
Return ONLY the complete updated code — no explanation, no markdown, no backticks.

Change: {instruction}

Original code:
{content}

Updated code:"""

    try:
        response = model.generate_content(prompt)
        edited   = _clean_code(response.text)
    except Exception as e:
        return f"Could not edit code: {e}"

    status = _save_file(Path(file_path), edited)
    print(f"[Code] ✅ Edited: {file_path}")
    return f"File edited. {status}\n\nPreview:\n{_preview(edited)}"


def _explain_action(file_path, code, player) -> str:
    if file_path and not code:
        code, err = _read_file(file_path)
        if err:
            return err
    if not code:
        return "Please provide code or a file path to explain."

    if player:
        player.write_log("[Code] Analyzing code...")

    model  = _get_gemini()
    prompt = f"""Explain what this code does in simple, clear language.
Focus on: what it does, how it works, and any important details.
Be concise — 3 to 6 sentences maximum.

Code:
{code[:4000]}

Explanation:"""

    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        return f"Could not explain code: {e}"


def _run_action(file_path, args, timeout, player) -> str:
    if not file_path:
        return "Please provide a file path to run."
    p = Path(file_path)
    if not p.exists():
        return f"File not found: {file_path}"
    if player:
        player.write_log(f"[Code] Running {p.name}...")
    return _run_file(p, args, timeout)


def _optimize_action(file_path, code, language, output_path, player) -> str:

    if file_path and not code:
        code, err = _read_file(file_path)
        if err:
            return err
    if not code:
        return "Please provide code or a file path to optimize."

    if player:
        player.write_log("[Code] Optimizing code...")

    lang  = language or "python"
    model = _get_gemini()

    prompt = f"""You are an expert {lang} developer and code reviewer.
Optimize the following code for:
1. Performance — eliminate unnecessary operations, use efficient data structures
2. Readability — clear variable names, proper formatting, logical structure
3. Best practices — modern {lang} patterns, error handling, type hints if applicable
4. Remove dead code, redundant comments, and unnecessary complexity

Return ONLY the optimized code — no explanation, no markdown, no backticks.

Original code:
{code[:6000]}

Optimized code:"""

    try:
        response  = model.generate_content(prompt)
        optimized = _clean_code(response.text)
    except Exception as e:
        return f"Could not optimize code: {e}"

    # Kaydet
    if file_path:
        save_path = Path(file_path)
    else:
        save_path = _resolve_save_path(output_path, lang)

    status = _save_file(save_path, optimized)
    print(f"[Code] ✅ Optimized: {save_path}")

    original_lines  = len(code.splitlines())
    optimized_lines = len(optimized.splitlines())
    diff = original_lines - optimized_lines

    return (
        f"Code optimized. {status}\n"
        f"Lines: {original_lines} → {optimized_lines} "
        f"({'−' if diff > 0 else '+'}{abs(diff)} lines)\n\n"
        f"Preview:\n{_preview(optimized)}"
    )


def _screen_debug_action(description, file_path, player, speak=None) -> str:

    if player:
        player.write_log("[Code] Taking screenshot for analysis...")

    print("[Code] 📸 Capturing screen for debug...")


    image_bytes = _take_screenshot()
    if not image_bytes:
        return "Could not take screenshot. Please make sure PyAutoGUI is installed."


    file_content = ""
    if file_path:
        file_content, err = _read_file(file_path)
        if err:
            print(f"[Code] ⚠️ Could not read file: {err}")

    try:
        from google.genai import types


        user_question = description or "What error or problem do you see on the screen? How can it be fixed?"

        context = ""
        if file_content:
            context = f"\n\nAdditionally, here is the related file content:\n```\n{file_content[:4000]}\n```"

        analysis_prompt = f"""You are an expert programmer and debugger analyzing a screenshot.

User's question: {user_question}{context}

Please:
1. Identify any errors, exceptions, or problems visible on the screen
2. Explain what is causing the problem in simple terms
3. Provide a concrete fix or solution
4. If there's code visible, show the corrected version

Be specific and actionable. If you see an error message, quote it exactly."""

        contents = [
            types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
            analysis_prompt,
        ]

        response = gemini.call(contents, tier=gemini.SMART, timeout_ms=45_000)
        if response is None:
            return "I couldn't reach Gemini to analyse that screenshot."

        analysis = (response.text or "").strip()
        print(f"[Code] ✅ Screen analysis complete")

        if file_path and file_content:

            code_match = re.search(r"```[a-zA-Z]*\n(.*?)```", analysis, re.DOTALL)
            if code_match:
                fixed_code = code_match.group(1).strip()
                save_path  = Path(file_path)
                _save_file(save_path, fixed_code)
                analysis += f"\n\n✅ Fixed code has been saved to: {file_path}"
                print(f"[Code] ✅ Fixed code saved: {file_path}")

        return analysis

    except Exception as e:
        return f"Screen analysis failed: {e}"


def code_helper(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
    speak=None
) -> str:
    """
    Called from main.py.

    parameters:
        action      : write | edit | explain | run | build | screen_debug | optimize | auto
        description : What the code should do / what change to make / what problem to analyze
        language    : Programming language (default: python)
        output_path : Where to save — user specifies full path or filename
        file_path   : Path to existing file (edit / explain / run / build / optimize)
        code        : Raw code string (explain/optimize without a file)
        args        : CLI argument list for run/build
        timeout     : Execution timeout in seconds (default: 30)
    """
    p           = parameters or {}
    action      = p.get("action", "auto").lower().strip()
    description = p.get("description", "").strip()
    language    = p.get("language", "python").strip()
    output_path = p.get("output_path", "").strip()
    file_path   = p.get("file_path", "").strip()
    code        = p.get("code", "").strip()
    args        = p.get("args", [])
    timeout     = int(p.get("timeout", 30))

    if action == "auto":
        action = _detect_intent(description, file_path, code)
        print(f"[Code] 🤖 Auto-detected: {action}")

    if action == "write":
        return _write_action(description, language, output_path, player)

    elif action == "edit":
        return _edit_action(
            file_path,
            description or p.get("instruction", ""),
            player
        )

    elif action == "explain":
        return _explain_action(file_path, code, player)

    elif action == "run":
        return _run_action(file_path, args, timeout, player)

    elif action == "build":
        return _build(description, language, output_path, args, timeout, speak, player)

    elif action == "optimize":
        return _optimize_action(file_path, code, language, output_path, player)

    elif action == "screen_debug":
        return _screen_debug_action(description, file_path, player, speak)

    else:
        return f"Unknown action: '{action}'. Use write, edit, explain, run, build, optimize, or screen_debug."


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


# Gated by a person, never by the model (core/confirm): the button on screen,
# or on the phone during a phone turn. A step the phone already confirmed runs
# straight through (core/phonegate.confirmed_run), so nothing is asked twice.
def run(parameters: dict, player=None, session_memory=None) -> str:
    p = dict(parameters or {})
    action = str(p.get("action") or "auto").lower().strip()
    if action == "auto":
        # Decided here, once, so the gate knows whether this call RUNS code.
        action = _detect_intent(str(p.get("description") or "").strip(),
                                str(p.get("file_path") or "").strip(),
                                str(p.get("code") or "").strip())
        p["action"] = action
    if action in ("run", "build"):
        from core import confirm
        from core.i18n import t
        where = str(p.get("file_path") or p.get("output_path") or "").strip()
        title = (t("confirm.run_code", name=Path(where).name or where) if action == "run"
                 else t("confirm.build_code"))
        detail = str(p.get("description") or "").strip() or where
        return confirm.request(
            key="code_helper", title=title, detail=detail[:600],
            run=lambda: code_helper(p, player=player, speak=_speaker(player)))
    return code_helper(p, player=player, speak=_speaker(player))


# ── Plugin declaration (auto-discovered by core/plugin_loader.py) ───────────
PLUGIN = {
    # For the plugin manager (core/plugin_loader): never sent to a model.
    "level": "advanced",
    "label": "Code helper",
    "summary": "Writes, explains and runs code for you.",
    "setup": [
        "Say: \"Write a Python script that renames my photos by date\" — it writes it, runs it and fixes its own errors.",
        "In these settings choose where the code is saved.",
    ],
    "name": "code_helper",
    "description": "Writes, edits, explains, runs, or builds code files.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "write | edit | explain | run | build | auto (default: auto)"
            },
            "description": {
                "type": "STRING",
                "description": "What the code should do or what change to make"
            },
            "language": {
                "type": "STRING",
                "description": "Programming language (default: python)"
            },
            "output_path": {
                "type": "STRING",
                "description": "Where to save the file"
            },
            "file_path": {
                "type": "STRING",
                "description": "Path to existing file for edit/explain/run/build"
            },
            "code": {
                "type": "STRING",
                "description": "Raw code string for explain"
            },
            "args": {
                "type": "STRING",
                "description": "CLI arguments for run/build"
            },
            "timeout": {
                "type": "INTEGER",
                "description": "Execution timeout in seconds (default: 30)"
            }
        },
        "required": [
            "action"
        ]
    },
}
