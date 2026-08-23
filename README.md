# arcgis-portal-mcp

**v1.9.0.** 60 tools for ArcGIS Enterprise Portal and ArcGIS Online.

A Model Context Protocol (MCP) server that gives AI assistants direct access to your ArcGIS content. Search, inspect, edit, publish, and admin through natural language.

Works with Claude Desktop, Cursor, VS Code Copilot, and any MCP-compatible client.

> **Disclaimer:** This is an independent open-source project. Not affiliated with, endorsed by, or sponsored by Esri. "ArcGIS" is a registered trademark of Esri.

## What's new in v1.9.0

- **Read-only mode**: block all write/mutating tools via `MCP_READ_ONLY=true` or `--read-only` CLI flag
- **Tool allowlisting**: restrict which tools the AI client can see and invoke via `MCP_ALLOWED_TOOLS=name1,name2`
- **Audit logging**: record every tool call to a JSONL file via `MCP_AUDIT_LOG=path`, with automatic sanitization of passwords and tokens

### What's new in v1.8.0

- **Collaborations**: list, inspect, and trigger sync for distributed GIS collaborations
- **Roles & privileges**: list organization roles with their full privilege sets
- **Scheduled tasks**: list and filter organization-wide or per-user scheduled tasks

### What's new in v1.7.0

- **Webhooks**: list, create, update, delete, and test organization webhooks for portal automation
- **Logs**: query and clean portal logs with level/source/time filters
- **Organization settings**: read and update portal configuration
- **Folders**: create and list content folders for better item organization

### What's new in v1.6.0

- **Configurable TLS verification**: certificate verification is now ON by default; set `MCP_TLS_VERIFY=false` for self-signed Enterprise certs
- **Scoped allowlists**: restrict which portals, owners, groups, and service URLs the server can access via `MCP_ALLOWED_*` env vars

> Security improvements inspired by community feedback from [muend](https://community.esri.com/t5/user/viewprofilepage/user-id/1002564) on Esri Community.

### What's new in v1.5.0

- **Item impact analysis**: find out what breaks if you delete an item.
- **Relationship explorer**: map services, web maps, layers, and apps.
- **Usage analytics**: API calls, active users, storage trends (admin).
- **Group membership audit**: who is in which groups, inactive users.
- **Service dependency scanner**: broken links, missing data sources.
- **Batch operations**: update, share, delete multiple items at once.
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
- **Hardened**: SQL injection validation on WHERE clauses, XSS protection in OAuth callbacks, automatic retry with exponential backoff, read-only mode, tool allowlisting, and audit logging.
- **Scoped allowlists**: optionally restrict which portals, owners, groups, and service URLs the server can access, reducing agent blast radius in production.

## Installation

```bash
pip install git+https://github.com/Asem-D/arcgis-portal-mcp.git
```

Or clone and install from source:

```bash
git clone https://github.com/Asem-D/arcgis-portal-mcp.git
cd arcgis-portal-mcp
pip install -e .
```

**Windows users:** `pywin32` is installed automatically as a platform-specific dependency.

## Configuration

### `.env` File (recommended)

Create a `.env` file for automatic connection on startup. A template is provided:

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

The server searches for `.env` in this order:
1. **Package directory** (next to the server code) — works for source installs
2. **Current working directory** — works when launched from the project root
3. **`~/.arcgis-portal-mcp/.env`** — works for `pip install` users

Quoted values are supported: `password="my secret"`. Existing OS environment variables take precedence over `.env` values.

The server reads `.env` on startup and connects automatically. If `username` + `password` are provided, it uses `generateToken` (user-level, full permissions). Otherwise, it falls back to `client_credentials` (app-level, limited). No manual `connect_portal` call needed.

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

The server auto-connects from `.env` (searched in package dir, CWD, or `~/.arcgis-portal-mcp/`). No env vars needed in the MCP config.

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

### Connecting MCP Clients

Step-by-step setup for popular AI coding assistants.

#### Claude Desktop

1. Install arcgis-portal-mcp (see [Installation](#installation))
2. Create `~/.arcgis-portal-mcp/.env` with your credentials
3. Open Claude Desktop Settings (Claude menu > Settings > Developer > Edit Config)
4. Add to `claude_desktop_config.json`:

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

5. Restart Claude Desktop. The server connects automatically from `.env`.

> **macOS/Linux:** Replace `python` with `python3` if needed. Use `which python3` to find the full path.

#### Cursor

1. Install arcgis-portal-mcp (see [Installation](#installation))
2. Create `~/.arcgis-portal-mcp/.env` with your credentials
3. Open Cursor Settings (gear icon > MCP)
4. Click "Add new global server" and paste:

```json
{
  "name": "arcgis-portal",
  "command": "python",
  "args": ["-m", "arcgis_portal_mcp.server"],
  "cwd": "/path/to/arcgis-portal-mcp"
}
```

5. Toggle the server on. Cursor connects automatically.

#### VS Code (GitHub Copilot)

1. Install arcgis-portal-mcp (see [Installation](#installation))
2. Create `~/.arcgis-portal-mcp/.env` with your credentials
3. Add to `.vscode/mcp.json` in your workspace (or user settings):

```json
{
  "servers": {
    "arcgis-portal": {
      "command": "python",
      "args": ["-m", "arcgis_portal_mcp.server"],
      "cwd": "/path/to/arcgis-portal-mcp"
    }
  }
}
```

4. Reload VS Code. The MCP server appears in the Copilot chat panel.

#### Windsurf

1. Install arcgis-portal-mcp (see [Installation](#installation))
2. Create `~/.arcgis-portal-mcp/.env` with your credentials
3. Open Windsurf Settings > Cascade > MCP Servers
4. Add a new server:

```json
{
  "name": "arcgis-portal",
  "command": "python",
  "args": ["-m", "arcgis_portal_mcp.server"],
  "cwd": "/path/to/arcgis-portal-mcp"
}
```

5. Restart Windsurf.

#### nanobot (this agent)

Already configured. The MCP server is registered in `config.json` under `tools.mcpServers.arcgis-portal`.

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

## Available Tools (60)

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
| `create_folder` | Create a content folder for organizing items |
| `list_folders` | List content folders for a user |

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
| `get_org_settings` | Get organization settings (admin) |
| `update_org_settings` | Update organization settings (admin) |
| `batch_delete_items` | Delete multiple items at once |
| `batch_share_items` | Share/unshare multiple items with the same audiences |
| `batch_update_items` | Update properties of multiple items at once |
| `explore_item_relationships` | Explore item relationships: services, web maps, layers, apps |
| `audit_group_members` | Audit group membership: who's in which groups, inactive users |
| `scan_service_dependencies` | Scan feature service dependencies: broken links, missing sources |
| `analyze_item_impact` | Analyze item impact: what breaks if an item is deleted |
| `get_usage_analytics` | Get portal usage analytics: API calls, active users, storage trends |

### Webhooks and Logs (v1.7.0)

| Tool | Description |
|------|-------------|
| `list_webhooks` | List all organization webhooks |
| `create_webhook` | Create a new organization webhook with event triggers |
| `update_webhook` | Update webhook properties (name, URL, triggers, active state) |
| `delete_webhook` | Delete a webhook by ID |
| `test_webhook` | Send a test payload to verify webhook connectivity |
| `query_logs` | Query portal logs with level, source, and time filters |
| `clean_logs` | Delete portal logs older than a specified time |

### Collaborations, Roles and Scheduled Tasks (v1.8.0)

| Tool | Description |
|------|-------------|
| `list_collaborations` | List all collaborations the portal participates in |
| `get_collaboration` | Get details of a specific collaboration |
| `sync_collaboration` | Trigger sync for a collaboration workspace |
| `list_roles` | List all organization roles with their privileges |
| `get_role_privileges` | Get privileges for a specific role |
| `list_scheduled_tasks` | List all scheduled tasks in the organization (admin) |
| `get_user_scheduled_tasks` | List scheduled tasks for a specific user |

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

## Security

### TLS Certificate Verification

By default, TLS certificate verification is **enabled**. For Enterprise portals with self-signed certificates, disable it in your `.env`:

```env
MCP_TLS_VERIFY=false
```

When verification is disabled, the server logs a warning. Re-enable it for production deployments with valid certificates.

### Scoped Allowlists

For production use, constrain the server to specific portals, owners, groups, and service endpoints. All allowlists are optional; when unset, the server operates without restrictions (backward compatible).

```env
# Only connect to these portals
MCP_ALLOWED_PORTAL_URLS=https://gis.example.com/portal

# Only access items owned by these users
MCP_ALLOWED_OWNERS=jsmith,mgarcia,admin

# Only share with these group IDs
MCP_ALLOWED_GROUPS=abc123,def456

# Only operate on services at these URL prefixes
MCP_ALLOWED_SERVICE_URLS=https://gis.example.com/portal/sharing/rest/services
```

Operations targeting resources outside the allowlist are rejected with a clear error message. For batch operations, items outside the owner allowlist are skipped with per-item reporting.

### Read-Only Mode (v1.9.0)

Blocks all write/mutating tools server-side. Useful for evaluation, demos, and environments where the AI should only read data.

```env
# Via environment variable
MCP_READ_ONLY=true
```

```bash
# Or via CLI flag (overrides env var)
python -m arcgis_portal_mcp.server --read-only
```

When active, any tool that creates, updates, or deletes portal content is rejected with a clear error message. Read-only tools (search, query, list, describe) remain fully functional.

### Tool Allowlisting (v1.9.0)

Restricts which tools the AI client can see and invoke. When set, only the named tools are exposed via the MCP protocol.

```env
# Only expose these 5 tools to the AI
MCP_ALLOWED_TOOLS=search_content,query_features,list_layers,describe_layer,portal_health
```

When unset (the default), all 60 tools are available. Tools not on the list are invisible to the AI client and rejected if called directly.

### Audit Logging (v1.9.0)

Records every tool call to a JSONL file for security review and compliance.

```env
# Log all tool calls to this file
MCP_AUDIT_LOG=/path/to/audit.jsonl
```

Each entry contains:

```json
{"ts": 1723500000.0, "tool": "search_content", "args": {"query": "parcels"}, "status": "ok", "duration_ms": 142.3}
```

Sensitive arguments (`password`, `token`, `client_secret`, `secret`) are automatically replaced with `***` in the log.

## What's Next

Here's what we're working on for upcoming releases:

### v2.0.0 — Feature Service Sync

- **createReplica / sync**: offline workflows and bidirectional data sync
- **Item relationship CRUD**: add/remove item dependencies
- **AI Services management**: enable/disable AI assistants (Enterprise 12.0+)

### v2.1.0 — Server Infrastructure

- **Server federation**: list, validate, federate, and unfederate servers
- **Machine monitoring**: list machines and deployment status
- **SSL certificate management**: list, import, generate

If any of these would solve a problem you're facing, [open an issue](https://github.com/Asem-D/arcgis-portal-mcp/issues) and let us know. We prioritize based on real-world needs.

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
