# arcgis-portal-mcp Enhancement Plan

**Date**: August 2026
**Current version**: v1.6.0 (42 tools)

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
| **Webhooks** | List/Create/Update/Delete/Test webhooks | Portal automation is incomplete without webhook management | `/portals/self/webhooks` |
| **Logs** | Query/Clean/Export logs | Essential for portal health monitoring | `/portals/self/logs/query` |
| **Collaborations** | List/Get/Sync collaborations | Distributed GIS workflows (Enterprise 12.1 enhanced this) | `/portals/self/collaborations` |
| **Organization Settings** | Get/Update org settings | Configure portal behavior, AI assistants (12.0+), token expiration | `/portals/self/settings` |
| **Role Privileges** | Get/Set role privileges | Manage what users can do | `/portals/self/roles/{roleId}/privileges` |
| **Scheduled Tasks** | List/Get user scheduled tasks (with `taskState` filter, new in Oct 2025) | Monitor automated workflows | `/portals/self/users/{username}/allScheduledTasks` |

### MEDIUM PRIORITY (enhances existing tools)

| Category | Missing Endpoint | Why It Matters |
|---|---|---|
| **Server Federation** | List/Validate/Federate/Unfederate servers | Enterprise portal management |
| **Machines** | List machines, machine status | Multi-machine deployment monitoring |
| **SSL Certificates** | List/Import/Generate certificates | Security management |
| **Folders** | Create/List user folders | Content organization |
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
| **Q4 2026** | Collaborations + Role Management + Scheduled Tasks | v1.8.0 |
| **Q1 2027** | Server Federation + Machines + SSL | v1.9.0 |
| **Q2 2027** | Feature Service Sync + Item Relationships CRUD + AI Services | v2.0.0 |

### v1.7.0 Plan (target: September 2026) -- IMPLEMENTED 2026-08-07

1. **Webhooks tool** (`manage_webhooks`): List, create, update, delete, test portal webhooks
2. **Logs tool** (`query_logs`): Query portal logs with filters (level, source, time range)
3. **Org Settings tool** (`get_org_settings`, `update_org_settings`): Read/write portal configuration
4. **Scheduled Tasks tool** (`list_scheduled_tasks`): List with `taskState` filter (Oct 2025 API)
5. **Create Folder tool** (`create_folder`): Organize items into folders
6. **Remove Group Users tool** (`remove_group_users`): Complement to `invite_to_group`

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
| **Portal mgmt** | No | Yes (42 tools) |
| **Feature CRUD** | No | Yes |
| **Enterprise support** | No (Location Platform only) | Yes (Portal + AGOL) |
| **Our position** | Complementary | **The only production MCP for portal admin** |

### Strategic Recommendation
- Do NOT compete with Esri MCP on Location Services (geocoding, routing, elevation, static maps) unless explicitly requested
- Double down on **portal administration** which Esri has no interest in covering via MCP
- Consider a thin wrapper around Esri's Location Services MCP for users who want both in one server (low effort, high value)
