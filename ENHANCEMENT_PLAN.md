# arcgis-portal-mcp Enhancement Plan

**Date**: August 2026
**Current version**: v1.12.0 (73 tools)
**Last updated**: 2026-10-01

---

## 1. ArcGIS REST API Landscape (as of August 2026)

| Platform | Latest Version | Key Features |
|---|---|---|
| ArcGIS Enterprise | **12.1** (2026) | AI assistants (beta), response caching, createReplica with sync, shared templates, Deep Learning packages, collaboration pagination, Spectral Library item type, workflow manager |
| ArcGIS Enterprise | 12.0 | AI services endpoints, Data Pipelines privilege, Organization AI settings |
| ArcGIS Online | **June 2026** | Service connection item type, new relationship types (Data2Survey, FeatureService2WorkforceMap, Item2Mission, etc.) |
| ArcGIS Online | Feb 2026 | `portal:user:useAIAssistants` privilege, Spectral Library item type |
| ArcGIS Online | Oct 2025 | Discussion item type, `taskState` filter for scheduled tasks |
| ArcGIS Online | June 2025 | Analysis Model (ModelBuilder) item type, Service2Report relationship |
| Enterprise 11.5 | 2025 | Deep Learning packages from Living Atlas, webhook delete-protection events |

### Esri's Own MCP Server (beta, June 29, 2026)
- Focus: **Location Services** (geocoding, routing, elevation, static maps)
- Does NOT cover Portal administration, content management, user/group management, or feature service CRUD
- This is our differentiation: Esri's MCP is a consumer of services, ours is a portal management tool

---

## 2. Gap Analysis: What We're Missing

### HIGH PRIORITY (directly useful for portal admins)

| Category | Missing Endpoint | Why It Matters | API Reference |
|---|---|---|---|
| **Webhooks** | List/Create/Update/Delete/Test webhooks | Portal automation is incomplete without webhook management | `/portals/self/webhooks` | Done (v1.7.0) |
| **Logs** | Query/Clean/Export logs | Essential for portal health monitoring | `/portals/self/logs/query` | Done (v1.7.0) |
| **Collaborations** | List/Get/Sync collaborations | Distributed GIS workflows (Enterprise 12.1 enhanced this) | `/portals/self/collaborations` | Done (v1.8.0) |
| **Organization Settings** | Get/Update org settings | Configure portal behavior, AI assistants (12.0+), token expiration | `/portals/self/settings` | Done (v1.7.0) |
| **Role Privileges** | Get/Set role privileges | Manage what users can do | `/portals/self/roles/{roleId}/privileges` | Done (v1.8.0) |
| **Scheduled Tasks** | List/Get user scheduled tasks (with `taskState` filter, new in Oct 2025) | Monitor automated workflows | `/portals/self/users/{username}/allScheduledTasks` | Done (v1.8.0) |

### MEDIUM PRIORITY (enhances existing tools)

| Category | Missing Endpoint | Why It Matters |
|---|---|---|
| **Server Federation** | List/Validate/Federate/Unfederate servers | Enterprise portal management |
| **Machines** | List machines, machine status | Multi-machine deployment monitoring |
| **SSL Certificates** | List/Import/Generate certificates | Security management |
| **Folders** | Create/List user folders | Content organization | Done (v1.7.0) |
| **Item Relationships** | Add/Remove relationships (we have explore, but not CRUD) | Manage item dependencies |
| **Feature Service: createReplica** | Sync support, export with `syncModel:None` | Offline workflows, data distribution |
| **Export Group Content** | Export as Desktop Style/Solution (12.1+) | Content migration |
| **AI Services** | Enable/Disable/Status (Enterprise 12.0+) | Manage AI assistants for org |

### LOWER PRIORITY (specialized use cases)

| Category | Missing Endpoint | Why It Matters |
|---|---|---|
| **Deep Learning Packages** | Import/Check updates/Install status (11.5+) | ML workflows on Enterprise |
| **System Properties** | Get/Update | Low-level config |
| **Email Settings** | Get/Update/Test | Notification management |
| **Web Adaptors** | List/Configure/Register | Infrastructure management |
| **Content Languages** | Add/Remove | Multilingual portal support |
| **Portal Information** | About/Report | Portal metadata |
| **Backup/Restore** | Export/Import site | Disaster recovery |

---

## 3. Maintenance Plan

### Quarterly Release Cycle

| Quarter | Focus | Target Version |
|---|---|---|
| **Q3 2026** | Webhooks + Logs + Org Settings + Folders | v1.7.0 ✅ |
| **Q4 2026** | Collaborations + Role Management + Scheduled Tasks | v1.8.0 ✅ |
| **Q1 2027** | Security Hardening (read-only mode, tool allowlist, audit log) | v1.9.0 ✅ |
| **Q2 2027** | Feature Service Sync + Item Relationships CRUD + AI Services | v2.0.0 |
| **Q3 2027** | Server Federation + Machines + SSL | v2.1.0 |

### v1.7.0 (August 2026) -- IMPLEMENTED

| # | Tool | Status |
|---|------|--------|
| 1 | `list_webhooks` | Done |
| 2 | `create_webhook` | Done |
| 3 | `update_webhook` | Done |
| 4 | `delete_webhook` | Done |
| 5 | `test_webhook` | Done |
| 6 | `query_logs` | Done |
| 7 | `clean_logs` | Done |
| 8 | `get_org_settings` | Done |
| 9 | `update_org_settings` | Done |
| 10 | `create_folder` | Done |
| 11 | `list_folders` | Done |

### v1.8.0 (August 2026) -- IMPLEMENTED

| # | Tool | Status |
|---|------|--------|
| 1 | `list_collaborations` | Done |
| 2 | `get_collaboration` | Done |
| 3 | `sync_collaboration` | Done |
| 4 | `list_roles` | Done |
| 5 | `get_role_privileges` | Done |
| 6 | `list_scheduled_tasks` | Done |
| 7 | `get_user_scheduled_tasks` | Done |

### v1.9.0 (August 2026) -- IMPLEMENTED

Security hardening release. No new tools added, but three cross-cutting features that dramatically improve production readiness.

| # | Feature | Description |
|---|---------|-------------|
| 1 | `MCP_READ_ONLY` / `--read-only` | Blocks all 25 write/mutating tools at the dispatch level |
| 2 | `MCP_ALLOWED_TOOLS` | Restricts which tools are visible and callable (comma-separated allowlist) |
| 3 | `MCP_AUDIT_LOG` | JSONL audit trail for every tool call with sanitized arguments |

Architecture: `_install_guards()` wraps `mcp._tool_manager.call_tool` and `list_tools` in `main()` before `mcp.run()`. Zero changes to existing tool functions.

Also fixed pre-existing lint issues: duplicate `get_item_data`, unused `items` variable, dead code, misplaced import.

### v1.11.1 (September 2026) -- SHIPPED

Bugfix and robustness release. No new tools, but quality-of-life improvements across 7 existing tools.

| # | Issue | Fix |
|---|-------|-----|
| 1 | `search_items("*")` returns 0 on Enterprise portals | Added `contentStatus=all` to search params |
| 2 | `list_users` returns null for `fullName` | Null-safe fallback to empty string |
| 3 | `find_stale_items` calls `get_item_details` per item (200+ HTTP calls) | Only fetch full details for governance checks on items already classified stale |
| 4 | `list_users` uses `full_name` but `get_user_details` uses `fullname` | Standardized to `full_name` everywhere |
| 5 | `portal_health` missing portal URL | Added `portal_url` and `connected_as` fields |
| 6 | `search_content` default max_items=20 | Increased to 100 |
| 7 | `admin_request` errors don't include HTTP status code | Added `http_status` field to error responses |
| 8 | No token auto-refresh during long operations | Added `_try_reconnect()` with stored credentials; auto-retry once on auth errors |
| 9 | `_ping_url` uses GET (downloads full response body) | Uses HEAD first, falls back to GET on 405 |
| 10 | `list_users` returns formatted date only | Added `last_login_epoch` (raw ms) alongside formatted string |
| 11 | `server_status` doesn't show who you're connected as | Added `auth_method` and `token_status` fields |
| 12 | SKILL.md missing `list_folders` -> `delete_folder` workflow | Added tip in SKILL.md |

---

### Backlog / Deferred Enhancements

Valid improvements identified during community review (September 2026). Not yet scheduled for a specific release.

| # | Issue | Why Deferred |
|---|-------|-------------|
| 3 | Custom roles show role ID not name | Requires extra API call to `/portals/self/roles` + lookup map; valid but adds latency |
| 5 | No admin search endpoint in `list_users` | Feature request (`/portaladmin/searchUsers`); needs `search_users_admin` tool design |
| 6 | `get_user_details` uses community endpoint | Needs try-admin-then-fallback-to-community logic; defer to avoid breaking non-admin usage |
| 7 | `portal_usage` returns 404 on some Enterprise versions | Needs version detection + fallback endpoint discovery |
| 8 | `search_items` doesn't expose `start` parameter | Feature request for explicit pagination control |
| 9 | `scan_broken_references` missing popup/dashboard URLs | Complex URL extraction across popups, print templates, feature actions, dashboard data sources |
| 19 | No rate limiting / request throttling | Configurable delay between requests (e.g. `MCP_REQUEST_DELAY_MS`) |
| 20 | Query special character escaping | Low risk currently; needs validation/sanitization for `+`, `&`, `(`, `)` in search queries |

---

### v2.0.0 (target: Q2 2027) -- PLANNED

Feature Service Sync release. The high-impact release for field ops and distributed GIS.

| # | Tool | Why It Matters |
|---|------|----------------|
| 1 | `create_replica` | Offline workflows, data distribution to field devices |
| 2 | `sync_replica` | Bidirectional data sync between portal and replicas |
| 3 | `add_item_relationship` | CRUD for item dependencies (we have explore, not create/delete) |
| 4 | `remove_item_relationship` | Clean up dependency chains |
| 5 | AI Services management | Enable/Disable/Status for Enterprise 12.0+ AI assistants |

### v2.1.0 (target: Q3 2027) -- PLANNED

Server infrastructure management. Lower priority, needed only for Enterprise admins managing multi-machine deployments.

| # | Tool | Why It Matters |
|---|------|----------------|
| 1 | `list_federated_servers` | See what ArcGIS Server instances are connected |
| 2 | `federate_server` | Register a new ArcGIS Server with the portal |
| 3 | `unfederate_server` | Remove a server registration |
| 4 | `list_machines` | Monitor multi-machine deployment health |
| 5 | `list_ssl_certificates` | Security certificate management |

### Ongoing Maintenance

- **Dependency updates**: Monthly check for `requests`, `fastmcp` updates
- **Test coverage**: Add integration tests for each new tool against test portal
- **CI/CD**: Ensure GitHub Actions runs on all PRs
- **README updates**: Document new tools in tool table, update version badge
- **PyPI releases**: Tag releases to GitHub, update changelog
- **Esri API tracking**: Monitor `developers.arcgis.com/rest/` what's-new pages quarterly

### Release Process

1. Create feature branch (`feature/v1.7.0-webhooks`)
2. Implement tools + tests
3. Run full test suite locally
4. Update README tool table + version
5. PR to master, merge
6. Tag release, push to GitHub
7. Update nanobot config if MCP server command changes

---

## 4. Competitive Positioning

### vs Esri's Official MCP (beta)
| Dimension | Esri MCP | arcgis-portal-mcp |
|---|---|---|
| **Focus** | Location Services (geocoding, routing, elevation) | Portal administration + content management |
| **Auth** | API key | Username/password, OAuth, client_credentials, token |
| **Portal mgmt** | No | Yes (60 tools) |
| **Feature CRUD** | No | Yes |
| **Enterprise support** | No (Location Platform only) | Yes (Portal + AGOL) |
| **Our position** | Complementary | **The only production MCP for portal admin** |

### Strategic Recommendation
- Do NOT compete with Esri MCP on Location Services (geocoding, routing, elevation, static maps) unless explicitly requested
- Double down on **portal administration** which Esri has no interest in covering via MCP
- Consider a thin wrapper around Esri's Location Services MCP for users who want both in one server (low effort, high value)

---

## 5. Obsolete / Completed Items

The following were planned or discussed but are already completed or superseded. Listed here for historical reference only.

| Original Plan Item | Actual Outcome |
|---|---|
| `manage_webhooks` (single tool) | Split into 5 tools: list/create/update/delete/test_webhook |
| `list_scheduled_tasks` | Deferred to v1.8.0 |
| `remove_group_users` | Deferred to v1.8.0 |
| "Phase 4" (clone_item, create_service, create_group, get_service_dependencies) | Already existed in earlier versions, never needed implementation |
| v1.6.0 (42 tools) label | Superseded: v1.7.0 shipped with 53 tools |
