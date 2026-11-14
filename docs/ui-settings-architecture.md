# Settings Architecture Plan

Stage 1 design, 2026-11-13. This plan follows the Phase 0 inventory and keeps
the existing Qt Widgets stack and UI-to-core callbacks intact.

## Architecture boundaries

```text
MainWindow (existing owner of callbacks and runtime objects)
  └─ SettingsCenter (Qt dialog, one instance per MainWindow)
       ├─ SettingsRegistry (immutable search/category metadata)
       ├─ SettingsNavigation (category selection and search results)
       └─ Category pages (widgets that receive a narrow host/callback object)
            └─ Existing config helpers and overlay/controller methods
```

- `main.py` and the `ui.py` compatibility facade remain unchanged.
- New code lives under `gui/settings/`; it may import theme tokens and Qt, but
  must not import `gui.app.main_window` or `ui.py`.
- `MainWindow` owns the `SettingsCenter`, supplies callbacks/config access, and
  remains the integration boundary for audio reconnects, wake state, updates,
  plugin loading, and other runtime services.
- Category widgets do not instantiate `MainWindow`, access module globals, or
  perform core work directly. They receive an injected host adapter with only
  the callbacks and state needed by that page.
- Preserve legacy overlay classes and public compatibility imports while
  migrating. Remove or hide an old entry point only after replacement behavior
  and its integrations have regression coverage.

## Registry and search contract

Each searchable setting has stable metadata: `id`, category ID, title,
description, keywords, page ID, and a widget/object name anchor. Registry
metadata drives navigation and search only. Pages own the actual widgets so
search never renders duplicate controls. A result navigates to the page, scrolls
to its setting row, focuses the control when safe, and exposes the description.

Category metadata provides stable ID, display title, short description, and
page factory. Search is case-insensitive across title, category, description,
and keywords. Up/Down changes the selected result; Enter opens it; Escape
returns focus to the search field or closes the search results. Empty results
have an explicit message. All search targets must be registered in tests.

## Category destinations

| Category | Inventory destination | Migration boundary |
|---|---|---|
| General | language, auto-start, fullscreen, morning brief, wake word, PTT, shortcuts | Keep existing MainWindow handlers; label restart/reconnect effects |
| Assistant Identity | assistant/user name, voice | Reuse Customize overlay save/preview/cancel logic through injected callbacks |
| Voice & Audio | input/output device, tests, output volume, wake/PTT | Keep audio overlay workers and reconnect callback; no blocking device work in page |
| Appearance & Personalization | presets, accent, opacity, HUD, cursor appearance | Reuse palette/theme APIs; config-only HUD values remain visible only if current API supports safe edit |
| Workspace & Layout | panel visibility and splitters; workspace/project controls marked hidden in current UI | Do not restore hidden workspace file browser without an explicit migrated feature path; add layout persistence only with a compatible format |
| Cursor Assistant | enable, style, size, hotkeys, menu actions | Keep existing config validation and cursor refresh calls |
| Computer Permissions | five existing permissions | Keep existing permission keys and make enabled/disabled state explicit |
| Integrations & Providers | mode, pipeline, credentials, sidecar routing | Retain schema-driven provider fields, validation, Apply and reconnect signals |
| Plugins | installed status, enable, settings, tests | Retain dynamic plugin metadata/schema and existing callbacks |
| Memory | list, copy, forget | Preserve storage API; add confirmation to destructive forget action |
| Security & Access | currently only caller-provided lock dialog is in scope | Do not invent enrollment, passcode or camera settings without an existing owner/API |
| Developer & Diagnostics | developer mode, debug visibility, logs/system details | Preserve console signals and visibility behavior |
| Updates & Maintenance | version/update status and update actions | Reuse UpdatePanel/service; maintain install confirmation and async workers |

## Persistence and interaction contract

- Existing config keys, helper functions, defaults, and runtime path selection
  remain the source of truth.
- Preserve each control's current save semantics during migration: immediate
  controls stay immediate; existing Apply/Cancel previews retain rollback; a
  page must not introduce a second unsaved copy of an immediately persisted
  value.
- Multi-field Apply pages validate first and only call their existing save or
  apply boundary after validation. Cancel restores previewed appearance and
  does not write uncommitted values.
- Errors from callbacks are surfaced inline; failed writes/reconnects must not
  leave a control visually claiming success.
- Secrets remain masked and are excluded from search text, logs, and diagnostic
  summaries. Existing provider field metadata continues to define secret input
  behavior.
- Existing destructive actions require a confirmation before deletion. No
  setting key is renamed as part of this UI work.

## Window and page lifecycle

- One reusable SettingsCenter instance per MainWindow, created on first open and
  raised on subsequent requests.
- A fixed navigation sidebar and a scrollable page host use Qt layouts and
  size policies. Narrow windows may collapse the sidebar behind a category
  selector; no absolute positioning is used.
- The current navigation shell creates lightweight category pages at window
  construction. During migration, expensive provider/plugin forms and worker
  backed views should load on first visit and remain cached for the dialog life.
- Long-running audio tests, provider checks, plugin tests, and update operations
  continue to use existing workers/signals. The dialog only reflects progress
  and results.
- Avoid importing categories from `MainWindow`; use dependency injection to
  prevent a `gui.settings` ↔ `gui.app.main_window` import cycle.

## Implementation sequence and exit checks

1. Create theme/component primitives without changing core integration.
2. Build navigation, category page host, registry, and keyboard-capable search.
3. Migrate General, Identity, Voice & Audio, Appearance, and Workspace in order.
4. Migrate advanced categories in the order in the supplied brief.
5. Keep old dialogs available until feature-level parity is verified; then
   consolidate launch points and remove duplicate entry paths.
6. Update the migration matrix and run focused tests after each category, then
   the full unittest suite and package/resource review.

No existing setting is considered migrated until the inventory entry points to
the new control, its old handler/config semantics are verified, and a relevant
regression test covers the behavior. Hardware, live provider, camera, and
installed-package checks remain separately identified when the environment
cannot verify them.
