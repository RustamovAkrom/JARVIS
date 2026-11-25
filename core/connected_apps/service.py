"""Connected Apps orchestration API."""
from __future__ import annotations

import json
import secrets
import time
import threading
import logging
import inspect
from functools import wraps
from core.connected_apps.models import AppConnection, AppDescriptor, AuthType, ConnectionStatus, TransportType
from core.connected_apps.registry import registry
from core.connected_apps.store import ConnectedAppsStore
from core.connected_apps.runtime import runtime

logger = logging.getLogger(__name__)


def _serialize(row):
    return {
        "id": row.id, "app_id": row.app_id, "display_name": row.display_name,
        "status": row.status.value, "enabled": row.enabled,
        "auth_type": row.auth_type.value, "scopes": list(row.scopes),
        "created_at": row.created_at, "updated_at": row.updated_at,
        "last_error": row.last_error, "metadata": dict(row.metadata),
    }


def _auto_approve_read_only(metadata, *, read_only, destructive=False, requires_confirmation=False):
    """Shared fail-closed policy for MCP and native tool execution."""
    return (
        isinstance(metadata, dict)
        and metadata.get("auto_approve_read_only") is True
        and read_only is True
        and destructive is not True
        and requires_confirmation is not True
    )


def _safe_error(exc: Exception) -> str:
    import re
    text = str(exc).replace("\r", " ").replace("\n", " ")
    text = re.sub(r"(?i)\bBearer\s+[^\s,;]+", "Bearer [redacted]", text)
    text = re.sub(
        r"(?i)(?:access_token|refresh_token|token|secret|password|api[_-]?key|authorization)\s*[=:]\s*[^\s,;]+",
        "[redacted]",
        text,
    )
    text = re.sub(r"(?i)(://[^:/@]+:)[^@]+@", r"\1[redacted]@", text)
    text = re.sub(r"(?i)(https?://)[^/@\s]+@", r"\1[redacted]@", text)
    return f"{type(exc).__name__}: {text[:500]}"



def _serialized_connection_operation(method):
    """Serialize lifecycle and execution operations for an existing connection."""
    signature = inspect.signature(method)

    @wraps(method)
    def wrapped(self, *args, **kwargs):
        bound = signature.bind_partial(self, *args, **kwargs)
        cid = bound.arguments.get("cid") or bound.arguments.get("connection_id")
        if not cid:
            return method(self, *args, **kwargs)
        with self._connection_lock(cid):
            return method(self, *args, **kwargs)

    return wrapped

class ConnectedAppsService:
    def __init__(self, app_registry=None, store=None):
        self.registry = app_registry or registry
        self.store = store or ConnectedAppsStore()
        self._adapter_lock = threading.RLock()
        self._connection_lock_guard = threading.RLock()
        self._connection_locks = {}
        self._adapters = {}
        self._native_adapters = {}
        # Native adapters are optional and must fail independently. A broken
        # provider must not prevent other native apps or the MCP subsystem from
        # being initialized.
        self._register_native_adapter("github", "core.connected_apps.adapters.github", "GitHubAdapter")
        self._register_native_adapter(
            "google_calendar", "core.connected_apps.adapters.google_calendar", "GoogleCalendarAdapter"
        )
        self._register_native_adapter(
            "telegram_personal", "core.connected_apps.adapters.telegram_personal", "TelegramPersonalAdapter"
        )

    def _connection_lock(self, cid):
        with self._connection_lock_guard:
            return self._connection_locks.setdefault(str(cid), threading.RLock())

    def _register_native_adapter(self, app_id, module_name, class_name):
        try:
            import importlib
            from core.connected_apps.native import native_registry

            module = importlib.import_module(module_name)
            adapter_class = getattr(module, class_name)
            adapter = adapter_class(self.store)
            self._native_adapters[app_id] = adapter
            if native_registry.get(app_id) is None:
                native_registry.register(adapter)
        except Exception:
            logger.exception("Failed to initialize optional native Connected App: %s", app_id)

    @staticmethod
    def normalize(name):
        return " ".join(str(name or "").strip().casefold().split())

    def register(self, adapter, replace=False):
        key = self.normalize(getattr(adapter, "name", ""))
        if not key or not callable(getattr(adapter, "execute", None)):
            raise TypeError("Adapter must have a non-empty name and execute()")
        with self._adapter_lock:
            if key in self._adapters and not replace:
                raise ValueError(f"Adapter '{key}' already registered")
            self._adapters[key] = adapter

    def get(self, name):
        key = self.normalize(name)
        with self._adapter_lock:
            direct = self._adapters.get(key)
            if direct:
                return direct
            matches = [a for a in self._adapters.values()
                       if self.normalize(getattr(a, "display_name", "")) == key]
            return matches[0] if len(matches) == 1 else None

    def unregister(self, name):
        key = self.normalize(name)
        with self._adapter_lock:
            if key in self._adapters:
                return self._adapters.pop(key, None) is not None
            matches = [k for k, a in self._adapters.items()
                       if self.normalize(getattr(a, "display_name", "")) == key]
            return self._adapters.pop(matches[0], None) is not None if len(matches) == 1 else False

    def list_apps(self):
        with self._adapter_lock:
            return [{"name": a.name, "display_name": getattr(a, "display_name", a.name)}
                    for a in self._adapters.values()]

    def summary(self):
        return ", ".join(a["display_name"] for a in self.list_apps())

    def catalog(self, query="", category=""):
        return self.registry.search(query, category=category)

    def connections(self):
        return self.store.list_connections()

    def _cleanup_failed_runtime_connection(self, cid):
        """Best-effort cleanup that must never replace the original connect error."""
        try:
            runtime.disconnect(cid)
        except Exception:
            logger.warning("Failed to clean up runtime after connection failure for %s", cid, exc_info=True)

    def _begin_connection(self, cid, descriptor, display_name, transport, metadata):
        previous = self.store.get_connection(cid)
        row = AppConnection(
            id=cid, app_id=descriptor.id, display_name=display_name or descriptor.name,
            status=ConnectionStatus.CONNECTING, auth_type=descriptor.auth_type,
            scopes=list(descriptor.scopes), created_at=(previous or {}).get("created_at", time.time()),
            metadata={**dict((previous or {}).get("metadata") or {}), "transport": transport, **metadata},
        )
        self.store.upsert_connection(_serialize(row))
        return row

    @_serialized_connection_operation
    def connect_stdio(self, descriptor, display_name=None, connection_id=None):
        cid = connection_id or secrets.token_urlsafe(16)
        row = self._begin_connection(cid, descriptor, display_name, "stdio",
                                     {"command": descriptor.command, "args": list(descriptor.args)})
        env = dict(descriptor.env or {})
        previous_env_keys = set((self.store.get_connection(cid) or {}).get("metadata", {}).get("env_keys", []))
        delete_credential = getattr(self.store, "delete_credential", None)
        if callable(delete_credential):
            for stale_key in previous_env_keys - set(env):
                delete_credential(cid, f"stdio_env:{stale_key}")
        for key, value in env.items():
            self.store.set_credential(cid, f"stdio_env:{key}", value)
        row.metadata["env_keys"] = list(env)
        try:
            tools = runtime.connect_stdio(cid, descriptor.command, descriptor.args, env)
            row.status = ConnectionStatus.CONNECTED
            row.metadata["tools"] = tools
        except Exception as exc:
            # Fail closed: a failed reconnect must not leave an older runtime
            # session active under the same connection ID.
            self._cleanup_failed_runtime_connection(cid)
            row.status = ConnectionStatus.ERROR
            row.last_error = _safe_error(exc)
        data = _serialize(row)
        self.store.upsert_connection(data)
        return data

    @_serialized_connection_operation
    def connect_http(self, descriptor, display_name=None, connection_id=None, headers=None):
        cid = connection_id or secrets.token_urlsafe(16)
        headers = dict(headers or {})
        row = self._begin_connection(cid, descriptor, display_name, "http",
                                     {"endpoint": descriptor.endpoint})
        authorization = headers.get("Authorization")
        if authorization:
            self.store.set_credential(cid, "authorization_header", authorization)
        else:
            delete_credential = getattr(self.store, "delete_credential", None)
            if callable(delete_credential): delete_credential(cid, "authorization_header")
        try:
            tools = runtime.connect_http(cid, descriptor.endpoint, headers=headers)
            row.status = ConnectionStatus.CONNECTED
            row.metadata["tools"] = tools
        except Exception as exc:
            # Failed cleanup must not mask the original transport failure.
            self._cleanup_failed_runtime_connection(cid)
            row.status = ConnectionStatus.ERROR
            row.last_error = _safe_error(exc)
        data = _serialize(row)
        self.store.upsert_connection(data)
        return data

    @_serialized_connection_operation
    def connect_sse(self, descriptor, display_name=None, connection_id=None, headers=None):
        cid = connection_id or secrets.token_urlsafe(16)
        headers = dict(headers or {})
        row = self._begin_connection(cid, descriptor, display_name, "sse",
                                     {"endpoint": descriptor.endpoint})
        authorization = headers.get("Authorization")
        if authorization:
            self.store.set_credential(cid, "authorization_header", authorization)
        else:
            delete_credential = getattr(self.store, "delete_credential", None)
            if callable(delete_credential): delete_credential(cid, "authorization_header")
        try:
            tools = runtime.connect_sse(cid, descriptor.endpoint, headers=headers)
            row.status = ConnectionStatus.CONNECTED
            row.metadata["tools"] = tools
        except Exception as exc:
            # Failed cleanup must not mask the original transport failure.
            self._cleanup_failed_runtime_connection(cid)
            row.status = ConnectionStatus.ERROR
            row.last_error = _safe_error(exc)
        data = _serialize(row)
        self.store.upsert_connection(data)
        return data

    @_serialized_connection_operation
    def reconnect(self, cid, *, attempts=3, backoff=0.5):
        row = self.store.get_connection(cid)
        if not row:
            return None
        if not row.get("enabled", True):
            return {**row, "status": ConnectionStatus.DISCONNECTED.value,
                    "last_error": "Connection is disabled"}
        meta = row.get("metadata", {})
        if meta.get("native"):
            adapter = self._native_adapters.get(str(row.get("app_id") or "").casefold())
            if not adapter:
                row["status"] = ConnectionStatus.ERROR.value
                row["last_error"] = "Native adapter is unavailable"
                self.store.upsert_connection(row)
                return row
            try:
                healthy = bool(adapter.health_check(cid))
                if not healthy:
                    raise RuntimeError("Native adapter health check returned unhealthy")
                row["status"] = ConnectionStatus.CONNECTED.value
                row["last_error"] = ""
                meta["health"] = "healthy"
            except Exception as exc:
                row["status"] = ConnectionStatus.ERROR.value
                row["last_error"] = _safe_error(exc)
                meta["health"] = "unhealthy"
            row["metadata"] = meta
            row["updated_at"] = time.time()
            self.store.upsert_connection(row)
            return row
        descriptor = AppDescriptor(
            id=row["app_id"], name=row["display_name"], source="stored",
            transport=(TransportType.SSE if meta.get("transport") == "sse" else TransportType.HTTP if meta.get("transport") == "http" else TransportType.STDIO),
            endpoint=meta.get("endpoint", ""), command=meta.get("command", ""),
            args=meta.get("args", []),
            env={key: self.store.get_credential(cid, f"stdio_env:{key}") for key in meta.get("env_keys", [])},
        )
        attempts = max(1, min(int(attempts), 5))
        last_result = None
        for attempt in range(attempts):
            if descriptor.transport == TransportType.SSE:
                last_result = self.connect_sse(descriptor, connection_id=cid, headers=self._saved_http_headers(cid))
            elif descriptor.transport == TransportType.HTTP:
                last_result = self.connect_http(descriptor, connection_id=cid, headers=self._saved_http_headers(cid))
            else:
                last_result = self.connect_stdio(descriptor, connection_id=cid)
            if last_result and last_result.get("status") == ConnectionStatus.CONNECTED.value:
                return last_result
            if attempt + 1 < attempts:
                self._cleanup_failed_runtime_connection(cid)
                time.sleep(max(0.0, float(backoff)) * (2 ** attempt))
        return last_result

    def _saved_http_headers(self, cid):
        provider = self.store.get_credential(cid, "oauth_provider")
        mcp_metadata = self.store.get_credential(cid, "oauth_metadata")
        if provider or mcp_metadata:
            from core.connected_apps.auth_service import OAuthService
            token = OAuthService(self.store).get_access_token(cid)
            token_type = self.store.get_credential(cid, "token_type") or "Bearer"
            return {"Authorization": f"{token_type} {token}"}
        value = self.store.get_credential(cid, "authorization_header")
        return {"Authorization": value} if value else {}

    def reconnect_all(self):
        """Re-establish persisted MCP sessions after application startup."""
        results = []
        for row in self.connections():
            if not row.get("enabled", True):
                continue
            cid = row.get("id")
            if not cid:
                continue
            try:
                result = self.reconnect(cid)
                results.append({"id": cid, "name": row.get("display_name", ""),
                    "status": result.get("status", "error") if result else "missing",
                    "error": result.get("last_error", "") if result else "Connection record disappeared"})
            except Exception as exc:
                results.append({"id": cid, "name": row.get("display_name", ""),
                               "status": "error", "error": _safe_error(exc)})
        return results

    def native_catalog(self):
        from core.connected_apps.native import native_registry
        return [adapter.descriptor for adapter in native_registry.adapters()]

    @_serialized_connection_operation
    def connect_native(self, app_id, *, display_name=None, access_token="", connection_id=None):
        adapter = self._native_adapters.get(str(app_id or "").casefold())
        if adapter is None:
            raise ValueError(f"Unknown native Connected App: {app_id}")
        if not access_token:
            raise ValueError("An authorization token is required")
        cid = connection_id or secrets.token_urlsafe(16)
        previous = self.store.get_connection(cid)
        row = AppConnection(
            id=cid, app_id=adapter.descriptor.id,
            display_name=display_name or adapter.descriptor.name,
            status=ConnectionStatus.CONNECTING,
            auth_type=AuthType.OAUTH2,
            scopes=list(adapter.descriptor.scopes),
            created_at=(previous or {}).get("created_at", time.time()),
            metadata={**dict((previous or {}).get("metadata") or {}),
                      "native": True, "native_adapter": adapter.descriptor.id,
                      "health": "pending"},
        )
        data = _serialize(row)
        self.store.upsert_connection(data)
        self.store.set_credential(cid, "access_token", access_token)
        # Never mark an OAuth native app connected until its bearer token
        # has passed the provider health check. This applies to GitHub and
        # Google Calendar equally; Telegram has its own MTProto login path.
        try:
            healthy = bool(adapter.health_check(cid))
            if not healthy:
                raise RuntimeError(f"{adapter.descriptor.name} authorization health check failed")
            row.metadata["health"] = "healthy"
            row.last_error = ""
            row.status = ConnectionStatus.CONNECTED
        except Exception as exc:
            row.metadata["health"] = "unhealthy"
            row.last_error = _safe_error(exc)
            row.status = ConnectionStatus.ERROR
        result = _serialize(row)
        self.store.upsert_connection(result)
        return result

    @_serialized_connection_operation
    def reauthorize_native_oauth(
        self, app_id, provider_id, *, client_id, client_secret="",
        connection_id=None, scopes=None,
    ):
        """Run a fresh provider OAuth flow without reusing an existing bearer token."""
        normalized_app = str(app_id or "").casefold()
        normalized_provider = str(provider_id or "").casefold()
        adapter = self._native_adapters.get(normalized_app)
        if adapter is None:
            raise RuntimeError(f"{app_id} native adapter is unavailable")
        if str(adapter.descriptor.auth_provider or "").casefold() != normalized_provider:
            raise ValueError("OAuth provider does not match the native Connected App")
        if not str(client_id or "").strip():
            raise ValueError("OAuth Client ID is required")

        cid = str(connection_id or f"{normalized_app}-native")
        row = self.store.get_connection(cid)
        if row and str(row.get("app_id") or "").casefold() != normalized_app:
            raise ValueError("Connection ID belongs to a different Connected App")

        # Remove bearer tokens only. Keep the OAuth client configuration and
        # scopes so users can reconnect without re-entering their app settings.
        delete_credential = getattr(self.store, "delete_credential", None)
        set_credential = getattr(self.store, "set_credential", None)
        for key in ("access_token", "refresh_token", "id_token", "expires_at", "token_type"):
            if callable(delete_credential):
                delete_credential(cid, key)
            elif callable(set_credential):
                set_credential(cid, key, "")

        if row:
            row["status"] = ConnectionStatus.CONNECTING.value
            row["last_error"] = ""
            metadata = dict(row.get("metadata") or {})
            metadata["health"] = "pending"
            row["metadata"] = metadata
            row["updated_at"] = time.time()
            self.store.upsert_connection(row)

        try:
            from core.connected_apps.auth_service import OAuthService

            selected_scopes = list(scopes or adapter.descriptor.scopes)
            OAuthService(self.store).authorize_connection(
                cid, normalized_provider, str(client_id).strip(),
                scopes=selected_scopes,
                client_secret=str(client_secret or ""),
            )
            fresh_token = self.store.get_credential(cid, "access_token")
            if not fresh_token:
                raise RuntimeError(f"{adapter.descriptor.name} OAuth completed without an access token")
            return self.connect_native(
                normalized_app, access_token=fresh_token, connection_id=cid
            )
        except Exception as exc:
            failed = self.store.get_connection(cid)
            if failed:
                failed["status"] = ConnectionStatus.ERROR.value
                failed["last_error"] = _safe_error(exc)
                metadata = dict(failed.get("metadata") or {})
                metadata["health"] = "unhealthy"
                failed["metadata"] = metadata
                failed["updated_at"] = time.time()
                self.store.upsert_connection(failed)
            raise

    @_serialized_connection_operation
    def reauthorize_github(self, *, client_id, client_secret="", connection_id="github-native"):
        """Compatibility wrapper for the GitHub OAuth card action."""
        return self.reauthorize_native_oauth(
            "github", "github",
            client_id=client_id,
            client_secret=client_secret,
            connection_id=connection_id,
        )

    @_serialized_connection_operation
    def begin_telegram_login(self, *, api_id, api_hash, phone):
        adapter = self._native_adapters.get("telegram_personal")
        if adapter is None:
            raise RuntimeError("Telegram Personal adapter is unavailable")
        return adapter.begin_login(api_id, api_hash, phone)

    def finish_telegram_login(self, *, phone, code, password=None, permissions=None, allowed_chats=None, display_name=None):
        adapter = self._native_adapters.get("telegram_personal")
        if adapter is None:
            raise RuntimeError("Telegram Personal adapter is unavailable")
        auth = adapter.finish_login(phone, code, password=password)
        if auth.get("two_factor_required"):
            return {"two_factor_required": True}
        connection = self.connect_telegram_personal(
            api_id=auth["api_id"],
            api_hash=auth["api_hash"],
            session_string=auth["session_string"],
            permissions=permissions, allowed_chats=allowed_chats,
            display_name=display_name or auth.get("user", {}).get("username") or "Telegram Personal",
        )
        return {"two_factor_required": False, "connection": connection, "user": auth.get("user")}

    def cancel_telegram_login(self, phone):
        adapter = self._native_adapters.get("telegram_personal")
        return bool(adapter and adapter.cancel_login(phone))

    def connect_telegram_personal(
        self, *, api_id, api_hash, session_string, permissions=None,
        allowed_chats=None, display_name="Telegram Personal", connection_id=None,
    ):
        """Connect Telegram Personal using a user-created MTProto session.

        Authentication/session creation is deliberately kept outside the AI
        tool surface. The caller must complete Telegram's login flow locally.
        """
        adapter = self._native_adapters.get("telegram_personal")
        if adapter is None:
            raise RuntimeError("Telegram Personal adapter is unavailable")
        if not session_string or not api_hash or not api_id:
            raise ValueError("api_id, api_hash and session_string are required")
        from core.connected_apps.adapters.telegram_personal import DEFAULT_PERMISSIONS
        perms = dict(DEFAULT_PERMISSIONS)
        if permissions:
            for key, value in permissions.items():
                if key not in perms:
                    raise ValueError(f"Unknown Telegram permission: {key}")
                if not isinstance(value, bool):
                    raise TypeError("Telegram permissions must be booleans")
                perms[key] = value
        # AI access remains hard-disabled. Telegram platform terms currently
        # restrict AI/ML use of platform content; never enable it by settings.
        perms["ai_access"] = False
        cid = connection_id or secrets.token_urlsafe(16)
        previous = self.store.get_connection(cid)
        row = AppConnection(
            id=cid, app_id="telegram_personal", display_name=display_name,
            status=ConnectionStatus.CONNECTING, auth_type=AuthType.CUSTOM,
            scopes=["local-mtproto-session"],
            created_at=(previous or {}).get("created_at", time.time()),
            metadata={
                **dict((previous or {}).get("metadata") or {}),
                "native": True, "native_adapter": "telegram_personal",
                "telegram_permissions": perms,
                "telegram_allowed_chats": sorted({str(x) for x in (allowed_chats or [])}),
                "health": "pending",
            },
        )
        self.store.upsert_connection(_serialize(row))
        self.store.set_credentials(cid, {
            "mtproto_session": session_string,
            "telegram_api_config": json.dumps({"api_id": int(api_id), "api_hash": str(api_hash)}),
        })
        try:
            if not adapter.health_check(cid):
                raise RuntimeError("Telegram session health check failed")
            row.status = ConnectionStatus.CONNECTED
            row.metadata["health"] = "healthy"
        except Exception as exc:
            row.status = ConnectionStatus.ERROR
            row.last_error = _safe_error(exc)
            row.metadata["health"] = "unhealthy"
        result = _serialize(row)
        self.store.upsert_connection(result)
        return result

    def update_telegram_permissions(self, cid, *, permissions=None, allowed_chats=None):
        row = self.store.get_connection(cid)
        if not row or row.get("app_id") != "telegram_personal":
            raise ValueError("Telegram Personal connection not found")
        from core.connected_apps.adapters.telegram_personal import DEFAULT_PERMISSIONS
        metadata = dict(row.get("metadata") or {})
        current = dict(DEFAULT_PERMISSIONS)
        current.update(metadata.get("telegram_permissions") or {})
        if permissions:
            for key, value in permissions.items():
                if key not in current or not isinstance(value, bool):
                    raise ValueError(f"Invalid Telegram permission: {key}")
                current[key] = value
        current["ai_access"] = False
        metadata["telegram_permissions"] = current
        if allowed_chats is not None:
            metadata["telegram_allowed_chats"] = sorted({str(x) for x in allowed_chats})
        row["metadata"] = metadata
        row["updated_at"] = time.time()
        self.store.upsert_connection(row)
        return row

    def native_tools(self, cid):
        row = self.store.get_connection(cid)
        if not row or not row.get("enabled", True) or not row.get("metadata", {}).get("native"):
            return []
        adapter = self._native_adapters.get(str(row.get("app_id") or "").casefold())
        if not adapter:
            return []
        return [{
            "name": tool.name, "description": tool.description,
            "input_schema": tool.input_schema,
            "annotations": {"readOnlyHint": tool.read_only, "destructiveHint": tool.destructive},
            "native": True,
        } for tool in adapter.tools]

    @_serialized_connection_operation
    def call_native_tool(self, cid, tool_name, arguments=None):
        row = self.store.get_connection(cid)
        if not row or not row.get("enabled", True):
            raise RuntimeError("Connected App is not enabled")
        if row.get("status") != ConnectionStatus.CONNECTED:
            raise RuntimeError("Connected App is not connected")
        adapter = self._native_adapters.get(str(row.get("app_id") or "").casefold())
        if adapter is None:
            raise RuntimeError("Native Connected App adapter is unavailable")
        tool = next((item for item in adapter.tools if item.name == tool_name), None)
        if not tool:
            raise ValueError("Native tool is not available")
        if tool_name in set((row.get("metadata") or {}).get("blocked_tools") or []):
            raise PermissionError("This tool is blocked by Connected Apps permissions")
        from core.connected_apps.agent import _validate_arguments
        args = {} if arguments is None else arguments
        _validate_arguments(tool.input_schema, args)
        # Native tools follow the same fail-closed policy as MCP tools:
        # only explicitly trusted, read-only, non-destructive operations may
        # bypass confirmation. The default is confirmation.
        metadata = row.get("metadata") or {}
        auto_approve = (
            metadata.get("auto_approve_read_only") is True
            and tool.read_only is True
            and tool.destructive is not True
            and tool.requires_confirmation is not True
        )
        if not auto_approve:
            from core import confirm
            return confirm.request(
                key=f"connected_apps:{cid}:{tool_name}",
                title=f"Connected App — {tool_name}",
                detail=f"Execute {tool_name} with arguments:\n\n{args!r}",
                run=lambda: adapter.execute(cid, tool_name, args),
            )
        return adapter.execute(cid, tool_name, args)

    def rename(self, cid, display_name):
        name = " ".join(str(display_name or "").split())
        if not name:
            raise ValueError("Connection name cannot be empty")
        if len(name) > 120:
            raise ValueError("Connection name cannot exceed 120 characters")
        row = self.store.get_connection(cid)
        if not row:
            return None
        row["display_name"] = name
        row["updated_at"] = time.time()
        self.store.upsert_connection(row)
        return row

    @_serialized_connection_operation
    def set_enabled(self, cid, enabled, *, reconnect=True):
        row = self.store.get_connection(cid)
        if not row:
            return None
        enabled = bool(enabled)
        row["enabled"] = enabled
        row["updated_at"] = time.time()
        if not enabled:
            if not (row.get("metadata") or {}).get("native"):
                runtime.disconnect(cid)
            row["status"] = ConnectionStatus.DISCONNECTED.value
            row["last_error"] = ""
            metadata = dict(row.get("metadata") or {})
            metadata["health"] = "disabled"
            row["metadata"] = metadata
            self.store.upsert_connection(row)
            return row
        row["status"] = ConnectionStatus.DISCONNECTED.value
        metadata = dict(row.get("metadata") or {})
        metadata["health"] = "pending"
        row["metadata"] = metadata
        self.store.upsert_connection(row)
        return self.reconnect(cid) if reconnect else row

    @_serialized_connection_operation
    def disconnect(self, cid):
        row = self.store.get_connection(cid)
        if not row:
            return False
        try:
            metadata = row.get("metadata") or {}
            app_id = str(row.get("app_id") or "").casefold()
            if metadata.get("native"):
                adapter = self._native_adapters.get(app_id)
                disconnect = getattr(adapter, "disconnect", None)
                if callable(disconnect):
                    disconnect(cid)
                if app_id in {"github", "google_calendar"}:
                    # Disconnect removes provider bearer tokens while retaining
                    # the OAuth client configuration for a later fresh login.
                    delete_credential = getattr(self.store, "delete_credential", None)
                    set_credential = getattr(self.store, "set_credential", None)
                    for key in ("access_token", "refresh_token", "id_token", "expires_at", "token_type"):
                        if callable(delete_credential):
                            delete_credential(cid, key)
                        elif callable(set_credential):
                            # Compatibility with lightweight stores that can
                            # write credentials but do not expose deletion.
                            set_credential(cid, key, "")
            else:
                runtime.disconnect(cid)
        except Exception as exc:
            row["status"] = ConnectionStatus.ERROR.value
            row["last_error"] = _safe_error(exc)
            row["updated_at"] = time.time()
            self.store.upsert_connection(row)
            raise RuntimeError(f"Disconnect failed: {row['last_error']}") from exc
        row["status"] = ConnectionStatus.DISCONNECTED.value
        row["last_error"] = ""
        if str(row.get("app_id") or "").casefold() in {"github", "google_calendar"}:
            metadata = dict(row.get("metadata") or {})
            metadata["health"] = "disconnected"
            row["metadata"] = metadata
        row["updated_at"] = time.time()
        self.store.upsert_connection(row)
        return True

    @_serialized_connection_operation
    def remove(self, cid):
        row = self.store.get_connection(cid)
        if row:
            metadata = row.get("metadata") or {}
            if metadata.get("native"):
                adapter = self._native_adapters.get(str(row.get("app_id") or "").casefold())
                disconnect = getattr(adapter, "disconnect", None)
                if callable(disconnect):
                    disconnect(cid)
            else:
                runtime.disconnect(cid)
        return self.store.remove_connection(cid)

    @_serialized_connection_operation
    def health_check(self, cid):
        row = self.store.get_connection(cid)
        if not row:
            return {"id": cid, "status": "missing", "healthy": False}
        if row.get("status") != ConnectionStatus.CONNECTED.value:
            return {"id": cid, "status": row.get("status", "disconnected"),
                    "healthy": False, "error": "Connection is not currently connected"}
        checked_at = time.time()
        metadata = dict(row.get("metadata") or {})
        if metadata.get("native"):
            adapter = self._native_adapters.get(str(row.get("app_id") or "").casefold())
            try:
                healthy = bool(adapter and adapter.health_check(cid))
                metadata["health"] = "healthy" if healthy else "unavailable"
                metadata["last_health_check"] = checked_at
                row["metadata"] = metadata
                # Health is a diagnostic, not the OAuth/session lifecycle.
                # Keep the connection usable while exposing health separately.
                row["status"] = (
                    ConnectionStatus.CONNECTED.value
                    if healthy else ConnectionStatus.ERROR.value
                )
                row["last_error"] = "" if healthy else "Native adapter health check failed"
                row["updated_at"] = checked_at
                self.store.upsert_connection(row)
                return {"id": cid, "status": row["status"], "healthy": healthy,
                        "checked_at": checked_at, "error": row["last_error"]}
            except Exception as exc:
                metadata["health"] = "unhealthy"
                metadata["last_health_check"] = checked_at
                row["metadata"] = metadata
                row["status"] = ConnectionStatus.ERROR.value
                row["last_error"] = _safe_error(exc)
                row["updated_at"] = checked_at
                self.store.upsert_connection(row)
                return {"id": cid, "status": row["status"], "healthy": False,
                        "checked_at": checked_at, "error": row["last_error"]}
        try:
            healthy = bool(runtime.health(cid))
            metadata["health"] = "healthy" if healthy else "unavailable"
            metadata["last_health_check"] = checked_at
            row["metadata"] = metadata
            if healthy:
                row["status"] = ConnectionStatus.CONNECTED.value
                row["last_error"] = ""
            else:
                row["status"] = ConnectionStatus.ERROR.value
                row["last_error"] = "MCP session is not active"
            row["updated_at"] = checked_at
            self.store.upsert_connection(row)
            return {"id": cid, "status": row["status"], "healthy": healthy,
                    "checked_at": checked_at, "error": row["last_error"]}
        except Exception as exc:
            metadata["health"] = "unhealthy"
            metadata["last_health_check"] = checked_at
            row["metadata"] = metadata
            row["status"] = ConnectionStatus.ERROR.value
            row["last_error"] = _safe_error(exc)
            row["updated_at"] = checked_at
            self.store.upsert_connection(row)
            runtime.disconnect(cid)
            return {"id": cid, "status": row["status"], "healthy": False,
                    "checked_at": checked_at, "error": row["last_error"]}

    def health_check_all(self):
        results = []
        for row in self.connections():
            if not row.get("enabled", True) or row.get("status") != ConnectionStatus.CONNECTED.value:
                continue
            results.append(self.health_check(row.get("id", "")))
        return results

    def tools(self, cid):
        row = self.store.get_connection(cid)
        if row and (row.get("metadata") or {}).get("native"):
            return self.native_tools(cid)
        return runtime.tools(cid)

    def set_tool_blocked(self, cid, tool_name, blocked=True):
        row = self.store.get_connection(cid)
        if not row:
            return False
        tools = self.tools(cid)
        if not any(item.get("name") == tool_name for item in tools):
            raise ValueError("Tool is not available on this connection")
        metadata = dict(row.get("metadata") or {})
        blocked_tools = set(metadata.get("blocked_tools") or [])
        if blocked:
            blocked_tools.add(tool_name)
        else:
            blocked_tools.discard(tool_name)
        metadata["blocked_tools"] = sorted(blocked_tools)
        row["metadata"] = metadata
        self.store.upsert_connection(row)
        return True

    @_serialized_connection_operation
    def call_tool(self, cid, tool, arguments=None):
        started = time.time()
        row = self.store.get_connection(cid)
        if not row:
            raise RuntimeError("Connected app does not exist")
        if not row.get("enabled", True):
            raise RuntimeError("Connected app is disabled")
        if row.get("status") != ConnectionStatus.CONNECTED.value:
            raise RuntimeError("Connected app is not connected")

        metadata = dict(row.get("metadata") or {})
        if tool in set(metadata.get("blocked_tools") or []):
            raise PermissionError("This MCP tool is blocked by Connected Apps permissions")

        available_tools = runtime.tools(cid)
        tool_descriptor = next((item for item in available_tools if item.get("name") == tool), None)
        if tool_descriptor is None:
            raise ValueError("Tool is not available on this connection")
        from core.connected_apps.agent import _validate_arguments
        args = {} if arguments is None else arguments
        _validate_arguments(tool_descriptor.get("input_schema") or {}, args)

        annotations = tool_descriptor.get("annotations")
        if isinstance(annotations, dict):
            read_only_hint = annotations.get("readOnlyHint") is True
            # A missing destructiveHint is not proof that an operation is safe.
            destructive_hint = annotations.get("destructiveHint")
            requires_confirmation = annotations.get("requiresConfirmation") is True
        else:
            read_only_hint = getattr(annotations, "readOnlyHint", False) is True
            destructive_hint = getattr(annotations, "destructiveHint", None)
            requires_confirmation = getattr(annotations, "requiresConfirmation", False) is True

        auto_approve = (
            _auto_approve_read_only(
                metadata,
                read_only=read_only_hint,
                destructive=destructive_hint is not False,
                requires_confirmation=requires_confirmation,
            )
        )
        if not auto_approve:
            from core import confirm

            def run_confirmed():
                # Confirmation may be approved after the original request returns.
                # Re-check connection state, permissions and the current schema at
                # approval time to prevent stale approvals racing disconnect/revoke.
                with self._connection_lock(cid):
                    current = self.store.get_connection(cid)
                    if not current or not current.get("enabled", True):
                        raise RuntimeError("Connected app is no longer enabled")
                    if current.get("status") != ConnectionStatus.CONNECTED.value:
                        raise RuntimeError("Connected app is no longer connected")
                    current_metadata = dict(current.get("metadata") or {})
                    if tool in set(current_metadata.get("blocked_tools") or []):
                        raise PermissionError("This MCP tool is blocked by Connected Apps permissions")
                    current_tools = runtime.tools(cid)
                    current_descriptor = next(
                        (item for item in current_tools if item.get("name") == tool), None
                    )
                    if current_descriptor is None:
                        raise ValueError("Tool is no longer available on this connection")
                    _validate_arguments(current_descriptor.get("input_schema") or {}, args)
                    return str(runtime.call(cid, tool, args))

            return confirm.request(
                key=f"connected_apps:{cid}:{tool}",
                title=f"Connected App — {tool}",
                detail=f"Execute tool {tool} with arguments:\\n\\n{args!r}",
                run=run_confirmed,
            )

        try:
            result = runtime.call(cid, tool, args)
        except Exception as exc:
            metadata = dict(row.get("metadata") or {})
            metadata["last_tool_activity"] = started
            metadata["last_tool_name"] = str(tool)[:200]
            metadata["last_tool_error"] = _safe_error(exc)
            metadata["tool_call_count"] = int(metadata.get("tool_call_count", 0)) + 1
            row["metadata"] = metadata
            row["updated_at"] = time.time()
            self.store.upsert_connection(row)
            raise
        metadata = dict(row.get("metadata") or {})
        metadata["last_tool_activity"] = started
        metadata["last_tool_name"] = str(tool)[:200]
        metadata["last_tool_error"] = ""
        metadata["tool_call_count"] = int(metadata.get("tool_call_count", 0)) + 1
        metadata["last_tool_duration_ms"] = round((time.time() - started) * 1000, 2)
        row["metadata"] = metadata
        row["updated_at"] = time.time()
        self.store.upsert_connection(row)
        return result


service = ConnectedAppsService()


def test_service_call_tool_enforces_connection_and_permissions(monkeypatch):
    import pytest
    from core.connected_apps.service import ConnectedAppsService
    from core.connected_apps.models import ConnectionStatus
    from core.connected_apps import service as service_module

    class Store:
        def __init__(self, row):
            self.row = dict(row)
        def get_connection(self, cid):
            return dict(self.row) if cid == self.row["id"] else None
        def upsert_connection(self, row):
            self.row = dict(row)
        def list_connections(self):
            return [dict(self.row)]

    base = {
        "id": "c1", "app_id": "demo", "display_name": "Demo",
        "status": "connected", "enabled": True,
        "metadata": {"blocked_tools": [], "auto_approve_read_only": True}, "last_error": "", "updated_at": 0,
    }
    store = Store(base)
    svc = ConnectedAppsService(store=store)
    monkeypatch.setattr(service_module.runtime, "tools", lambda cid: [
        {"name": "read", "annotations": {"readOnlyHint": True, "destructiveHint": False}, "input_schema": {"type": "object", "properties": {
            "limit": {"type": "integer", "minimum": 1}
        }, "required": ["limit"], "additionalProperties": False}}
    ])
    calls = []
    monkeypatch.setattr(service_module.runtime, "call", lambda cid, tool, args: calls.append((cid, tool, args)) or "ok")

    assert svc.call_tool("c1", "read", {"limit": 1}) == "ok"
    assert len(calls) == 1

    store.row["metadata"]["blocked_tools"] = ["read"]
    with pytest.raises(PermissionError, match="blocked"):
        svc.call_tool("c1", "read", {"limit": 1})
    assert len(calls) == 1

    store.row["metadata"]["blocked_tools"] = []
    store.row["enabled"] = False
    with pytest.raises(RuntimeError, match="disabled"):
        svc.call_tool("c1", "read", {"limit": 1})
    store.row["enabled"] = True
    store.row["status"] = ConnectionStatus.DISCONNECTED.value
    with pytest.raises(RuntimeError, match="not connected"):
        svc.call_tool("c1", "read", {"limit": 1})
    store.row["status"] = ConnectionStatus.CONNECTED.value

    with pytest.raises(ValueError, match="required"):
        svc.call_tool("c1", "read", {})
    with pytest.raises(ValueError, match="unknown"):
        svc.call_tool("c1", "read", {"limit": 1, "unexpected": True})
    assert len(calls) == 1


def test_service_call_tool_requires_confirmation_without_explicit_safe_annotations(monkeypatch):
    from core.connected_apps.service import ConnectedAppsService
    from core.connected_apps import service as service_module

    class Store:
        def __init__(self):
            self.row = {
                "id": "c2", "app_id": "demo", "display_name": "Demo",
                "status": "connected", "enabled": True,
                "metadata": {"blocked_tools": [], "auto_approve_read_only": True},
                "last_error": "", "updated_at": 0,
            }
        def get_connection(self, cid):
            return dict(self.row) if cid == self.row["id"] else None
        def upsert_connection(self, row):
            self.row = dict(row)
        def list_connections(self):
            return [dict(self.row)]

    store = Store()
    svc = ConnectedAppsService(store=store)
    descriptors = [
        {"name": "missing_destructive_hint", "annotations": {"readOnlyHint": True}},
        {"name": "destructive", "annotations": {"readOnlyHint": True, "destructiveHint": True}},
        {"name": "not_read_only", "annotations": {"readOnlyHint": False, "destructiveHint": False}},
    ]
    current = {"tool": descriptors[0]}
    monkeypatch.setattr(service_module.runtime, "tools", lambda cid: [current["tool"]])
    calls = []
    monkeypatch.setattr(service_module.runtime, "call", lambda *args: calls.append(args) or "executed")

    confirmation_requests = []
    from core import confirm
    monkeypatch.setattr(confirm, "request", lambda **kwargs: confirmation_requests.append(kwargs) or "confirmation_required")

    for descriptor in descriptors:
        current["tool"] = descriptor
        assert svc.call_tool("c2", descriptor["name"], {}) == "confirmation_required"

    assert len(confirmation_requests) == 3
    assert calls == []


def test_service_call_tool_safe_auto_approval_requires_user_opt_in_and_explicit_hints(monkeypatch):
    from core.connected_apps.service import ConnectedAppsService
    from core.connected_apps import service as service_module

    class Store:
        def __init__(self):
            self.row = {
                "id": "c3", "app_id": "demo", "display_name": "Demo",
                "status": "connected", "enabled": True,
                "metadata": {"blocked_tools": [], "auto_approve_read_only": True},
                "last_error": "", "updated_at": 0,
            }
        def get_connection(self, cid):
            return dict(self.row) if cid == self.row["id"] else None
        def upsert_connection(self, row):
            self.row = dict(row)
        def list_connections(self):
            return [dict(self.row)]

    store = Store()
    svc = ConnectedAppsService(store=store)
    descriptor = {
        "name": "read",
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
        "input_schema": {"type": "object"},
    }
    monkeypatch.setattr(service_module.runtime, "tools", lambda cid: [descriptor])
    calls = []
    monkeypatch.setattr(service_module.runtime, "call", lambda cid, tool, args: calls.append((cid, tool, args)) or "ok")

    assert svc.call_tool("c3", "read", {}) == "ok"
    assert calls == [("c3", "read", {})]

    store.row["metadata"]["auto_approve_read_only"] = False
    from core import confirm
    prompts = []
    monkeypatch.setattr(confirm, "request", lambda **kwargs: prompts.append(kwargs) or "confirmation_required")
    assert svc.call_tool("c3", "read", {}) == "confirmation_required"
    assert len(prompts) == 1
    assert len(calls) == 1


def test_service_call_tool_revalidates_state_when_confirmation_is_approved(monkeypatch):
    import pytest
    from core.connected_apps.service import ConnectedAppsService
    from core.connected_apps import service as service_module
    from core.connected_apps.models import ConnectionStatus

    class Store:
        def __init__(self):
            self.row = {
                "id": "c4", "app_id": "demo", "display_name": "Demo",
                "status": "connected", "enabled": True,
                "metadata": {"blocked_tools": []}, "last_error": "", "updated_at": 0,
            }
        def get_connection(self, cid):
            return dict(self.row) if cid == self.row["id"] else None
        def upsert_connection(self, row):
            self.row = dict(row)
        def list_connections(self):
            return [dict(self.row)]

    store = Store()
    svc = ConnectedAppsService(store=store)
    descriptor = {"name": "write", "input_schema": {"type": "object"}}
    monkeypatch.setattr(service_module.runtime, "tools", lambda cid: [descriptor])
    calls = []
    monkeypatch.setattr(service_module.runtime, "call", lambda *args: calls.append(args) or "ok")

    from core import confirm
    pending = []
    monkeypatch.setattr(confirm, "request", lambda **kwargs: pending.append(kwargs) or "confirmation_required")

    assert svc.call_tool("c4", "write", {}) == "confirmation_required"
    store.row["status"] = ConnectionStatus.DISCONNECTED.value
    with pytest.raises(RuntimeError, match="no longer connected"):
        pending[0]["run"]()
    assert calls == []
