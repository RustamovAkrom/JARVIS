import json
import re
import time
from pathlib import Path
from pathlib import PurePosixPath, PureWindowsPath
from core.runtime_paths import PATHS
from core.user_data import update_json


def get_base_dir() -> Path:
    return PATHS.user_data_root


BASE_DIR = get_base_dir()
CONFIG_DIR = PATHS.config_dir
CONFIG_FILE = PATHS.api_config_file


def ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def config_exists() -> bool:
    return CONFIG_FILE.exists()


def save_api_keys(gemini_api_key: str) -> None:
    _patch_config(gemini_api_key=gemini_api_key.strip())


def load_api_keys() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"❌ Failed to load api_keys.json: {e}")
        return {}


def get_gemini_key() -> str | None:
    return load_api_keys().get("gemini_api_key")


def is_configured() -> bool:
    key = get_gemini_key()
    return bool(key and len(key) > 15)


def get_assistant_name() -> str:
    """Return the configured assistant name, or 'JARVIS' if not set."""
    return load_api_keys().get("assistant_name", "JARVIS") or "JARVIS"


def get_user_name() -> str:
    """Return the configured user name for addressing."""
    return load_api_keys().get("user_name", "")


def get_ui_opacity() -> int:
    """Return the main-window opacity percentage, clamped to a readable range."""
    value = load_api_keys().get("ui_opacity", 100)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 100
    try:
        value = round(value)
    except OverflowError, ValueError:
        return 100
    return max(60, min(100, value))


def get_ui_language() -> str:
    """Return a supported interface language, defaulting to English."""
    from memory.translations import LANGUAGES

    value = load_api_keys().get("ui_language", "en")
    return value if isinstance(value, str) and value in LANGUAGES else "en"


def save_ui_language(language: str) -> None:
    """Save a supported language without disturbing other settings."""
    from memory.translations import LANGUAGES

    value = str(language or "").strip().lower()
    if value not in LANGUAGES:
        raise ValueError(f"Unsupported UI language: {language}")
    _save_flag("ui_language", value)


def get_workspace_path() -> str:
    """Return the configured project folder if it still exists."""
    value = load_api_keys().get("workspace_path", "")
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        path = Path(value).expanduser().resolve()
    except OSError, RuntimeError:
        return ""
    return str(path) if path.is_dir() else ""


def save_workspace_path(path: str) -> None:
    """Save an existing project directory as the active workspace."""
    value = str(path or "").strip()
    if not value:
        _save_flag("workspace_path", "")
        return
    try:
        resolved = Path(value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Workspace folder is unavailable: {value}") from exc
    if not resolved.is_dir():
        raise ValueError(f"Workspace path is not a folder: {resolved}")
    _save_flag("workspace_path", str(resolved))


def get_project_open_mode() -> str:
    """Return whether project files open internally or in the OS default app."""
    value = load_api_keys().get("project_open_mode", "jarvis")
    return value if value in ("jarvis", "external") else "jarvis"


def save_project_open_mode(mode: str) -> None:
    """Save the project file opener preference."""
    value = str(mode or "").strip().lower()
    if value not in ("jarvis", "external"):
        raise ValueError(f"Unsupported project file opener: {mode}")
    _save_flag("project_open_mode", value)


def get_active_project_file() -> str:
    """Return the last opened project file as a workspace-relative path."""
    value = load_api_keys().get("active_project_file", "")
    return value if isinstance(value, str) else ""


def save_active_project_file(path: str) -> None:
    """Save a project-relative active file path without allowing absolute paths."""
    value = str(path or "").strip()
    if value:
        # Config values may be moved between operating systems. Check both
        # path syntaxes so a Windows absolute/traversal path cannot slip
        # through validation performed on POSIX (or vice versa).
        posix_path = PurePosixPath(value.replace("\\", "/"))
        windows_path = PureWindowsPath(value)
        if (
            posix_path.is_absolute()
            or windows_path.is_absolute()
            or windows_path.drive
            or ".." in posix_path.parts
            or ".." in windows_path.parts
        ):
            raise ValueError(
                "Active project file must be a relative path inside the project."
            )
    _save_flag("active_project_file", value)


def save_assistant_config(assistant_name: str, user_name: str) -> None:
    """Persist assistant name and user name to config."""
    _patch_config(
        assistant_name=assistant_name.strip() or "JARVIS",
        user_name=user_name.strip(),
    )


# ── Assistant voice ──────────────────────────────────────────────────────────
# Gemini Live prebuilt voices. Names are proper nouns — identical in every
# language, so this list is safe to show verbatim in any locale.
AVAILABLE_VOICES = ["Charon", "Puck", "Kore", "Fenrir", "Aoede"]
DEFAULT_VOICE = "Charon"


def get_voice() -> str:
    """Return the configured Live voice, falling back to the default if unset
    or if the stored value is not a voice we recognise."""
    v = load_api_keys().get("voice_name", DEFAULT_VOICE) or DEFAULT_VOICE
    return v if v in AVAILABLE_VOICES else DEFAULT_VOICE


def save_voice(voice_name: str) -> None:
    """Persist the chosen Live voice. Unknown names collapse to the default so a
    bad value can never reach the API and break the session."""
    v = (voice_name or "").strip()
    _patch_config(voice_name=v if v in AVAILABLE_VOICES else DEFAULT_VOICE)


def get_wake_word_enabled() -> bool:
    """Whether local wake-word gating is on (assistant sleeps until 'Hey Jarvis')."""
    return load_api_keys().get("wake_word_enabled", False)


def save_wake_word_enabled(enabled: bool) -> None:
    _patch_config(wake_word_enabled=bool(enabled))


def get_push_to_talk_enabled() -> bool:
    """Hold-a-key-to-speak. When on, the mic is closed unless the chord is held."""
    return load_api_keys().get("push_to_talk_enabled", False)


def save_push_to_talk_enabled(enabled: bool) -> None:
    _save_flag("push_to_talk_enabled", enabled)


HUD_STYLES = ("core", "orbital")


def get_hud_style() -> str:
    """Which non-face centrepiece the HUD draws."""
    v = str(load_api_keys().get("hud_style", "core")).strip().lower()
    return v if v in HUD_STYLES else "core"


def save_hud_style(style: str) -> None:
    s = str(style or "").strip().lower()
    _save_flag("hud_style", s if s in HUD_STYLES else "core")


# ── Live-session tuning ──────────────────────────────────────────────────────
# Everything here is optional and has a working default, so an untouched
# config behaves exactly like a configured one. Each value is also a way out:
# if a future model dislikes one of these, set it back and nothing else changes.


def get_thinking_enabled() -> bool:
    """Whether the Live model may spend tokens thinking before it answers.

    Off by default. A voice assistant is judged on how fast it starts talking,
    and the reasoning that actually needs deliberation in this app is delegated
    to the planning tools, which run on a separate non-Live model.
    """
    return bool(load_api_keys().get("thinking_enabled", False))


def save_thinking_enabled(enabled: bool) -> None:
    _save_flag("thinking_enabled", enabled)


def get_turn_tuning() -> dict:
    """How eagerly the server decides you have stopped speaking.

    OFF by default, and that default was earned. Cutting turns shorter looks
    like a free speed win and is not: proactive audio has to judge whether an
    utterance was even addressed to the assistant, and a turn clipped early
    gives it less to judge, so it stays quiet — and the reply to your first
    sentence only arrives once your second one has given it enough context.
    That reads as the assistant being a turn behind, which is far worse than
    the fraction of a second the tuning saves.

    Turn it on with "turn_tuning": {"enabled": true} if your own microphone and
    speaking pace suit it. `silence_ms` is the one that is felt: the pause the
    server sits through before accepting your turn is over.
    """
    cfg = load_api_keys().get("turn_tuning")
    cfg = cfg if isinstance(cfg, dict) else {}

    def _int(key, default, lo, hi):
        try:
            return max(lo, min(hi, int(cfg.get(key, default))))
        except TypeError, ValueError:
            return default

    return {
        "enabled": bool(cfg.get("enabled", False)),
        "silence_ms": _int("silence_ms", 550, 200, 3000),
        "prefix_ms": _int("prefix_ms", 150, 0, 1000),
        # "high" = quicker to decide speech has ended.
        "end_sensitivity": str(cfg.get("end_sensitivity", "high")).lower(),
        "start_sensitivity": str(cfg.get("start_sensitivity", "default")).lower(),
    }


def save_turn_tuning(values: dict) -> None:
    def merge(data: dict) -> None:
        cur = data.get("turn_tuning")
        cur = dict(cur) if isinstance(cur, dict) else {}
        cur.update(values or {})
        data["turn_tuning"] = cur

    _update_config(merge)


def get_proactive_audio_enabled() -> bool:
    """Whether the model gets to decide an utterance was not aimed at it and
    stay quiet.

    On by default — it is what stops the assistant answering the room. But it
    is also the first thing to switch off if replies ever seem to arrive a turn
    late: what looks like lag is usually the model having judged your previous
    sentence as not addressed to it, and only changing its mind once the next
    one arrives.
    """
    return bool(load_api_keys().get("proactive_audio", True))


def save_proactive_audio_enabled(enabled: bool) -> None:
    _save_flag("proactive_audio", enabled)


MEDIA_RESOLUTIONS = ("default", "low", "medium", "high")


def get_media_resolution() -> str:
    """How finely the model tokenises the screenshots and camera frames it is
    sent. 'medium' keeps on-screen text readable at a fraction of the tokens a
    full-resolution frame costs; 'low' is cheaper still but starts losing small
    text, which is most of what screen captures are for."""
    v = str(load_api_keys().get("media_resolution", "medium")).strip().lower()
    return v if v in MEDIA_RESOLUTIONS else "medium"


def save_media_resolution(value: str) -> None:
    v = str(value or "").strip().lower()
    _save_flag("media_resolution", v if v in MEDIA_RESOLUTIONS else "medium")


def _save_flag(key: str, value) -> None:
    """Read-modify-write one key without disturbing the rest of the config."""
    _patch_config(**{key: bool(value) if isinstance(value, bool) else value})


CURSOR_STYLES = ("halo", "orb", "reticle", "dot")
CURSOR_SIZES = (18, 24, 32, 40)
CURSOR_HOTKEYS = ("Ctrl+Shift+Space", "Ctrl+Alt+J", "Ctrl+Shift+J")
REGION_HOTKEYS = ("Ctrl+Alt+R", "Ctrl+Shift+R", "Ctrl+Shift+X")
CURSOR_MENU_ACTIONS = (
    "microphone",
    "ask",
    "attach",
    "summarize",
    "explain",
    "translate",
    "fix",
    "screen",
    "interrupt",
    "settings",
)
DEFAULT_CURSOR_MENU_ACTIONS = (
    "microphone",
    "ask",
    "attach",
    "summarize",
    "explain",
    "translate",
    "fix",
    "screen",
    "interrupt",
    "settings",
)
DEFAULT_COMPUTER_PERMISSIONS = {
    "observe_screen": True,
    "find_elements": False,
    "move_mouse": False,
    "click": False,
    "type": False,
    "clipboard": False,
}
COMPUTER_MODES = ("chat", "agent")
COMPUTER_ACCESS_LEVELS = ("ask", "approve", "full")


def get_computer_access_settings() -> dict[str, str | int | float]:
    """Return the effective computer-access policy; Full Access always expires."""
    cfg = load_api_keys()
    mode = str(cfg.get("computer_mode", "chat")).lower()
    access = str(cfg.get("computer_access_level", "approve")).lower()
    try:
        expires_at = max(0.0, float(cfg.get("computer_access_expires_at", 0) or 0))
    except (TypeError, ValueError):
        expires_at = 0.0
    remaining = max(0.0, expires_at - time.time()) if expires_at else 0.0
    # Legacy Full Access with no expiry is expired rather than indefinite.
    if access == "full" and remaining <= 0:
        access = "approve"
    return {
        "mode": mode if mode in COMPUTER_MODES else "chat",
        "access_level": access if access in COMPUTER_ACCESS_LEVELS else "approve",
        "expires_at": expires_at if access == "full" else 0.0,
        "remaining_seconds": int(remaining) if access == "full" else 0,
    }


def save_computer_access_settings(
    mode: str | None = None,
    access_level: str | None = None,
    *,
    temporary_minutes: int | None = None,
) -> None:
    """Persist settings and attach a bounded deadline to Full Access."""
    updates = {}
    if mode is not None:
        mode = str(mode).lower()
        if mode in COMPUTER_MODES:
            updates["computer_mode"] = mode
    if access_level is not None:
        access_level = str(access_level).lower()
        if access_level in COMPUTER_ACCESS_LEVELS:
            updates["computer_access_level"] = access_level
            if access_level != "full":
                updates["computer_access_expires_at"] = 0.0
    if access_level == "full":
        if temporary_minutes is not None:
            minutes = max(1, min(60, int(temporary_minutes)))
            updates["computer_access_expires_at"] = time.time() + minutes * 60
        else:
            try:
                expiry = float(load_api_keys().get("computer_access_expires_at", 0) or 0)
            except (TypeError, ValueError):
                expiry = 0.0
            if expiry <= time.time():
                updates["computer_access_expires_at"] = time.time() + 15 * 60
    elif temporary_minutes is not None:
        # Changing a duration alone only renews an already elevated grant.
        current = load_api_keys()
        try:
            expiry = float(current.get("computer_access_expires_at", 0) or 0)
        except (TypeError, ValueError):
            expiry = 0.0
        if str(current.get("computer_access_level", "")).lower() == "full" and expiry > time.time():
            minutes = max(1, min(60, int(temporary_minutes)))
            updates["computer_access_expires_at"] = time.time() + minutes * 60
    if updates:
        _patch_config(**updates)


def get_cursor_assistant_settings() -> dict:
    cfg = load_api_keys().get("cursor_assistant")
    cfg = cfg if isinstance(cfg, dict) else {}
    style = str(cfg.get("style", "halo")).lower()
    try:
        size = int(cfg.get("size", 24))
    except TypeError, ValueError:
        size = 24
    actions = cfg.get("menu_actions", DEFAULT_CURSOR_MENU_ACTIONS)
    if not isinstance(actions, list):
        actions = list(DEFAULT_CURSOR_MENU_ACTIONS)
    actions = list(
        dict.fromkeys(action for action in actions if action in CURSOR_MENU_ACTIONS)
    )
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "cursor_visible": bool(cfg.get("cursor_visible", True)),
        "style": style if style in CURSOR_STYLES else "halo",
        "size": size if size in CURSOR_SIZES else 24,
        "hotkey": (
            cfg.get("hotkey", CURSOR_HOTKEYS[0])
            if cfg.get("hotkey", CURSOR_HOTKEYS[0]) in CURSOR_HOTKEYS
            else CURSOR_HOTKEYS[0]
        ),
        "region_hotkey": (
            cfg.get("region_hotkey", REGION_HOTKEYS[0])
            if cfg.get("region_hotkey", REGION_HOTKEYS[0]) in REGION_HOTKEYS
            else REGION_HOTKEYS[0]
        ),
        "menu_actions": actions,
    }


def save_cursor_assistant_settings(values: dict) -> None:
    values = values if isinstance(values, dict) else {}
    current = load_api_keys().get("cursor_assistant")
    current = dict(current) if isinstance(current, dict) else {}
    if "enabled" in values:
        current["enabled"] = bool(values["enabled"])
    if "cursor_visible" in values:
        current["cursor_visible"] = bool(values["cursor_visible"])
    if "style" in values:
        style = str(values["style"]).lower()
        current["style"] = style if style in CURSOR_STYLES else "halo"
    if "size" in values:
        try:
            size = int(values["size"])
        except TypeError, ValueError:
            size = 24
        current["size"] = size if size in CURSOR_SIZES else 24
    if "hotkey" in values:
        hotkey = str(values["hotkey"])
        current["hotkey"] = hotkey if hotkey in CURSOR_HOTKEYS else CURSOR_HOTKEYS[0]
    if "region_hotkey" in values:
        hotkey = str(values["region_hotkey"])
        current["region_hotkey"] = (
            hotkey if hotkey in REGION_HOTKEYS else REGION_HOTKEYS[0]
        )
    if "menu_actions" in values:
        actions = values["menu_actions"]
        if not isinstance(actions, (list, tuple, set)):
            actions = DEFAULT_CURSOR_MENU_ACTIONS
        current["menu_actions"] = list(
            dict.fromkeys(action for action in actions if action in CURSOR_MENU_ACTIONS)
        )
    _patch_config(cursor_assistant=current)


def get_configured_computer_permissions() -> dict:
    """Return saved per-capability choices, without the temporary full-access override."""
    api_cfg = load_api_keys()
    cfg = api_cfg.get("computer_permissions")
    cfg = cfg if isinstance(cfg, dict) else {}
    return {
        key: cfg.get(key) if isinstance(cfg.get(key), bool) else default
        for key, default in DEFAULT_COMPUTER_PERMISSIONS.items()
    }


def get_computer_permissions() -> dict:
    """Return effective permissions, including the explicit full-access override."""
    if get_computer_access_settings()["access_level"] == "full":
        return {key: True for key in DEFAULT_COMPUTER_PERMISSIONS}
    return get_configured_computer_permissions()


def save_computer_permissions(values: dict) -> None:
    values = values if isinstance(values, dict) else {}
    current = load_api_keys().get("computer_permissions")
    current = dict(current) if isinstance(current, dict) else {}
    for key in DEFAULT_COMPUTER_PERMISSIONS:
        if isinstance(values.get(key), bool):
            current[key] = values[key]
    _patch_config(computer_permissions=current)


def get_computer_safety_settings() -> dict:
    """Read microphone gating and per-application scoping safeguards."""
    cfg = load_api_keys().get("computer_safety", {})
    cfg = cfg if isinstance(cfg, dict) else {}
    mode = str(cfg.get("application_rule_mode", "blocklist") or "blocklist").lower()
    if mode not in {"blocklist", "allowlist"}:
        mode = "blocklist"
    apps = cfg.get("applications", [])
    if isinstance(apps, str):
        apps = re.split(r"[,;\n]", apps)
    if not isinstance(apps, (list, tuple, set)):
        apps = []
    return {
        "microphone_during_screen_actions": bool(
            cfg.get("microphone_during_screen_actions", True)
        ),
        "application_rules_enabled": bool(cfg.get("application_rules_enabled", False)),
        "application_rule_mode": mode,
        "applications": list(dict.fromkeys(
            str(value).strip()[:120] for value in apps if str(value).strip()
        ))[:64],
    }


def save_computer_safety_settings(values: dict) -> None:
    """Persist only known safeguards, preserving unrelated settings."""
    values = values if isinstance(values, dict) else {}
    current = load_api_keys().get("computer_safety", {})
    current = dict(current) if isinstance(current, dict) else {}
    if "microphone_during_screen_actions" in values:
        current["microphone_during_screen_actions"] = bool(
            values["microphone_during_screen_actions"]
        )
    if "application_rules_enabled" in values:
        current["application_rules_enabled"] = bool(values["application_rules_enabled"])
    if "application_rule_mode" in values:
        mode = str(values["application_rule_mode"] or "").strip().lower()
        if mode in {"blocklist", "allowlist"}:
            current["application_rule_mode"] = mode
    if "applications" in values:
        apps = values["applications"]
        if isinstance(apps, str):
            apps = re.split(r"[,;\n]", apps)
        if not isinstance(apps, (list, tuple, set)):
            apps = []
        current["applications"] = list(dict.fromkeys(
            str(value).strip()[:120] for value in apps if str(value).strip()
        ))[:64]
    _patch_config(computer_safety=current)


def get_brief_enabled() -> bool:
    return load_api_keys().get("morning_brief_enabled", True)


def save_brief_enabled(enabled: bool) -> None:
    _patch_config(morning_brief_enabled=bool(enabled))


# ── Audio devices ────────────────────────────────────────────────────────────
# Stored as device NAMES, not sounddevice indices. Indices shift every time a
# USB device is plugged in or removed, so a saved index silently starts pointing
# at a different microphone. The empty string means "system default", which is
# both the factory setting and what an unresolvable saved device falls back to —
# so unplugging a headset degrades to the built-in speakers instead of crashing.


def _patch_config(**fields) -> None:
    """Read-modify-write one or more keys in api_keys.json.

    Every setter in this file open-coded this. Collapsing it here means a new
    setting is one line, and there is one place where a corrupt config file is
    handled instead of nine."""
    _update_config(lambda data: data.update(fields))


def _update_config(update) -> dict:
    ensure_config_dir()
    return update_json(CONFIG_FILE, update)


def get_input_device() -> str:
    """Microphone device name, or '' for the system default."""
    return (load_api_keys().get("input_device", "") or "").strip()


def save_input_device(name: str) -> None:
    _patch_config(input_device=(name or "").strip())


def get_output_device() -> str:
    """Speaker device name, or '' for the system default."""
    return (load_api_keys().get("output_device", "") or "").strip()


def save_output_device(name: str) -> None:
    _patch_config(output_device=(name or "").strip())


def get_plugins_subsystem_enabled() -> bool:
    """Global optional-plugin switch. Built-in actions are unaffected."""
    value = load_api_keys().get("plugins_subsystem_enabled", True)
    return value if isinstance(value, bool) else True


def save_plugins_subsystem_enabled(enabled: bool) -> None:
    """Enable/disable plugin discovery and execution without affecting actions."""
    _patch_config(plugins_subsystem_enabled=bool(enabled))


def get_plugin_enabled(plugin_name: str) -> bool:
    """Plugins are enabled by default the moment they're discovered (opt-out model)."""
    return load_api_keys().get("plugins_enabled", {}).get(plugin_name, True)


# ── Per-plugin settings ("tokens" / connection details) ───────────────────────
# Generic store so a plugin can declare its own config fields (PLUGIN_SETTINGS)
# and the settings UI renders + persists them WITHOUT any core edit — keeping the
# drop-in model intact. Values live under plugin_config[<namespace>][<key>].
# A namespace defaults to the plugin name, but a suite of plugins (e.g. the
# several printer plugins) can share ONE namespace.
def get_plugin_config(namespace: str) -> dict:
    """All stored values for a namespace (empty dict if none set yet)."""
    cfg = load_api_keys().get("plugin_config")
    val = cfg.get(namespace) if isinstance(cfg, dict) else None
    return dict(val) if isinstance(val, dict) else {}


def get_plugin_setting(namespace: str, key: str, default=None):
    """A single value from a namespace, or `default` if unset."""
    return get_plugin_config(namespace).get(key, default)


def save_plugin_config(namespace: str, values: dict) -> None:
    """Merge `values` into a namespace's stored config (read-modify-write, like
    every other helper here). Only the provided keys are touched."""

    def merge(data: dict) -> None:
        pc = data.get("plugin_config")
        pc = dict(pc) if isinstance(pc, dict) else {}
        cur = pc.get(namespace)
        cur = dict(cur) if isinstance(cur, dict) else {}
        cur.update(values)
        pc[namespace] = cur
        data["plugin_config"] = pc

    _update_config(merge)


def save_plugin_enabled(plugin_name: str, enabled: bool) -> None:
    def merge(data: dict) -> None:
        plugins_cfg = data.get("plugins_enabled")
        plugins_cfg = dict(plugins_cfg) if isinstance(plugins_cfg, dict) else {}
        plugins_cfg[plugin_name] = bool(enabled)
        data["plugins_enabled"] = plugins_cfg

    _update_config(merge)


# ── Providers: local STT/LLM/TTS and non-Gemini APIs ─────────────────────────
# Stored here rather than in a file of their own, for the same reason plugin
# settings are: one config to back up, one already in .gitignore, one place a
# user knows to look.
#
# The important property is what happens when nobody has configured anything.
# `get_provider_mode()` returns "gemini" on a config that has never heard of
# providers, and every caller checks that BEFORE importing the providers
# package — so an ordinary install never executes a line of it. This is the
# whole basis of the claim that the folder costs nothing when unused.
PROVIDER_MODE_GEMINI = "gemini"  # integrated online conversation
PROVIDER_MODE_PIPELINE = "pipeline"  # configurable voice/model/voice stack
PROVIDER_ENVIRONMENTS = ("online", "offline")
PROVIDER_SELECTIONS = ("online", "offline", "auto")
_LOCAL_CATALOG_IDS = frozenset({"ollama", "lmstudio", "llamacpp", "vllm", "jan", "local", "piper", "system"})


def get_providers() -> dict:
    """The whole providers block, or an empty dict."""
    val = load_api_keys().get("providers")
    return dict(val) if isinstance(val, dict) else {}


def _legacy_provider_environment(block: dict) -> str:
    """Infer the environment used by legacy configs that stored one pipeline."""
    mode = str(block.get("mode", PROVIDER_MODE_GEMINI) or "").strip().lower()
    if mode != PROVIDER_MODE_PIPELINE:
        return "online"

    pipeline = block.get("pipeline")
    pipeline = pipeline if isinstance(pipeline, dict) else {}
    creds = block.get("creds")
    creds = creds if isinstance(creds, dict) else {}

    llm_id = str(pipeline.get("llm") or "").strip()
    llm_cfg = creds.get(llm_id)
    llm_cfg = llm_cfg if isinstance(llm_cfg, dict) else {}
    catalog_id = str(llm_cfg.get("catalog") or "").strip().lower()
    if catalog_id in _LOCAL_CATALOG_IDS:
        return "offline"

    stt_id = str(pipeline.get("stt") or "").strip()
    stt_cfg = creds.get(stt_id)
    stt_cfg = stt_cfg if isinstance(stt_cfg, dict) else {}
    if str(stt_cfg.get("backend") or "").strip().lower() == "local":
        return "offline"

    if str(pipeline.get("tts") or "").strip().lower() in {"piper", "system_tts"}:
        return "offline"
    return "online"


def get_provider_selection() -> str:
    """Return the requested runtime policy: online, offline or auto."""
    block = get_providers()
    value = str(block.get("selection") or "").strip().lower()
    if value in PROVIDER_SELECTIONS:
        return value
    # Migrate installations that only stored the former two-state environment.
    legacy = str(block.get("environment") or "").strip().lower()
    return legacy if legacy in PROVIDER_ENVIRONMENTS else _legacy_provider_environment(block)


def get_provider_environment() -> str:
    """Return the currently resolved runtime environment, not the Auto policy."""
    block = get_providers()
    selection = get_provider_selection()
    if selection in PROVIDER_ENVIRONMENTS:
        return selection
    value = str(block.get("environment") or "").strip().lower()
    if value in PROVIDER_ENVIRONMENTS:
        return value
    return _legacy_provider_environment(block)


def resolve_provider_environment(
    selection: str,
    *,
    online_available: bool,
    offline_ready: bool,
) -> str:
    """Resolve a user's Online/Offline/Auto choice without performing I/O."""
    value = str(selection or "").strip().lower()
    if value == "online":
        return "online"
    if value == "offline":
        return "offline"
    if value != "auto":
        raise ValueError(f"Unsupported AI environment selection: {selection}")
    if online_available or not offline_ready:
        return "online"
    return "offline"


def get_online_provider_mode() -> str:
    """Return the online engine: integrated Gemini Live or a configurable stack."""
    block = get_providers()
    mode = str(
        block.get("online_mode")
        or (block.get("mode") if get_provider_environment() == "online" else "")
        or PROVIDER_MODE_GEMINI
    ).strip().lower()
    return mode if mode in (PROVIDER_MODE_GEMINI, PROVIDER_MODE_PIPELINE) else PROVIDER_MODE_GEMINI


def get_provider_mode() -> str:
    """Runtime engine for the currently selected Online/Offline environment."""
    if get_provider_environment() == "offline":
        return PROVIDER_MODE_PIPELINE
    return get_online_provider_mode()


def get_provider_config(
    provider_id: str,
    environment: str | None = None,
) -> dict:
    """Return settings from one environment, falling back to legacy shared data."""
    block = get_providers()
    target = str(environment or get_provider_environment()).strip().lower()
    if target not in PROVIDER_ENVIRONMENTS:
        target = get_provider_environment()

    scoped = block.get(f"creds_{target}")
    value = scoped.get(provider_id) if isinstance(scoped, dict) else None
    if isinstance(value, dict):
        return dict(value)

    # Read-only fallback for configurations created before profile separation.
    legacy = block.get("creds")
    value = legacy.get(provider_id) if isinstance(legacy, dict) else None
    return dict(value) if isinstance(value, dict) else {}


def merge_provider_catalog_defaults(
    current_values: dict,
    preset_values: dict,
    catalog_id: str,
) -> dict:
    """Fill missing catalog defaults without overwriting edited or stored values."""
    merged = dict(preset_values or {})
    merged.update(current_values or {})
    if catalog_id:
        merged["catalog"] = str(catalog_id)
    return merged


def get_pipeline(environment: str | None = None) -> dict:
    """Provider slots for one environment, without overwriting the other mode."""
    block = get_providers()
    target = str(environment or get_provider_environment()).strip().lower()
    if target not in PROVIDER_ENVIRONMENTS:
        target = get_provider_environment()

    value = block.get(f"pipeline_{target}")
    if isinstance(value, dict):
        return dict(value)

    # Compatibility path for installations created before Online and Offline
    # had separate provider selections.
    legacy = block.get("pipeline")
    if isinstance(legacy, dict) and target == _legacy_provider_environment(block):
        return dict(legacy)
    return {}


def get_sidecar_provider() -> str:
    """Optional cloud fallback for background requests while using Gemini Live."""
    return str(get_providers().get("sidecar", "") or "").strip()


def _patch_providers(**fields) -> None:
    p = get_providers()
    p.update(fields)
    _patch_config(providers=p)


def save_provider_environment(environment: str) -> None:
    """Persist an explicit runtime selection and its current active environment."""
    value = str(environment or "").strip().lower()
    if value not in PROVIDER_ENVIRONMENTS:
        raise ValueError(f"Unsupported AI environment: {environment}")
    _patch_providers(environment=value, selection=value)


def save_provider_runtime_environment(environment: str) -> None:
    """Update Auto's last resolved environment without changing the user's policy."""
    value = str(environment or "").strip().lower()
    if value not in PROVIDER_ENVIRONMENTS:
        raise ValueError(f"Unsupported runtime AI environment: {environment}")
    _patch_providers(environment=value)


def save_provider_mode(mode: str) -> None:
    """Legacy single-mode API: selecting an engine activates its Online profile.

    New UI code should use save_providers(environment=..., online_mode=...)
    when it needs to update profiles without changing the active environment.
    """
    value = str(mode or "").strip().lower()
    if value not in (PROVIDER_MODE_GEMINI, PROVIDER_MODE_PIPELINE):
        value = PROVIDER_MODE_GEMINI
    _patch_providers(
        environment="online",
        selection="online",
        mode=value,
        online_mode=value,
    )


def save_provider_config(
    provider_id: str,
    values: dict,
    environment: str | None = None,
) -> None:
    """Merge provider settings into the Online or Offline profile only."""
    target = str(environment or get_provider_environment()).strip().lower()
    if target not in PROVIDER_ENVIRONMENTS:
        target = get_provider_environment()

    p = get_providers()
    key = f"creds_{target}"
    creds = p.get(key)
    creds = dict(creds) if isinstance(creds, dict) else {}
    current = creds.get(provider_id)
    if not isinstance(current, dict):
        legacy = p.get("creds")
        legacy_value = legacy.get(provider_id) if isinstance(legacy, dict) else None
        current = dict(legacy_value) if isinstance(legacy_value, dict) else {}
    else:
        current = dict(current)

    current.update(values or {})
    creds[provider_id] = current
    p[key] = creds
    _patch_config(providers=p)


def save_pipeline(
    stt: str = None,
    llm: str = None,
    tts: str = None,
    environment: str | None = None,
) -> None:
    """Save provider slots for the selected environment only."""
    target = str(environment or get_provider_environment()).strip().lower()
    if target not in PROVIDER_ENVIRONMENTS:
        target = get_provider_environment()

    block = get_providers()
    key = f"pipeline_{target}"
    pipeline = block.get(key)
    if not isinstance(pipeline, dict):
        pipeline = get_pipeline(target)
    else:
        pipeline = dict(pipeline)
    for slot, value in (("stt", stt), ("llm", llm), ("tts", tts)):
        if value is not None:
            pipeline[slot] = str(value).strip()

    block[key] = pipeline
    block["pipeline"] = dict(pipeline)  # legacy readers see the active environment
    block["environment"] = target
    _patch_config(providers=block)


def save_sidecar_provider(provider_id: str) -> None:
    _patch_providers(sidecar=(provider_id or "").strip())


def get_sidecar_first() -> bool:
    return bool(get_providers().get("sidecar_first", False))


def save_sidecar_first(enabled: bool) -> None:
    _patch_providers(sidecar_first=bool(enabled))


def save_providers(
    mode: str = None,
    pipeline: dict = None,
    creds: dict = None,
    sidecar: str = None,
    sidecar_first: bool = None,
    environment: str = None,
    online_mode: str = None,
    pipelines: dict = None,
    selection: str = None,
) -> None:
    """Persist one environment's engine, slots and credentials in one write.

    The pipeline argument remains the active-environment alias for older
    callers. Explicit pipeline_online and pipeline_offline keys retain both
    selections.
    """
    def merge(data: dict) -> None:
        block = data.get("providers")
        block = dict(block) if isinstance(block, dict) else {}

        active_environment = str(
            environment or block.get("environment") or _legacy_provider_environment(block)
        ).strip().lower()
        if active_environment not in PROVIDER_ENVIRONMENTS:
            active_environment = "online"
        block["environment"] = active_environment
        selected_mode = str(selection or block.get("selection") or "").strip().lower()
        if selected_mode in PROVIDER_SELECTIONS:
            block["selection"] = selected_mode
        elif not block.get("selection"):
            block["selection"] = active_environment

        if online_mode is not None:
            candidate = str(online_mode).strip().lower()
            block["online_mode"] = (
                candidate
                if candidate in (PROVIDER_MODE_GEMINI, PROVIDER_MODE_PIPELINE)
                else PROVIDER_MODE_GEMINI
            )
        elif mode is not None and active_environment == "online":
            candidate = str(mode).strip().lower()
            block["online_mode"] = (
                candidate
                if candidate in (PROVIDER_MODE_GEMINI, PROVIDER_MODE_PIPELINE)
                else PROVIDER_MODE_GEMINI
            )

        if mode is not None and active_environment == "online":
            block["mode"] = block.get("online_mode", PROVIDER_MODE_GEMINI)
        elif active_environment == "offline":
            block["mode"] = PROVIDER_MODE_PIPELINE

        if pipelines is not None:
            for target, values in (pipelines or {}).items():
                target = str(target or "").strip().lower()
                if target not in PROVIDER_ENVIRONMENTS or not isinstance(values, dict):
                    continue
                key = f"pipeline_{target}"
                current = block.get(key)
                current = dict(current) if isinstance(current, dict) else {}
                current.update({k: str(v or "").strip() for k, v in values.items()})
                block[key] = current

        if pipeline is not None:
            target_key = f"pipeline_{active_environment}"
            current = block.get(target_key)
            current = dict(current) if isinstance(current, dict) else {}
            current.update({k: str(v or "").strip() for k, v in pipeline.items()})
            block[target_key] = current

        active_pipeline = block.get(f"pipeline_{active_environment}")
        if isinstance(active_pipeline, dict):
            block["pipeline"] = dict(active_pipeline)

        if creds is not None:
            # Credentials follow the same Online/Offline boundary as pipelines.
            # The legacy shared map remains a read-only fallback for old configs.
            credentials_key = f"creds_{active_environment}"
            store = block.get(credentials_key)
            store = dict(store) if isinstance(store, dict) else {}
            legacy_store = block.get("creds")
            legacy_store = legacy_store if isinstance(legacy_store, dict) else {}
            for pid, values in (creds or {}).items():
                one = store.get(pid)
                if not isinstance(one, dict):
                    legacy_value = legacy_store.get(pid)
                    one = dict(legacy_value) if isinstance(legacy_value, dict) else {}
                else:
                    one = dict(one)
                one.update(values or {})
                store[pid] = one
            block[credentials_key] = store
        if sidecar is not None:
            block["sidecar"] = str(sidecar or "").strip()
        if sidecar_first is not None:
            block["sidecar_first"] = bool(sidecar_first)
        data["providers"] = block

    _update_config(merge)


def get_workspace_layout_settings() -> dict:
    """Return persisted workspace splitter sizes, normalized and validated."""
    raw = load_api_keys().get("workspace_layout", {})
    raw = raw if isinstance(raw, dict) else {}
    result = {}
    for key in ("main", "root", "workspace", "center"):
        value = raw.get(key)
        if isinstance(value, list) and len(value) in (2, 3):
            sizes = []
            for item in value:
                try:
                    sizes.append(max(0, min(100000, int(item))))
                except (TypeError, ValueError):
                    sizes.append(0)
            if any(sizes):
                result[key] = sizes
    return result


def save_workspace_layout_settings(**sizes: list[int]) -> None:
    """Persist only valid splitter size arrays; unrelated settings are preserved."""
    fields = {}
    for key, value in sizes.items():
        if key not in {"main", "root", "workspace", "center"}:
            continue
        if not isinstance(value, (list, tuple)) or len(value) not in (2, 3):
            continue
        try:
            fields[key] = [max(0, min(100000, int(item))) for item in value]
        except (TypeError, ValueError):
            continue
    if not fields:
        return

    def merge(data: dict) -> None:
        current = data.get("workspace_layout")
        current = dict(current) if isinstance(current, dict) else {}
        current.update(fields)
        data["workspace_layout"] = current

    _update_config(merge)


def get_update_settings() -> dict:
    """Read safe update preferences; installation is opt-in."""
    raw = load_api_keys().get("updates", {})
    raw = raw if isinstance(raw, dict) else {}
    return {
        "auto_check": bool(raw.get("auto_check", True)),
        "auto_download": bool(raw.get("auto_download", False)),
        "auto_install": bool(raw.get("auto_install", False)),
    }


def save_update_settings(
    *,
    auto_check: bool | None = None,
    auto_download: bool | None = None,
    auto_install: bool | None = None,
) -> None:
    """Update only supplied options while preserving all other settings."""
    fields = {
        key: bool(value)
        for key, value in (
            ("auto_check", auto_check),
            ("auto_download", auto_download),
            ("auto_install", auto_install),
        )
        if value is not None
    }

    def merge(data: dict) -> None:
        current = data.get("updates")
        current = dict(current) if isinstance(current, dict) else {}
        current.update(fields)
        data["updates"] = current

    _update_config(merge)
