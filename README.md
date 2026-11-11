# JARVIS

![JARVIS](assets/images/banner.png)

[![Tests](https://github.com/RustamovAkrom/JARVIS/actions/workflows/tests.yml/badge.svg?branch=v2)](https://github.com/RustamovAkrom/JARVIS/actions/workflows/tests.yml?query=branch%3Av2)

JARVIS is a desktop AI assistant built around voice, a customizable HUD, and practical tools for everyday computer work. It combines Gemini Live with an optional, configurable speech and model pipeline, local memory, project-aware file access, and extensions that can be enabled when needed.

The interface includes an animated orbital display, system telemetry, conversation and task panels, a project browser and editor, a terminal, and optional cursor and screen tools.

## At a glance

- **Talk or type:** speak with JARVIS in a live session, use typed messages, or attach files and screenshots.
- **Choose how AI runs:** use Gemini Live or configure separate speech-to-text, language-model, and text-to-speech providers, including local services.
- **Work with a project:** select a workspace, ask JARVIS to inspect its files, edit text in the built-in editor, and open a project-scoped terminal.
- **Use computer tools deliberately:** enable optional screen and mouse/keyboard abilities from Controls; disabled abilities are not available to the assistant.
- **Make it yours:** choose the interface language, accent, opacity, voice, cursor appearance, and shortcuts.
- **Extend JARVIS:** turn optional plugins on or off from the interface.

## Capabilities

### Conversation, speech, and models

- Gemini Live supports spoken conversation, streamed audio, and tool use in one session. Typed input and file attachments are also available.
- Push-to-talk and the local wake-word feature are optional.
- The alternate provider pipeline can combine a speech recognizer, a language model, and a speech synthesizer. Providers can be local or hosted; the three stages are configured independently.
- Eligible background requests can use a separately selected provider and model.
- Assistant memory stores selected facts and session summaries locally and can retrieve relevant entries when asked.

### Computer, browser, and project tools

Built-in actions cover web search, browser control, opening applications, file processing, reminders, desktop and window actions, selected operating-system settings, weather and flight information, messaging, and coding assistance. The exact actions available depend on the operating system, configured services, and installed applications.

JARVIS can inspect files in the folder selected in **PROJECT**. The project-file tool is read-only and restricted to that selected workspace. Text files can be opened in the built-in editor or in the system's default application. New terminal sessions start in the selected project folder, or the home folder if no project is selected.

### Screen, cursor, and computer permissions

- Attach a screenshot or select a screen region for a question about visible content.
- A clipboard action card can offer operations on copied text; the text is sent only after an action is selected.
- The cursor assistant has configurable appearance, size, shortcut, and quick actions.
- Computer permissions are separate switches. Screen capture is available by default; interface discovery, mouse movement, clicking or dragging, and keyboard input require explicit permission.
- Some consequential actions request confirmation. Availability and behavior vary by platform.

### HUD and system information

- The HUD keeps JARVIS's animated orbital core and responds to voice activity and cursor movement.
- The system panel displays CPU, memory, network, GPU, and temperature readings where available, plus recent CPU and memory history.
- GPU and temperature sensors can report **N/A** on Windows or on systems without a supported sensor.
- Conversation, archive, tasks, activity log, content, and quiz views are integrated into the desktop interface.
- Accent color, window opacity, assistant voice, and interface language can be customized.

### Reminders and background activity

- Create and manage reminders and local tasks.
- Optional morning brief, wake word, and push-to-talk controls are available in the interface.
- Topic monitoring and context-aware check-ins can run when enabled.
- Read-only background research can continue while JARVIS is being used and delivers its report in the content panel.

### Remote dashboard

The optional dashboard provides a browser-based view and remote controls for a paired device. It runs locally and can be configured for HTTPS. When exposing it beyond the computer, enable HTTPS and use the pairing controls; do not share dashboard credentials or pairing codes.

## Plugins

Plugins are optional extensions discovered from `plugins/`. Manage them in **SETUP → PLUGINS** and configure supported integrations in **PLUGIN SETTINGS**. A plugin may require an account, API key, companion application, or additional operating-system permissions.

| Plugin | What it adds |
|---|---|
| Battery dashboard | Battery status and history |
| Calorie counter | AI-assisted food and calorie estimates from an image |
| Chat assistance | Optional help with the currently open chat |
| Disk dashboard | Storage overview and selected cache maintenance |
| Document review | Document explanation and structured review |
| Excel writer | Spreadsheet creation and editing |
| Game updater | Game-library discovery and selected install/update tasks |
| God's Eye | Visual assistance and screen-oriented tools |
| Network dashboard | Network status and diagnostics |
| Obsidian vault | Search and work with a configured notes vault |
| OSINT toolkit | Public-source lookup utilities |
| Pomodoro | Focus and break sessions |
| Quiz | Interactive quizzes in the JARVIS interface |
| Spotify control | Control a configured Spotify account |
| System dashboard | Live system diagnostics |
| Telegram remote | Optional remote assistant controls through a configured bot |
| Trading agent | Market quotes and analysis tools |
| Water reminder | Local water-intake tracking and reminders |
| Weather dashboard | Weather information and forecast panels |

Plugins run as Python code with the current user's permissions; they are not sandboxed. Enable only extensions you trust and review their required access before use.

## Quick start

### Requirements

- Python 3.14 or newer. Python 3.14 is the version currently tested by the project.
- Windows, Linux, or macOS. Individual computer-control features vary by platform.
- A Gemini API key for the default Gemini Live experience.
- Internet access for package installation, hosted AI providers, and online integrations.

### Install

The setup script detects Windows, Linux, or macOS, installs the Python packages from `requirements.txt`, and downloads the Chromium and Firefox runtimes used by browser automation. Platform markers in the requirements file keep operating-system-specific Python packages off other platforms.

**Windows (PowerShell):**

```powershell
git clone https://github.com/RustamovAkrom/JARVIS.git
cd JARVIS
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe setup.py
.\.venv\Scripts\python.exe main.py
```

**Linux or macOS:**

```bash
git clone https://github.com/RustamovAkrom/JARVIS.git
cd JARVIS
python3 -m venv .venv
.venv/bin/python setup.py
.venv/bin/python main.py
```

The installer handles Python packages and browser runtimes. Some Linux desktop actions also rely on native utilities supplied by the distribution; the setup script prints guidance for those optional integrations rather than installing system packages with elevated privileges.

### Optional: install with uv

If you already use [uv](https://docs.astral.sh/uv/) or want its fast, lockfile-based installation, install uv by following the [official installation guide](https://docs.astral.sh/uv/getting-started/installation/). From the project directory, run:

```bash
uv sync --no-install-project
uv run --no-sync python -m playwright install chromium firefox
uv run --no-sync python main.py
```

uv installs the dependencies declared in `pyproject.toml` using `uv.lock`. The `--no-install-project` option installs dependencies without trying to package JARVIS as a Python library. The Playwright command downloads the browser runtimes used by browser automation. See the [uv project guide](https://docs.astral.sh/uv/guides/projects/) and [environment syncing guide](https://docs.astral.sh/uv/concepts/projects/sync/) for details.

On first launch, follow the setup screen to choose a language and configure the Gemini API key. Keep the key private. To use a different speech or model provider, open **SETUP → ENGINE & PROVIDERS** after startup.

### Browser runtime recovery

If the browser download is interrupted, the assistant can still run without browser automation. Retry it from the project environment:

```bash
python -m playwright install chromium firefox
```

On Windows, use `.venv\Scripts\python.exe -m playwright install chromium firefox`.

## Interface and controls

- **SETUP** contains provider, audio, integration, and assistant settings.
- **CONTROLS** contains language, everyday behavior, cursor-assistant options, and computer permissions. Russian, Uzbek, and English are available for the interface.
- **PROJECT** lets you select a workspace, browse its files, and choose whether text files open in the JARVIS editor or the system default application.
- **TERMINAL** opens on demand. New sessions use the selected workspace when one is set.
- The side panels can be shown or hidden independently. Hover over controls for concise descriptions.

Useful shortcuts: `F4` toggles the microphone, `Escape` interrupts a response, `F11` toggles full screen, `Ctrl+K` opens the command palette, `Ctrl+Shift+Space` opens quick actions, and `Ctrl+Alt+R` starts region selection. Global shortcut availability depends on the operating system and desktop session.

On Windows, `Win+Shift+S` is also available for selecting a region with the system capture tool. Return to JARVIS and press `Ctrl+V` to attach the image.

## Models and providers

Gemini Live is the default conversation mode. It needs a valid API key and network access. The optional pipeline lets you select separate providers for:

| Stage | Available options |
|---|---|
| Speech-to-text | Local Whisper or a compatible hosted transcription service |
| Language model | Local OpenAI-compatible servers and supported hosted providers |
| Text-to-speech | System voices, local Piper, or a configured hosted voice service |

Local services such as Ollama, LM Studio, llama.cpp servers, vLLM, and Jan must be installed and started separately. Configure the endpoint and model in **ENGINE & PROVIDERS**, then use the available connection test before applying changes.

A local language model does not make the entire assistant offline: web search, hosted providers, remote integrations, and any feature that sends content to an external service still require network access. Check the provider selected for each feature before sharing sensitive material.

Local Whisper and Piper backends are optional downloads and can be installed from the provider setup instead of being included in the base environment.

More details: [Gemini configuration](docs/gemini.md) · [Local and compatible models](docs/local-llm.md) · [Plugin guide](docs/plugins.md)


## Project structure

The repository is organized by responsibility. These are the main entry points and modules; runtime state and generated settings are created locally and are not part of the source tree.

```text
JARVIS/
|-- .github/workflows/tests.yml    Cross-platform GitHub Actions test job
|-- actions/                       Tools exposed to the assistant
|   |-- background_tasks.py        Background research task commands
|   |-- browser_control.py         Browser sessions and page interaction
|   |-- computer_control.py        Permission-gated screen and input tools
|   |-- file_controller.py         File and folder operations
|   |-- reminder.py                Reminder scheduling and delivery
|   |-- web_search.py              Search and research tools
|   `-- workspace_files.py         Read-only access to the selected project
|-- assets/                        Fonts, icons, images, and bundled terminal UI
|-- config/                        App identity and local, ignored user settings
|-- core/                          Runtime services and tool/plugin loading
|   |-- background_tasks.py        Persistent background task manager
|   |-- gemini.py                  Gemini Live session handling
|   |-- confirm.py                 Confirmation for consequential actions
|   |-- outcome.py                 Structured tool results and trust boundaries
|   `-- platform_support.py        OS capability checks
|-- dashboard/                     Local web dashboard and paired-device API
|-- docs/                           Setup guides and plugin documentation
|-- memory/                         Settings, translations, and assistant memory
|   |-- config_manager.py           Settings validation and persistence
|   |-- memory_manager.py           Long-term memory storage and retrieval
|   `-- translations.py             English, Russian, and Uzbek UI strings
|-- plugins/                        Optional integrations and dashboards
|-- providers/                      Optional STT -> LLM -> TTS pipeline
|-- tests/                          Unit tests for app behavior and UI
|-- main.py                         Application startup and assistant session
|-- ui.py                           PyQt desktop UI, HUD, editor, and terminal
|-- setup.py                        Dependency and browser-runtime installer
|-- requirements.txt                Install requirements and platform markers
`-- pyproject.toml                  Python project metadata
```

`actions/` modules provide tools to the assistant. `plugins/` contains optional extensions managed in the UI. The `providers/` pipeline is an alternative to Gemini Live and is configured separately. Local credentials, history, and generated state belong to the running installation; do not add them to commits.

## Your data

- **`config/api_keys.json`:** the Gemini key, provider credentials, language, appearance, permissions, and other settings.
- **`config/ui_chat.json` and `config/ui_history.json`:** recent conversation and archived turns.
- **`config/ui_tasks.json` and `config/ui_reminders.json`:** tasks and reminders created in the interface.
- **`memory/long_term.json`:** facts and summaries retained by JARVIS.
- **`~/.jarvis/background_tasks.json`:** background research task status and reports.
- **`~/.jarvis/reminders/`:** operating-system reminder state and scheduling files.
- **`~/.jarvis_profiles/`:** browser profiles created for JARVIS-controlled sessions.

Most settings and memory are currently stored alongside the project, in `config/` and `memory/`; background-task and reminder files use the home directory. Back up these files before reinstalling or moving the project. Never commit API keys, bot tokens, private conversation history, or personal project data. Content sent to a hosted model or integration leaves the computer and is governed by that service's policies.

## Development

Run the test suite from the project environment:

```bash
python -m unittest discover -s tests
```

On Windows, run `.venv\Scripts\python.exe -m unittest discover -s tests`. Tests that create Qt widgets use the offscreen platform when configured by the test environment.

Actions are discovered from `actions/`; plugins declare a schema and handler in a module under `plugins/`. Plugin settings are optional, and plugin failures are reported in the interface. See [docs/plugins.md](docs/plugins.md) before adding an extension.

## Support

- **Telegram:** [@Akrom_Rustamov](https://t.me/Akrom_Rustamov)
- **Portfolio:** [akrom-rustamov.vercel.app](https://akrom-rustamov.vercel.app/)

When reporting a problem, include your operating system, Python version, and steps to reproduce it. Remove keys, tokens, private file contents, and personal data from logs and screenshots.
