# arcgis-portal-mcp

**v1.2.0.** 34 tools for ArcGIS Enterprise Portal and ArcGIS Online.

A Model Context Protocol (MCP) server that gives AI assistants direct access to your ArcGIS content. Search, inspect, edit, publish, and admin through natural language.

Works with Claude Desktop, Cursor, VS Code Copilot, and any MCP-compatible client.

> **Disclaimer:** This is an independent open-source project. Not affiliated with, endorsed by, or sponsored by Esri. "ArcGIS" is a registered trademark of Esri.

## What's new in v1.2.0

The last few releases added tools that were missing for real-world workflows:

- **`describe_layer`**: Get the full schema of any layer: fields, types, domains, subtypes, relationships, extent, renderer. No more guessing what a feature service contains.
- **`get_gp_task_info`**: Inspect GP tool parameter schemas before you run them.
- **`export_map_image`**: Render MapServer or FeatureServer layers as JPG, PNG, GIF, PDF, or SVG. Useful for thumbnails, reports, and quick visual checks.
- **`server_status`**: One call to check if the MCP server is connected and healthy.
- **`portal_usage`**: Pull usage statistics: active users, API calls, storage breakdown.
- **`get_item_data`**: Read the actual data inside an item: web map definitions, app configs, feature collections.
- **`batch_delete_items`**: Delete multiple items in one shot instead of one at a time.
- **`batch_share_items`**: Share or unshare a list of items with the same audiences.
- **`batch_update_items`**: Update tags, titles, descriptions, or access on multiple items at once.
- **`connect_portal`**: Now supports `username_password` auth method (generateToken, user-level permissions).

## Features

- **Connect** to any ArcGIS Enterprise Portal or ArcGIS Online.
- **Search** for items: feature services, web maps, layers, dashboards.
- **Inspect** item metadata, tags, and descriptions.
- **Describe layers**: full schema with fields, domains, subtypes, relationships, and renderer info.
- **Query features** with attribute filters, spatial filters, field selection, and pagination.
- **Add, update, and delete features** in hosted feature layers.
- **Manage content**: update properties, share/unshare, delete items, read web map definitions.
- **Manage users**: list users, get detailed profiles.
- **Manage groups**: list, create, and invite users.
- **Publish services**: upload files and publish as hosted feature services.
- **Inspect GP tools**: list tasks and view parameter schemas before execution.
- **Run geoprocessing**: execute synchronous and asynchronous GP tasks.
- **Export map images**: render layers as JPG/PNG/GIF/PDF/SVG.
- **Portal admin**: system info, license management, usage statistics.
- **Batch operations**: bulk delete, share, and update multiple items.
- **Health check**: portal system status (requires admin privileges).

## Design Principles

- **No `arcgis` Python package dependency**: uses raw REST API calls via `requests`, so you don't need to fight with Esri's Python SDK.
- **Works with Enterprise Portal AND ArcGIS Online**: same tools, same API.
- **Multiple auth methods**: token, username/password, client_credentials, and OAuth2.
- **Auto-connect**: reads your `.env` file on startup, no manual auth needed per session.
- **2FA-friendly**: works with Enterprise portals that require two-factor authentication.
- **Self-signed cert friendly**: handles Enterprise portals with self-signed certificates.
- **Hardened**: SQL injection validation on WHERE clauses, XSS protection in OAuth callbacks, automatic retry with exponential backoff.

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
# Option 1: Username/password (recommended for most users)
username=your-portal-username
password=your-portal-password
# Option 2: OAuth2 app credentials (app-level, limited permissions)
# oauth_client_id=your-oauth-app-client-id
# oauth_client_secret=your-oauth-app-client-secret
```

The server reads these on startup and connects automatically. If `username` + `password` are provided, it uses `generateToken` (user-level, full permissions). Otherwise, it falls back to `client_credentials` (app-level, limited). No manual `connect_portal` call needed.

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
        "username": "your-portal-username",
        "password": "your-portal-password"
      }
    }
  }
}
```

### `connect_portal` Tool Parameters

The `connect_portal` tool accepts these `auth_method` values:

| Value | Behavior |
|-------|----------|
| `auto` | Read from `.env` (default). Tries username/password first, then client_credentials |
| `username_password` | Portal username + password -> generateToken (user-level) |
| `token` | Use explicit portal token |
| `client_credentials` | Use explicit client_id/secret |
| `oauth2` | Browser-based OAuth2 (blocks ~2 min) |

## Usage

### Connect and Search

```
User: Search for all feature services in my portal
Agent: [calls connect_portal, then search_content with item_type="Feature Service"]
```

### Inspect a Layer's Full Schema

```
User: Show me the field schema of the infrastructure layer
Agent: [calls list_layers to find the layer, then describe_layer to get fields, domains, relationships]
```

### Query a Layer

```
User: Show me the first 10 parcels from the cadastral layer
Agent: [calls list_layers to find the parcel layer, then query_features with limit=10]
```

### Inspect a GP Tool Before Running

```
User: What parameters does the buffer analysis tool expect?
Agent: [calls get_gp_task_info with the GP service URL to inspect parameter schemas]
```

### Add Features

```
User: Add these 3 buildings to the infrastructure layer
Agent: [calls add_features with the feature service URL and JSON features]
```

### Share a Web Map

```
User: Share item abc123 with the "Planning Team" group and the whole org
Agent: [calls share_item with org=true and groups=<planning-team-id>]
```

### Read a Web Map

```
User: What basemap and layers are in this web map?
Agent: [calls get_item_data to read the web map JSON, summarizes basemap and operational layers]
```

### Publish a Shapefile

```
User: Publish this shapefile as a hosted feature service
Agent: [calls upload_item to upload the .zip, then publish_from_item to create the service]
```

### Run a Geoprocessing Task

```
User: Run the buffer analysis on the parcels layer with a 100m distance
Agent: [calls get_gp_task_info to inspect parameters, then execute_gp_task with the right inputs]
```

### Bulk Operations

```
User: Delete all my draft items
Agent: [calls search_content to find items, then batch_delete_items to remove them]
```

### Portal Administration

```
User: How many licenses do we have left?
Agent: [calls list_licenses to show license allocation and usage]
```

## Available Tools (34)

### Discovery and Inspection

| Tool | Description |
|------|-------------|
| `connect_portal` | Authenticate with the portal (auto, token, username/password, client_credentials, or OAuth2) |
| `search_content` | Search items by keyword, type, and owner |
| `get_item_details` | Get detailed metadata for a specific item |
| `list_layers` | List layers in a feature/map service with geometry types and counts |
| `describe_layer` | Get full layer schema: fields, types, domains, subtypes, relationships, extent, renderer |
| `query_features` | Query features with attribute/spatial filters and pagination |
| `list_users` | List portal users with roles and status |
| `list_groups` | List portal groups with access levels |
| `portal_health` | Check portal health and system status |
| `server_status` | Check MCP server connection state |

### Feature CRUD, User/Group and Content Management

| Tool | Description |
|------|-------------|
| `add_features` | Add new features to a hosted feature layer |
| `update_features` | Update existing features (by OBJECTID) |
| `delete_features` | Delete features by OBJECTIDs or WHERE clause |
| `get_user_details` | Get detailed user profile (role, privileges, storage, last login) |
| `create_group` | Create a new group with access control |
| `invite_to_group` | Invite users to a group with a role assignment |
| `update_item` | Update item properties (title, description, tags, access) |
| `delete_item` | Delete an item from the portal |
| `share_item` | Share/unshare an item with everyone, org, or specific groups |
| `get_item_data` | Read item data (web map JSON, app config, feature collections) |

### Publishing, Geoprocessing, Admin and Batch

| Tool | Description |
|------|-------------|
| `upload_item` | Upload a local file (CSV, Shapefile, etc.) to portal content |
| `publish_from_item` | Publish an uploaded item as a hosted feature service |
| `create_service` | Create an empty hosted feature service with schema |
| `get_gp_task_info` | Inspect GP tool schemas: list tasks or view parameter definitions before execution |
| `execute_gp_task` | Run a synchronous geoprocessing task |
| `submit_gp_job` | Submit an async GP job and get a job ID for polling |
| `get_gp_job_status` | Check status of a running async geoprocessing job |
| `export_map_image` | Export a MapServer/FeatureServer layer as JPG/PNG/GIF/PDF/SVG |
| `portal_system_info` | Get portal version, platform, and system info (admin) |
| `list_licenses` | Get license information and assignments (admin) |
| `portal_usage` | Get portal usage statistics: users, API calls, storage (admin) |
| `batch_delete_items` | Delete multiple items at once |
| `batch_share_items` | Share/unshare multiple items with the same audiences |
| `batch_update_items` | Update properties of multiple items at once |

## Authentication Methods

| Method | Pros | Cons |
|--------|------|------|
| **Auto** (default) | Zero-config, reads `.env` on startup | Falls back to app-level if no username/password |
| **Username/Password** | User-level permissions via generateToken | Tokens expire in 2 hours; blocked by 2FA |
| **Token** | Quick, no dependencies | Tokens expire, must be obtained separately |
| **Client Credentials** | No browser needed, no 2FA | App-level only (no user identity) |
| **OAuth2** | Full user permissions, 14-day tokens | Opens browser, blocks for ~2 min |

**Enterprise portals with 2FA:** Use `auto` (`.env` with `client_credentials`). Token auth won't work because 2FA blocks token generation. The `client_credentials` flow uses app-level OAuth2, no browser, no 2FA, no user interaction.

**ArcGIS Online or portals without 2FA:** Username/password auth via `generateToken` is the fastest for MCP. Put `username` and `password` in your `.env` file for auto-connect on startup.

**Full user permissions:** Use OAuth2 once to get a long-lived token, then pass it directly.

## What's Next

Here's what we're working on for upcoming releases:

- **`describe_portal`**: A comprehensive portal overview tool that gives you organization details, user counts, storage, and licensing in a single call.
- **`portal_storage_report`**: Detailed breakdown of storage usage by item type, owner, and age.
- **`batch_publish`**: Upload and publish multiple files in one operation.
- **`service_health_check`**: Validate that all feature services in your portal are responding, have correct schemas, and aren't hitting query limits.
- **`OAuth2 token refresh`**: Automatic token refresh so you never get logged out mid-session.
- **`QGIS integration`**: A companion skill that lets you push/pull layers between QGIS Desktop and your portal through the MCP server.

If any of these would solve a problem you're facing, open an issue and let us know. We prioritize based on real-world needs.

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

MIT. See [LICENSE](LICENSE).

## Author

Asem Daaboul ([asem.daaboul@gmail.com](mailto:asem.daaboul@gmail.com))
