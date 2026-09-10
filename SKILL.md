---
name: arcgis-portal-mcp
description: "MCP server for ArcGIS Portal and ArcGIS Online. 70 tools for search, inspect, publish, query, manage, audit, and administer ArcGIS content. Use when: the user wants to work with feature services, web maps, layers, groups, users, webhooks, or portal administration through an AI assistant."
---

# arcgis-portal-mcp

MCP server for ArcGIS Enterprise Portal and ArcGIS Online. 70 tools covering the full ArcGIS REST API surface.

## When to Use

- User wants to search, publish, query, or manage ArcGIS content
- User needs to audit portal health, usage, or security
- User asks about feature services, web maps, layers, groups, or users
- User wants to clean up, organize, or migrate portal content
- User needs to run geoprocessing tasks or export map images

## Prerequisites

- Python 3.10+
- `pip install arcgis-portal-mcp` (or install from source)
- `.env` file with portal credentials (or MCP client env vars)
- The MCP server must be configured in the AI client

## Tool Reference (70 Tools)

### Connection and Status

| Tool | Description |
|------|-------------|
| `connect_portal` | Connect to a portal. Supports auto, username/password, token, client_credentials, oauth2. |
| `server_status` | Check MCP server connection state, version, active portal. |
| `portal_health` | Quick system status check (requires admin). |

### Discovery and Inspection

| Tool | Description |
|------|-------------|
| `search_content` | Find items by keyword, type, or owner. |
| `get_item_details` | Get metadata for a known item ID. |
| `list_layers` | Find what layers exist in a feature/map service. |
| `describe_layer` | Full schema: fields, types, domains, subtypes, relationships, extent, renderer. |
| `query_features` | Pull data from a layer with attribute/spatial filters. |
| `get_item_data` | Read web map JSON, app config, or feature collection payloads. |
| `explore_item_relationships` | Map how items connect: services, web maps, layers, apps. |
| `analyze_item_impact` | Blast radius assessment before deleting or modifying an item. |
| `scan_service_dependencies` | Find all items that depend on a given service. |

### Feature CRUD

| Tool | Description |
|------|-------------|
| `add_features` | Insert new features into a hosted layer. |
| `update_features` | Modify existing features by OBJECTID. |
| `delete_features` | Remove features by OBJECTID or WHERE clause. |

### Content Management

| Tool | Description |
|------|-------------|
| `update_item` | Change title, description, tags, or access level. |
| `delete_item` | Remove a single item. |
| `share_item` | Share/unshare with everyone, org, or groups. |
| `clone_item` | Duplicate an item with its data. |
| `move_items` | Reassign content ownership between users. |
| `upload_item` | Upload local CSV, Shapefile (zipped), GeoJSON, KML, etc. |
| `publish_from_item` | Publish an uploaded item as a hosted feature service. |
| `create_service` | Create an empty service with a custom schema. |
| `create_folder` | Organize items into folders. |
| `list_folders` | See folder structure. |
| `delete_folder` | Remove a content folder (WARNING: deletes contents too). |

### Users and Groups

| Tool | Description |
|------|-------------|
| `list_users` | See all portal users with roles and status. |
| `get_user_details` | Deep dive on one user: role, privileges, storage, last login. |
| `search_users` | Find users by name, email, or username. |
| `list_groups` | See all groups with access levels. |
| `create_group` | Create a new group. |
| `update_group` | Rename, change visibility, or toggle invitation-only mode. |
| `delete_group` | Remove a group. |
| `invite_to_group` | Add users to a group. |
| `remove_from_group` | Evict members from a group. |
| `list_group_users` | Lightweight targeted group member listing. |
| `audit_group_members` | Full membership audit with role and login details. |

### Geoprocessing

| Tool | Description |
|------|-------------|
| `get_gp_task_info` | Inspect a GP tool's parameters before running it. |
| `execute_gp_task` | Run a synchronous GP task (completes in <5 min). |
| `submit_gp_job` | Submit an async GP job, get a job ID. |
| `get_gp_job_status` | Poll an async job until it completes. |
| `export_map_image` | Render a layer as JPG/PNG/PDF/SVG. |

### Admin

| Tool | Description |
|------|-------------|
| `portal_system_info` | Portal version, platform, license mode. |
| `portal_usage` | User activity, API calls, storage trends. |
| `list_licenses` | License allocation and expiration. |
| `get_org_settings` / `update_org_settings` | Read/write portal configuration. |
| `get_usage_analytics` | Enhanced analytics with per-user ranking. |

### Batch Operations

| Tool | Description |
|------|-------------|
| `batch_delete_items` | Delete multiple items by ID. |
| `batch_share_items` | Apply same sharing to multiple items. |
| `batch_update_items` | Apply same property changes to multiple items. |

### Webhooks and Logs

| Tool | Description |
|------|-------------|
| `list_webhooks` | See all configured webhooks. |
| `create_webhook` / `update_webhook` / `delete_webhook` | Manage webhook lifecycle. |
| `test_webhook` | Verify a webhook endpoint is reachable. |
| `query_logs` | Search portal logs by level, source, time. |
| `clean_logs` | Purge old log entries. |

### Collaborations, Roles, and Scheduled Tasks

| Tool | Description |
|------|-------------|
| `list_collaborations` / `get_collaboration` | Inspect distributed GIS collaborations. |
| `sync_collaboration` | Trigger a collaboration sync. |
| `list_roles` / `get_role_privileges` | Audit organization roles and privileges. |
| `list_scheduled_tasks` / `get_user_scheduled_tasks` | Monitor automated workflows. |

### Admin Problem Solvers (v1.11.0)

| Tool | Description |
|------|-------------|
| `scan_broken_references` | Scan web maps/apps for unreachable service URLs (item or group-level). |
| `find_stale_items` | Find items not modified in N days with governance violation detection. |
| `export_group_content` | Export group items to an .epk package for content migration (Enterprise only). |
| `import_group_content` | Import items from an .epk package into a target group (Enterprise only). |

## Common Workflows

### 1. Publish a Local File as a Hosted Feature Service

```
Step 1: upload_item
  - file_path: path to the local file
  - title: descriptive name
  - type: CSV | Shapefile | GeoJSON | KML
  - tags: comma-separated for searchability
  - access: private | org | public

Step 2: publish_from_item
  - item_id: from Step 1 result
  - service_type: featureService (default)

Step 3: search_content (verify)
  - query: the title you gave it
  - item_type: Feature Service
```

### 2. Inspect a Layer Before Querying

```
Step 1: search_content
  - query: keyword or title
  - item_type: Feature Service

Step 2: list_layers
  - item_id: from Step 1

Step 3: describe_layer
  - service_url: the service URL from Step 2
  - layer_id: the layer you care about

Step 4: query_features
  - item_id: from Step 1
  - layer_id: from Step 2
  - where: SQL filter (use field names from Step 3)
  - out_fields: specific fields to reduce response size
```

### 3. Portal Health Audit

```
Step 1: portal_health
Step 2: portal_system_info
Step 3: portal_usage (period: 1d | 1w | 1M)
Step 4: get_usage_analytics
Step 5: list_licenses
Step 6: query_logs (level: WARNING | SEVERE)
```

### 4. Clean Up Orphaned Content

```
Step 1: search_content
  - query: owner username or keyword
  - max_items: 100

Step 2: For each item:
  scan_service_dependencies
    - service_item_id: item ID from Step 1

Step 3: analyze_item_impact
  - item_id: items with zero dependencies

Step 4: batch_delete_items
  - item_ids: confirmed orphan IDs
```

### 5. Scan for Broken References

```
Step 1: scan_broken_references
  - target_id: web map item ID
  - target_type: item (or group for bulk scan)
  - timeout: 5

Step 2: Review results
  - total_urls: how many URLs found
  - healthy / broken: counts
  - urls[].status: healthy | unreachable | timeout | error
  - urls[].referenced_by: which layer has the problem
```

### 6. Find and Clean Stale Content

```
Step 1: find_stale_items
  - owner: username (or blank for all)
  - days_threshold: 365 (items older than this)
  - item_types: Web Map,Feature Service (optional filter)

Step 2: Review results
  - summary.stale_count: total stale items
  - summary.total_stale_storage_mb: space reclaimable
  - governance_violations: items missing description/tags/snippet

Step 3: batch_delete_items
  - item_ids: IDs of confirmed stale items
```

### 5. Audit Group Membership

```
Step 1: list_groups
Step 2: For each group: audit_group_members
Step 3: get_user_details for users with old last_login timestamps
```

### 6. Manage Group Members

```
Step 1: search_users
  - query: name or email

Step 2: invite_to_group
  - group_id: target group
  - users: comma-separated usernames from Step 1

Step 3: list_group_users (verify)
  - group_id: target group
```

### 7. Export a Map Image

```
Step 1: export_map_image
  - service_url: MapServer or FeatureServer URL
  - bbox: xmin,ymin,xmax,ymax in WGS84
  - format: png | jpg | pdf | svg
```

## Error Patterns

### Authentication Failed

```
Fix:
  1. Check .env has correct portal_url, username, password
  2. For 2FA portals: use client_credentials (app-level)
  3. For AGOL: username/password via generateToken works
  4. Call connect_portal with auth_method="auto" to re-authenticate
```

### TLS Certificate Error

```
Fix: Set MCP_TLS_VERIFY=false in .env
Note: Re-enable for production with valid certs
```

### WHERE Clause Error

```
Fix:
  1. describe_layer to get exact field names and types
  2. Strings need single quotes: NAME = 'value'
  3. Dates need timestamps: DATE_FIELD > timestamp '2024-01-01'
```

### Feature Service Schema Mismatch

```
Fix:
  1. describe_layer to get the exact schema
  2. Match field names exactly (case-sensitive)
  3. Geometry must include spatialReference: {"wkid": 4326}
```

## Security Best Practices

1. **Use read-only mode for demos**: `MCP_READ_ONLY=true`
2. **Use tool allowlisting for production**: `MCP_ALLOWED_TOOLS=name1,name2`
3. **Enable audit logging**: `MCP_AUDIT_LOG=path`
4. **Use scoped allowlists**: Limit portals, owners, and groups
5. **Never commit .env**: Use `.env.example` as template
6. **Verify before deleting**: Always run `analyze_item_impact` first
7. **Use WHERE clauses carefully**: Server validates SQL, but complex joins may fail

## Tips

- Start broad, then narrow: `search_content` first, then `describe_layer`, then `query_features`
- Use `out_fields` to limit returned fields
- Check `server_status` if tools fail mysteriously
- Use async GP jobs (`submit_gp_job` + `get_gp_job_status`) for >5 min tasks
- For >50 items, process batch operations in chunks
- Item IDs are stable: they don't change when items are renamed or moved
- **Delete a folder safely**: `list_folders` first to get the folder ID, then `delete_folder(folder_id)`
  (deleting a folder also deletes all items inside it)
