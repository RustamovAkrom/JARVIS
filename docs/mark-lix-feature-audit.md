# Mark LIX feature audit for JARVIS

This is a selective integration audit, not a plan to copy Mark LIX wholesale. JARVIS already has its own architecture, UI, providers, safety permissions, plugin system, memory and packaging.

## Already integrated or present in JARVIS

| Mark LIX feature | JARVIS status | Decision |
|---|---|---|
| Window enumeration/control | Integrated on integration branch | Keep; validate cross-platform CI and permission boundaries |
| Screen accessibility view | Integrated in `core/screenview.py` | Keep; strengthen tests and fallback handling |
| App identity/ranked matching | `core/appidentity.py` present | Audit callers; use consistently for open/close/focus |
| Undo stack | `core/undo.py` present | Expand reversible action coverage |
| Structured tool outcomes | `core/outcome.py` present | Standardize action results; avoid false success |
| Echo suppression | `core/echo.py` present | Audit Live audio integration and regression tests |
| Proactive engine | `actions/proactive.py` present | Keep; add presence/quiet-hours gating |
| Background monitor | `actions/background_monitor.py` present | Keep; review polling, dedupe and notification policy |
| Background research | `core/background_tasks.py` present | Keep; bounded, persisted, read-only |

## High-value features to integrate

### P0 — Presence-aware assistant
Port/adapt `core/presence.py` and its action contract:
- OS idle detection, lock state where supported, explicit user "I'm leaving" signal, voice activity.
- Away state and return transition.
- Hold stale notifications while away; summarize useful events when user returns.
- No camera tracking; unknown platform signals must not imply the user is away.
- Connect to proactive notifications, reminders, background tasks and Live speech.

### P0 — Reliable routines/scheduler
Port/adapt `core/routines/`:
- One scheduler with time, startup and idle triggers.
- Persist routines and next-run state.
- Quiet hours; exact-time routines are distinct from discretionary idle prompts.
- Missed-run policy and bounded late delivery.
- Idempotent execution and action outcome reporting.
- Reuse JARVIS reminders/proactive/background-task systems instead of creating parallel schedulers.

### P1 — Conversation recall
Port the retrieval strategy from `memory/conversations.py` / `actions/recall_conversation.py`, but use JARVIS's existing chat/history storage:
- Search by text and date range.
- Summaries/indexing to reduce context.
- Privacy controls: disable, retention, clear/delete, local-only.
- Return source date/snippet and avoid inventing recalled details.
- Do not create a second conversation database unless migration is justified.

### P1 — Situation/context registry
Adapt `core/situation.py`:
- Plugins can register short context providers.
- Prompt pipeline gathers bounded context snapshots.
- Plugins can register a screen/panel host so commands target the visible surface.
- Provider timeout/error isolation; no provider can stall a request.
- Context is ephemeral and not automatically persisted.

### P1 — Sensor/activity registry
Adapt `core/sensors.py`:
- Explicit registry of active camera/screen observers.
- UI indicator, per-sensor stop and Stop All.
- Plugin-owned stop callbacks; thread-safe lifecycle.
- Never silently start camera/screen capture.
- Integrate with current permissions and activity panel.

### P1 — Safer desktop action pipeline
Use Mark LIX's element-first approach:
- Accessibility tree is primary; OCR/vision fallback is explicitly lower-confidence.
- Stable logical coordinates and DPI normalization.
- Every target includes provenance and confidence.
- Resolve ambiguous targets instead of guessing.
- Recheck target before destructive action.
- Use existing JARVIS permission policy, confirmations, outcomes and undo.

### P2 — Better application matching
Audit `core/appidentity.py`:
- Ranked match: exact executable/window title > normalized alias > prefix > substring.
- Detect ambiguity (Discord vs Discord Canary).
- Exclude JARVIS process tree and launcher ancestors from close targets.
- Reuse one resolver for open, close, focus, process queries.

### P2 — Audio algorithm improvements
Audit Mark LIX's `core/clap.py` and `core/echo.py`:
- Relative adaptive noise floor, transient shape and silence windows for optional clap trigger.
- Echo cancellation by content/band energy plus adaptive gain rather than fixed volume threshold.
- Keep voice interruption disabled until real-device testing confirms no false positives.
- Gemini Live remains the speech/language recognizer; do not add redundant language detection on its transcript.

### P2 — Task engine
Mark LIX `core/tasks/` provides checkpointed multi-round tasks, time/round budgets, cancellation, steering and quota backoff. JARVIS's background research manager is simpler and read-only.
Potential integration:
- Start with a generalized task lifecycle around existing background tasks/dev agent.
- Persist checkpoints after each model/tool round.
- Bounded concurrency and token/time budgets.
- Resume only safe tasks after restart.
- Explicit cancellation and progress UI.
- Do not allow unrestricted autonomous computer actions.

## Not recommended to copy directly

- Mark LIX's Qt compatibility shims or UI: JARVIS already has a separate PyQt6 architecture.
- Its plugin catalog wholesale: JARVIS has its own plugin contracts and installed features.
- Its language detector as the authority for Gemini Live speech: Live already handles speech recognition; use it only for typed text if a concrete problem is demonstrated.
- Duplicate schedulers, memories, audio pipelines, permission systems or background workers.
- Any feature that bypasses JARVIS's existing permissions/confirmation model.

## Integration order

1. Presence + event/notification delivery gating.
2. Routine scheduler, reusing reminders and proactive engine.
3. Conversation recall over existing history.
4. Situation registry and sensor/activity indicators.
5. Desktop perception/action reliability improvements.
6. App identity matching audit and caller consolidation.
7. Audio echo/clap review with hardware tests.
8. Generic checkpointed task engine, if it offers a clear improvement over current background tasks.

## Required validation

Each phase gets isolated unit tests, mocked OS/model dependencies, platform-specific tests where applicable, lifecycle tests (start/stop/restart), and CI. Features that touch microphone, camera, screen capture, computer control, memory or autonomous tasks must be opt-in and visible.
