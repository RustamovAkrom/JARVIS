"""Searchable metadata for existing JARVIS settings and their legacy routes."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SettingsCategory:
    id: str
    title: str
    description: str
    actions: tuple[tuple[str, str], ...] = ()  # (label, action id)


@dataclass(frozen=True)
class SettingEntry:
    id: str
    category_id: str
    title: str
    description: str
    keywords: tuple[str, ...]
    action_id: str


CATEGORIES = (
    SettingsCategory("general", "General", "Startup, language, and everyday behavior.", (("Open setup options", "setup"), ("Open everyday controls", "controls"))),
    SettingsCategory("identity", "Assistant Identity", "Names, voice, and assistant personalization.", (("Customize assistant", "customize"),)),
    SettingsCategory("audio", "Voice & Audio", "Devices, voice activation, and microphone controls.", (("Audio devices", "audio"), ("Wake word and push-to-talk", "audio"))),
    SettingsCategory("appearance", "Appearance & Personalization", "Theme, accent color, opacity, and visual preferences.", (("Customize appearance", "customize"),)),
    SettingsCategory("workspace", "Workspace & Layout", "Project folder, file opening and visible workspace panels.", (("Workspace panel controls", "layout"),)),
    SettingsCategory("cursor", "Cursor Assistant", "Cursor behavior, shortcuts, and quick actions.", (("Cursor assistant controls", "controls"),)),
    SettingsCategory(
        "permissions",
        "Security & Permissions",
        "Manage Chat and Agent modes, per-action access, approval rules and authentication.",
        (("Review computer permissions", "security_info"), ("Authentication integration", "security_info")),
    ),
    SettingsCategory("integrations", "AI Providers", "Choose where JARVIS runs its models: Online cloud services or Offline models on this device.", (("AI provider settings", "providers"),)),
    SettingsCategory("plugins", "Plugins", "Installed extensions and their settings.", (("Plugin manager", "plugins"), ("Plugin settings", "plugin_settings"))),
    SettingsCategory("memory", "Memory", "Facts JARVIS has saved. Conversation history is kept separately in Conversations.", (("Open memory", "memory"),)),
    SettingsCategory("conversations", "Conversations", "Local transcript search, privacy and retention.", (("Manage conversation history", "conversations"),)),
    SettingsCategory("automation", "Automation & Presence", "Presence awareness, routines, and quiet hours.", (("Manage automation", "automation"),)),

    SettingsCategory("developer", "Developer & Diagnostics", "Confirmed development tools, runtime configuration, logs and testing diagnostics.", (("Developer options", "setup"), ("Debug console", "console"))),
    SettingsCategory("updates", "Updates & Maintenance", "Application version and update operations.", (("Open updates", "updates"),)),
)


ENTRIES = (
    SettingEntry("app_language", "general", "Interface language", "Choose the language used by the JARVIS interface.", ("locale", "translation"), "controls"),
    SettingEntry("typed_language_detection", "general", "Typed language detection", "Detect typed requests offline and reply in the detected language.", ("automatic language", "response language", "multilingual"), "controls"),
    SettingEntry("autostart", "general", "Start with Windows", "Launch JARVIS automatically when you sign in.", ("startup", "login"), "setup"),
    SettingEntry("fullscreen", "general", "Fullscreen", "Toggle the main window fullscreen mode.", ("display", "window"), "controls"),
    SettingEntry("morning_brief", "general", "Morning brief", "Enable or disable the daily brief.", ("daily", "brief"), "controls"),
    SettingEntry("remote_control", "general", "Phone control link", "Open a temporary control link for your phone.", ("remote", "mobile"), "setup"),
    SettingEntry("desktop_shortcut", "general", "Desktop shortcut", "Create a shortcut for launching JARVIS.", ("launch", "desktop"), "setup"),
    SettingEntry("assistant_name", "identity", "Assistant name", "Change the name displayed for the assistant.", ("jarvis", "display name"), "customize"),
    SettingEntry("user_name", "identity", "Your name", "Set the name used when addressing you.", ("profile", "identity"), "customize"),
    SettingEntry("voice", "identity", "Assistant voice", "Choose the voice used for spoken responses.", ("speech", "tts"), "customize"),
    SettingEntry("microphone", "audio", "Microphone", "Select and test the speech input device.", ("input", "recording"), "audio"),
    SettingEntry("speaker", "audio", "Speaker and output volume", "Select and test the speech output device and volume.", ("output", "volume"), "audio"),
    SettingEntry("wake_word", "audio", "Wake word", "Enable or disable wake phrase listening.", ("activation", "listen"), "audio"),
    SettingEntry("push_to_talk", "audio", "Push to talk", "Use the configured keyboard chord to activate listening.", ("ptt", "keyboard"), "audio"),
    SettingEntry("theme", "appearance", "Theme and accent color", "Choose a color preset or customize the accent.", ("black", "white", "color", "palette"), "customize"),
    SettingEntry("opacity", "appearance", "Interface opacity", "Adjust the main window opacity.", ("glass", "transparency"), "customize"),
    SettingEntry("hud_style", "appearance", "HUD centrepiece", "Choose the reactor core or orbital network shown in the main HUD.", ("core", "orbital", "reactor", "orb", "centrepiece"), "customize"),
    SettingEntry("panel_visibility", "workspace", "Panel visibility", "Show or hide the project explorer, chat, results and debug panels.", ("splitter", "layout"), "layout"),
    SettingEntry("workspace_folder", "workspace", "Project folder", "Choose the active folder for project files and new terminal sessions.", ("root folder", "project path"), "layout"),
    SettingEntry("project_open_mode", "workspace", "Open text files", "Choose the built-in editor or the system default application.", ("external editor", "file opener"), "layout"),
    SettingEntry("cursor_actions", "cursor", "Cursor quick actions", "Configure cursor assistant actions and shortcuts.", ("pointer", "radial menu", "hotkey"), "controls"),
    SettingEntry("computer_permissions", "permissions", "Computer permissions", "Review what JARVIS may inspect or control when Agent mode is active.", ("screen capture", "computer use"), "controls"),
    SettingEntry("computer_mode", "permissions", "Chat or Agent mode", "Chat is read-only. Agent can use the computer according to the permissions below.", ("chat", "agent", "computer control"), "security_info"),
    SettingEntry("computer_access_level", "permissions", "Approval policy", "Confirm every action, use enabled permissions, or temporarily allow full access.", ("ask", "approve", "full access", "permissions"), "security_info"),
    SettingEntry("thinking", "audio", "Model thinking", "Allow the Live model additional reasoning before responding.", ("reasoning", "latency"), "audio"),
    SettingEntry("proactive_audio", "audio", "Proactive audio", "Allow the model to detect speech not addressed to JARVIS.", ("ambient", "room audio"), "audio"),
    SettingEntry("media_resolution", "audio", "Screen and camera detail", "Control visual detail sent with screen captures and camera frames.", ("vision", "image quality"), "audio"),
    SettingEntry("turn_tuning", "audio", "Turn tuning", "Adjust speech turn detection, silence duration, prefix and end sensitivity.", ("vad", "speech detection", "silence", "prefix"), "audio"),
    SettingEntry("providers", "integrations", "AI mode", "Select Online or Offline, then configure voice recognition, the AI model and spoken voice.", ("engine", "model", "llm", "stt", "tts", "api key"), "providers"),
    SettingEntry("plugins", "plugins", "Installed plugins", "Enable or disable installed plugins.", ("extension", "integration"), "plugins"),
    SettingEntry("plugin_system", "plugins", "Plugin system", "Globally enable or disable the optional plugin runtime. Core functionality does not depend on plugins.", ("optional extensions", "plugin runtime", "restart"), "plugins"),
    SettingEntry("plugin_config", "plugins", "Plugin configuration", "Edit plugin-specific settings and run plugin tests.", ("extension settings",), "plugin_settings"),
    SettingEntry("saved_memory", "memory", "Saved facts", "Review facts JARVIS has saved. Conversation history is managed separately.", ("facts", "long term"), "memory"),
    SettingEntry("conversation_memory", "conversations", "Conversation memory", "Opt in to local transcript storage, set retention, and delete stored history.", ("recall", "transcripts", "history", "privacy", "retention"), "conversations"),
    SettingEntry("presence", "automation", "Presence awareness", "Configure away detection, return greetings and notification handling.", ("idle", "away", "lock screen", "welcome back"), "automation"),
    SettingEntry("routines", "automation", "Routines", "Manage schedules, recurring tasks, and quiet hours.", ("schedule", "reminder", "automation", "quiet hours"), "automation"),
    SettingEntry("presence_away_threshold", "automation", "Away threshold", "Set how long JARVIS waits without input before marking you away.", ("idle", "minutes", "absence"), "automation"),
    SettingEntry("presence_greeting", "automation", "Return greeting", "Enable or disable a greeting and queued notification digest when you return.", ("welcome back", "notifications", "digest"), "automation"),
    SettingEntry("quiet_hours", "automation", "Quiet hours", "Defer routine notifications during a configured time window.", ("notifications", "silence", "night"), "automation"),

    SettingEntry("auth", "permissions", "Application authentication", "Passcode and face verification are supplied by the configured lock integration.", ("pin", "passphrase", "face unlock", "camera"), "security_info"),
    SettingEntry("developer_mode", "developer", "Developer mode", "Enable detailed diagnostics and optional testing tools after confirmation.", ("debug", "logs"), "console"),
    SettingEntry("updates", "updates", "Update preferences", "Check, download, and install application updates.", ("maintenance", "version"), "updates"),
)


def search_entries(query: str) -> list[SettingEntry]:
    terms = tuple(part.casefold() for part in query.split() if part)
    if not terms:
        return []
    categories = {category.id: category.title.casefold() for category in CATEGORIES}
    results = []
    for entry in ENTRIES:
        haystack = " ".join(
            (entry.title, entry.description, categories.get(entry.category_id, ""), *entry.keywords)
        ).casefold()
        if all(term in haystack for term in terms):
            results.append(entry)
    return results
