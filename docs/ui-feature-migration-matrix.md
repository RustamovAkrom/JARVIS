# UI Feature Migration Matrix

Status reflects the current migration. **PRESERVED** means the original path
is still present; it does not imply full live integration testing. The Settings
Center embeds the existing surfaces where they have not yet been fully
rewritten, keeping their current handlers while avoiding separate floating
windows. Coverage details are in `ui-functionality-inventory.md`.

| Original feature group | Original handler / owner | Current location | Status | Automated coverage |
|---|---|---|---|---|
| Settings category navigation and search | `SettingsCenter`, registry search | Settings Center | IMPROVED | `tests.test_settings_components` |
| First-run language/API-key/OS setup | `SetupOverlay._submit`, MainWindow setup flow | Setup overlay for first run; recurring launch, phone control, and desktop shortcut options in Settings → General | IMPROVED | Setup tests |
| Assistant and user identity | `AssistantIdentityPage.apply` → `_apply_identity_settings` → `_apply_name_update` | Settings Center Identity page; legacy overlay retained | MIGRATED | Apply/Cancel/error semantics tested with fake persistence callback |
| Voice selection | `AssistantIdentityPage.apply`, existing `on_voice_change` reconnect | Settings Center Identity page; legacy overlay retained | MIGRATED | Form transaction tested; live reconnect not tested |
| Theme presets/custom color/opacity | `CustomizeOverlay`, palette functions | Embedded in Settings Center Appearance page | EMBEDDED | Partial; theme matrix not tested |
| Fullscreen / morning brief / auto-start / language | MainWindow handlers | Settings Center General page; legacy drawers remain | MIGRATED | Generic state/signal tests; no OS auto-start action or language restart notice run |
| Wake / push-to-talk | MainWindow handlers | Embedded Controls page in Settings Center | EMBEDDED | Partial |
| Microphone/speaker selection, device tests, output volume | `AudioDeviceOverlay`, `core.audio_devices` | Embedded in Settings Center Voice & Audio page | MIGRATED | Worker UI/config paths retained; no hardware |
| Cursor settings and actions | `CursorFollowerOverlay`, `CursorRadialMenu`, config manager | Embedded Controls page in Settings Center | EMBEDDED | Cursor/config tests |
| Computer interaction permissions | config manager, tool policy, and confirmation gate | Settings → Computer Permissions; Chat/Agent mode, Ask/Approve/Full policies, granular permissions, and high-impact action confirmations | IMPLEMENTED / TODO SAFEGUARDS | Permission tests |
| Gemini / provider pipeline | `ProvidersOverlay`, provider registry | Embedded Integrations page in Settings Center | EMBEDDED | Config/routing only |
| Plugin enable/disable | `PluginManagerOverlay`, plugin loader | Embedded Plugins page in Settings Center | EMBEDDED | Not every plugin |
| Plugin settings and test actions | `PluginSettingsOverlay`, schema callbacks | Embedded Plugins page in Settings Center | EMBEDDED | Not every schema |
| Long-term memory viewer/copy/delete | `MemoryOverlay`, memory manager | Embedded Memory page in Settings Center | EMBEDDED | Storage tests; UI delete not tested |
| Lock/passcode/face verifier dialog | `_show_auth_lock`, caller-supplied verifier | Modal lock dialog; Settings Center explains current ownership | PRESERVED | Not verified end-to-end |
| Developer mode and debug mirroring | `_toggle_development_mode`, `_ConsoleMirror` | Settings → Developer & Diagnostics; Debug Console stays in the main workspace | EMBEDDED | Limited UI coverage |
| Update preferences and actions | `UpdatePanel`, update service | Embedded Settings → Updates & Maintenance page; removed from right chat tabs | EMBEDDED | Update service tests |
| Workspace folder/open mode/project editor | MainWindow workspace methods | Legacy page constructed but not mounted in visible tabs | PRESENT IN CODE / HIDDEN IN UI | Workspace/editor tests; no current UI entry point |
| Panel visibility and splitters | `_toggle_side_panel`, `_toggle_content_panel`, `_toggle_debug_panel` | Header / splitters | PRESERVED | Partial; no layout persistence |
| Auto-start | `_toggle_autostart` | Settings Center General page; Setup drawer retained | MIGRATED | State readback tested; OS action not run |

General, Assistant Identity, and Voice & Audio have native Settings Center pages.
Other controls listed as embedded reuse their existing widgets and handlers in
shared Settings cards while their UI is refined. Microphone scoping, per-app
rules, and expiring access remain explicitly marked TODO. Config-only
preferences and absent security setup UI remain documented audit limitations.
