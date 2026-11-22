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
- [x] Enforce enabled/connected state, blocked-tool policy, and schema validation at service-level MCP execution.
- [x] Fail closed for missing native adapters and invalid argument types.
- [x] Close stale MCP sessions after failed STDIO/HTTP/SSE connect attempts.
- [x] Capture native connection ID in disconnect callbacks to prevent selection races.
- [x] Run reconnect from Details dialog in a worker instead of blocking Qt UI.
- [x] Audit Connected Apps theme-token references; no undefined tokens remain.
- [x] Add regression tests for service guards, adapter isolation, and failed-connect cleanup.
- [x] Fix HTTP/SSE failed-connect stale-session cleanup.
- [x] Fix runtime disconnect shutdown handling and avoid unawaited coroutine warnings.
- [x] Surface disconnect failures through Service/UI instead of silently marking disconnected.
- [x] Replace implicit permission checkbox persistence with explicit Apply permissions action.
- [x] Make permission apply persist read-only automation and enabled/reconnect state together.
- [x] Fix tool activity regression test to mock discovered runtime tools.
- [x] Add regression test for runtime disconnect when event loop is stopped.
- [x] Fix HTTP/SSE failed-connect stale-session cleanup.
- [x] Fix runtime shutdown disconnect handling and avoid unawaited coroutine warnings.
- [x] Surface disconnect failures through Service/UI instead of silently marking disconnected.
- [x] Replace implicit permission checkbox persistence with explicit Apply permissions action.
- [x] Persist read-only automation and enabled/reconnect state together.
- [x] Fix tool activity regression test to mock discovered runtime tools.
- [x] Add regression test for runtime disconnect when event loop is stopped.
- [x] Continue audit for lifecycle, OAuth, permissions, and UI regressions.
- [ ] Run focused Connected Apps suite locally.

### Stage 2 — Runtime lifecycle and concurrency
- [x] Serialize connect/reconnect/call/disconnect runtime operations per connection ID.
- [x] Ensure shutdown continues when an individual disconnect cannot be confirmed.
- [x] Fix test_connected_apps_tool_calls_record_activity_and_errors to mock runtime tool discovery.
- [x] Prevent native GitHub/Google Calendar duplicate connect actions while already connected.
- [x] Use schema-valid arguments in runtime-error activity regression test.
- [x] Serialize service lifecycle, health-check, and tool execution operations per connection ID using reentrant locks.
- [x] Hide native Connect buttons when the app is already connected; connected status remains visible.
- [x] Add regression tests for hidden native Connect actions and service-level lock identity.
- [x] Keep native Connect actions hidden when a connected app is selected; selection only controls Disconnect availability.
- [x] Add regression test ensuring native selection does not reveal Connect.
- [x] Make failed-connect and reconnect-retry cleanup best-effort so cleanup errors cannot mask the original failure.
- [x] Add regression tests for cleanup failures across STDIO, HTTP, and SSE.
- [x] Audit service lifecycle serialization and native-vs-MCP disconnect/remove behavior.
- [x] Prevent native app lifecycle operations from calling the MCP runtime.
- [x] Preserve original HTTP/SSE connection errors when cleanup fails.
- [x] Add regression tests for native lifecycle and HTTP/SSE cleanup failures.
- [x] Preserve original MCP connection errors when transport cleanup also fails.
- [x] Audit timeout cancellation and event-loop pending-task cleanup paths; cancellation is propagated and loop shutdown drains pending tasks.
- [ ] Final focused test run and review remaining timeout/cancellation behavior.
- [x] Fix runtime event-loop startup deadlock caused by ready-thread lock acquisition.
- [x] Add regression test for runtime loop startup and shutdown.
- [x] Event-loop shutdown cancels and drains pending tasks.
- [x] Reconnect-all behavior and exponential retry backoff implemented.
- [ ] Audit reconnect-all cancellation and shutdown interaction.
- [ ] Health check state transitions.
- [x] Ensure failed connect cleanup is best-effort and original connection error remains visible.
- [ ] Ensure failed connect does not leave stale sessions or credentials.

### Stage 3 — OAuth and credential security
- [x] Validate authorization/token endpoint URL schemes and hosts.
- [x] Validate client ID, authorization code, verifier, state, and PKCE inputs.
- [x] Validate authorization/token endpoint URL schemes and hosts.
- [x] Validate client ID, authorization code, verifier, state, and PKCE inputs.
- [x] Add regression tests for unsafe endpoints and missing PKCE verifier.
- [x] Add regression test for refresh-token rotation persistence.
- [x] PKCE/state/callback validation audit and additional edge-case tests.
- [x] Restrict OAuth callback binding to loopback addresses.
- [x] Require redirect URI to be loopback `/callback` with no query/fragment/userinfo.
- [x] Validate discovered authorization-server issuer and endpoint URLs.
- [x] Validate protected-resource authorization-server metadata types and scope metadata.
- [x] Per-connection token refresh locking with post-lock token recheck.
- [x] Persist refresh-token rotation when provider returns a replacement.
- [x] Persist token sets and stale-token removals in one atomic store write when supported.
- [x] Clear stale refresh/id tokens when the new token response omits them.
- [x] Normalize unsafe OAuth discovery endpoint validation failures into stable discovery errors.
- [x] Audit token refresh failure handling: preserve stored refresh credentials and require reauthorization only when refresh cannot proceed.
- [x] Audit refresh failure state and authorization metadata persistence.
- [x] Atomic token-set persistence and stale optional-token cleanup.
- [x] Local credential revocation removes the complete credential set and refresh-lock state.
- [x] OAuth token storage fallback supports credential stores without an optional delete method.
- [x] Credential deletion/revocation behavior: local revoke clears all credentials and refresh locks; connection removal clears persisted credentials.
- [x] Redact OAuth token exchange and refresh request exceptions from user-facing errors.
- [x] Audit secret redaction in OAuth exchange/refresh errors and Connected Apps diagnostics; credential values are not included in diagnostics.
- [x] Validate OAuth scope tokens and state length at authorization URL construction.
- [x] Validate provider configuration behavior and scope persistence; provider scopes remain explicit and stored per connection.

### Stage 4 — Tool execution and permissions
- [x] MCP schema validation edge cases: type unions, combinators, numeric/string/array/object constraints, pattern properties, schema-based additional properties.
- [x] Fail-closed annotation handling; only explicit boolean `true` is trusted.
- [x] Blocked tool enforcement across MCP/native paths at service execution boundary.
- [x] Read-only auto-approval requires explicit connection opt-in plus read-only and non-destructive annotations.
- [x] Confirmation requirements for writes/destructive actions.
- [x] Tool discovery and stale-tool handling: execution re-resolves tool against current enabled/connected connection state.
- [x] Added regression tests for schema combinators, unsupported schema keywords, pattern properties, and annotation truthiness.

### Stage 5 — UI/UX and theme audit
- [x] Light/Dark theme and accent compatibility audit; theme tokens are applied to all panel surfaces.
- [x] Native app status, icon, selection, and disconnect state reviewed.
- [x] Details dialog layout and permissions controls reviewed.
- [x] Busy state and duplicate-action protection reviewed.
- [x] Error dialogs and actionable messages reviewed.
- [x] Responsive sizing and accessibility basics reviewed (word wrapping, bounded lists, labels/tooltips).
- [x] First-show gray surface regression mitigation: repolish at show, tab changes, and delayed layout/paint intervals.

### Stage 6 — Test and documentation completion
- [ ] Focused test suite passes (must be run in the user's local checkout; GitHub connector cannot execute pytest).
- [ ] Full suite passes (must be run in the user's local checkout).
- [x] Optional dependency absence tests are present.
- [x] Add regression tests for defects found during static audit.
- [x] Update Connected Apps documentation and known limitations.

### Stage 7 — Provider expansion
Only after Stages 1–6 are complete:
- [ ] Select the next native integration based on user value.
- [ ] Define scopes and least-privilege permissions.
- [ ] Implement adapter, UI, confirmation policy, tests, and docs together.

## Scope Guard

During Stages 1–6, do not add unrelated JARVIS features, redesign other settings pages, refactor unrelated subsystems, or introduce new providers. If a finding is outside Connected Apps, record it separately and continue with the current stage.


## Current implementation note

The GitHub branch contains Stage 4 schema-validation and permission hardening, plus a Stage 5 first-show surface polish fix. Source changes and regression tests were committed directly to `v2`. Tests have **not** been executed by this remote editing session; local pytest results are required before marking Stage 6 complete.
