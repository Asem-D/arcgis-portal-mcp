"""ArcGIS Portal MCP Server.

MCP server that gives AI assistants access to ArcGIS Portal and Online
via structured tools. Built on the Model Context Protocol for integration
with Claude Desktop, Cursor, VS Code Copilot, and other MCP clients.

No dependency on the `arcgis` Python package. Uses raw REST API calls.
"""

__version__ = "1.12.0"  # v1.12.0: offboard_user, bulk_reassign_ownership, portal_inventory
