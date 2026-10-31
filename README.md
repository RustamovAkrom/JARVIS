# JARVIS / MARK LIV

[Документация на русском](README_ru.md) · [Plugins and development](docs/plugins.md) · [Gemini](docs/gemini.md) · [Local and third-party models](docs/local-llm.md)

JARVIS is a Python and PyQt6 desktop voice assistant. It brings together spoken conversation, computer tools, local memory, and a HUD. Gemini Live is the default engine; an optional pipeline combines selected providers and can run fully locally.

> The current code calls this build MARK LIV. JARVIS is also used in the interface and project history.

## Contents

- [Capabilities](#capabilities)
- [Install](#install)
- [Interface and controls](#interface-and-controls)
- [Settings](#settings)
- [Models and AI](#models-and-ai)
- [Actions and plugins](#actions-and-plugins)
- [Data and privacy](#data-and-privacy)
- [For developers](#for-developers)
- [Troubleshooting](#troubleshooting)

## Capabilities

**Voice and conversation:** Gemini Live combines microphone input, speech recognition, spoken responses, and tool calls in one session. The UI also supports typed messages and file attachments. Push-to-talk and the local “Hey Jarvis” wake word are optional switches.

**Computer tools:** built-in actions in `actions/` open applications and websites, search the web, process files, control windows and selected OS settings, manage reminders, play video, and use screen or camera context. Availability depends on OS, permissions, and installed software. The app may ask before irreversible operations; some reversible operations support undo.

**HUD and memory:** animated avatar with speech synchronisation, CPU/memory/network and available sensor metrics, conversation/task/history/content/quiz panels, local long-term memory, mini window, appearance controls, and command palette.

**Integrations:** optional dashboards and tools are loaded as plugins. The repository includes Obsidian, Spotify, and Telegram integrations; system/disk/network/battery/weather dashboards; and other plugins. Features may require credentials, authorisation, third-party apps, or internet access.

## Install

The project requires Python 3.14 or newer. Audio and multimedia dependencies can make installation take a while.

```powershell
git clone https://github.com/RustamovAkrom/JARVIS.git
cd JARVIS
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python setup.py
python main.py
```

If PowerShell blocks activation, run `.venv\Scripts\python.exe setup.py`, then `.venv\Scripts\python.exe main.py`. On macOS/Linux, use `source .venv/bin/activate`, followed by `python setup.py` and `python main.py`. The installer prints additional Linux system dependencies when needed.

On first launch, enter a Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey) in the setup window and confirm the detected OS. The key and settings are stored in `config/api_keys.json`. Do not publish or commit this file.

The setup script installs `requirements.txt` and Playwright Chromium/Firefox browsers. If the browser download fails, it prints a retry command; the rest of the app can run without browser automation.

## Interface and controls

- The centre shows the avatar or active media. System metrics are on the left; `L`, `R`, and `DOC` hide the corresponding panels.
- Conversation and tasks are on the right. Type a message or use the microphone. Attach a file and describe what to do with it.
- **SETUP** (`⚙`) contains configuration and integrations. **CONTROLS** (`🎛`) contains everyday switches.
- **PLUGINS** enables/disables plugins; **PLUGIN SETTINGS** configures their integrations.
- **ENGINE & PROVIDERS** switches between Gemini Live and the alternate STT/LLM/TTS pipeline.

Shortcuts: `F4` toggles microphone, `Escape` interrupts a response, `F11` toggles full screen, `Ctrl+K` opens the command palette, and `Ctrl+Shift+Space` opens the mini window. Available buttons can vary slightly by OS.

## Settings

Prefer the UI over editing JSON by hand: it validates fields and stores the format expected by the current code. Most settings live in `config/api_keys.json`.

Depending on the build, **SETUP** configures the Gemini key and assistant name, HUD theme/colour, audio devices, providers, plugins, and OS-specific integrations. **CONTROLS** includes the morning brief, wake word, sleep/wake, push-to-talk, and behaviour options.

Additional keys recognised by the current code include `media_resolution` (`low`, `medium`, `high`, `default`), `thinking_enabled`, `proactive_audio`, the `turn_tuning` object, `plugins_enabled`, and `providers`. The exact format is defined by `memory/config_manager.py` and `providers/`. Back up the file before manual edits. Example shape only; do not replace your file with it:

```json
{
  "gemini_api_key": "<your key>",
  "plugins_enabled": {"pomodoro": true, "trading_agent": false}
}
```

## Models and AI

Open **SETUP → ENGINE & PROVIDERS**.

### Gemini Live

The default mode. It requires a Gemini API key and internet access. The model handles conversation, audio, and tool calls in one session. Model access depends on your Google project and can change.

### MY OWN SETUP: STT → LLM → TTS pipeline

The three independent parts can be mixed:

| Part | Implementations | Notes |
|---|---|---|
| STT — speech to text | Local faster-whisper; remote OpenAI-compatible transcription endpoint | Local Whisper defaults to CPU and model `base`; the model loads on first use. |
| LLM — responses and tools | Ollama, LM Studio, llama.cpp server, vLLM, Jan; OpenAI and compatible services including Groq, DeepSeek, Mistral, xAI, OpenRouter, Together, Cerebras | Requires Base URL, model ID, and sometimes a key. The model list may be fetched from the server. |
| TTS — text to speech | Local Piper; OS voices; ElevenLabs | Piper may download a voice model. ElevenLabs requires a key and internet access. |

For a local LLM, start its server separately, choose the matching provider, check the URL and model ID, use TEST if available, then **SAVE & APPLY**. Preset addresses: Ollama `127.0.0.1:11434/v1`, LM Studio `:1234/v1`, llama.cpp `:8080/v1`, vLLM `:8000/v1`, Jan `:1337/v1`.

For another hosted LLM, select a vendor and enter the model, URL, and key. A custom service must be compatible with OpenAI Chat Completions and the tool-calling format in use; a proprietary incompatible API needs a dedicated adapter.

An optional **sidecar** is a separate model for short background requests. It does not replace the main conversational model. Some local models use a reduced tool budget; advanced options are under **MORE**.

A fully local pipeline requires local STT, LLM, and TTS. A local LLM alone does not make search, cloud STT/TTS, plugins, or network actions offline. The pipeline is usually slower than Gemini Live, and tool support depends on the model and engine. See [local and third-party models](docs/local-llm.md) and [Gemini](docs/gemini.md).

## Actions and plugins

**Actions** in `actions/` are core computer tools. They are discovered at startup; there is no individual action toggle in the UI. Some depend on the OS.

**Plugins** in `plugins/` are optional tools and dashboards. Toggle them in **PLUGINS** and back on without restarting. A disabled plugin is no longer offered to the model. You can delete an unneeded plugin file; some modules share `plugins/_*.py` helpers, so check dependencies first. The [plugin guide](docs/plugins.md) lists the current modules and explains removal.

The current folder includes battery, disk, network, system, and weather dashboards; image-based calorie estimation, document review, chat assistance, and OSINT; Excel, game, Hermes Agent, Obsidian, Spotify, and Telegram integrations; Pomodoro, quizzes, market information, and water reminders. Some require external accounts or keys. Plugins execute trusted Python code with the user’s permissions; they are not sandboxed.

## Data and privacy

- `config/api_keys.json` stores keys and settings locally. Treat it as a secret.
- `memory/` stores long-term memory and feature state; `config/` also contains UI history, tasks, and reminders.
- Cloud providers receive the prompts and context sent to them. Screen, camera, microphone, files, and tool results may be sent to an AI/service depending on the enabled feature.
- For local processing, check all three STT/LLM/TTS slots and disable cloud plugins you do not need. A local model alone does not guarantee every feature is local.

## For developers

```text
main.py                  startup and conversation loop
ui.py                    PyQt6 HUD, panels, and settings
actions/                 system tools
plugins/                 optional dynamically discovered tools
core/                    Gemini, audio, confirmation, loaders, OS helpers
providers/               STT/LLM/TTS contracts and alternate engine
memory/config_manager.py settings persistence
dashboard/               web dashboard and HTTP integrations
```

Add a plugin as a module in `plugins/` with a `PLUGIN` declaration (`name`, `description`, `parameters` of type `OBJECT`) and a `run(parameters, ...)` function. Optional `PLUGIN_SETTINGS` renders a settings form; the loader passes `player` and `session_memory` only when declared in the function signature. Discovery happens at startup, while toggling a discovered plugin applies immediately. Adding a file or changing code requires a restart. See the [developer guide](docs/plugins.md) for the contract and examples.

Project strengths include separation of UI, tools, integrations, and AI providers; declarative tool schemas; configurable plugins; a shared event contract for the alternate engine; and a default Gemini path that does not depend on enabling the `providers/` package. Actual OS support is feature-specific.

## Troubleshooting

- **App does not start:** use a supported Python version and the active `.venv`.
- **Gemini does not answer:** check the key, model access, and network; enable development mode for diagnostics.
- **No audio:** check OS permissions, **AUDIO DEVICES**, and selected STT/TTS.
- **Local server is unavailable:** start it separately and check Base URL, model ID, and API compatibility.
- **Plugin is BROKEN:** read its **PLUGINS** entry; dependencies, shared helpers, or authorisation may be missing.
- **Browser automation fails:** run `python -m playwright install chromium firefox`.

When filing an issue, include the OS, Python version, and reproduction steps. Remove keys, tokens, personal data, and private file contents from logs.
