"""Optional Connected Apps subsystem; importing it does not load MCP dependencies."""
from core.connected_apps.models import AppDescriptor, AppConnection, AppTool, ToolCall, AuthType, TransportType, ConnectionStatus
from core.connected_apps.registry import AppRegistry, registry
from core.connected_apps.store import ConnectedAppsStore
from core.connected_apps.service import ConnectedAppsService, service
from core.connected_apps.agent import ConnectedAppsAgent, agent

__all__ = ["AppDescriptor", "AppConnection", "AppTool", "ToolCall", "AuthType",
"TransportType", "ConnectionStatus", "AppRegistry", "registry", "ConnectedAppsStore",
"ConnectedAppsService", "service", "ConnectedAppsAgent", "agent"]
