# Changelog

## v1.13.0 (2026-09-22)

### Bug Fixes
- **Enterprise search fix**: `search_items("*")` now works on Enterprise portals. When `q=*` is used on a non-arcgis.com portal, it automatically falls back to `access:"private" OR access:"shared" OR access:"org" OR access:"public"` which matches all content (fixes 0-item results on Enterprise).

### Improvements
- **`portal_inventory` system content filtering**: new `exclude_system_content` parameter (default `true`) filters out Esri system accounts (`esri_*`, `portaladmin`) that inflate inventory counts on Enterprise deployments. Set to `false` to include system content.
- `portal_inventory` summary now includes `system_items_filtered` count when filtering is active.

### Tests
- 4 new tests (165 total): Enterprise fallback query, AGOL star preservation, system content exclusion, system content opt-in

## v1.12.0 (2026-09-20)

### New Tools (3)
- `portal_inventory`: scan portal content by type, owner, age, and storage
- `bulk_reassign_ownership`: transfer content ownership between users in bulk
- `offboard_user`: offboard a user by transferring content and removing from groups

### Bug Fixes
- `portal_inventory` AGOL fix: uses `orgid:` filter instead of `q=*` for org-wide scans (which returned 0 items on AGOL)
- `connect_portal` username/password auth now reads credentials from `.env` file instead of stale parent-process env vars
- `_load_env` no longer caches `.env` values across the server lifetime; always reads fresh from disk

### Improvements
- Auto-connect error messages now surface the actual connection error details

## v1.11.1 (2026-09-15)

### Bug Fixes
- Fixed broken `list_portal_users` import (same day as v1.11.0)

## v1.11.0 (2026-09-15)

### New Tools (4)
- `scan_broken_references`: scan web maps/apps for unreachable service URLs
- `find_stale_items`: find items not modified in N days with governance violation detection
- `export_group_content`: export group items to .epk package (Enterprise only)
- `import_group_content`: import items from .epk package into a target group (Enterprise only)

### Improvements
- 4 new client methods for admin operations
- Auto-connect now reports actual error details instead of generic failure
