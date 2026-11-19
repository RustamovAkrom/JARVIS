# JARVIS Plugin Catalogue

This catalogue documents every standalone Python plugin module currently present in the repository's `plugins/` directory on the `v2` branch.

**Catalogue size: 40 plugin modules.** The names in parentheses are the exact tool identifiers exposed to the assistant. Select a plugin name to open its implementation.

| Emoji | # | Plugin name | Description |
|:---:|---:|---|---|
| 🎵 | 1 | [Audio Ripper](../plugins/audio_ripper.py) (`audio_ripper`) | Extracts audio from supported video or media URLs and saves an MP3 in the user's Music folder. It can use a URL copied to the clipboard when no link is supplied and supports selectable audio bitrates. Requires `yt-dlp`; MP3 conversion normally uses FFmpeg. |
| 👁️ | 2 | [Auto-Pause Eyes](../plugins/auto_pause_eyes.py) (`auto_pause_eyes`) | Uses the webcam to detect when the user leaves the desk, pauses the currently playing media and resumes it when the user returns. Camera availability and global media-key support depend on the host system. |
| 🔋 | 3 | [Battery Dashboard](../plugins/battery_dashboard.py) (`battery_dashboard`) | Opens a live battery panel in the HUD with charge percentage, charging state and estimated remaining time when the operating system exposes battery information. |
| 🛠️ | 4 | [Build Guide](../plugins/build_guide.py) (`build_guide`) | Examines physical parts, cables or a project using the camera, screen or an image file. Produces connection diagrams or annotated photos, numbered assembly steps, component identification and visual troubleshooting; it can also compare a new photo with the current plan. |
| 📅 | 5 | [Google Calendar](../plugins/calendar_control.py) (`calendar_control`) | Reads a connected Google Calendar agenda, lists upcoming events, searches calendar entries and creates events from voice requests. Each user connects their own Google account using Google OAuth credentials and consent. |
| 🥗 | 6 | [Calorie Counter](../plugins/calorie_counter.py) (`calorie_counter`) | Uses the webcam to capture food and asks the configured AI model to estimate calories and nutrition information such as protein, fat, carbohydrates, sugar and fibre. Results are estimates, not clinical measurements. |
| 💬 | 7 | [Chat Takeover](../plugins/chat_takeover.py) (`chat_takeover`) | Monitors the messaging conversation visible on screen, detects new messages and prepares replies in the user's style. It can send replies through the focused conversation until stopped; it therefore needs careful screen, focus and input-permission handling. |
| 💽 | 8 | [Disk Dashboard](../plugins/disk_dashboard.py) (`disk_dashboard`) | Opens a live storage dashboard showing partitions, used/free space, read/write activity and disk history. It also exposes a cache-cleanup operation. |
| 🗺️ | 9 | [Disk Map](../plugins/disk_map.py) (`disk_map`) | Scans a drive or folder and displays a clickable treemap in the HUD, where block size represents file or folder size. Users can zoom into folders, inspect large items and navigate back. The map itself does not delete files. |
| 📄 | 10 | [Document Review](../plugins/document_review.py) (`document_review`) | Structures an explanation of a document such as a contract, policy, offer, consent form or official letter: what it says, what the reader may be agreeing to and which points deserve attention. It presents AI-provided findings; it is not legal advice or a legal-rule engine. |
| 🧍 | 11 | [Ergonomic & Posture Guard](../plugins/ergonomic_guard.py) (`ergonomic_guard`) | Provides desk-health assistance including posture checks, 20-20-20 eye-rest breaks, guided neck/shoulder/wrist stretches and work-session reminders. Webcam-based posture checks require a usable camera. |
| 🎂 | 12 | [Event Countdown](../plugins/event_countdown.py) (`event_countdown`) | Stores birthdays, anniversaries, holidays, bill dates and personal milestones, then shows days remaining and upcoming events. Recurring birthdays and anniversaries advance to the next year; event data is persisted locally. |
| 📊 | 13 | [Excel Writer](../plugins/excel_writer.py) (`excel_writer`) | Converts a natural-language request into a real `.xlsx` workbook and saves it to the Desktop (or the configured fallback directory). Generated sheets can include headers, data rows, totals and charts. Requires `openpyxl`. |
| 🔐 | 14 | [Face Activation / JARVIS App Lock](../plugins/face_activation.py) (`face_activation`) | Locks the JARVIS interface with a local PIN or passphrase and optional face matching. This is an application overlay, not an operating-system login screen or an anti-spoof biometric security boundary. |
| 🎮 | 15 | [Game Updater](../plugins/game_updater.py) (`game_updater`) | Inspects and manages supported Steam and Epic games-library operations, including installed-game status, downloads, installation, updates and progress reporting. It interacts with the installed game clients and may perform downloads or other state-changing operations. |
| ✋ | 16 | [Gesture Control](../plugins/gesture_control.py) (`gesture_control`) | Uses the webcam and local hand tracking for gestures: an open palm interrupts speech, swipes change tracks, a held two-finger gesture toggles play/pause, a pinch adjusts volume and thumbs up/down can answer an on-screen confirmation. |
| 🐙 | 17 | [GitHub Sentinel](../plugins/github_sentinel.py) (`github_sentinel`) | Works with GitHub profiles, repositories, repository details, issues, pull requests and repository search. It can also inspect local Git status and pull changes in a local repository. Public GitHub data works without a token; private-repository access needs a GitHub Personal Access Token. |
| 📧 | 18 | [Gmail Control](../plugins/gmail_control.py) (`gmail_control`) | Connects to a user's Gmail account to check unread mail, read messages, search the inbox, mark messages as read, save drafts and send email. Sending is intended to wait for the application's approval flow. Requires Google OAuth credentials and Gmail API access. |
| 🌐 | 19 | [God's Eye View](../plugins/gods_eye_plugin.py) (`gods_eye_view`) | Opens the local God's Eye Earth-observation HUD, provides a location/map view and searches public camera or YouTube results. Its YouTube list is restricted to results explicitly identified as currently live. |
| 📈 | 20 | [Live Panels](../plugins/live_panels.py) (`live_panels`) | Shows interactive HUD panels for system diagnostics (CPU, memory, GPU, disk, network, per-core load and busy processes) and, when its weather component is available, city weather with hourly and multi-day views. |
| ⬇️ | 21 | [Media Downloader](../plugins/media_downloader.py) (`media_downloader`) | Downloads supported online videos, including sources such as YouTube, Instagram Reels, TikTok, X/Twitter, Facebook and Reddit, to the user's Videos folder. It accepts a URL and can use a copied clipboard link when available. Requires `yt-dlp` and suitable media tools for some formats. |
| 📶 | 22 | [Network Dashboard](../plugins/network_dashboard.py) (`network_dashboard`) | Opens a live network diagnostics panel with download/upload throughput, recent speed history, latency to network endpoints, connection counts and interface information. |
| 📡 | 23 | [Network Radar](../plugins/network_radar.py) (`network_radar`) | Scans the local network for visible devices and shows their names/types, vendor information, IP addresses and latency. It also supports speed tests, device naming/trust lists and alerts when new devices appear. Use it only on networks you own or are authorized to assess; an unfamiliar device is not proof of intrusion. |
| 🔔 | 24 | [Notification Reader](../plugins/notification_reader.py) (`notification_reader`) | Reads recent notifications from the Windows notification center, optionally filters by app and can watch for a keyword or sender and alert when a matching notification appears. Windows-only; requires the user to grant notification access. |
| 🗒️ | 25 | [Obsidian Vault](../plugins/obsidian_vault.py) (`obsidian_vault`) | Searches, reads and lists Markdown notes in a configured local Obsidian vault; creates notes, appends content, updates daily notes, lists unchecked tasks, finds backlinks and opens notes in Obsidian. File access is confined to the configured vault and the plugin does not delete notes. |
| ⚡ | 26 | [PC Electricity & Power Cost](../plugins/pc_electricity_cost.py) (`pc_electricity_cost`) | Estimates PC power consumption and component draw, calculates running electricity cost from the configured tariff, tracks session energy and displays a power/cost panel in the HUD. The accuracy depends on the hardware readings and power model available on the machine. |
| 🌍 | 27 | [Planet Watch](../plugins/planet_watch.py) (`planet_watch`) | Provides a voice-controlled 3-D globe and map that can zoom from a global view to local areas. It layers public information such as earthquakes, disaster alerts, aircraft, aurora activity, local weather/time and the ISS; data freshness and coverage depend on the upstream feeds. |
| 🍅 | 28 | [Pomodoro](../plugins/pomodoro.py) (`pomodoro`) | Runs background focus and break cycles, announces phase changes through JARVIS speech and stores completed focus sessions so the user can review daily focus time. |
| 💪 | 29 | [Push-up Counter](../plugins/pushup_counter.py) (`pushup_counter`) | Uses the webcam to count push-up repetitions during a workout and can track a requested target. It uses MediaPipe when installed, with an OpenCV-based fallback mode. |
| 🧠 | 30 | [Quiz](../plugins/quiz.py) (`quiz`) | Displays an interactive quiz generated from the active conversation context, a topic or supplied material. The user answers on screen and JARVIS reports the result; question generation is performed by the conversation model rather than a separate plugin API call. |
| 🎥 | 31 | [Screen Recorder](../plugins/screen_recorder.py) (`screen_recorder`) | Records the desktop as a video, with start, stop, pause, resume and status controls, and optional microphone audio. Voice controls are cross-platform where capture is supported; global Win+Alt+R and Win+Alt+P hotkeys are Windows-specific. |
| 🗂️ | 32 | [Spaced Repetition](../plugins/spaced_repetition.py) (`spaced_repetition`) | Creates persistent flashcard decks and schedules reviews using an SM-2-style spaced-repetition algorithm. It supports adding individual cards, generating batches, reviewing due cards and grading recall so difficult cards return sooner. |
| 🎧 | 33 | [Spotify Control](../plugins/spotify_control.py) (`spotify_control`) | Connects to Spotify through the Spotify Web API to search and play tracks, pause/resume, skip tracks, adjust volume, inspect playback status and list available devices. Requires a configured Spotify API integration and an authorized account/device. |
| 📲 | 34 | [Telegram Remote](../plugins/telegram_remote.py) (`telegram_remote`) | Creates a Telegram-based remote bridge for sending commands to JARVIS and relaying supported information such as screenshots or system status back to an authorized phone. It uses outbound bot polling, a configured bot token and an explicit chat allowlist. |
| 📈 | 35 | [Trading Agent](../plugins/trading_agent.py) (`trading_agent`) | Retrieves stock/ETF quotes and headlines, summarizes sentiment, calculates selected chart indicators, compares symbols, scans watchlists and provides macro or portfolio-style summaries using public market data. It is informational, not investment advice. |
| 💧 | 36 | [Water Reminder](../plugins/water_reminder.py) (`water_reminder`) | Runs recurring spoken hydration reminders, logs water intake, tracks progress toward a daily goal and reports the user's intake for the current day. Its local daily intake counter resets on a new date while the goal and interval persist. |
| 🌦️ | 37 | [Weather Dashboard](../plugins/weather_dashboard.py) (`weather_dashboard`) | Opens a city-specific live weather dashboard with current conditions, the next 24 hours and a seven-day forecast. It uses Open-Meteo geocoding/forecast data and does not require an API key. |
| 📞 | 38 | [WhatsApp Call](../plugins/whatsapp_call.py) (`whatsapp_call`) | Places WhatsApp voice or video calls to a named contact and can hang up an active call. The shared WhatsApp driver supports different desktop/browser transports depending on OS and configuration; the user must be signed in to WhatsApp. |
| 🛡️ | 39 | [WhatsApp Call Guard](../plugins/whatsapp_guard.py) (`whatsapp_guard`) | Handles incoming WhatsApp calls according to the user's instruction: announce, accept, decline, send a text response or speak to the caller. The source explicitly warns that automated WhatsApp operation is not permitted by WhatsApp and may put the account at risk of restriction; enable only after understanding that risk. |
| ▶️ | 40 | [YouTube Controller](../plugins/youtube_control.py) (`youtube_controller`) | Controls an already-open YouTube tab using keyboard/desktop automation: play/pause, captions, next/previous video, seek, rewind/forward, volume, playback speed, fullscreen, theater/miniplayer and supported ad skipping. It does not search for or open new videos. |

## Catalogue notes

- The catalogue is a snapshot of the repository's `v2` branch. The plugin manager in the running application is the source of truth for which modules actually loaded on a particular machine.
- The plugin loader scans top-level `plugins/*.py` files that do not begin with an underscore and validates their `PLUGIN` declaration and `run()` handler. A module can be present in the repository but still be unavailable if an optional dependency, companion application, account authorization, device or operating-system capability is missing.
- Files such as `_google_core.py`, `_whatsapp_core.py`, `_live_panels_core.py`, `_hands.py` and other `_*_core.py` modules are shared implementation helpers, not separate user-facing plugins. `_template.py` is a starter template, and `gods_eye_view.html` is a plugin asset.
- Some plugins use cameras, screen capture, keyboard/mouse automation, local files, third-party accounts or external services. Read the relevant source and settings before enabling them. Plugins execute as Python code with the current user's privileges; the plugin interface is not a sandbox.
- For Google Calendar and Gmail, create your own Google Cloud OAuth client and grant only the requested scopes. For GitHub private repositories, use a token with the minimum scopes needed. Keep all tokens and credentials out of source control.
- Network Radar should only be used on networks you are authorized to inspect. Chat Takeover, Screen Recorder, Notification Reader and messaging integrations may handle private communications or screen content.
- The Notification Reader is Windows-only. Other desktop automation and camera capabilities vary by OS and desktop session; check each plugin's setup text and reported availability in the plugin manager.

## Enabling and configuring plugins

1. Open **SETUP → PLUGINS** in JARVIS.
2. Enable the plugin you want to use.
3. Open **PLUGIN SETTINGS** when the plugin declares a settings form.
4. Supply the required local path, optional dependency, API key or account authorization.
5. Use the plugin's **TEST**, **CHECK** or **AUTHORIZE** action when provided.
6. Try a small request first and review the activity/console output if the plugin reports an error.

Plugin discovery happens when JARVIS starts. Restart the application after adding, removing or replacing a plugin module or one of its shared helper files. Enabling or disabling an already-discovered plugin can take effect without re-importing its Python module.

## Developing a plugin

Use [`plugins/_template.py`](../plugins/_template.py) as the starting point. A plugin normally exposes:

- A module-level `PLUGIN` dictionary containing a unique `name`, a non-empty `description` and an object-style `parameters` schema.
- A callable `run(parameters, ...)` function.
- Optionally, a `PLUGIN_SETTINGS` dictionary for namespaced configuration fields and a settings test action.
- Optionally, a startup hook such as `on_app_start(player=...)` and execution metadata such as `behavior`, `scheduling`, `tier` or `brief`.

The loader validates the declaration, checks for name collisions, records import/validation failures and continues discovering other modules. Keep handlers bounded, validate parameters, use timeouts for network work, and avoid logging credentials. Review the [plugin loader](../core/plugin_loader.py) and [template](../plugins/_template.py) alongside the implementation.


## Optional Plugin Subsystem & Core Isolation

Plugins are an optional extension layer. Built-in actions in `actions/` are
independent and remain available when the plugin subsystem is disabled or fails.

- Global switch: `plugins_subsystem_enabled` in runtime configuration
  (`api_keys.json`), defaulting to `true` for backward compatibility.
- When disabled, JARVIS skips plugin discovery, registration, tool declarations,
  and startup hooks. The no-op registry keeps the Core interface stable.
- Plugin loader import/discovery failures are caught by
  `core.plugin_runtime.safe_discover_plugins`; JARVIS continues with built-in
  tools and actions.
- Startup-hook failures are logged and do not abort the Live session.
- A failure in one plugin module continues to be isolated by the plugin loader.
- Plugins execute with the user's permissions and are **not sandboxed**. Only
  install plugins you trust.

The current implementation exposes the global setting through
`memory.config_manager.get_plugins_subsystem_enabled()` and
`save_plugins_subsystem_enabled()`. A Settings control should call these APIs
and clearly report subsystem state (Enabled / Disabled / Unavailable). Do not
couple this control to built-in Actions.
