"""ArcGIS Portal MCP Server.

MCP server that gives AI assistants access to ArcGIS Portal and Online
via structured tools. Built on the Model Context Protocol for integration
with Claude Desktop, Cursor, VS Code Copilot, and other MCP clients.

No dependency on the `arcgis` Python package. Uses raw REST API calls.
"""

__version__ = "1.11.1"  # v1.11.1: bugfixes & robustness (Enterprise search, auto-reconnect, HEAD pings)
