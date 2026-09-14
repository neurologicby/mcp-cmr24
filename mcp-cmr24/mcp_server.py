"""MCP server instances for compact and native tool catalogues."""

from mcp.server import MCPServer

SINGLE_MCP = MCPServer("CMR24 Server Single", version="0.0.1")
NATIVE_MCP = MCPServer("CMR24 Server Native", version="0.0.1")

# Compatibility alias for code that imported the original singleton.
MCP = SINGLE_MCP
