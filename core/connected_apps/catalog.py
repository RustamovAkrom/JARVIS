"""Curated, user-configurable MCP server templates.

Templates are launch recipes only. JARVIS never installs packages silently;
connecting a template starts the configured executable and may download packages
through the selected package manager, so the user must review the command.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MCPTemplate:
    id: str
    name: str
    description: str
    command: str
    args: tuple[str, ...]
    env_keys: tuple[str, ...] = ()
    category: str = "general"
    setup_url: str = ""


CATALOG: tuple[MCPTemplate, ...] = (
    MCPTemplate(
        "filesystem",
        "Filesystem",
        "Read and manage files within explicitly allowed directories.",
        "npx",
        ("-y", "@modelcontextprotocol/server-filesystem", "<allowed-path>"),
        category="files",
        setup_url="https://github.com/modelcontextprotocol/servers",
    ),
    MCPTemplate(
        "playwright",
        "Playwright",
        "Browser automation through Playwright MCP.",
        "npx",
        ("-y", "@playwright/mcp@latest"),
        category="browser",
        setup_url="https://github.com/microsoft/playwright-mcp",
    ),
    MCPTemplate(
        "fetch",
        "Fetch",
        "Fetch and convert web pages to readable content.",
        "uvx",
        ("mcp-server-fetch",),
        category="web",
        setup_url="https://github.com/modelcontextprotocol/servers",
    ),
    MCPTemplate(
        "memory",
        "Memory",
        "Persistent knowledge graph memory for MCP.",
        "npx",
        ("-y", "@modelcontextprotocol/server-memory"),
        category="memory",
        setup_url="https://github.com/modelcontextprotocol/servers",
    ),
    MCPTemplate(
        "sequential-thinking",
        "Sequential Thinking",
        "Structured reasoning and step-by-step thought process tool.",
        "npx",
        ("-y", "@modelcontextprotocol/server-sequential-thinking"),
        category="reasoning",
        setup_url="https://github.com/modelcontextprotocol/servers",
    ),
)


def list_templates(query: str = "") -> list[MCPTemplate]:
    query = " ".join(str(query or "").casefold().split())
    if not query:
        return list(CATALOG)
    return [
        template for template in CATALOG
        if query in f"{template.id} {template.name} {template.description} {template.category}".casefold()
    ]


def get_template(template_id: str) -> MCPTemplate | None:
    return next((item for item in CATALOG if item.id == str(template_id or "").casefold()), None)
