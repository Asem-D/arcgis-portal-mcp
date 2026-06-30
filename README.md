# arcgis-portal-mcp

MCP server for ArcGIS Portal and Online — lets AI assistants search content, query feature layers, list users and groups, and check portal health.

Built on the [Model Context Protocol](https://modelcontextprotocol.io) for integration with Claude Desktop, Cursor, VS Code Copilot, and other MCP clients.

## Features

- **Connect** to any ArcGIS Enterprise Portal or ArcGIS Online
- **Search** for items (feature services, web maps, layers, dashboards)
- **Inspect** item metadata, tags, and descriptions
- **List layers** in a feature service with geometry types and counts
- **Query features** with attribute filters, spatial filters, field selection, and pagination
- **List users** with roles, status, and last login
- **List groups** with access levels and member counts
- **Health check** portal system status (requires admin privileges)

## Design Principles

- **No `arcgis` Python package dependency** — uses raw REST API calls via `requests` for maximum compatibility (the `arcgis` package has installation issues on Windows)
- **Works with Enterprise Portal AND ArcGIS Online** — same tools, same API
- **Token-first auth** — supports existing tokens for zero-friction MCP integration
- **Auto-connect** — reads `.env` file on startup, no manual auth needed per session
- **2FA-friendly** — works with Enterprise portals that require two-factor authentication (client_credentials, no browser)
- **Self-signed cert friendly** — handles Enterprise portals with self-signed certificates

## Installation

```bash
# From source
cd arcgis-portal-mcp
pip install -e .

# Or install from GitHub
pip install git+https://github.com/Asem-D/arcgis-portal-mcp.git
```

**Windows users:** `pywin32` is installed automatically as a platform-specific dependency.

## Configuration

### `.env` File (recommended)

Create a `.env` file in the project root for automatic connection on startup. A template is provided:

```bash
cp .env.example .env
# Edit .env with your portal credentials
```

```env
portal_url=https://gis.example.com/portal
oauth_client_id=your-oauth-app-client-id
oauth_client_secret=your-oauth-app-client-secret
```

The server reads these on startup and connects via `client_credentials` automatically — no manual `connect_portal` call needed.

> **Note:** The `.env` file is gitignored. Never commit credentials. `.env.example` is safe to commit.

### MCP Client Configuration

For MCP clients (Claude Desktop, Cursor, etc.), the simplest setup uses a `.env` file:

```json
{
  "mcpServers": {
    "arcgis-portal": {
      "command": "python",
      "args": ["-m", "arcgis_portal_mcp.server"],
      "cwd": "/path/to/arcgis-portal-mcp"
    }
  }
}
```

The server auto-connects from `.env` in the working directory. No env vars needed in the MCP config.

Alternatively, pass credentials via MCP client env vars:

```json
{
  "mcpServers": {
    "arcgis-portal": {
      "command": "python",
      "args": ["-m", "arcgis_portal_mcp.server"],
      "env": {
        "portal_url": "https://gis.example.com/portal",
        "oauth_client_id": "your-client-id",
        "oauth_client_secret": "your-client-secret"
      }
    }
  }
}
```

### `connect_portal` Tool Parameters

The `connect_portal` tool accepts these `auth_method` values:

| Value | Behavior |
|-------|----------|
| `auto` | Read from `.env` (default) |
| `token` | Use explicit portal token |
| `client_credentials` | Use explicit client_id/secret |
| `oauth2` | Browser-based OAuth2 (blocks ~2 min) |

## Usage

### Connect and Search

```
User: Search for all feature services in my portal
Agent: [calls connect_portal, then search_content with item_type="Feature Service"]
```

### Query a Layer

```
User: Show me the first 10 parcels from the cadastral layer
Agent: [calls list_layers to find the parcel layer, then query_features with limit=10]
```

### Audit Users

```
User: Who are the administrators in our portal?
Agent: [calls list_users, filters by role]
```

## Available Tools

| Tool | Description |
|------|-------------|
| `connect_portal` | Authenticate with the portal (auto, token, client_credentials, or OAuth2) |
| `search_content` | Search items by keyword, type, and owner |
| `get_item_details` | Get detailed metadata for a specific item |
| `list_layers` | List layers in a feature/map service |
| `query_features` | Query features with filters and pagination |
| `list_users` | List portal users with roles and status |
| `list_groups` | List portal groups with access levels |
| `portal_health` | Check portal health and system status |
| `server_status` | Check MCP server connection state |

## Authentication Methods

| Method | Pros | Cons |
|--------|------|------|
| **Auto** (default) | Zero-config, reads `.env` on startup | App-level only (no user identity) |
| **Token** | Quick, no dependencies | Tokens expire, must be obtained separately |
| **Client Credentials** | No browser needed, auto-refresh | App-level only (no user identity) |
| **OAuth2** | Full user permissions, 14-day tokens | Opens browser, blocks for ~2 min |

**Recommendation for Enterprise portals with 2FA:** Use `auto` (`.env` with `client_credentials`). Token auth won't work because 2FA blocks token generation. The `client_credentials` flow uses app-level OAuth2 — no browser, no 2FA, no user interaction.

**For ArcGIS Online or portals without 2FA:** Token auth is the fastest for MCP. Use OAuth2 once to get a long-lived token, then use that token for MCP sessions.

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run linting
ruff check arcgis_portal_mcp/
```

## License

MIT — see [LICENSE](LICENSE).

## Author

Asem Daaboul — [asem.daaboul@gmail.com](mailto:asem.daaboul@gmail.com)
