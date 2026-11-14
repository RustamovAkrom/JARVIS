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
    SettingsCategory("audio", "Voice & Audio", "Devices, voice activation, and microphone controls.", (("Audio devices", "audio"), ("Wake word and push-to-talk", "controls"))),
    SettingsCategory("appearance", "Appearance & Personalization", "Theme, accent color, opacity, and visual preferences.", (("Customize appearance", "customize"),)),
    SettingsCategory("workspace", "Workspace & Layout", "Panels and workspace surfaces.", (("Workspace panel controls", "layout"),)),
    SettingsCategory("cursor", "Cursor Assistant", "Cursor behavior, shortcuts, and quick actions.", (("Cursor assistant controls", "controls"),)),
    SettingsCategory("permissions", "Computer Permissions", "Choose which computer interaction capabilities are allowed.", (("Review computer permissions", "controls"),)),
    SettingsCategory("integrations", "Integrations & Providers", "AI engine, provider credentials, and request routing.", (("Engine and providers", "providers"),)),
    SettingsCategory("plugins", "Plugins", "Installed extensions and their settings.", (("Plugin manager", "plugins"), ("Plugin settings", "plugin_settings"))),
    SettingsCategory("memory", "Memory", "Review and manage saved assistant memories.", (("Open memory", "memory"),)),
    SettingsCategory("security", "Security & Access", "Authentication surfaces currently supplied by integrations.", (("Security information", "security_info"),)),
    SettingsCategory("developer", "Developer & Diagnostics", "Development mode, diagnostic logs, and system details.", (("Developer options", "setup"), ("Debug console", "console"))),
    SettingsCategory("updates", "Updates & Maintenance", "Application version and update operations.", (("Open updates", "updates"),)),
)


ENTRIES = (
    SettingEntry("app_language", "general", "Interface language", "Choose the language used by the JARVIS interface.", ("locale", "translation"), "controls"),
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
    SettingEntry("wake_word", "audio", "Wake word", "Enable or disable wake phrase listening.", ("activation", "listen"), "controls"),
    SettingEntry("push_to_talk", "audio", "Push to talk", "Use the configured keyboard chord to activate listening.", ("ptt", "keyboard"), "controls"),
    SettingEntry("theme", "appearance", "Theme and accent color", "Choose a color preset or customize the accent.", ("black", "white", "color", "palette"), "customize"),
    SettingEntry("opacity", "appearance", "Interface opacity", "Adjust the main window opacity.", ("glass", "transparency"), "customize"),
    SettingEntry("panel_visibility", "workspace", "Panel visibility", "Show or hide the left, right, and lower workspace panels.", ("splitter", "layout"), "layout"),
    SettingEntry("cursor_actions", "cursor", "Cursor quick actions", "Configure cursor assistant actions and shortcuts.", ("pointer", "radial menu", "hotkey"), "controls"),
    SettingEntry("computer_permissions", "permissions", "Computer permissions", "Review screen, mouse, and keyboard permissions.", ("screen capture", "computer use"), "controls"),
    SettingEntry("assistant_mode", "permissions", "Chat or Agent mode", "Choose whether JARVIS may run computer actions.", ("chat", "agent", "computer control"), "controls"),
    SettingEntry("approval_policy", "permissions", "Approval policy", "Ask before each action, respect allowed switches, or enable full computer access.", ("ask", "approve", "full access", "permissions"), "controls"),
    SettingEntry("providers", "integrations", "AI providers", "Configure Gemini Live, pipeline providers, and routing.", ("engine", "model", "llm", "stt", "tts", "api key"), "providers"),
    SettingEntry("plugins", "plugins", "Installed plugins", "Enable or disable installed plugins.", ("extension", "integration"), "plugins"),
    SettingEntry("plugin_config", "plugins", "Plugin configuration", "Edit plugin-specific settings and run plugin tests.", ("extension settings",), "plugin_settings"),
    SettingEntry("saved_memory", "memory", "Saved memories", "Copy or remove stored long-term memories.", ("facts", "long term"), "memory"),
    SettingEntry("auth", "security", "Application authentication", "Authentication is currently provided by a caller-supplied lock integration.", ("pin", "passphrase", "face unlock", "camera"), "security_info"),
    SettingEntry("developer_mode", "developer", "Developer mode", "Enable development diagnostics and console mirroring.", ("debug", "logs"), "console"),
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
