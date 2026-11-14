# UI Functionality Inventory

Audit date: 2026-11-13. This inventory records the implementation before the
settings redesign and tracks the current Settings Center migration. Locations refer to `gui/app/main_window.py` unless stated
otherwise. “Automated” means a relevant unit test exists; it does not imply
that every control was manually exercised against a live subsystem.

| ID | Category | Feature / interactive controls | Current location | Handler / integration | Configuration and save behavior | Baseline status |
|---|---|---|---|---|---|---|
| SET-001 | Setup | Interface language, masked Gemini API key, OS choice, Initialize | `SetupOverlay`, opened by startup flow | `_sel`, `_submit`; `done` signal to `MainWindow` setup handler | `ui_language`, API credential; saves on Initialize | Automated setup/config tests; physical first-run flow not manually verified |
| SET-002 | General | Fullscreen toggle (`F11`) | Settings Center / Controls drawer | `_toggle_fullscreen` | Runtime window state; no persistent key | Settings control reads back window state; UI window tests cover chrome |
| SET-003 | General | Morning brief | Settings Center / Controls drawer | `_toggle_brief` | `morning_brief_enabled`; immediate read/write | Settings row calls legacy handler and resynchronizes actual config state |
| SET-004 | General / Voice | Wake-word enable | Controls drawer and header state | `_toggle_wake_word`; `wake_get_state` callback | `wake_word_enabled`; immediate write plus wake subsystem callback | Core behavior not exercised with microphone in unit suite |
| SET-005 | General / Voice | Manual wake/sleep action (conditionally shown) | Controls drawer | `_tap_wake_manual` | Runtime state; no preference key | Not manually verified |
| SET-006 | Voice & Audio | Push-to-talk toggle and keyboard chord lifecycle | Controls drawer | `_toggle_ptt`, `_apply_ptt_shortcut`, `ptt_hold` callback | `push_to_talk_enabled`; immediate write; keyboard hook updated | Config tests exist; physical key/audio behavior not verified |
| SET-007 | General | UI language dropdown | Settings Center / Controls drawer | `_change_interface_language` → `_save_ui_language` | `ui_language`; saves immediately and displays restart-required notice | Config/language tests exist; both controls synchronize selection; translations not exhaustive |
| SET-008 | Assistant Identity | Assistant name and user name | Settings Center / Customize Assistant overlay | `AssistantIdentityPage.apply` → `_apply_identity_settings` → `_apply_name_update`; old overlay remains | `assistant_name`, `user_name`; Apply | Page Apply/Cancel/error behavior tested with callbacks; live config write not integration-tested |
| SET-009 | Assistant Identity / Voice | Voice choice (Charon, Puck, Kore, Fenrir, Aoede) | Settings Center / Customize Assistant overlay | `AssistantIdentityPage.apply` → `_apply_name_update` and existing session-rebuild callback | `voice_name`; Apply; changed voice invokes session rebuild callback | Page transaction tested; live session reconnect not exercised |
| SET-010 | Appearance | Black, White, Default presets; hue wheel; hex input | Customize Assistant overlay | `_set_color`, `_on_wheel_*`, `_preview_ui_color` | `ui_color`; preview immediately, commit on Apply, revert on Cancel | Palette helpers are present; full theme/widget matrix not tested |
| SET-011 | Appearance | Interface opacity / Glass slider | Customize Assistant overlay | `_on_opacity_changed`, `_preview_ui_opacity`, `_apply_name_update` | `ui_opacity`; preview immediately, persisted on Apply; Cancel restores | Config clamp test and left-panel light-theme regression test exist |
| SET-012 | Appearance / HUD | HUD style | Config API supports `hud_style` (`core` / `orbital`) | `get_hud_style` / `save_hud_style` | `hud_style`; setter exists | No visible settings control found in current UI; destination/accessibility requires investigation |
| SET-013 | Voice / Model | Thinking, turn tuning, proactive audio, media resolution | Config API supports these options | `get_*` / `save_*` in `memory/config_manager.py` | `thinking_enabled`, `turn_tuning`, `proactive_audio`, `media_resolution` | No corresponding visible Controls/Settings widget found in audited builder; availability needs confirmation |
| SET-014 | Voice & Audio | Microphone device selector | Settings Center Voice & Audio page (embedded Audio Devices UI) | `AudioDeviceOverlay._apply` → `save_input_device`; emits `picked` | `input_device`; Apply, then session reconnect callback | Existing audio config paths retained; hardware not tested |
| SET-015 | Voice & Audio | Speaker device selector | Settings Center Voice & Audio page (embedded Audio Devices UI) | `AudioDeviceOverlay._apply` → `save_output_device`; emits `picked` | `output_device`; Apply, then session reconnect callback | Existing audio config paths retained; hardware not tested |
| SET-016 | Voice & Audio | Microphone TEST, live level meter, result message | Settings Center Voice & Audio page | `_run_test` worker → `_level_seen` / `_test_result` signals | No config change | Worker/signal path retained; microphone not physically tested |
| SET-017 | Voice & Audio | Speaker TEST tone and result | Settings Center Voice & Audio page | `_run_test` worker → `_test_result` signal | No config change | Speaker not physically tested |
| SET-018 | Voice & Audio | Output volume slider | Settings Center Voice & Audio page | `_apply_volume` worker; `_volume_follows_device` | Sets OS endpoint volume; slider release applies; not stored as JARVIS preference | No hardware test |
| SET-019 | Cursor Assistant | Enable quick actions | Controls drawer | `_save_cursor_controls` → `save_cursor_assistant_settings` and refresh | `cursor_assistant.enabled`; immediate | Cursor UI/config tests exist |
| SET-020 | Cursor Assistant | Custom system cursor visibility | Controls drawer | `_save_cursor_controls` → `_refresh_cursor_assistant` | `cursor_assistant.cursor_visible`; immediate; Windows system cursor integration | Cursor rendering tests exist; OS-level behavior platform-dependent |
| SET-021 | Cursor Assistant | Pointer style and size | Controls drawer | `_enable_custom_cursor` / `_save_cursor_controls` | `cursor_assistant.style`, `size`; immediate | Cursor rendering/config tests exist |
| SET-022 | Cursor Assistant | Quick-menu and select-area shortcuts | Controls drawer | `_save_cursor_controls`; shortcut refresh | `cursor_assistant.hotkey`, `region_hotkey`; immediate | Config tests exist; global hotkeys not exhaustively tested |
| SET-023 | Cursor Assistant | Per-action quick-menu checkboxes: Mic, Ask, File, Summary, Explain, Translate, Fix, Region, Stop, Settings | Controls drawer | `_save_cursor_controls`; `CursorRadialMenu` action dispatch | `cursor_assistant.menu_actions`; immediate | Cursor UI tests cover selected actions, not every action |
| SET-024 | Computer Permissions | Screen capture | Controls drawer | `_save_computer_permission_controls` | `computer_permissions.observe_screen`; immediate | Permission tests exist |
| SET-025 | Computer Permissions | Find UI elements | Controls drawer | Same handler | `computer_permissions.find_elements`; immediate | Permission tests exist |
| SET-026 | Computer Permissions | Move / scroll mouse | Controls drawer | Same handler | `computer_permissions.move_mouse`; immediate | Permission tests exist |
| SET-027 | Computer Permissions | Click / drag | Controls drawer | Same handler | `computer_permissions.click`; immediate | Permission tests exist |
| SET-028 | Computer Permissions | Type / keyboard | Controls drawer | Same handler | `computer_permissions.type`; immediate | Permission tests exist |
| SET-029 | Integrations & Providers | Gemini Live vs own pipeline selection | Engine & Providers overlay | `ProvidersOverlay`; `_apply`, `changed` signal | `providers.mode`; Apply, reconnects on change | Config/routing tests exist; live providers not tested |
| SET-030 | Integrations & Providers | Pipeline STT, LLM, TTS provider choices | Engine & Providers overlay | Dynamic provider cards/fields; `_apply` | `providers.pipeline.{stt,llm,tts}` | Provider metadata/config inspected; no all-provider UI coverage |
| SET-031 | Integrations & Providers | Provider credentials and declared configuration fields | Engine & Providers overlay; advanced fields behind MORE | Dynamic registry fields and validation/test actions | `providers.creds[provider_id]`; field save / Apply varies by provider | Provider-specific external services not tested |
| SET-032 | Integrations & Providers | Sidecar provider and preference order | Engine & Providers overlay | `_apply` | `providers.sidecar`, `providers.sidecar_first` | Config tests exist; live routing not tested |
| SET-033 | Plugins | Plugin enable/disable | Plugin Manager overlay | `_toggle` | `plugins_enabled[plugin_name]`; immediate | Plugin loader/config tests not comprehensive for each plugin |
| SET-034 | Plugins | Plugin description, broken-plugin status, Close | Plugin Manager overlay | Metadata from plugin loader; invalid plugin toggle disabled | No config change | Rendering inspected; each plugin not manually checked |
| SET-035 | Plugins | Plugin settings fields and Save | Plugin Settings overlay | Schema-driven controls; `_save_all` | `plugin_config[namespace][key]`; Save merges values | Plugin API inspected; per-plugin forms not exhaustively tested |
| SET-036 | Plugins | Plugin TEST actions | Plugin Settings overlay | `_test` / declared test callback | Saves entered values before invoking test in current implementation | No all-plugin test coverage; save-before-test is current behavior |
| SET-037 | Memory | List stored facts, Copy, Forget, Close | Memory overlay | `all_entries_for_ui`, clipboard, `_forget` | `memory/long_term.json`; delete is immediate | Memory engine tests exist; UI destructive confirmation absent |
| SET-038 | Security & Access | PIN/passphrase unlock and optional face verification | Lock dialog created by `_show_auth_lock`; invoked through callback/plugin integration | `verifier`, optional `verify_face`; retry delay after three failures | Credential/configuration ownership is external to this dialog | Lock UI exists; enrollment and credential settings not found in JARVIS Settings UI |
| SET-039 | Developer & Diagnostics | Developer mode toggle | Setup drawer | `_toggle_development_mode` | `development_mode`; immediate | No focused UI test |
| SET-040 | Developer & Diagnostics | Debug console visibility and log output | Header and Debug Console | `_toggle_debug_panel`; `_ConsoleMirror`; queued `LogWidget` signals | Visibility not persisted | Log widget and UI tests exist; sustained log volume not measured |
| SET-041 | Updates & Maintenance | Auto-check, auto-download, auto-install | Updates tab / `UpdatePanel` | `_save_settings` | `updates.{auto_check,auto_download,auto_install}`; immediate toggles | Update config/service tests exist |
| SET-042 | Updates & Maintenance | Check, Download, Install | Updates tab | `check_now`, `download_now`, `install_now` | Update workflow; install requires confirmation | Service tests exist; installed Velopack flow not run |
| SET-043 | Workspace | Workspace folder selection | Workspace file page is constructed but not added to visible tabs | `_select_workspace_folder`, `_save_workspace_path` | `workspace_path`; immediate after valid directory selection | Workspace path tests exist; control is currently not user-accessible |
| SET-044 | Workspace | Open project files internally or externally | Workspace file page is hidden | `_save_project_open_mode`, `_open_project_file` | `project_open_mode`; immediate | Project-file tests exist; controls are currently not user-accessible |
| SET-045 | Workspace | Active file, refresh, edit/save | Workspace file tree/editor code retained but page is not mounted in visible tabs | `_open_project_file`, `_save`, `_refresh_project_files` | `active_project_file`; workspace-relative path; save on open/edit | Workspace/editor tests exist; current UI entry point is hidden |
| SET-046 | Workspace | Left, lower results, right, debug panel visibility; splitters | Main header and QSplitters | `_toggle_side_panel`, `_toggle_content_panel`, `_toggle_debug_panel` | Split sizes/visibility are not persisted | UI behavior partly tested; layout persistence absent |
| SET-047 | Chat / Maintenance | Clear chat and archive | Chat and Archive tabs | `_clear_chat`, `_clear_history` | Active chat/history JSON; user confirmation flow where implemented | Not all clear paths tested |
| SET-048 | General / System | Auto-start with operating system | Settings Center / Setup drawer | `_toggle_autostart` | Windows Run key, macOS LaunchAgent, or Linux autostart file; OS-level immediate | Settings row verifies resulting OS state; actual registration not changed in tests |
| SET-049 | Main Workspace | Settings/Setup drawer and everyday Controls drawer | Header | `_toggle_drawer`, `_toggle_controls` | Visibility only | Connected; no unified settings navigation |
| SET-050 | Main Workspace | Left monitor, lower results, right workspace, debug visibility | Header | `_toggle_side_panel`, `_toggle_content_panel`, `_toggle_debug_panel` | Runtime visibility/splitter sizes | Partially tested; not persisted |
| SET-051 | Main Workspace | Legacy terminal panel and terminal tabs | Terminal panel and toggle button are hidden in current UI | `_toggle_terminal_panel`, `_close_terminal_tab`, session lifecycle | Terminal sessions; selected working directory | Terminal UI lifecycle tests exist; full WebEngine IO not exercised; no visible terminal button |
| SET-052 | Chat | Message input, Enter/send, stop/interrupt, image paste/drop, file attach | Chat composer | `ChatInput`, `_send`, `_do_interrupt`, `_on_file_selected` | Active conversation and pending attachment state | Image/chat/terminal UI tests cover selected paths |
| SET-053 | Chat | Microphone, recording, mute, lecture recording/library | Chat controls / header | `_toggle_mute`, `_toggle_lecture_recording`, `_show_lecture_library` and callbacks | Audio/session state and lecture files | Not all audio paths tested with hardware |
| SET-054 | Chat / Archive | Message copy/delete actions, archive clear | Chat HTML links / Archive | `_chat_link_clicked`, `_clear_history` | Active chat/history JSON; clear confirmation | Not all link targets tested |
| SET-055 | Workspace / Tasks | Add task, complete task, task/reminder list | Tasks tab | `_add_ui_task`, `_task_item_changed`, `_refresh_tasks` | Local `ui_tasks.json` and reminder list | No dedicated full task UI test |
| SET-056 | Workspace / Projects | Choose folder, refresh, open mode, browse/open/edit file | Legacy Workspace page is constructed but not mounted in tabs | `_select_workspace_folder`, `_refresh_project_files`, `_save_project_open_mode`, `_open_project_file` | `workspace_path`, `project_open_mode`, `active_project_file` | Workspace/editor tests exist; no visible UI entry point currently |
| SET-057 | Results / Content | Reading mode, expand, detach, close results | Lower results panel | `_toggle_reading_mode`, `_toggle_content_expand`, `_detach_content`, `_dismiss_content` | Runtime view state | Not exhaustively tested |
| SET-058 | Results / Quiz | Answer, next question, close quiz | Quiz panel | `_quiz_submit`, `_quiz_next`, `_hide_quiz`; grader callback | Runtime quiz state | Not verified in current baseline |
| SET-059 | Global Navigation | Command palette search and action rows | Ctrl+K / palette | `_open_command_palette`, `CommandPalette._run` | No preference; dispatches existing action callbacks | Command palette not covered comprehensively |
| SET-060 | Clipboard / Cursor | Clipboard quick actions, cursor menu actions, screen region selection | Floating panels/shortcuts | `ClipboardPanel`, `CursorRadialMenu`, `ScreenRegionSelector` | Clipboard and cursor config | UI tests cover clipboard flow, cursor rendering, and region capture |
| SET-061 | Remote | Generate/show remote access key and close overlay | Setup drawer / remote overlay | `_open_remote`, `on_remote_clicked`, `RemoteKeyOverlay` | Delegated to remote dashboard callback | Not verified with dashboard service |
| SET-062 | Project Editing | Save, external-open, close editor | Project editor dialog | `_ProjectEditorDialog._save`, `_open_external_project_file` | Workspace-relative file | Editor test exists |
| SET-063 | Footer / Keyboard | F4 mute, F11 fullscreen, Esc interrupt, Ctrl+K commands | Footer and shortcuts | Shortcut registrations in `MainWindow` | Runtime actions | Individual shortcut coverage incomplete |

## Configuration ownership notes

- Most preferences are merged into `api_keys.json` with `memory.config_manager`
  read-modify-write helpers. Existing keys are listed in the table above.
- UI chat/history/tasks/reminders have separate JSON files under the runtime
  config directory.
- Long-term memory uses `memory.memory_manager` and the runtime memory file.
- Output volume is changed on the selected OS audio endpoint; it is not stored
  in the JARVIS config as a slider value.
- Auto-start writes OS-specific state rather than an `api_keys.json` boolean.
- Provider and plugin fields are schema-driven; exact controls vary with
  installed plugin/provider metadata and must remain dynamic.

## Inventory gaps to resolve before redesign

1. Confirm whether `hud_style`, thinking, turn tuning, proactive audio, and
   media-resolution options are intentionally headless/config-only or are
   supposed to have visible controls.
2. Locate ownership and user-facing setup for face enrollment, passcode type,
   lock-at-startup, and camera selection; the audited UI only exposes the
   verifier callback lock dialog.
3. Exercise dynamic plugin/provider schemas with representative valid and
   invalid forms; this static audit cannot enumerate values contributed at
   runtime by every installed extension.
4. Record platform-specific callback wiring and persistence for auto-start,
   custom cursor, and audio tests during later integration verification.
