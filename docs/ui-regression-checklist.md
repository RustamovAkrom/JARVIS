# UI Regression Checklist

Updated 2026-11-14. This is the verification list for continued settings
migration. It does not assert that every integration has been manually tested.

| Area | Baseline behavior / test | Current result | Required after migration |
|---|---|---|---|
| UI compatibility imports | `tests.test_ui_facade` verifies `JarvisUI`, `MainWindow`, extracted widgets, private `_SysMetrics` | Pass | Keep all legacy imports working |
| Full test suite | `.venv\Scripts\python.exe -m unittest discover -s tests -v` | 84 passed | Run after each category migration |
| Settings Center / General | `tests.test_settings_components` plus `tests.test_ui_facade` cover rows, typography, navigation, search, theme refresh, embedded routing, and toggle state sync | Passing; action routing keeps Settings open and loads the embedded audio/controls pages | Add real config write, restart persistence, and save/cancel checks |
| Setup/config | `tests.test_setup`, `tests.test_cursor_assistant_settings` | Pass | Preserve API key and language setup |
| Assistant identity | `tests.test_settings_components` covers Apply/Cancel/error feedback; `_apply_name_update` is the existing config boundary | UI transaction passes; live config/reconnect not verified | Retest Apply, cancel, voice reconnect, old keys after identity changes |
| Theme / opacity | palette/config helpers; left panel Qt surface test | Partial | Black/white/default/custom colors, cancel rollback, DPI |
| Audio | Settings Center embeds existing worker-backed device controls | Page integration present; no physical devices verified | Input/output tests, volume, reconnect, preserved session |
| Cursor | `tests.test_cursor_assistant_ui`, settings tests | Pass for covered UI paths | Every quick action and hotkey on supported OS |
| Permissions | `tests.test_computer_control_permissions` | Pass | UI state and actual denied/allowed action paths |
| Providers | config/routing tests exist | Live provider not tested | Mode, fields, Apply, reconnection, invalid values |
| Plugins | plugin loader and schema integration inspected | Per-plugin UI not exhaustively tested | Enable, settings, test, errors, reload compatibility |
| Memory | storage APIs inspected | UI interactions not fully tested | Search/copy/delete/error and confirm destructive actions |
| Security / face | auth callback dialog inspected | Not verified with provider or camera | Lock, PIN fallback, rate limit, face success/failure |
| Updates | `tests.test_update_service` | Pass for service logic | Preferences, check/download/install, Velopack package |
| Workspace | `tests.test_workspace_files` | Pass | Folder, open mode, editor save, panel sizes/visibility |
| Terminal | terminal-related UI tests | Pass for tested lifecycle paths | WebEngine, QWebChannel, PTY IO and packaged startup |
| Startup/shutdown | MainWindow construction in offscreen regression test | Partial | Real source, installed, portable, clean shutdown |
| DPI / small window | no systematic multi-scale test found | Not verified | 100%, 125%, 150%, 200%, narrow window and long translations |

For the handler and configuration details, see `ui-control-handler-map.md`.
