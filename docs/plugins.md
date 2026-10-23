# JARVIS plugins: catalogue and development

[Русская версия](plugins-ru.md) · [Main documentation](../README.md)

A plugin is an optional tool or dashboard loaded from `plugins/`. Open its manager through **SETUP → PLUGINS**.

## Enable, disable, and remove

- The **ON/OFF** switch is stored in the `plugins_enabled` object in `config/api_keys.json`.
- Discovery happens at startup. Toggling a discovered plugin takes effect without restarting; a disabled plugin is no longer offered to the model.
- To remove a feature, delete its `.py` file from `plugins/`. Check imports first: `plugins/_*.py` files are shared helpers used by multiple plugins.
- Saved plugin configuration may remain in JSON after code removal. Remove it separately only after backing up the file and checking the namespace.
- **PLUGIN SETTINGS** renders fields declared through `PLUGIN_SETTINGS`; a TEST button appears only when the plugin declares a test action.
- Import failures are shown as **BROKEN** with a reason.

Plugins run with the current user's permissions. They are not sandboxed: importing a module executes Python code. Use only plugins you trust.

## Plugins in this repository

| Module | Purpose | Requirements and notes |
|---|---|---|
| `battery_dashboard.py` | Battery dashboard | Useful on devices with a battery. |
| `calorie_counter.py` | Food and calorie estimates from an image | Uses AI; check the selected endpoint and image transfer. |
| `chat_takeover.py` | Assistance with the open chat and replies | Can inspect the screen and send text; enable deliberately. |
| `disk_dashboard.py` | Disk usage and maintenance | Check the target before clearing a cache. |
| `document_review.py` | Document explanation and structured review | Contents may be sent to the selected AI. |
| `excel_writer.py` | Create/edit Excel workbooks | Check the path and output before replacing important files. |
| `game_updater.py` | Steam/Epic game management | Requires the clients; installation and updates change files. |
| `network_dashboard.py` | Network status and diagnostics | Depends on OS support and system utilities. |
| `obsidian_vault.py` | Search, read, and write Obsidian notes | Configure a vault; writes change notes. |
| `osint_toolkit.py` | Public-source search | Follow laws, privacy expectations, and source policies. |
| `pomodoro.py` | Focus sessions and breaks | State is stored locally. |
| `quiz.py` | Interactive quiz | Displayed in the HUD. |
| `spotify_control.py` | Spotify control | Requires Spotify API credentials and authorisation. |
| `system_dashboard.py` | System dashboard | Metrics depend on OS and libraries. |
| `telegram_remote.py` | Remote commands through Telegram | Requires bot/access setup; treat the token as a secret. |
| `trading_agent.py` | Quotes, news, and market analysis | Data may be delayed; not financial advice. |
| `water_reminder.py` | Water tracking and reminders | Not medical advice. |
| `weather_dashboard.py` | Weather by location | Requires network access and a data source. |

## Plugin contract

Create a Python module in `plugins/`. Files prefixed with `_` are reserved for shared helpers. Minimal declaration:

```python
PLUGIN = {
    "name": "example_lookup",
    "description": "Searches the configured catalogue.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "query": {"type": "STRING", "description": "Text to find"}
        },
        "required": ["query"],
    },
}

def run(parameters, player=None, session_memory=None):
    return f"Looked up: {parameters.get('query', '')}"
```

`name` must be a unique Python identifier of at most 64 characters; `description` must be non-empty; `parameters` must be an `OBJECT` schema understood by the selected engine. `run` must be synchronous and return a concise result. The loader passes `player` and `session_memory` only when declared in the signature (or both with `**kwargs`). Exceptions are caught and logged. Avoid blocking the conversation loop; use project background mechanisms and timeouts for long operations.

Optional `PLUGIN` fields: `behavior` (`BLOCKING` or `NON_BLOCKING`), `scheduling` (`WHEN_IDLE`, `SILENT`, `INTERRUPT`), `tier` (`core`/`extra`), and a short `brief`. The loader validates these values; unknown values may be ignored.

## Settings form

Declare `PLUGIN_SETTINGS` to render a form in **PLUGIN SETTINGS**:

```python
PLUGIN_SETTINGS = {
    "namespace": "example_service",
    "title": "Example service",
    "fields": [
        {"key": "api_key", "label": "API key", "type": "password", "default": ""},
        {"key": "base_url", "label": "Server URL", "type": "text",
         "default": "https://example.invalid"},
    ],
    # Optional: {"label": "TEST CONNECTION", "run": callable}
}
```

Use `plugins/spotify_control.py`, `plugins/obsidian_vault.py`, and `plugins/telegram_remote.py` as examples. Check the UI implementation for supported field types and TEST format. Store credentials through configuration and never print them to logs.

## Verification and structure

Check imports, unique name, schema, invalid input, timeouts, success, and service failure. Restart the app and inspect the **PLUGINS** row, settings form, and TEST action. Python changes are not hot-reloaded.

```text
plugins/                  plugins
plugins/_*.py             shared helpers
core/plugin_loader.py     discovery, validation, execution
memory/config_manager.py  settings and enable state
ui.py                     manager and settings forms
```
