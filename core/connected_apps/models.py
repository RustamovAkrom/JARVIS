"""Typed models for the optional Connected Apps subsystem."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ConnectionStatus(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    AUTH_REQUIRED = "auth_required"
    ERROR = "error"


class AuthType(StrEnum):
    NONE = "none"
    API_KEY = "api_key"
    OAUTH2 = "oauth2"
    CUSTOM = "custom"


class TransportType(StrEnum):
    STDIO = "stdio"
    HTTP = "http"
    SSE = "sse"


@dataclass(slots=True)
class AppDescriptor:
    id: str
    name: str
    description: str = ""
    category: str = "other"
    source: str = "builtin"
    auth_type: AuthType = AuthType.NONE
    transport: TransportType | None = None
    endpoint: str = ""
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    scopes: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AppConnection:
    id: str
    app_id: str
    display_name: str
    status: ConnectionStatus = ConnectionStatus.DISCONNECTED
    enabled: bool = True
    auth_type: AuthType = AuthType.NONE
    scopes: list[str] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0
    last_error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AppTool:
    app_id: str
    connection_id: str
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    read_only: bool = False
    destructive: bool = False
    requires_confirmation: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolCall:
    app_id: str
    connection_id: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
