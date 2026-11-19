# Mark LIX Integration Audit

This branch is an isolated integration branch based on `v2`. The integration
is additive and must preserve JARVIS-specific systems.

## Protected systems

- `main.py` and `ui.py` runtime/UI lifecycle
- AI provider routing and configuration
- plugin discovery and plugin contracts
- Developer Mode / Dev Agent
- Gesture Control, WebRTC, HUD and activity panels
- Memory and user-data migration
- update service, Velopack, PyInstaller and release workflow
- source / installed / portable runtime paths
- permissions and confirmation gates

## Imported in this phase

| Component | Origin | Status | Notes |
|---|---|---|---|
| `core/screenview.py` | Mark LIX | Added | Cross-platform window enumeration and accessibility data. Optional platform backends; graceful degradation is required. |
| `actions/window_control.py` | Mark LIX | Added | Window actions, verified outcomes, application volume and confirmation-gated close-others. Automatically discovered by the existing action loader via `TOOL`. |
| Existing `core/action_loader.py` | JARVIS v2 | Preserved | Its `TOOL` discovery contract is compatible; no replacement. |
| Existing `core/appidentity.py` | JARVIS v2 | Preserved | Window action uses existing application identity and own-process protection. |
| Existing `core/outcome.py` | JARVIS v2 | Preserved | Compatible outcome API. |
| Existing `core/confirm.py` | JARVIS v2 | Preserved | Irreversible close-others action requires HUD confirmation. |

## Safety properties

- Never target JARVIS or its parent/launcher process tree.
- Do not use global keyboard shortcuts for named-window operations.
- Verify window state after an operation; report partial outcomes as unverified.
- Closing multiple applications requires UI confirmation.
- Unsupported platform capabilities return explicit failures.
- Optional OS dependencies must not prevent JARVIS startup.

## Planned phases

1. Window management — imported, regression tests added; runtime CI still needs to run.
2. Presence — integrate with existing proactive/background lifecycle, avoiding duplicate polling.
3. Conversation recall — integrate with existing memory, retention and privacy settings.
4. Routines — consolidate rather than introduce a competing scheduler beside Reminder/Proactive.
5. Audio/wake-word — only after compatibility and measured startup/audio regression tests.
6. Remove obsolete code — only after usage analysis, replacement tests, and migration checks.

## Verification status

The GitHub integration branch contains the code and regression tests. Local Python
execution and CI have not been run from this environment; do not treat this branch
as verified until its workflow passes on supported operating systems.
