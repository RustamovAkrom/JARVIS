# UI Control-to-Handler Map

Audit date: 2026-11-13. This map covers settings, controllers, and adjacent
workspace controls in the current implementation. Dynamic plugin/provider
fields are mapped to their schema-driven dispatcher rather than guessed field
names. “Not verified” means static wiring was found but a live side effect was
not exercised.

| Control | Signal / activation | Handler | Read/write target | Timing / integration | Verification |
|---|---|---|---|---|---|
| Setup language | `currentData` read on submit | `SetupOverlay._submit` → MainWindow setup handler | `ui_language` | Initialize writes language and credential setup, then starts | Setup tests; live onboarding not run |
| Gemini key | Initialize click | `SetupOverlay._submit` → setup callback | Gemini API credential | Password-masked field; write on Initialize | No live credential used |
| Setup OS buttons | `clicked` lambda | `SetupOverlay._sel` | Overlay `_sel_os` | Selection only; used on submit | Static wiring |
| Initialize Systems | `clicked` | `SetupOverlay._submit` | Setup completion signal | Save/setup and overlay completion | Setup tests |
| Fullscreen | `clicked` | `MainWindow._toggle_fullscreen` | Window state | Immediate; F11 is also advertised | Not individually tested |
| Morning Brief | `clicked` | `MainWindow._toggle_brief` | `morning_brief_enabled` | Immediate config write | Not individually tested |
| Wake Word | `clicked` | `MainWindow._toggle_wake_word` | `wake_word_enabled`, `wake_get_state` callback | Immediate config + wake subsystem | No live mic test |
| Manual sleep/wake | `clicked` when shown | `MainWindow._tap_wake_manual` | `on_wake_manual` callback | Immediate state change | Not verified |
| Push to Talk | `clicked` | `MainWindow._toggle_ptt` | `push_to_talk_enabled`, shortcut/hold callbacks | Immediate config + key-hook state | Config tests only |
| UI language | `currentIndexChanged` | `_change_interface_language` → `_save_ui_language` | `ui_language` | Immediate save; restart notice | Language config tests |
| Assistant/user names | Apply click | `CustomizeOverlay._save` → `saved` → `_apply_name_update` | `assistant_name`, `user_name` | Persist on Apply | Not individually tested |
| Assistant voice pills | `clicked` | `_on_voice_pick`; Apply then `_apply_name_update` | `voice_name`, `on_voice_change` | Voice persisted on Apply; session reconnect if changed | Live session not tested |
| BLACK / WHITE / DEFAULT | `clicked` | `_set_color` → `_preview_ui_color`; Apply persists | `ui_color`, mutable palette | Live preview; Cancel restores | Theme switching not fully tested |
| Hue wheel / hex | wheel signals / `textEdited` | `_on_wheel_pick`, `_on_wheel_commit`, `_on_hex_edited` | `ui_color` preview | Drag updates preview at commit; valid hex previews | No full theme matrix |
| Opacity / Glass slider | `valueChanged` | `_on_opacity_changed` → `_preview_ui_opacity` | Window opacity; Apply writes `ui_opacity` | Live preview, Apply commits, Cancel restores | Clamp and panel-background tests |
| Customize Assistant Apply Changes | `clicked` | `CustomizeOverlay._save` → `saved` → `_apply_name_update` | Identity, voice, theme, opacity values | Validates/persists selected values and performs any required reconnect | Not individually tested |
| Customize Assistant Cancel | `clicked` | `CustomizeOverlay._cancel` | Restores original color/opacity preview | Does not commit pending edits | Not individually tested |
| Audio input selector | `currentData` read on Apply | `AudioDeviceOverlay._apply` | `input_device`; `picked` signal | Save then request session reconnect | Config tests; hardware not tested |
| Audio output selector | `currentData` read on Apply | `AudioDeviceOverlay._apply` | `output_device`; `picked` signal | Save then request session reconnect | Config tests; hardware not tested |
| Audio Devices Close | `clicked` | `hide` | Overlay visibility | Leaves unapplied device choices uncommitted | Not individually tested |
| Microphone TEST | `clicked` | `_run_test` worker → `_level_seen` / `_test_result` | `core.audio_devices.measure_input` | Worker thread; Qt signal updates meter/result | No physical device test |
| Speaker TEST | `clicked` | `_run_test` worker → `_test_result` | `core.audio_devices.play_tone` | Worker thread | No physical device test |
| Output volume | `sliderReleased` | `_apply_volume` worker | `core.audio_devices.set_output_volume` | Applies OS endpoint level; selector changes refresh from OS | No hardware test |
| Cursor quick-actions enable | `toggled` | `_save_cursor_controls` → `_refresh_cursor_assistant` | `cursor_assistant.enabled` | Immediate save and refresh | Cursor/config tests |
| Custom cursor | `toggled` | `_save_cursor_controls` → `_refresh_cursor_assistant` | `cursor_assistant.cursor_visible` | Immediate; native pointer application on supported platform | Rendering tests, not OS-wide behavior |
| Pointer style / size | `currentIndexChanged` | `_enable_custom_cursor` | `cursor_assistant.style`, `size`, visibility | Immediate save; selecting style enables cursor | Cursor rendering tests |
| Cursor quick-menu shortcut | `currentIndexChanged` | `_save_cursor_controls` | `cursor_assistant.hotkey` | Immediate save and shortcut refresh | Config tests |
| Region-selection shortcut | `currentIndexChanged` | `_save_cursor_controls` | `cursor_assistant.region_hotkey` | Immediate save and shortcut refresh | Config tests |
| Cursor action checkboxes | `toggled` | `_save_cursor_controls`; radial menu dispatches action | `cursor_assistant.menu_actions` | Immediate; action callback in `CursorRadialMenu` | Some action paths covered |
| Permission checkboxes | `toggled` | `_save_computer_permission_controls` | `computer_permissions` five booleans | Immediate config write; action layer checks permission | Permission tests |
| Provider mode cards | `clicked` / Apply | `ProvidersOverlay` mode selection → `_apply` | `providers.mode` | Apply emits `changed`; reconnect via caller | Config tests; live providers not tested |
| STT/LLM/TTS provider fields | dynamic Qt signals / Apply | `ProvidersOverlay` registry field handlers | `providers.pipeline`, `providers.creds` | Provider-declared validation; Apply | Not exhaustive per provider |
| Provider MORE | `clicked` | provider form rebuild/toggle | In-memory `_more` visibility | Reveals advanced schema fields | Static wiring |
| Provider status/test | provider-specific click | dynamic provider callback | Provider prefetch/test methods | May save form values before test | Not exhaustive |
| Sidecar settings | `clicked` / Apply | `ProvidersOverlay._apply` | `providers.sidecar`, `sidecar_first` | Apply writes and refreshes routing | Config tests |
| Provider Apply | `clicked` | `ProvidersOverlay._apply` | Provider mode, pipeline, credentials, sidecar | Persists settings; emits `changed` if conversation engine needs reconnect | Config tests; live reconnect not tested |
| Provider Close | `clicked` | Overlay hide handler | Overlay visibility | No separate transactional Cancel behavior found | Not individually tested |
| Plugin enable/disable | `clicked` | `PluginManagerOverlay._toggle` | `plugins_enabled[name]` via config manager | Immediate; plugin loader reads enabled state | Per-plugin execution not tested |
| Plugin Manager Close | `clicked` | `hide` | Overlay visibility | No config change | Not individually tested |
| Plugin Settings | Setup drawer click | `MainWindow._open_plugin_settings` | `get_plugin_settings` callback → `PluginSettingsOverlay` | Opens dynamic forms | Static integration inspected |
| Plugin form Save | `clicked` | `PluginSettingsOverlay._save_all` | `plugin_config[namespace]` | Save merges declared fields | Not all plugin schemas tested |
| Plugin form Test | `clicked` | `PluginSettingsOverlay` test dispatcher | Declared plugin test method | Current implementation saves entered values before test | Not exhaustive |
| Plugin Settings Close | `clicked` | `hide` | Overlay visibility | Does not roll back values already saved by Test | Not individually tested |
| Memory Copy | `clicked` | `QApplication.clipboard().setText` | System clipboard | Immediate | No dedicated test |
| Memory Forget | `clicked` | `_forget` → memory manager `forget` → queued rebuild | Long-term memory store | Immediate destructive deletion; no confirmation currently | Memory UI delete not tested |
| Memory Close | `clicked` | `hide` | Overlay visibility | No config change | Not individually tested |
| Lock PIN/passphrase | button click / Return | `_show_auth_lock` local `attempt` | Caller-supplied `verifier` callback | Three failures trigger 10-second retry delay | No credential integration test |
| Face verification | `clicked` worker → `_auth_face_result_sig` | `verify_face` callback / `_finish_face_auth` | Caller-supplied verifier | Worker thread; PIN remains fallback | Face hardware not tested |
| Developer Mode | `clicked` | `_toggle_development_mode` | `development_mode` | Immediate save; console mirroring follows mode | No dedicated test |
| Debug Console button | `clicked` | `_toggle_debug_panel` | `_debug_panel` visibility | Immediate; visibility not persisted | Console view UI tests, not long session |
| Update preference toggles | `toggled` | `UpdatePanel._save_settings` | `updates.auto_check`, `auto_download`, `auto_install` | Immediate save | Config/update tests |
| Check for updates | `clicked` | `UpdatePanel.check_now` | Update service | Async update check | Service tests use mocked/network-independent paths |
| Download update | `clicked` | `UpdatePanel.download_now` | Update service | Background download | No package download run |
| Install update | `clicked` | `UpdatePanel.install_now` | Update service / Velopack | Confirmation then restart/install | Not run in baseline |
| Workspace folder (legacy page not mounted) | No live signal; page is not added to visible tabs | `_select_workspace_folder` → `_save_workspace_path` | `workspace_path` | Handler saves a valid directory immediately if invoked | Workspace validation tests; no current UI entry point |
| Project opener (legacy page not mounted) | No live signal; page is not added to visible tabs | `_save_project_open_mode` | `project_open_mode` | Handler saves immediately if invoked | Workspace tests; no current UI entry point |
| Project file open/edit (legacy page not mounted) | No live signal; tree/editor code is retained but unreachable from current tab UI | `_open_project_file`, `_ProjectEditorDialog._save` | `active_project_file`; workspace file | Open according to mode, save guarded to project root | Workspace/editor tests; no current UI entry point |
| Legacy terminal toggle | Button exists but is explicitly hidden; terminal panel is hidden by default | `_toggle_terminal_panel`, terminal session lifecycle | Terminal sessions and working directory | Runtime only | Terminal lifecycle tests; no visible UI entry point |
| Panel layout buttons | `clicked` | `_toggle_side_panel`, `_toggle_content_panel`, `_toggle_debug_panel` | QSplitter sizes / visibility | Immediate; no persistence/reset action found | Not fully tested |
| Auto-start | `clicked` | `_toggle_autostart` | Registry / LaunchAgent / `.desktop` file | Immediate platform-specific write/removal | Helper tests; actual OS state not changed in baseline |
| Clear current chat | `clicked` | `_clear_chat` | Active chat JSON; archive retained | Immediate and currently has no confirmation | Not tested |
| Clear archive and current chat | `clicked` | `_clear_history` | History and active chat JSON; memory retained | Yes/No confirmation before write | No complete flow test |
| Delete one chat turn | Chat HTML `anchorClicked` | `_chat_link_clicked` → `_delete_chat_turn` | Removes turn from active chat and archive | Immediate and currently has no confirmation | Not tested |
| Setup drawer | `clicked` | `_toggle_drawer` | Overlay visibility | Immediate | Not individually tested |
| Everyday Controls drawer | `clicked` | `_toggle_controls` | Overlay visibility | Immediate | UI tests cover scrollable controls layout |
| Settings Center button | `clicked` | `_open_settings_center` → `SettingsCenter` | Reusable non-modal settings window | Opens/raises one instance; category actions route to existing handlers | Navigation/search covered in `tests.test_settings_components`; owner routing covered in `tests.test_ui_facade` |
| Chat send | Button click / Enter | `_send` | Existing `on_text_command` callback and conversation state | Existing background dispatch; image/file payload supported | Chat route tests cover text/image path |
| Chat interrupt | Button click / Esc | `_do_interrupt` | Existing `on_interrupt` callback | Immediate request to stop active response/audio | Not all streaming states tested |
| File attachment | Browse, drag/drop, clipboard image paste | `FileDropZone`, `ChatInput`, `_on_file_selected` | Pending file/image and existing conversation callback | Sends existing attachment payload | Image paste/send tests exist; each file type not tested |

## Settings Center routes and migrated General controls

- The header Settings button opens or raises one `SettingsCenter`; category
  routes still call the original MainWindow handlers.
- General now hosts the language selector plus Auto-start, Morning Brief, and
  Fullscreen. These controls call existing owner methods;
  toggles read back the owner state and revert when the requested state was not
  applied.
- Assistant Identity now has name, user-name, and voice fields with Apply and
  Cancel. Apply uses `_apply_identity_settings` and `_apply_name_update`; config
  persistence happens before the live interface is updated, and voice changes
  retain the existing reconnect callback.
- Voice & Audio embeds `AudioDeviceOverlay` in the Settings Center on first
  visit. Its device tests and volume work keep their original worker/signal
  paths. The embedded panel omits its old Close button; Apply remains in-place
  and triggers the existing audio-session reconnect callback.
- The UI-language selector is also retained in the Controls drawer. Both
  selectors use `_change_interface_language` and synchronize after a save.
- The migrated General widget and its generic save/state behavior are tested;
  actual OS auto-start registration and the language restart notice are not
  exercised by the automated suite.
| Mute | Button / F4 | `_toggle_mute` | Existing audio/UI muted state | Immediate; syncs assistant audio callbacks | Not physical audio verified |
| Lecture recording | Header action | `_toggle_lecture_recording` | Recorder lifecycle | Start/stop recording | Not covered by current suite |
| Lecture library | Header action | `_show_lecture_library` | Local lecture files; open/attach actions | Immediate dialog actions | Not covered |
| Add task | `clicked` / Return | `_add_ui_task` | Local `ui_tasks.json` | Immediate append and refresh | No dedicated test |
| Complete task | `itemChanged` | `_task_item_changed` | Local `ui_tasks.json` | Immediate mark complete | No dedicated test |
| Project folder chooser | `clicked` | `_select_workspace_folder` | `workspace_path` | Save after valid selection | Path tests exist |
| Refresh project files | `clicked` | `_refresh_project_files` | Workspace file listing | Immediate refresh | Workspace tests exist |
| Open project file | Tree activation | `_open_project_file` | `active_project_file`; editor/external app | Open according to selected mode | Workspace/editor tests exist |
| Editor Save / external open | `clicked` | `_ProjectEditorDialog._save`, `_open_external_project_file` | Workspace-relative file contents | Save or OS open | Save path tested; external launch not tested |
| Results reading mode | `clicked` | `_toggle_reading_mode` | Main/center/workspace splitter snapshots | Hide/restore workspace panels | Not exhaustively tested |
| Results expand/detach/close | `clicked` | `_toggle_content_expand`, `_detach_content`, `_dismiss_content` | Results panel/window state | Immediate | Not exhaustively tested |
| Quiz answer/next/close | Button / Return | `_quiz_submit`, `_quiz_next`, `_hide_quiz` | Existing grader callback and quiz state | Immediate | Not tested |
| Command palette | Ctrl+K / search result | `_open_command_palette`, `CommandPalette._run` | Existing command callbacks | Immediate | Partial UI coverage |
| Clipboard panel actions | Action button | `ClipboardPanel` action callback | Clipboard data / existing chat dispatch | Immediate | Clipboard popover test exists |
| Screen region selection | Shortcut / region action | `ScreenRegionSelector`, analysis callback | Captured image bytes | Existing screen-analysis path | Region selector tests exist |
| Remote access | Setup drawer click | `_open_remote` → `on_remote_clicked` | Dashboard service callback and overlay | Key generation delegated to dashboard | Not tested against running dashboard |
| Header panel controls | `clicked` | `_toggle_side_panel`, `_toggle_content_panel`, `_toggle_debug_panel` | Visibility and splitter sizes | Immediate; no saved layout | Partial |
| Terminal open/close | Header/tab buttons | `_toggle_terminal_panel`, terminal session handlers | Existing QWebEngine/QWebChannel/PTY bridge | Session lifecycle and active working directory | Terminal UI tests cover lifecycle paths |
| Keyboard shortcuts | F4/F11/Esc/Ctrl+K | Existing `QShortcut` and key event handlers | Mute/fullscreen/interrupt/command palette | Immediate | Not every shortcut tested |

## Signal and callback boundaries

- `main.py` constructs `JarvisUI` and assigns callbacks for text commands,
  audio reconnection, wake controls, remote access, confirmation, auth locking,
  and plugin integration.
- `MainWindow` emits Qt signals for audio/video/camera, setup, auth, confirmation,
  quiz, review, clipboard, and terminal-related cross-thread events.
- Settings UI must preserve the callback identity and signal ownership; a visual
  move alone does not verify those integrations.
