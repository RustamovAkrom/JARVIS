# Presence phase 2 — lifecycle integration

The presence state machine is now started as a named task inside the existing
Live session TaskGroup. It therefore follows the session lifecycle and is
cancelled with the rest of the session tasks.

## Runtime behavior

- Reads presence settings in a worker thread.
- Polls OS idle time in a worker thread every ~2 seconds.
- Samples lock state every third poll to reduce platform/process overhead.
- Updates presence from Gemini Live input transcription.
- Logs only state transitions (away / returned), not continuous activity.
- Handles cancellation explicitly.
- Errors are logged and retried with a longer delay.
- No camera, microphone recording, notification delivery or autonomous speech.

## CI

The test workflow now triggers on push and pull requests for
`integration/mark-lix-features`, in addition to `main` and `v2`. It runs the
existing 3-OS matrix (Windows, Ubuntu, macOS) with Python 3.14 and the full
unittest discovery suite.

## Not yet implemented

- Welcome-back speech or inbox delivery.
- Proactive/monitor/background notification gating.
- UI settings panel.
- Explicit voice command for declaring away.
- Platform-specific manual verification of lock/idle probes.

These are deferred to the next phase to avoid changing notification or Live
conversation behavior in the same commit.
