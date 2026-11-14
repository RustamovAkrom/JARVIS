# JARVIS UI Baseline

Audit date: 2026-11-13  
Branch at audit: `v2`  
Latest commit at audit: `af9cb1f add version`

## Working tree and preservation

The working tree was already dirty before this audit. At the start it contained
modified `main.py` and `ui.py`, untracked `gui/`, three Codicons SVG files, and
`tests/test_ui_facade.py`. These are pre-existing user/session changes and are
preserved. No settings redesign code was changed during this audit; this file
and the related audit documents record findings only.

Recent commits inspected: `af9cb1f`, `6839d63`, `170c937`, `d05eb6f`, and
`bd56796`.

## Current UI architecture

- `main.py` imports `JarvisUI` from top-level `ui.py`.
- `ui.py` is a compatibility facade that forwards names to
  `gui.app.main_window`.
- `gui/app/main_window.py` still contains the bulk of UI implementation,
  including `MainWindow`, overlays, dialogs, terminal integration, chat,
  workspace, and startup/shutdown hooks.
- Extracted modules currently include theme tokens/styles/typography,
  `ChatInput`, system telemetry and its charts, and the debug `LogWidget`.
- Settings are split among the Setup and Controls drawers and several floating
  overlays. There is no unified settings window, category registry, or settings
  search.
- Configuration is primarily read-modify-written through
  `memory/config_manager.py` into the runtime `api_keys.json` selected by
  `core.runtime_paths.PATHS`. Memory has its own storage API.
- Plugin settings are declared by plugin metadata and rendered dynamically.
  Provider settings are declared by provider registry metadata and rendered
  dynamically.
- The legacy workspace/project file page is constructed in code but is not
  added to the visible workspace tabs. The terminal panel and its header toggle
  are also explicitly hidden; retain these as hidden legacy code paths in the
  migration inventory, not as currently available controls.

## Baseline verification

Command: `.venv\\Scripts\\python.exe -m unittest discover -s tests -v`  
Result: **76 tests passed** in 1.388 seconds.

The suite includes settings/config validation, cursor UI, screen selection,
workspace file safety, update service, runtime paths, setup, permissions, and
background task tests. The latest UI tests also instantiate `MainWindow` in
Qt's offscreen mode and check the light-theme left-panel surfaces.

Import/initialization smoke behavior is covered by the UI unit tests. A manual
interactive launch, live provider/API call, audio hardware test, camera, face
enrollment, installed package, portable package, and Velopack update were not
performed as part of this baseline.

## Packaging and resource paths

- `JARVIS.spec` builds a PyInstaller onedir application and includes `assets/icons`,
  `assets/vendor`, `dashboard`, and `actions`, plus selected runtime files.
- `tools/build_release.py` and `.github/workflows/release.yml` are the release
  path; the workflow runs tests before packaging and uses Velopack.
- Runtime data locations are selected by `core/runtime_paths.py`; portable and
  installed data must not be redirected to the replaceable application folder.
- No settings redesign has changed package resources or the runtime path logic.

## Known baseline risks

- The compatibility target is broad: tests and callers import both public and
  private names from `ui.py`.
- The large `gui/app/main_window.py` remains the source for most UI behavior;
  moving controls without maintaining those names and lifetimes risks circular
  imports and signal regressions.
- Settings changes have mixed persistence semantics: immediate writes, Apply
  buttons, previews with rollback, OS-level changes, and reconnect callbacks.
- A passing unit suite does not verify physical devices, real credentials, or
  packaged startup.
- Full details and currently unverified controls are in
  `ui-functionality-inventory.md` and `ui-control-handler-map.md`.
