# Presence integration — phase 1

This phase adds an isolated state machine and persistence API. It is intentionally
not wired into the Live session, notification delivery, proactive engine, or UI
yet. Integration is a separate phase so lifecycle changes can be reviewed and
tested without risking audio/session stability.

## Files

- `core/presence.py`: state machine, optional OS idle/lock probes, bounded inbox.
- `memory/config_manager.py`: validated persisted presence settings.
- `tests/test_presence.py`: deterministic state-transition tests.
- `tests/test_presence_config.py`: config validation/persistence tests.

## Settings

```json
{
  "presence": {
    "enabled": true,
    "away_minutes": 10,
    "greet": true
  }
}
```

Defaults are enabled, 10 idle minutes, and welcome-back enabled. Timeout is
clamped to 1–240 minutes.

## Safety properties

- No camera, recording, or network access.
- Unknown OS idle/lock state is treated as unknown, never as away.
- Lock detection is debounced.
- Explicit away declaration has a short grace window.
- Inbox is bounded to 12 items and deduplicates identical text.
- State and inbox are protected by a lock.
- Presence engine has no autonomous thread in this phase.

## Next phase

Add lifecycle wiring in the application host: initialize from config, mark
activity when user speech is received, poll OS state from a bounded worker,
and stop the worker during shutdown. Then add delivery gating for proactive
notifications/background results. Only after that should a UI settings panel
and welcome-back summary be introduced.
