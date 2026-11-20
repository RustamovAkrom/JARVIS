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


BASE_DIR         = get_base_dir()
API_CONFIG_PATH  = BASE_DIR / "config" / "api_keys.json"
from core.user_dirs import folder as _user_folder
PROJECTS_DIR     = _user_folder("desktop") / f"{APP_ID}Projects"   # core/user_dirs: the real desktop
MAX_FIX_ATTEMPTS = 5
# Model choice, timeout and fallback ladder all live in core/gemini.py.
from core import gemini

MODEL_PLANNER    = gemini.SMART
MODEL_WRITER     = gemini.SMART

# ── Plugin settings (⚙ SETUP → PLUGIN SETTINGS) ─────────────────────────────
_NS = "dev_agent"

PLUGIN_SETTINGS = {
    "namespace": _NS,
    "title": "🛠️  DEV AGENT",
    "fields": [
        {"key": "projects_dir", "label": "Where to build projects", "type": "text",
         "placeholder": str(PROJECTS_DIR)},
        {"key": "fix_attempts",
         "label": "How many times to try fixing its own errors",
         "type": "choice", "options": ["1", "3", "5", "8"], "default": "5"},
        {"key": "open_editor", "label": "Open the project in an editor when done",
         "type": "toggle", "default": True},
        {"key": "editor_command", "label": "Editor command", "type": "text",
         "placeholder": "code   —   empty falls back to VS Code (subl, idea, zed …)"},
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


def _projects_dir() -> Path:
    """Falls back to the default folder if the configured one cannot be made —
    a whole project must not be lost to a typo in a settings box."""
    raw = str(_setting("projects_dir", "")).strip()
    if not raw:
        return PROJECTS_DIR
    try:
        p = Path(raw).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        return p
    except Exception as e:
        print(f"[DevAgent] ⚠️ projects_dir {raw!r} unusable ({e}) — using {PROJECTS_DIR}")
        return PROJECTS_DIR


def _max_fix_attempts() -> int:
    try:
        return max(1, int(str(_setting("fix_attempts", MAX_FIX_ATTEMPTS)).strip()))
    except Exception:
        return MAX_FIX_ATTEMPTS


def _get_api_key() -> str:
    """The Gemini credential: the user's key, or a relay licence (core/gemini.py)."""
    from core import gemini
    return gemini.api_key()


def _get_model(model_name: str = gemini.SMART):
    """Planning and writing whole files — the reasoning tier, and a long
    deadline because the answer is a source file rather than a sentence."""
    class _W:
        def generate_content(self, contents):
            resp = gemini.call(contents, tier=model_name, timeout_ms=60000)
            if resp is None:
                raise RuntimeError("every Gemini model on the ladder failed")
            return resp

    return _W()


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\r?\n?", "", text)
    text = re.sub(r"\r?\n?```\s*$", "", text)
    return text.strip()


def _is_rate_limit(error: Exception) -> bool:
    msg = str(error).lower()
    return "429" in msg or "quota" in msg or "resource_exhausted" in msg


def _safe_project_path(project_dir: Path, raw_path: str) -> Path:
    """Resolve a generated path without allowing it to escape the project."""
    raw = str(raw_path or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise ValueError(f"Unsafe project path: {raw_path!r}")
    parts = raw.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"Unsafe project path: {raw_path!r}")
    root = project_dir.resolve()
    target = (root / Path(*parts)).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"Project path escapes its root: {raw_path!r}")
    return target


def _parse_traceback(output: str, project_files: list[str]) -> tuple[str | None, int | None]:

    pattern = re.compile(r'File ["\']([^"\']+\.py)["\'],\s+line\s+(\d+)', re.IGNORECASE)
    matches = pattern.findall(output)

    for raw_path, line_str in reversed(matches):
        raw_name = Path(raw_path).name
        for pf in project_files:
            if Path(pf).name == raw_name or pf == raw_path or raw_path.endswith(pf):
                return pf, int(line_str)

    return None, None


def _classify_error(output: str) -> str:

    low = output.lower()

    if any(x in low for x in ("no module named", "modulenotfounderror", "importerror")):
        return "dependency_error"

    if "syntaxerror" in low or "invalid syntax" in low:
        return "syntax_error"

    if "cannot import" in low or "importerror" in low:
        return "import_error"

    if any(x in low for x in (
        "traceback", "exception", "error:", "nameerror", "typeerror",
        "attributeerror", "valueerror", "keyerror", "indexerror",
        "zerodivisionerror", "filenotfounderror", "permissionerror",
    )):
        return "runtime_error"

    return "none"


def _has_error(output: str, run_command: str) -> bool:

    low = output.lower()

    if "timed out" in low:
        return False

    if not output.strip():
        return False

    error_type = _classify_error(output)
    return error_type != "none"

class RateLimitError(Exception):
    pass


def _plan_project(description: str, language: str) -> dict:
    model = _get_model(MODEL_PLANNER)

    prompt = f"""You are a senior software architect. Create a minimal, complete file plan for this project.

Language: {language}
Description: {description}

Return ONLY valid JSON — no markdown, no explanation:
{{
  "project_name": "snake_case_name",
  "entry_point": "main.py",
  "files": [
    {{
      "path": "main.py",
      "description": "Entry point — what it does and which modules it imports",
      "imports": ["utils.helpers", "core.engine"]
    }},
    {{
      "path": "utils/helpers.py",
      "description": "Helper utilities — what functions it exposes",
      "imports": []
    }}
  ],
  "run_command": "python main.py",
  "dependencies": ["requests"]
}}

Critical rules:
1. List files in DEPENDENCY ORDER — files with no imports come first, entry point comes last.
2. The "imports" field must list every other project module this file imports (dot-notation, e.g. "utils.helpers").
3. Keep it minimal — only files truly needed.
4. Entry point must be in the files list.
5. Use relative paths only (e.g. "utils/helpers.py", not absolute paths).
6. Standard library modules (os, sys, json, etc.) do NOT go in "dependencies".

JSON:"""

    try:
        response = model.generate_content(prompt)
        raw = _strip_fences(response.text)
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Planner returned invalid JSON: {e}\nRaw: {response.text[:300]}")
    except Exception as e:
        if _is_rate_limit(e):
            raise RateLimitError(str(e))
        raise

def _write_file(
    file_info: dict,
    project_description: str,
    all_files: list[dict],
    language: str,
    project_dir: Path,
    already_written: dict[str, str],
) -> str:
    model = _get_model(MODEL_WRITER)

    file_path = file_info["path"]
    file_desc = file_info.get("description", "")
    file_imports = file_info.get("imports", [])

    file_list = "\n".join(
        f"  [{i+1}] {f['path']}: {f.get('description', '')}"
        for i, f in enumerate(all_files)
    )

    dependency_context = ""
    for dep_dotted in file_imports:
        dep_path = dep_dotted.replace(".", "/") + ".py"
        if dep_path in already_written:
            code_snippet = already_written[dep_path][:2000]
            dependency_context += f"\n\n--- {dep_path} (you must import from this) ---\n{code_snippet}"

    lang_rules = ""
    if language.lower() == "python":
        lang_rules = """
Python-specific rules:
- Use type hints for all function signatures.
- Add docstrings for all public functions and classes.
- Use if __name__ == "__main__": guard in the entry point.
- For relative imports within the project, use: from utils.helpers import foo  (match the project structure exactly).
- Do NOT use implicit relative imports (from . import ...) unless it's a proper package with __init__.py.
- If this is a package subdirectory, create __init__.py files where needed."""
    elif language.lower() in ("javascript", "typescript", "js", "ts"):
        lang_rules = """
JS/TS-specific rules:
- Use ES modules (import/export), not CommonJS (require).
- Add JSDoc comments for all exported functions.
- Handle promise rejections with try/catch in async functions."""

    prompt = f"""You are a senior {language} developer writing production-quality code for a real project.

Project goal: {project_description}

Complete project file structure (in dependency order):
{file_list}

{f"Dependencies this file must import from other project files:{dependency_context}" if dependency_context else ""}

Your task: Write the complete, working code for: {file_path}
Purpose of this file: {file_desc}
{f"This file imports from: {', '.join(file_imports)}" if file_imports else "This file has no project-internal imports."}

{lang_rules}

General rules:
- Output ONLY raw code. Absolutely no explanation, no markdown, no triple backticks.
- Write COMPLETE, RUNNABLE code — no placeholders, no "# TODO", no "pass" stubs.
- Every import must either be from the standard library, listed dependencies, or the project files shown above.
- Match import paths EXACTLY to the file paths in the project structure (e.g. if file is "utils/helpers.py", import as "from utils.helpers import ...").
- Use proper error handling (try/except) where I/O or network calls are made.
- The code must work correctly when the project entry point is run from the project root directory.

Code for {file_path}:"""

    try:
        response = model.generate_content(prompt)
        code = _strip_fences(response.text)

        full_path = _safe_project_path(project_dir, file_path)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(code, encoding="utf-8")

        print(f"[DevAgent] ✅ Written: {file_path} ({len(code)} chars)")
        return code

    except Exception as e:
        if _is_rate_limit(e):
            raise RateLimitError(str(e))
        raise

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


def _install_dependencies(dependencies: list[str], project_dir: Path) -> str:
    if not dependencies:
        return "No external dependencies."
    python = _user_python()
    if not python:
        return "Python is not installed on this computer (python.org), so this cannot run."

    to_install = []
    for dep in dependencies:
        pkg_name = re.split(r"[>=<!]", dep)[0].strip()
        result = subprocess.run(
            [python, "-m", "pip", "show", pkg_name],
            capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        if result.returncode != 0:
            to_install.append(dep)
        else:
            print(f"[DevAgent] ✓ Already installed: {pkg_name}")

    if not to_install:
        return f"All dependencies already installed: {', '.join(dependencies)}"

    print(f"[DevAgent] 📦 Installing: {to_install}")
    try:
        result = subprocess.run(
            [python, "-m", "pip", "install"] + to_install,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=120, cwd=str(project_dir)
        )
        if result.returncode == 0:
            return f"Installed: {', '.join(to_install)}"
        return f"Install warning (non-fatal): {result.stderr[:200]}"
    except subprocess.TimeoutExpired:
        return "Dependency install timed out (non-fatal)."
    except Exception as e:
        return f"Install error (non-fatal): {e}"

def _open_vscode(project_dir: Path) -> bool:
    # VS Code is the default, not the assumption. Somebody who works in another
    # editor should not have a second one open itself on every build, and the
    # two absolute paths below only exist on Windows.
    if not bool(_setting("open_editor", True)):
        return False
    configured = str(_setting("editor_command", "")).strip()
    vscode_candidates = ([configured] if configured else []) + [
        "code",
        rf"C:\Users\{Path.home().name}\AppData\Local\Programs\Microsoft VS Code\bin\code.cmd",
        r"C:\Program Files\Microsoft VS Code\bin\code.cmd",
    ]
    for cmd in vscode_candidates:
        try:
            # Launch executables directly. Batch files require cmd.exe,
            # but shell=True with a list has platform-dependent argument handling
            # and unnecessarily routes configured editor commands through a shell.
            if Path(cmd).suffix.lower() in {".cmd", ".bat"}:
                launch = ["cmd.exe", "/c", cmd, str(project_dir)]
            else:
                launch = [cmd, str(project_dir)]
            subprocess.Popen(
                launch,
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(1.5)
            print(f"[DevAgent] 💻 VSCode opened: {project_dir}")
            return True
        except Exception:
            continue
    return False

def _run_project(run_command: str, project_dir: Path, timeout: int = 30) -> str:
    print(f"[DevAgent] 🚀 Running: {run_command}")
    try:
        parts = run_command.split()
        if parts[0].lower() == "python":
            python = _user_python()
            if not python:
                return "Python is not installed on this computer (python.org), so this cannot run."
            parts[0] = python

        result = subprocess.run(
            parts,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout,
            cwd=str(project_dir)
        )

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        combined_parts = []
        if stdout:
            combined_parts.append(f"STDOUT:\n{stdout}")
        if stderr:
            combined_parts.append(f"STDERR:\n{stderr}")

        return "\n\n".join(combined_parts) if combined_parts else "Ran with no output."

    except subprocess.TimeoutExpired:
        return f"Timed out after {timeout}s — long-running app (server/GUI) is likely working."
    except FileNotFoundError as e:
        return f"Command not found: {e}"
    except Exception as e:
        return f"Run error: {e}"

def _try_auto_install(error_output: str, project_dir: Path) -> bool:
    """If there is a ModuleNotFoundError, tries to auto-install the missing package."""
    pattern = re.compile(
        r"No module named ['\"]([a-zA-Z0-9_\-\.]+)['\"]", re.IGNORECASE
    )
    match = pattern.search(error_output)
    if not match:
        return False

    pkg = match.group(1).replace("_", "-").split(".")[0]
    print(f"[DevAgent] 🔧 Auto-installing missing package: {pkg}")
    python = _user_python()
    if not python:
        return False
    try:
        result = subprocess.run(
            [python, "-m", "pip", "install", pkg],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=60, cwd=str(project_dir)
        )
        return result.returncode == 0
    except Exception:
        return False

def _fix_files(
    error_output: str,
    project_description: str,
    all_files: list[dict],
    file_codes: dict[str, str],
    language: str,
    project_dir: Path,
    entry_point: str,
) -> dict[str, str]:

    model = _get_model(MODEL_PLANNER)

    error_file, error_line = _parse_traceback(error_output, list(file_codes.keys()))
    error_type = _classify_error(error_output)

    files_to_fix: list[str] = []

    if error_file:
        files_to_fix.append(error_file)
        if error_type == "import_error":
            for fi in all_files:
                if error_file.replace("/", ".").replace(".py", "") in fi.get("imports", []):
                    p = fi["path"]
                    if p not in files_to_fix:
                        files_to_fix.append(p)
    else:
        files_to_fix.append(entry_point)

    updated_codes: dict[str, str] = {}

    for fix_path in files_to_fix:
        current_code = file_codes.get(fix_path, "")

        other_ctx = ""
        for fp, code in file_codes.items():
            if fp != fix_path and code:
                snippet = code[:1500] + ("..." if len(code) > 1500 else "")
                other_ctx += f"\n--- {fp} ---\n{snippet}\n"

        line_hint = f"\nError appears to be near line {error_line} in this file." if (
            error_line and fix_path == error_file
        ) else ""

        prompt = f"""You are an expert {language} debugger. Fix the broken file below.

Project goal: {project_description}

All project files:
{chr(10).join(f"  - {f['path']}: {f.get('description', '')}" for f in all_files)}

Other files for context (read-only — fix only the target file):
{other_ctx[:3500]}

File to fix: {fix_path}{line_hint}
Error type: {error_type}

Error output:
{error_output[:2500]}

Current (broken) code:
{current_code}

Rules:
- Output ONLY the complete fixed code. No explanation, no markdown, no backticks.
- Fix ALL errors visible in the error output.
- Keep all existing correct logic — do not remove working features.
- Ensure import paths match the actual project file structure exactly.
- Do NOT introduce new bugs or remove error handling.

Fixed code for {fix_path}:"""

        try:
            response = model.generate_content(prompt)
            fixed = _strip_fences(response.text)

            full_path = _safe_project_path(project_dir, fix_path)
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(fixed, encoding="utf-8")

            updated_codes[fix_path] = fixed
            print(f"[DevAgent] 🔧 Fixed: {fix_path}")

        except Exception as e:
            if _is_rate_limit(e):
                raise RateLimitError(str(e))
            print(f"[DevAgent] ⚠️ Could not fix {fix_path}: {e}")

    return updated_codes

def _build_project(
    description: str,
    language: str,
    project_name: str,
    timeout: int,
    speak=None,
    player=None,
) -> str:

    def log(msg: str):
        print(f"[DevAgent] {msg}")
        if player:
            player.write_log(f"[DevAgent] {msg}")

    log("Planning project structure...")
    try:
        plan = _plan_project(description, language)
    except RateLimitError:
        msg = "Rate limit reached. Please try again in a moment."
        if speak: speak(msg)
        return msg
    except ValueError as e:
        msg = f"Planning failed: {e}"
        if speak: speak(msg)
        return msg

    proj_name    = project_name or plan.get("project_name", "project")
    proj_name    = re.sub(r"[^\w\-]", "_", proj_name)
    projects_root = _projects_dir()
    project_dir = projects_root / proj_name
    # Never overwrite a previous project, even when the model reuses a name.
    if project_dir.exists() or project_dir.is_symlink():
        # Existing symlinks are never used as project destinations.
        suffix = 2
        while ((projects_root / f"{proj_name}-{suffix}").exists()
               or (projects_root / f"{proj_name}-{suffix}").is_symlink()):
            suffix += 1
        project_dir = projects_root / f"{proj_name}-{suffix}"
        log(f"Existing project preserved; creating {project_dir.name} instead.")
    project_dir.mkdir(parents=True, exist_ok=True)

    files        = plan.get("files", [])
    entry_point  = plan.get("entry_point", "main.py")
    run_command  = plan.get("run_command", f"python {entry_point}")
    dependencies = plan.get("dependencies", [])

    log(f"Project: {proj_name} | Files: {len(files)} | Entry: {entry_point}")

    def _dep_sort_key(fi: dict) -> int:
        return len(fi.get("imports", []))

    sorted_files = sorted(files, key=_dep_sort_key)

    file_codes: dict[str, str] = {}

    for file_info in sorted_files:
        file_path = file_info.get("path", "")
        if not file_path:
            continue

        log(f"Writing {file_path}...")
        for attempt in range(2):
            try:
                code = _write_file(
                    file_info=file_info,
                    project_description=description,
                    all_files=files,
                    language=language,
                    project_dir=project_dir,
                    already_written=file_codes,
                )
                file_codes[file_path] = code
                time.sleep(0.4)
                break
            except RateLimitError:
                if attempt == 0:
                    log("Rate limit — waiting 20s...")
                    time.sleep(20)
                else:
                    log(f"Rate limit retry failed for {file_path}, skipping.")
            except Exception as e:
                log(f"Failed to write {file_path}: {e}")
                break

    if not file_codes:
        msg = "I could not write any project files."
        if speak: speak(msg)
        return msg

    if dependencies:
        install_result = _install_dependencies(dependencies, project_dir)
        log(install_result)

    _open_vscode(project_dir)

    last_output   = ""
    auto_installs = 0

    budget = _max_fix_attempts()
    for attempt in range(1, budget + 1):
        log(f"Running project (attempt {attempt}/{budget})...")
        last_output = _run_project(run_command, project_dir, timeout)
        log(f"Output preview: {last_output[:150]}")

        if not _has_error(last_output, run_command):
            msg = (
                f"Project '{proj_name}' is working. "
                f"Built in {attempt} attempt{'s' if attempt > 1 else ''}. "
                f"Saved to: {project_dir}"
            )
            if speak: speak(msg)
            return f"{msg}\n\nOutput:\n{last_output}"

        if attempt == budget:
            break

        error_type = _classify_error(last_output)
        if error_type == "dependency_error" and auto_installs < 3:
            installed = _try_auto_install(last_output, project_dir)
            if installed:
                auto_installs += 1
                log("Missing dependency installed, retrying...")
                time.sleep(1)
                continue

        log(f"Fixing errors (type: {error_type})...")
        try:
            updated = _fix_files(
                error_output=last_output,
                project_description=description,
                all_files=files,
                file_codes=file_codes,
                language=language,
                project_dir=project_dir,
                entry_point=entry_point,
            )
            file_codes.update(updated)
            time.sleep(1)
        except RateLimitError:
            msg = "Rate limit reached during fix. Project saved, check it manually in VSCode."
            if speak: speak(msg)
            return msg
        except Exception as e:
            log(f"Fix step failed: {e}")

    msg = (
        f"I couldn't fully fix '{proj_name}' after {budget} attempts. "
        f"Project is saved at {project_dir} — open it in VSCode and check manually."
    )
    if speak: speak(msg)
    return f"{msg}\n\nLast error:\n{last_output[:600]}"


def dev_agent(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
    speak=None,
) -> str:
    p            = parameters or {}
    description  = p.get("description", "").strip()
    language     = p.get("language", "python").strip()
    project_name = p.get("project_name", "").strip()
    timeout      = int(p.get("timeout", 30))

    if not description:
        return "Please describe the project you want me to build."

    return _build_project(
        description  = description,
        language     = language,
        project_name = project_name,
        timeout      = timeout,
        speak        = speak,
        player       = player,
    )


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
    description = str((parameters or {}).get("description") or "").strip()
    if not description:
        return dev_agent(parameters, player=player, speak=_speaker(player))
    from core import confirm
    from core.i18n import t
    return confirm.request(
        key="dev_agent", title=t("confirm.dev_project"), detail=description[:600],
        run=lambda: dev_agent(parameters, player=player, speak=_speaker(player)))


# ── Plugin declaration (auto-discovered by core/plugin_loader.py) ───────────
PLUGIN = {
    # For the plugin manager (core/plugin_loader): never sent to a model.
    "level": "advanced",
    "label": "Developer agent",
    "summary": "Builds a complete software project for you.",
    "setup": [
        "Say: \"Build me a small web app that…\" — it creates the project and fixes its own errors.",
        "Projects go to the folder set here; it can open your editor when done (VS Code by default).",
    ],
    "name": "project_builder",
    "description": "Builds complete multi-file projects from scratch: plans, writes files, installs deps, opens VSCode, runs and fixes errors. This plugin is separate from the built-in dev_agent tool.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "description": {
                "type": "STRING",
                "description": "What the project should do"
            },
            "language": {
                "type": "STRING",
                "description": "Programming language (default: python)"
            },
            "project_name": {
                "type": "STRING",
                "description": "Optional project folder name"
            },
            "timeout": {
                "type": "INTEGER",
                "description": "Run timeout in seconds (default: 30)"
            }
        },
        "required": [
            "description"
        ]
    },
}
