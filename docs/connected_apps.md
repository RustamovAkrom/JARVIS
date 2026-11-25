# Connected Apps

JARVIS Connected Apps has two separate integration layers:

- **Native Apps**: first-party adapters with curated API operations and stable AI tool schemas.
- **MCP Servers**: developer integrations using STDIO, Streamable HTTP, SSE, MCP OAuth and catalog templates.

Native Apps use the shared connection store, protected credential storage, connection lifecycle, tool validation, permission checks, confirmation flow and AI tool registry. MCP remains optional and isolated from the core.

## Native adapter contract

Native adapters live under `core/connected_apps/adapters/<app_id>/` and implement:

- `descriptor`: app metadata and auth provider/scopes.
- `tools`: typed tool definitions with JSON Schema and read/write policy.
- `execute(connection_id, tool_name, arguments)`: provider API operation.
- `health_check(connection_id)`: validate the connection.

The platform provides the native registry, connection records, credential storage, AI discovery and execution path.

## GitHub

The first native integration is GitHub. Current tools:

| Tool | Access |
|---|---|
| `github_get_profile` | Read |
| `github_list_repositories` | Read |
| `github_search_repositories` | Read |
| `github_list_issues` | Read |
| `github_get_issue` | Read |
| `github_list_pull_requests` | Read |
| `github_get_file` | Read |
| `github_search_code` | Read |
| `github_get_pull_request` | Read |
| `github_list_commits` | Read |
| `github_create_issue` | Write; confirmation |
| `github_create_issue_comment` | Write; confirmation |

GitHub credentials are stored through the Connected Apps credential store. API requests use the official GitHub REST API and a bounded timeout. Error responses avoid returning credential material.

### OAuth setup

GitHub OAuth Apps require a registered OAuth Client ID and Client Secret. JARVIS cannot ship a shared secret safely in a desktop application. Create a GitHub OAuth App and use the OAuth button in Connected Apps to authorize it. The callback uses a loopback listener and PKCE/state validation. The user may alternatively use a fine-grained Personal Access Token if OAuth app setup is not desired.

Recommended least-privilege token permissions depend on the tools used. Read operations should use read-only repository permissions. Issue creation/commenting requires Issues write access.

## AI execution

Native and MCP tools are discovered by the Connected Apps agent. Gemini Live receives namespaced tool declarations and JARVIS maps calls back to the adapter. Native write operations require confirmation; tool schemas are validated before execution. A disconnected, disabled or blocked app does not expose its tools.

## Development and tests

Native adapter tests are in `tests/test_connected_apps_native_github.py`. Run:

```powershell
python -m pytest -v tests/test_connected_apps_native_github.py
python -m pytest -v
```


## Google Calendar

Google Calendar is a native integration using the Google OAuth provider. JARVIS requests the `calendar.events` scope and stores OAuth credentials in the Connected Apps credential store.

| Tool | Access |
|---|---|
| `google_calendar_list_events` | Read |
| `google_calendar_get_event` | Read |
| `google_calendar_create_event` | Write; confirmation |

The adapter uses the Google Calendar API v3, bounded requests, and refresh-token support through the shared OAuth service. Event creation is always confirmation-gated. Configure a Google OAuth Client ID in Connected Apps; client secrets are optional for supported desktop OAuth configurations.
