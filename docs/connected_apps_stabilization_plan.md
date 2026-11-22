# Connected Apps — Stabilization Plan

## Goal

Finish the Connected Apps subsystem without scope drift: stabilize existing MCP and native integrations, make security/permission behavior predictable, eliminate UI regressions, and establish repeatable test coverage before adding more providers.

## Definition of Done

- Core JARVIS works when MCP SDK and native provider dependencies are unavailable.
- A failure in one optional native adapter cannot disable another adapter or MCP.
- MCP STDIO, HTTP/Streamable HTTP, and SSE lifecycle behavior is covered by tests.
- OAuth callback, PKCE, metadata discovery, token storage, refresh, and failure paths are tested.
- Tool argument validation, blocked tools, read-only auto-run, and confirmation gates fail closed.
- Native app health checks use the adapter instance belonging to the active service/store.
- Connected Apps UI opens and operates in Light/Dark themes without undefined tokens or stale state.
- Errors shown to users are actionable and secrets are redacted.
- Regression tests cover every bug found during this stabilization.
- No new native providers are added until this checklist is complete.

## Work Plan

### Stage 1 — Baseline audit and immediate reliability fixes
- [x] Inspect Connected Apps architecture, service, runtime, OAuth, credential store, native adapters, agent execution, UI, and tests.
- [x] Fix native adapter initialization so each optional adapter fails independently.
- [x] Fix native health checks to use the service's own adapter/store instance.
- [x] Make native disconnect feedback provider-neutral.
- [ ] Continue audit for lifecycle, OAuth, permissions, and UI regressions.
- [ ] Add tests for fixes and run focused Connected Apps suite.

### Stage 2 — Runtime lifecycle and concurrency
- [ ] Connect/reconnect/disconnect/remove race audit.
- [ ] Timeout and cancellation audit.
- [ ] Event-loop shutdown and pending-task cleanup.
- [ ] Reconnect-all behavior and backoff.
- [ ] Health check state transitions.
- [ ] Ensure failed connect does not leave stale sessions or credentials.

### Stage 3 — OAuth and credential security
- [ ] PKCE/state/callback validation.
- [ ] OAuth metadata and endpoint validation.
- [ ] Token refresh locking and rotation.
- [ ] Credential deletion/revocation behavior.
- [ ] Secret redaction in logs, diagnostics, and UI.
- [ ] Validate scope and provider configuration behavior.

### Stage 4 — Tool execution and permissions
- [ ] MCP schema validation edge cases.
- [ ] Fail-closed annotation handling.
- [ ] Blocked tool enforcement across MCP/native paths.
- [ ] Read-only auto-approval policy.
- [ ] Confirmation requirements for writes/destructive actions.
- [ ] Tool discovery and stale-tool handling.

### Stage 5 — UI/UX and theme audit
- [ ] Light/Dark theme and accent compatibility.
- [ ] Native app status, icon, selection, and disconnect state.
- [ ] Details dialog layout and permissions controls.
- [ ] Busy state and duplicate-action protection.
- [ ] Error dialogs and actionable messages.
- [ ] Responsive sizing and accessibility.

### Stage 6 — Test and documentation completion
- [ ] Focused test suite passes.
- [ ] Full suite passes.
- [ ] Optional dependency absence tests pass.
- [ ] Add regression tests for all defects.
- [ ] Update Connected Apps documentation and known limitations.

### Stage 7 — Provider expansion
Only after Stages 1–6 are complete:
- [ ] Select the next native integration based on user value.
- [ ] Define scopes and least-privilege permissions.
- [ ] Implement adapter, UI, confirmation policy, tests, and docs together.

## Scope Guard

During Stages 1–6, do not add unrelated JARVIS features, redesign other settings pages, refactor unrelated subsystems, or introduce new providers. If a finding is outside Connected Apps, record it separately and continue with the current stage.
