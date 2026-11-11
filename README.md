# JARVIS

JARVIS is a desktop AI assistant built around voice, a customizable HUD, and practical tools for everyday computer work. It combines Gemini Live with an optional, configurable speech and model pipeline, local memory, project-aware file access, and extensions that can be enabled when needed.

The interface is designed as a working assistant, not a chat window alone: it includes an animated orbital display, system telemetry, conversation and task panels, a project browser and editor, a terminal, and optional cursor and screen tools. The HUD uses the orbital core; a face-based HUD is not part of the current interface.

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
- A separate provider can be configured for eligible background requests. Model selection is user-configured; JARVIS does not claim an automatic model ladder.
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

### Application

| Path | Purpose |
|---|---|
| `main.py` | Application entry point, assistant session, audio lifecycle, tool registration, and background integrations |
| `ui.py` | PyQt desktop window, orbital HUD, panels, controls, editor, terminal, and overlays |
| `setup.py` | Platform-aware dependency and browser-runtime installer |
| `requirements.txt` | Python dependency list with platform markers |
| `pyproject.toml` | Python project metadata and environment requirements |
| `uv.lock` | Locked environment resolution for uv |
| `.python-version` | Preferred Python interpreter version |
| `.gitignore` | Excludes local environments, credentials, and generated state |
| `dev.py` | Development launcher entry point |
| `LICENSE` | Project license |
| `config/` | Local settings, credentials, UI history, tasks, and reminders |

### `actions/` — assistant tools

| File | Purpose |
|---|---|
| `background_monitor.py` | User-configured topic monitoring |
| `background_tasks.py` | Tool interface for background research tasks |
| `browser_control.py` | Browser navigation and page interaction |
| `code_helper.py` | Coding and debugging assistance |
| `computer_control.py` | Permission-gated screen, mouse, and keyboard actions |
| `computer_settings.py` | Supported operating-system settings |
| `desktop.py` | Desktop and window operations |
| `dev_agent.py` | Project-oriented development tasks |
| `file_controller.py` | File and folder operations |
| `file_processor.py` | Read and process supported document and media formats |
| `flight_finder.py` | Flight search |
| `open_app.py` | Launch desktop applications |
| `proactive.py` | Context-aware check-in scheduling logic |
| `reminder.py` | Reminder creation and delivery |
| `screen_processor.py` | Analyze user-provided screen images and selected regions |
| `send_message.py` | Supported message-sending integrations |
| `system_monitor.py` | System status and threshold alerts |
| `video_player.py` | HUD media playback controls |
| `weather_report.py` | Weather lookup |
| `web_search.py` | Web search and research lookup |
| `workspace_files.py` | Read-only access to the selected project workspace |

### `core/` — runtime services

| File | Purpose |
|---|---|
| `action_loader.py` | Discover and validate action tools |
| `appidentity.py` | Cross-platform application identification and activation helpers |
| `audio_devices.py` | Audio device discovery and selection |
| `avatar.py` | Legacy animated-figure renderer; not connected to the active HUD |
| `avatar_mesh.py` | Legacy mesh loader; the active HUD uses the orbital display instead |
| `background_tasks.py` | Persistent background research task manager |
| `confirm.py` | Confirmation flow for consequential actions |
| `echo.py` | Audio echo handling |
| `gemini.py` | Gemini Live client and event handling |
| `hotkey.py` | Shortcut registration and platform handling |
| `installer.py` | In-app optional dependency installation |
| `lazyimport.py` | Deferred optional imports |
| `llm_client.py` | Language-model client helpers |
| `outcome.py` | Structured tool outcomes and content-trust boundaries |
| `platform_support.py` | Operating-system and desktop capability checks |
| `plugin_loader.py` | Discover and manage optional plugins |
| `promt.txt` | Assistant system-prompt template |
| `stt.py` | Speech recognition interface |
| `tts.py` | Speech synthesis interface |
| `undo.py` | Reversal support for selected operations |
| `viseme.py` | Speech-synchronized visual timing |
| `wake_word.py` | Optional local wake-word support |

### `providers/` — alternate speech and model pipeline

| File | Purpose |
|---|---|
| `audio.py` | Audio conversion, resampling, and chunking |
| `base.py` | Provider interface and capability declarations |
| `engine_pipeline.py` | Connect configured speech, model, and voice stages |
| `llm_openai.py` | OpenAI-compatible language-model provider |
| `registry.py` | Provider discovery and registration |
| `schema.py` | Shared provider configuration schema |
| `stt_whisper.py` | Local Whisper speech recognition |
| `tts_elevenlabs.py` | Hosted ElevenLabs speech synthesis |
| `tts_piper.py` | Local Piper speech synthesis |
| `tts_system.py` | Operating-system speech voices |
| `_tests.py` | Provider and audio-pipeline tests |

### `memory/` — settings and local state

| File | Purpose |
|---|---|
| `__init__.py` | Package marker |
| `config_manager.py` | Read and validate assistant settings |
| `memory_manager.py` | Store, retrieve, and format long-term memory |
| `translations.py` | English, Russian, and Uzbek interface strings |
| `long_term.json` | Local long-term memory data |
| `pomodoro_state.json` | Local focus-session state |
| `water_state.json` | Local water-reminder state |

### `plugins/` — optional integrations

| File | Purpose |
|---|---|
| `_live_panels_core.py` | Shared components for live dashboard panels |
| `_telegram_ops.py` | Shared operations for the remote-control integration |
| `_template.py` | Example plugin structure |
| `battery_dashboard.py` | Battery status dashboard |
| `calorie_counter.py` | Image-based food estimate |
| `chat_takeover.py` | Optional open-chat assistance |
| `disk_dashboard.py` | Disk usage and cache tools |
| `document_review.py` | Document review panel |
| `excel_writer.py` | Spreadsheet creation and editing |
| `game_updater.py` | Game client and library operations |
| `gods_eye_plugin.py` | Visual assistance tools |
| `gods_eye_view.html` | Interface template for visual assistance |
| `network_dashboard.py` | Network diagnostics panel |
| `obsidian_vault.py` | Obsidian vault integration |
| `osint_toolkit.py` | Public-source research tools |
| `pomodoro.py` | Focus timer |
| `quiz.py` | Interactive quiz panel |
| `spotify_control.py` | Spotify integration |
| `system_dashboard.py` | System diagnostics panel |
| `telegram_remote.py` | Paired remote-control integration |
| `trading_agent.py` | Market information tools |
| `water_reminder.py` | Water tracking and reminders |
| `weather_dashboard.py` | Weather dashboard |

### `dashboard/`, `docs/`, and `tests/`

| Path | Purpose |
|---|---|
| `dashboard/server.py` | Local web dashboard and paired-device API |
| `dashboard/__init__.py` | Dashboard package marker |
| `providers/__init__.py` | Provider package marker |
| `plugins/__init__.py` | Plugin package marker |
| `tests/__init__.py` | Test package marker |
| `docs/gemini.md` | Gemini setup notes |
| `docs/gemini-ru.md` | Russian Gemini setup notes |
| `docs/local-llm.md` | Local and compatible model setup |
| `docs/local-llm-ru.md` | Russian local-model setup notes |
| `docs/plugins.md` | Plugin catalogue and development contract |
| `docs/plugins-ru.md` | Russian plugin guide |
| `tests/test_background_tasks.py` | Background research task tests |
| `tests/test_computer_control_permissions.py` | Computer permission tests |
| `tests/test_cursor_assistant_settings.py` | Cursor and settings tests |
| `tests/test_cursor_assistant_ui.py` | UI, editor, terminal, and interaction tests |
| `tests/test_screen_region_selector.py` | Screen selection tests |
| `tests/test_setup.py` | Platform detection and installer behavior tests |
| `tests/test_workspace_files.py` | Workspace boundary and file-reading tests |

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
