import json
import logging
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)


class NokiaMCPError(Exception):
    """Base exception for Nokia MCP gateway errors."""


class UnauthorizedMCPToolError(NokiaMCPError):
    """Raised when an MCP tool invocation is attempted that is not permitted by the static allowlist."""


@runtime_checkable
class NokiaMCPClientProtocol(Protocol):
    """Protocol defining the interface for the Nokia MCP client."""

    async def list_tools(self) -> list[dict[str, Any]]:
        ...

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        ...

    async def check_health(self) -> bool:
        ...


class NokiaMCPClient:
    """Client for invoking Nokia Network as Code carrier tools over the Model Context Protocol (MCP).

    Supports stdio transport running `npx mcp-remote ...` to proxy Nokia RapidAPI endpoints.
    Enforces a strict static allowlist:
    - If `static_allowlist` is empty, all tools provided by the MCP server are eligible.
    - If `static_allowlist` is non-empty, only tools matching the allowlist may be listed or called.
    """

    def __init__(
        self,
        config_path: str | Path | None = "nokia_mcp.json",
        static_allowlist: set[str] | None = None,
        command: str = "npx",
        args: list[str] | None = None,
    ) -> None:
        # Default empty set means all tools eligible as per specification
        self.static_allowlist: set[str] = set(static_allowlist) if static_allowlist else set()
        self.command = command
        self.args: list[str] = args or []

        # Attempt to load from JSON config if provided and file exists
        if config_path:
            p = Path(config_path)
            if p.exists():
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    servers = data.get("mcpServers", {})
                    # Look for RapidAPI Hub - Network as Code
                    server_cfg = servers.get("RapidAPI Hub - Network as Code") or next(iter(servers.values()), None)
                    if server_cfg:
                        self.command = server_cfg.get("command", self.command)
                        self.args = server_cfg.get("args", self.args)
                except Exception as exc:
                    logger.warning("Failed to parse Nokia MCP config at '%s': %s", config_path, exc)

    def is_tool_allowed(self, tool_name: str) -> bool:
        """Evaluates whether a tool is permitted by the static allowlist."""
        if not self.static_allowlist:
            return True
        return tool_name in self.static_allowlist

    def _get_server_params(self) -> StdioServerParameters:
        if not self.args:
            raise NokiaMCPError("Nokia MCP client has no command arguments configured")
        return StdioServerParameters(
            command=self.command,
            args=self.args,
            env=None,
        )

    async def check_health(self) -> bool:
        """Verifies reachability and initialization of the Nokia MCP server."""
        if not self.args:
            return False
        try:
            params = self._get_server_params()
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return True
        except Exception as exc:
            logger.debug("Nokia MCP health check failed: %s", exc)
            return False

    async def list_tools(self) -> list[dict[str, Any]]:
        """Queries the Nokia MCP server for available tools, filtered by the static allowlist."""
        if not self.args:
            return []
        try:
            params = self._get_server_params()
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    resp = await session.list_tools()
                    allowed: list[dict[str, Any]] = []
                    for t in resp.tools:
                        if self.is_tool_allowed(t.name):
                            allowed.append({
                                "name": t.name,
                                "description": t.description or "",
                                "input_schema": t.input_schema or {},
                            })
                    return allowed
        except Exception as exc:
            logger.error("Failed to list tools from Nokia MCP server: %s", exc)
            raise NokiaMCPError(f"Failed to list tools from Nokia MCP server: {exc}") from exc

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Executes a tool on the Nokia MCP server after validating against the static allowlist."""
        if not self.is_tool_allowed(name):
            raise UnauthorizedMCPToolError(
                f"Tool '{name}' is not in the Nokia MCP static allowlist and cannot be executed"
            )

        params = self._get_server_params()
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(name=name, arguments=arguments or {})
                    # Serialize response content
                    output_parts = []
                    for content in result.content:
                        if hasattr(content, "text"):
                            output_parts.append(content.text)
                        else:
                            output_parts.append(str(content))
                    return "\n".join(output_parts)
        except UnauthorizedMCPToolError:
            raise
        except Exception as exc:
            logger.error("Failed to execute tool '%s' via Nokia MCP: %s", name, exc)
            raise NokiaMCPError(f"Failed to execute tool '{name}' on Nokia MCP server: {exc}") from exc
