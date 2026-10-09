![banner](/assets/images/banner.png)
# JARVIS (v2)
[RUSSIAN DOCS](/README_ru.md)

> **Gemini setup, configured models, quotas, and security:** see the Russian
> operational guide [docs/gemini-ru.md](/docs/gemini-ru.md). It includes the
> current project model mapping and official Google links.

> **Local models:** the Russian guide [docs/local-llm-ru.md](/docs/local-llm-ru.md)
> covers the experimental Ollama/LM Studio client and its current limitations.

### The Real-Time Personal AI Assistant for Your Computer — By Akrom Rustamov

A real-time voice AI assistant that can hear, see, speak, remember, and control your computer. JARVIS (v2) is built around the Gemini Live API, a modular action system, a dynamic plugin architecture, persistent local memory, computer vision, real-time audio, and a PyQt6 HUD.

## ⚡ Start here: installation in minutes

You need Python 3.11+ and a Gemini API key. Create the key in
[Google AI Studio](https://aistudio.google.com/apikey), then run:

```powershell
git clone https://github.com/RustamovAkrom/JARVIS.git
cd JARVIS
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

On macOS/Linux, use `source .venv/bin/activate` after creating the environment.

On the first launch, the **INITIALISATION REQUIRED** window opens. Paste your key
into **GEMINI API KEY**, verify or select **Windows**, **macOS**, or **Linux**, and
click **INITIALISE SYSTEMS**. JARVIS stores the key and OS locally in
`config/api_keys.json`; no additional configuration is needed for your first
conversation.

---

## ✨ Overview

JARVIS (v2) is designed as a practical extension of your computer rather than a simple chatbot.

It combines real-time voice interaction with visual awareness, computer control, persistent memory, background tasks, plugins, live system telemetry, interactive content, remote control, and a custom PyQt6 HUD.

The central runtime stays separate from individual capabilities. Built-in computer actions are automatically discovered from `actions/`, while specialized capabilities can be added as standalone plugins in `plugins/`.

JARVIS can work with voice, keyboard input, screen context, webcam input, files, browser interaction, and remote commands while keeping the main interaction inside one assistant session.

It's not just a chatbot — it's a personal computer intelligence system.

---

## 🚀 Capabilities

### Core Features
| Feature | Description |
|---|---|
| 🧑‍🎤 Holographic Avatar | Software-rendered 3D-style facial avatar with real face geometry, lighting, expressions, and animated status states |
| 👄 Real Lip-Sync | Audio and transcript driven viseme system with multiple mouth shapes for natural speech animation |
| 🙂 Facial Acting | Animated blinking, gaze movement, brows, nods, and expression changes tied to assistant activity |
| 😐 Face as Status | The avatar changes its behavior while listening, thinking, speaking, sleeping, and displaying new content |
| 🎚️ Push-to-Talk | Global `Ctrl+Space` push-to-talk on Windows with platform-specific fallback behavior |
| 🔇 Self-Echo Guard | Detects and suppresses the assistant's own audio echo so JARVIS does not answer itself |
| 🪪 Runtime Self-Knowledge | JARVIS can build its identity, operating-system information, capabilities, and limits from the live runtime |
| 🎙️ Wake Word | Local `Hey Jarvis` wake-word detection with background processing |
| ⚡ Instant Acknowledgment | Gives a short spoken acknowledgment before longer operations so the interaction does not feel silent |
| 🧩 Self-Describing Actions | Every bundled action exposes a `TOOL` definition and is automatically discovered at startup |
| 🧠 Persistent Memory | Long-term local memory stores projects, preferences, identity information, and other user-approved context |
| 🧠 Recallable Memory | JARVIS can search its local memory when information is not included directly in the active prompt |
| ↩️ Undo | Reversible computer operations can register an inverse action and be undone through JARVIS |
| ⚠️ Real Confirmation | Irreversible operations require confirmation issued by the UI rather than a model-generated parameter |
| 🎧 Audio Device Picker | Microphone and speaker devices can be selected and validated through the audio-device system |
| 🔗 Session Continuity | Live-session resumption preserves conversation context across supported reconnects |
| 🧩 Plugin System | Drop a Python plugin into `plugins/` and JARVIS can discover it on the next launch |
| 🗺️ Interactive Map | MapLibre-based map panel with layers, routing, markers, measuring, drawing, globe mode, and dynamic JARVIS theming |
| 📺 Video on the HUD | Video content can use the central HUD area where the avatar normally appears |
| 🪜 Gemini Model Fallback | Centralized Gemini one-shot calls use a fallback strategy with timeouts and model availability handling |
| 🎙️ Real-Time Voice | Low-latency voice interaction through the Gemini Live API |
| 🎨 Live Theming | HUD colors can be customized through the JARVIS interface and configuration |
| 〰️ Reactive HUD | Audio visualization reacts to microphone and assistant speech activity |
| 🎙️ Voice Selection | Supported Gemini voice configurations can be selected from the UI |
| ♾️ Long Sessions | Session handling and context management are designed for extended conversations |
| 🖥️ System Control | Applications, volume, brightness, Wi-Fi, power operations, shortcuts, windows, and desktop controls |
| 🧩 Autonomous Tasks | Multi-step tasks can be planned and executed through the action/plugin system |
| 👁️ Visual Awareness | Screen and webcam information can be supplied to the assistant for visual reasoning |
| 🧠 Context-Aware Memory | JARVIS can use stored project and preference context across sessions |
| ⌨️ Hybrid Input | Voice and text commands can be used through the same assistant interaction path |
| 🌤️ Weather | Live weather information can be presented through JARVIS's content interface |
| 📊 Hardware Monitoring | CPU, RAM, GPU, temperature, network, and process information can be displayed and monitored |
| 🗺️ Dynamic Content | Web results, research, maps, reports, and other information can be displayed inside the HUD content area |
| 🔍 Web Search | Search and research capabilities through the centralized web-search action |
| ⏰ Smart Reminders | Background reminder plugins can manage recurring tasks and notifications |
| 📂 File Processing | Local documents and files can be read, summarized, and analyzed |
| 🌐 Browser Control | Browser navigation and computer interaction can be performed through dedicated actions |
| 📨 Messaging | Messaging integrations can send commands and messages through supported services |
| 🖱️ Desktop Control | Keyboard, mouse, window, taskbar, and desktop-level operations |
| 📱 Telegram Remote | JARVIS can be controlled remotely through a secured Telegram integration |
| 📋 Clipboard Intelligence | Clipboard content can be processed for explanation, translation, summarization, and correction |
| 🪪 Assistant Customization | Assistant name, user name, voice, and UI color can be configured |
| 📧 Email Monitoring | Gmail monitoring plugin for checking relevant email changes through OAuth |
| 📝 Document Review | Documents can be analyzed and presented with findings grouped by severity |
| 📊 Excel Generation | Natural-language spreadsheet requests can produce real `.xlsx` files |
| ⏱️ Pomodoro | Background focus sessions, breaks, statistics, and persistent focus history |
| 💧 Water Reminder | Periodic hydration reminders and daily intake tracking |
| 🧠 Quiz Mode | Interactive quizzes displayed directly inside the JARVIS HUD |
| 💬 Chat Takeover | Screen-based conversation assistance for supported visible chat interfaces |
| 🍎 Food Analysis | Camera-based food recognition and nutrition estimation |
| 💹 Trading Intelligence | Public market quotes, news, analysis, charts, comparisons, watchlists, and macro information |
| 🔎 OSINT Research | Extendable public-source investigation workflows for entities, domains, companies, usernames, and other research targets |

---

## 🧠 Architecture

JARVIS (v2) is organized around a modular runtime.

### Core Runtime

`main.py` coordinates the live assistant session, audio input/output, Gemini Live communication, tool execution, memory, plugins, background behavior, and UI integration.

`ui.py` provides the PyQt6 HUD, avatar interface, system panels, camera/video surfaces, content panels, settings, plugin settings, memory interfaces, remote controls, quiz/review views, and interaction callbacks.

The main runtime and UI are deliberately kept separate from individual capabilities.

### Actions

Built-in computer capabilities live inside:

```text
actions/
```

Every action exposes a module-level `TOOL` dictionary.

Example:
```py
TOOL = {
    "name": "example_action",
    "description": "Example computer action",
    "parameters": {
        "type": "OBJECT",
        "properties": {}
    },
    "handler": handler,
}
```

The action loader automatically discovers valid action files at startup.

Adding a new built-in action does not require manually registering it in the main runtime.

### Plugins

Additional capabilities live inside:

```text
plugins/
```

A plugin exposes a `PLUGIN` definition and a runtime entry point:

 - [plugins/_template.py](/plugins/_template.py)

Plugins are discovered automatically and can optionally provide their own settings schema.

This keeps specialized functionality outside the core runtime.

---
## Plugins
 - [all optional plugins](/docs/plugins.md)
---

## 👁️ Visual Intelligence

JARVIS can work with both screen and camera context.

The visual system can provide:

- screen capture
- webcam capture
- document images
- visual content displayed on the HUD
- camera-based food analysis
- visual chat analysis
- contextual information from the active desktop

Different visual capabilities use the existing JARVIS session and UI rather than creating separate assistant runtimes.

---

## 🎧 Audio System

The audio architecture is split into independent components:

- microphone input
- speech-to-text
- wake-word detection
- echo protection
- audio-device selection
- Gemini Live audio streaming
- text-to-speech
- reactive audio visualization
- viseme generation for avatar lip-sync

Supported speech components include local and cloud-assisted pipelines such as Whisper/faster-whisper, Vosk, Edge TTS, Kokoro, and ElevenLabs where configured.

---

## 🧠 Memory System

JARVIS stores long-term memory locally.

The memory system separates persistent storage from the prompt budget.

```text
memory/
├── memory_manager.py
├── config_manager.py
└── long_term.json
```

The assistant can store information about:

- identity
- preferences
- projects
- workflows
- useful context
- session-related information

Memory can be searched locally when required instead of sending the entire store to the model on every request.

The UI also provides a memory interface for reviewing and removing stored facts.

---

## ↩️ Undo & Safety

JARVIS uses two different mechanisms for reversible and irreversible operations.

### Undo

Reversible operations can register an inverse operation.

Examples include:

- file moves
- renames
- file creation
- copies
- writes
- desktop organization
- supported settings changes

The undo stack is thread-safe and keeps a limited number of recent operations.

### Confirmation

Irreversible operations use a UI-issued confirmation token.

The model cannot simply pass:

```text
confirmed=yes
```

and execute a dangerous operation.

The confirmation must come from the user interface.

This mechanism is intended for operations such as shutdown, restart, Wi-Fi changes, and other genuinely irreversible or disruptive actions.

---

## 📱 Remote Control

JARVIS can be extended with remote control through Telegram.

The remote architecture uses:

- Telegram long polling
- private-chat restrictions
- user allowlists
- pairing
- freshness checks
- rate limiting
- constant-time pairing comparison
- shared JARVIS command handling

Remote text commands are routed into the same assistant command path used by the desktop interface.

Voice messages can use the existing recognition pipeline.

---

## 🖥️ HUD & Interface

The JARVIS interface is built with PyQt6.

The main HUD includes:

- animated avatar
- real-time waveform
- system metrics
- activity log
- camera view
- video view
- dynamic content panel
- quiz interface
- document-review interface
- settings drawer
- controls drawer
- plugin settings
- memory management
- remote controls
- customizable accent color
- clock and system information

The central HUD uses a shared surface stack so the avatar, camera, and video/content surfaces can occupy the same main visual area without requiring multiple application windows.

---

## 🧱 Project Structure

```text
JARVIS/
├── main.py
├── ui.py
├── dev.py
├── setup.py
├── pyproject.toml
├── requirements.txt
│
├── actions/
│   ├── web_search.py
│   ├── screen_processor.py
│   ├── background_monitor.py
│   ├── proactive.py
│   ├── reminder.py
│   ├── system_monitor.py
│   ├── computer_settings.py
│   ├── computer_control.py
│   ├── open_app.py
│   ├── browser_control.py
│   ├── file_controller.py
│   ├── file_processor.py
│   ├── send_message.py
│   ├── weather_report.py
│   ├── video_player.py
│   └── desktop.py
│
├── plugins/
│   ├── map_assistant.py
│   ├── email_monitor.py
│   ├── calorie_counter.py
│   ├── chat_takeover.py
│   ├── document_review.py
│   ├── excel_writer.py
│   ├── game_updater.py
│   ├── pomodoro.py
│   ├── quiz.py
│   ├── telegram_remote.py
│   ├── trading_agent.py
│   ├── water_reminder.py
│   └── _template.py
│
├── core/
│   ├── gemini.py
│   ├── llm_client.py
│   ├── prompt.txt
│   ├── avatar_mesh.py
│   ├── viseme.py
│   ├── echo.py
│   ├── hotkey.py
│   ├── undo.py
│   ├── confirm.py
│   ├── audio_devices.py
│   ├── plugin_loader.py
│   ├── action_loader.py
│   ├── wake_word.py
│   ├── stt.py
│   └── tts.py
│
├── memory/
│   ├── memory_manager.py
│   ├── config_manager.py
│   └── long_term.json
│
├── dashboard/
├── config/
└── .vault/
```

---

## ⚙️ Configuration

JARVIS stores runtime configuration under:

```text
config/api_keys.json
```

Configuration can include:

- Gemini API configuration
- assistant name
- user name
- selected voice
- UI accent color
- plugin configuration
- enabled/disabled plugin states
- remote configuration
- audio preferences

Plugin-specific configuration is handled through the shared configuration manager.

---

## 🛠️ Technology Stack

| Area | Technology |
|---|---|
| Language | Python |
| GUI | PyQt6 |
| AI | Google Gemini / Gemini Live API |
| Local LLM | Ollama / OpenAI-compatible local servers |
| STT | faster-whisper, Vosk |
| TTS | Edge TTS, Kokoro, ElevenLabs |
| Computer Vision | OpenCV, NumPy, MediaPipe-based geometry |
| Database / Storage | Local JSON and application state |
| Maps | MapLibre GL JS + Qt WebEngine |
| Automation | PyAutoGUI and OS-specific APIs |
| Async Processing | asyncio, threads, worker executors |
| Containers / Deployment | Docker-compatible tooling where required |
| Monitoring | Prometheus / Grafana / Loki / Sentry where integrated |
| Frontend Technologies | HTML, CSS, JavaScript inside selected embedded panels |

---

## 📋 Requirements

| Requirement | Details |
|---|---|
| **OS** | Windows, macOS, or Linux depending on enabled platform features |
| **Python** | Python 3.x compatible with the project's dependency lock |
| **Microphone** | Required for voice interaction |
| **Speakers / Headphones** | Required for spoken responses |
| **Gemini API Key** | Required for Gemini-powered capabilities |
| **GPU** | Not required for the core avatar/HUD architecture |
| **Internet** | Required for Gemini Live and cloud-based integrations |
| **Camera** | Optional; required for camera-based visual plugins |
| **Telegram** | Optional; required only for Telegram remote control |
| **Gmail OAuth** | Optional; required only for Gmail monitoring |
| **Map dependencies** | Required only when using the interactive map plugin |

---

## Additional installation details

```bash
git clone https://github.com/RustamovAkrom/JARVIS.git
cd JARVIS

python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
```

Linux/macOS:

```bash
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Start JARVIS:

```bash
python main.py
```

---

## 🔐 Security

JARVIS is designed around local-first control and explicit boundaries.

Important principles include:

- API keys are kept outside source code.
- Sensitive configuration should not be committed to Git.
- Irreversible actions require UI confirmation.
- Plugins run through a controlled plugin registry.
- Action loading validates tool definitions before activation.
- Remote Telegram control uses allowlists, pairing, rate limiting, and private-chat restrictions.
- Memory is stored locally.
- Specialized plugins should not bypass the existing action/plugin architecture.

---

## 🧩 Extending JARVIS

A new built-in computer capability belongs in:

```text
actions/
```

A specialized optional capability belongs in:

```text
plugins/
```

The plugin architecture supports:

- automatic discovery
- tool declarations
- parameter schemas
- enable/disable state
- plugin settings
- background execution
- access to the JARVIS player/UI
- access to session memory
- isolated error handling

This makes it possible to expand JARVIS without continuously modifying the central runtime.

---

## 🎯 Project Direction

JARVIS (v2) is being developed toward a more autonomous personal computer assistant with:

- stronger visual intelligence
- richer computer control
- deeper memory
- advanced OSINT research
- remote control
- interactive maps
- more specialized plugins
- proactive assistance
- improved real-time voice interaction
- better HUD visualization
- more reliable model fallback
- stronger safety and confirmation mechanisms

The goal is a modular JARVIS-style system that can grow through independent actions and plugins instead of becoming a single monolithic application.

---

## [📜 License](/LICENSE)
