# UI Known Issues and Audit Findings

Updated 2026-11-14. Findings include the original audit and issues fixed during
the Settings Center migration.

| ID | Finding | Evidence / impact | Status |
|---|---|---|---|
| UI-AUD-001 | Most controls started in drawers, tabs, and floating overlays; the Settings Center had routed to those surfaces and hidden itself. | Settings routes in `MainWindow` and category page builders | Improved: General, Identity, Audio, Appearance, Cursor Controls, Providers, Plugins, Memory, Permissions, and Updates are available inside Settings; Debug Console remains a workspace surface |
| UI-AUD-011 | Settings category viewport could paint a native gray surface that conflicted with the JARVIS theme. | Screenshot of Settings Center Plugins page | Fixed with explicit stack, page, scroll area, and viewport backgrounds |
| UI-AUD-012 | Settings routes hid the center before opening a legacy panel, making the UI jump between generations. | `_handle_settings_action` | Fixed: routes select embedded pages and leave the center open |
| UI-AUD-013 | Controls panel was fixed at 330 px and capped at 590 px high, making long labels and settings hard to read. | `_build_controls_drawer` / `_place_drawer` | Improved to 460 px on wide windows, responsive width on narrow windows, and height based on available space |
| UI-AUD-014 | Chat transcript cards used 11 pt body text and generous vertical padding. | `_refresh_chat` HTML card rendering | Tightened to 10 pt body text (about 13 px) and reduced card spacing; hover-only actions remain a follow-up |
| UI-AUD-015 | Embedded legacy panels retained their floating-window framing and duplicated headings. | Settings category builders | Improved with shared Settings cards, common headings and spacing, and hidden legacy close/title chrome; individual forms remain on their existing handlers |
| UI-AUD-016 | Updates appeared as a tab in the right chat panel, separate from application preferences. | Main workspace tab construction | Fixed: UpdatePanel is mounted inside Settings → Updates & Maintenance |
| UI-AUD-017 | Permissions page did not distinguish Chat and Agent behavior or explain approval levels and high-impact actions. | Settings → Computer Permissions | Improved with Chat/Agent mode, per-action approval policy, full-access warning, and explicit confirmations; microphone scoping, per-app rules, and temporary access remain TODO |
| UI-AUD-018 | Settings used the platform title bar and border, which appeared gray and visually unrelated to JARVIS. | Settings Center window frame | Fixed with a draggable JARVIS title bar and token-based cyan outline |
| UI-AUD-002 | Multiple preferences have config getters/setters but no visible control was found in the audited Controls/Settings builders. | `hud_style`, `thinking_enabled`, `turn_tuning`, `proactive_audio`, `media_resolution` in `memory/config_manager.py` | Open; determine intended accessibility before moving |
| UI-AUD-003 | No Settings UI was found for lock-at-startup, passcode type/management, face enrollment, or camera selection. | Auth dialog accepts a caller-provided verifier and optional `verify_face`; it does not own enrollment/configuration | Open; locate plugin/core owner |
| UI-AUD-004 | Memory deletion is immediate and has no confirmation step. | `MemoryOverlay` Copy/Forget buttons call clipboard/`_forget` directly | Open; add confirmation during redesign without changing storage API |
| UI-AUD-005 | Plugin Settings TEST currently persists entered fields before invoking the test callback. | `PluginSettingsOverlay` test dispatcher calls `_save_ns` before testing | Open; preserve compatibility but make semantics explicit |
| UI-AUD-006 | Workspace panel visibility and splitter sizes are not persisted; no restore-default-layout action was found. | `_toggle_side_panel`, `_toggle_content_panel`, `_toggle_debug_panel` | Open; layout requirement |
| UI-AUD-007 | Theme switching updates existing stylesheets by replacing palette strings across `QApplication.allWidgets()`. | `retheme_all_widgets` implementation | Open; fragile update path identified in the user brief |
| UI-AUD-008 | Responsive/DPI behavior has no systematic automated matrix. | No tests found for 100/125/150/200% scale, long translations, or narrow windows | Open; add focused tests |
| UI-AUD-009 | Baseline automated tests do not exercise physical audio devices, live providers, face camera, or real installed update flow. | Existing suite uses local/mocked paths | Open; require hardware/environment for full verification |
| UI-AUD-010 | Clear Chat and per-turn Delete remove conversation data without confirmation. | `_clear_chat` writes an empty active chat; `_delete_chat_turn` immediately rewrites chat and history; only Clear History asks first | Open; preserve behavior intentionally during audit; redesign should add explicit confirmation for destructive actions |

The previous left-panel background issue has a focused regression test and is
currently passing in the 76-test baseline.
