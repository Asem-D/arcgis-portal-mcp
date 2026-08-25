"""ArcGIS Portal MCP Server.

An MCP server that gives AI assistants access to ArcGIS Portal and Online
via structured tools. Built on the Model Context Protocol for integration
with Claude Desktop, Cursor, VS Code Copilot, and other MCP clients.

Phase 1 (v0.1): Auth, content search, item details, layer queries, user/group listing.
Phase 2 (v0.2): Feature CRUD, user/group management, content management.
Phase 3 (v1.0): Service publishing, geoprocessing, portal admin, batch operations.
v1.1.0: Username/password auth via generateToken.
v1.2.0: describe_layer (full layer schema), get_gp_task_info (GP task inspection).
v1.7.0: Webhooks, logs, org settings, folders.
v1.8.0: Collaborations, roles & privileges, scheduled tasks.
v1.9.0: Security hardening (read-only mode, tool allowlisting, audit logging).
"""

from __future__ import annotations

import argparse
import functools
import json
import logging
import os
import re
import sys
import time as _time
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import __version__
from .client import ArcGISClient

logger = logging.getLogger("arcgis-portal-mcp")

# Destructive SQL keywords that should never appear in a WHERE clause
_DANGEROUS_SQL_PATTERNS = re.compile(
    r"(?:;\s*(?:DROP|DELETE|INSERT|UPDATE|ALTER|TRUNCATE|CREATE|EXEC|EXECUTE|GRANT|REVOKE)"
    r"|--\s"
    r"|/\*"
    r"|\*/"
    r"|\b(?:DROP|DELETE|INSERT|UPDATE|ALTER|TRUNCATE|CREATE|EXEC|EXECUTE|GRANT|REVOKE)\b"
    r")",
    re.IGNORECASE,
)


def _validate_where_clause(where: str) -> str | None:
    """Validate a WHERE clause for safety. Returns an error message if invalid, else None."""
    if not where or where.strip() == "1=1":
        return None
    if _DANGEROUS_SQL_PATTERNS.search(where):
        return (
            f"WHERE clause contains potentially dangerous SQL: '{where}'. "
            "Only simple attribute filters are allowed (e.g., \"STATUS = 'Active'\")."
        )
    return None


# Create the MCP server
mcp = FastMCP(
    "arcgis-portal-mcp",
    instructions=(
        "Access ArcGIS Portal and Online via AI-friendly tools. "
        "Search content, query features, manage users and groups, "
        "publish services, run geoprocessing tasks, and administer "
        "the portal. Supports both Enterprise Portal and "
        "ArcGIS Online. No dependency on the `arcgis` Python package, "
        "uses raw REST API for maximum compatibility."
    ),
)

# Global client instance (shared across tools)
_client: ArcGISClient | None = None


def _get_client() -> ArcGISClient:
    """Get or create the global client."""
    global _client
    if _client is None:
        _client = ArcGISClient()
    return _client


def _require_connected() -> ArcGISClient | None:
    """Get client, or return None with error message if not connected."""
    client = _get_client()
    if not client.is_connected:
        return None
    return client


# =========================================================================
# Security: Scoped Allowlists (v1.6.0)
# =========================================================================
# When set, these env vars restrict which portals, owners, groups, and
# service URLs the server can access.  Unset = no restriction (backward
# compatible).  Comma-separated, whitespace trimmed.

_allowlist_cache: dict[str, list[str] | None] | None = None


def _load_allowlists() -> dict[str, list[str] | None]:
    """Load and cache allowlists from environment variables."""
    global _allowlist_cache
    if _allowlist_cache is not None:
        return _allowlist_cache

    def _parse(key: str) -> list[str] | None:
        raw = os.environ.get(key, "").strip()
        if not raw:
            return None
        return [item.strip().rstrip("/") for item in raw.split(",") if item.strip()]

    _allowlist_cache = {
        "portal_urls": _parse("MCP_ALLOWED_PORTAL_URLS"),
        "owners": _parse("MCP_ALLOWED_OWNERS"),
        "groups": _parse("MCP_ALLOWED_GROUPS"),
        "service_urls": _parse("MCP_ALLOWED_SERVICE_URLS"),
    }
    return _allowlist_cache


def _check_portal_url(url: str) -> str | None:
    """Check if a portal URL is on the allowlist. Returns error or None."""
    lists = _load_allowlists()
    allowed = lists["portal_urls"]
    if allowed is None:
        return None  # no restriction
    normalised = url.rstrip("/").lower()
    for entry in allowed:
        if normalised.startswith(entry.lower()):
            return None
    return (
        f"Portal URL '{url}' is not on the allowlist. "
        f"Allowed: {', '.join(allowed)}. "
        "Set MCP_ALLOWED_PORTAL_URLS in .env to permit this portal."
    )


def _check_item_owner(item_id: str) -> str | None:
    """Check if an item's owner is on the allowlist. Returns error or None."""
    lists = _load_allowlists()
    allowed = lists["owners"]
    if allowed is None:
        return None
    client = _get_client()
    details = client.get_item_details(item_id)
    owner = (details or {}).get("owner", "")
    if owner in allowed:
        return None
    return (
        f"Item {item_id} is owned by '{owner}', which is not on the allowlist. "
        f"Allowed owners: {', '.join(allowed)}."
    )


def _check_group_ids(groups_str: str) -> str | None:
    """Check if group IDs are on the allowlist. Returns error or None."""
    lists = _load_allowlists()
    allowed = lists["groups"]
    if allowed is None:
        return None
    ids = [g.strip() for g in groups_str.split(",") if g.strip()]
    bad = [g for g in ids if g not in allowed]
    if bad:
        return (
            f"Group ID(s) {', '.join(bad)} not on the allowlist. "
            f"Allowed groups: {', '.join(allowed)}."
        )
    return None


def _check_service_url(url: str) -> str | None:
    """Check if a service URL is on the allowlist. Returns error or None."""
    lists = _load_allowlists()
    allowed = lists["service_urls"]
    if allowed is None:
        return None
    normalised = url.rstrip("/").lower()
    for entry in allowed:
        if normalised.startswith(entry.lower()):
            return None
    return (
        f"Service URL '{url}' is not on the allowlist. "
        f"Allowed prefixes: {', '.join(allowed)}."
    )


# =========================================================================
# Security: Read-Only Mode, Tool Allowlisting, Audit Logging (v1.9.0)
# =========================================================================
# Read-only mode: blocks all write/mutating tools when enabled.
# Tool allowlist: restricts which tools the AI client can see and invoke.
# Audit log: records every tool call (name, args, status, duration) to a JSONL file.

_READ_ONLY: bool = os.environ.get("MCP_READ_ONLY", "false").lower() in (
    "true", "1", "yes",
)

_raw_allow = os.environ.get("MCP_ALLOWED_TOOLS", "").strip()
_TOOL_ALLOWLIST: set[str] | None = (
    {t.strip() for t in _raw_allow.split(",") if t.strip()} if _raw_allow else None
)

_AUDIT_LOG_PATH: str | None = os.environ.get("MCP_AUDIT_LOG") or None

# Tools that modify portal state — blocked when read-only mode is active.
_WRITE_TOOLS: set[str] = {
    # Feature CRUD
    "add_features", "update_features", "delete_features",
    # Content management
    "create_group", "invite_to_group", "update_item", "delete_item",
    "share_item", "upload_item", "publish_from_item", "create_service",
    "create_folder", "clone_item", "move_items",
    # Batch operations
    "batch_delete_items", "batch_share_items", "batch_update_items",
    # Webhooks
    "create_webhook", "update_webhook", "delete_webhook", "test_webhook",
    # Admin
    "update_org_settings", "clean_logs",
    # Collaborations
    "sync_collaboration",
}


def _write_audit_entry(
    tool_name: str,
    args: dict[str, Any],
    status: str,
    duration_ms: float,
    error: str | None = None,
) -> None:
    """Append a single audit entry to the JSONL log file."""
    if not _AUDIT_LOG_PATH:
        return
    entry: dict[str, Any] = {
        "ts": _time.time(),
        "tool": tool_name,
        "status": status,
        "duration_ms": round(duration_ms, 1),
    }
    if error:
        entry["error"] = error
    # Sanitize sensitive args — never log passwords, tokens, secrets
    _SENSITIVE_KEYS = {"password", "token", "client_secret", "secret"}
    entry["args"] = {
        k: ("***" if k.lower() in _SENSITIVE_KEYS else v)
        for k, v in args.items()
    }
    try:
        with open(_AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as exc:
        logger.warning("Failed to write audit entry: %s", exc)


_env_cache: dict[str, str] | None = None


def _find_env_path() -> Path | None:
    """Search for .env file in standard locations.

    Returns the first path that exists, or None.
    """
    candidates = [
        Path(__file__).resolve().parent.parent / ".env",  # package dir
        Path.cwd() / ".env",                               # working dir
        Path.home() / ".arcgis-portal-mcp" / ".env",       # home dir (pip installs)
    ]

    for candidate in candidates:
        if candidate.exists():
            return candidate

    logger.debug("No .env file found (searched: %s)", ", ".join(str(c) for c in candidates))
    return None


def _load_env_file(env_path: Path) -> dict[str, str]:
    """Parse a .env file and return key-value pairs.

    Handles: comments, blank lines, quoted values, BOM, empty values.
    Sets non-empty values in os.environ (existing env vars take precedence).
    """
    env_vars: dict[str, str] = {}

    logger.info("Loading env from %s", env_path)

    with open(env_path, encoding="utf-8-sig") as f:  # utf-8-sig strips BOM
        for lineno, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                logger.warning(".env line %d: no '=' sign, skipping: %r", lineno, line)
                continue

            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()

            if not key:
                logger.warning(".env line %d: empty key, skipping", lineno)
                continue

            # Strip surrounding quotes (single or double) from values
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]

            # Skip empty values — don't pollute os.environ with ghost credentials
            if not value:
                logger.debug(".env: skipping empty value for key '%s'", key)
                continue

            # Don't override existing env vars (real env takes precedence)
            if key not in os.environ:
                os.environ[key] = value
            env_vars[key] = os.environ[key]

    logger.info(
        "Loaded %d env var(s) from %s",
        len(env_vars),
        env_path,
    )
    return env_vars


def _load_env() -> dict[str, str]:
    """Load environment variables from .env file.

    Searches for .env in this order:
    1. Package directory (next to this file) — works for dev/source installs
    2. Current working directory — works when launched from project root
    3. User home (~/.arcgis-portal-mcp/.env) — works for pip installs

    Returns a dict of KEY=VALUE pairs found in the .env file.
    Existing environment variables take precedence (not overwritten).
    Results are cached after the first call.
    """
    global _env_cache
    if _env_cache is not None:
        return _env_cache

    env_path = _find_env_path()
    if env_path is None:
        _env_cache = {}
        return {}

    _env_cache = _load_env_file(env_path)
    return _env_cache


def _reset_env_cache() -> None:
    """Reset the env cache. For testing only."""
    global _env_cache
    _env_cache = None


def _auto_connect() -> tuple[bool, str | None]:
    """Auto-connect using credentials from .env file.

    Tries in order:
    0. Reuse existing connection if client is already connected
    1. username + password -> generateToken (user-level, full permissions)
    2. client_id + client_secret -> client_credentials (app-level, limited)

    Returns (True, method_name) if connected, (False, None) otherwise.
    """
    client = _get_client()

    # If already connected, reuse existing connection (check FIRST to avoid
    # unnecessary .env loading and to survive env-path quirks on some hosts)
    if client.is_connected:
        logger.info("Auto-connect: reusing existing connection as %s", client.username)
        return True, "reused"

    env = _load_env()

    portal_url = env.get("portal_url") or os.environ.get("PORTAL_URL")
    username = env.get("username") or os.environ.get("ARCGIS_USERNAME")
    password = env.get("password") or os.environ.get("ARCGIS_PASSWORD")
    client_id = env.get("oauth_client_id") or os.environ.get("OAUTH_CLIENT_ID")
    client_secret = env.get("oauth_client_secret") or os.environ.get("OAUTH_CLIENT_SECRET")

    if not portal_url:
        logger.info("Auto-connect skipped: no portal_url in .env")
        return False, None

    # Try username/password first (user-level token)
    if username and password:
        try:
            result = client.connect_username_password(portal_url, username, password)
            logger.info(
                "Auto-connected to %s via generateToken (user: %s)",
                portal_url,
                result.get("username", "unknown"),
            )
            return True, "generateToken"
        except Exception as e:
            logger.warning("generateToken auth failed, falling back to client_credentials: %s", e)

    # Fall back to client_credentials (app-level token)
    if client_id and client_secret:
        try:
            result = client.connect_client_credentials(portal_url, client_id, client_secret)
            logger.info(
                "Auto-connected to %s via client_credentials (user: %s)",
                portal_url,
                result.get("username", "unknown"),
            )
            return True, "client_credentials"
        except Exception as e:
            logger.warning("client_credentials auth failed: %s", e)

    logger.info(
        "Auto-connect skipped: no usable credentials in .env "
        "(need username+password or oauth_client_id+oauth_client_secret)"
    )
    return False, None


# =========================================================================
# Tools
# =========================================================================


@mcp.tool()
def connect_portal(
    portal_url: str | None = None,
    auth_method: str = "auto",
    token: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
    username: str | None = None,
    password: str | None = None,
) -> dict[str, Any]:
    """Connect to an ArcGIS Portal or ArcGIS Online instance.

    Supports five authentication methods:
    - auto: Read credentials from .env file (default)
    - username_password: Portal username + password -> generateToken (user-level)
    - token: Use an existing portal token (quickest for MCP)
    - client_credentials: OAuth2 app-level auth (no browser needed)
    - oauth2: Browser-based OAuth2 (full user permissions, blocks for ~2 min)

    For Enterprise portals with 2FA, use client_credentials or oauth2.
    The .env file should contain: portal_url, and either (username + password)
    or (oauth_client_id + oauth_client_secret).

    Args:
        portal_url: Base URL of the portal (e.g. https://gis.example.com/portal).
                    Required for token/client_credentials/oauth2; optional for auto
                    (reads from .env).
        auth_method: "auto", "username_password", "token", "client_credentials", or "oauth2"
        token: Existing portal token (required when auth_method="token")
        client_id: OAuth2 client ID (required for client_credentials/oauth2,
                   optional for auto, reads from .env)
        client_secret: OAuth2 client secret (required for client_credentials/oauth2,
                       optional for auto, reads from .env)
        username: Portal username (required for username_password,
                  optional for auto, reads from .env)
        password: Portal password (required for username_password,
                  optional for auto, reads from .env)
    """
    client = _get_client()

    try:
        # Allowlist: check portal URL before connecting
        if portal_url:
            url_err = _check_portal_url(portal_url)
            if url_err:
                return {"status": "error", "error": url_err}

        if auth_method == "auto":
            # Try auto-connect from .env
            connected, method = _auto_connect()
            if connected:
                if method == "reused":
                    method_label = f"{client._auth_method} (auto, reused)" if client._auth_method else "auto (reused)"
                else:
                    method_label = f"{method} (auto)"
                return {
                    "status": "ok",
                    "username": client.username,
                    "portal_url": client.portal_url,
                    "auth_method": method_label,
                    "expires_in": client._token_expires - __import__("time").time() if client._token_expires else None,
                }
            else:
                return {
                    "status": "error",
                    "error": (
                        "Auto-connect failed. Ensure .env contains: "
                        "portal_url and either (username + password) or "
                        "(oauth_client_id + oauth_client_secret)."
                    ),
                }

        elif auth_method == "username_password":
            if not username:
                username = os.environ.get("username") or os.environ.get("ARCGIS_USERNAME")
            if not password:
                password = os.environ.get("password") or os.environ.get("ARCGIS_PASSWORD")
            if not portal_url:
                portal_url = os.environ.get("portal_url") or os.environ.get("PORTAL_URL")

            if not all([portal_url, username, password]):
                return {
                    "status": "error",
                    "error": "portal_url, username, and password are required (or set in .env)",
                }
            result = client.connect_username_password(portal_url, username, password)
            return {
                "status": "ok",
                "username": result["username"],
                "portal_url": client.portal_url,
                "auth_method": "username_password",
                "expires_in": result["expires_in"],
            }

        elif auth_method == "token":
            if not token:
                return {"status": "error", "error": "token is required for token auth"}
            if not portal_url:
                return {"status": "error", "error": "portal_url is required for token auth"}
            user_info = client.connect_portal(portal_url, token)
            return {
                "status": "ok",
                "username": client.username,
                "portal_url": client.portal_url,
                "auth_method": "token",
                "user_full_name": user_info.get("fullName", ""),
                "user_email": user_info.get("email", ""),
            }

        elif auth_method == "client_credentials":
            # Fall back to .env values if not explicitly provided
            if not client_id:
                client_id = os.environ.get("oauth_client_id") or os.environ.get("OAUTH_CLIENT_ID")
            if not client_secret:
                client_secret = os.environ.get("oauth_client_secret") or os.environ.get("OAUTH_CLIENT_SECRET")
            if not portal_url:
                portal_url = os.environ.get("portal_url") or os.environ.get("PORTAL_URL")

            if not all([portal_url, client_id, client_secret]):
                return {
                    "status": "error",
                    "error": "portal_url, client_id, and client_secret are required (or set in .env)",
                }
            result = client.connect_client_credentials(portal_url, client_id, client_secret)
            return {
                "status": "ok",
                "username": result["username"],
                "portal_url": client.portal_url,
                "auth_method": "client_credentials",
                "expires_in": result["expires_in"],
            }

        elif auth_method == "oauth2":
            if not client_id:
                client_id = os.environ.get("oauth_client_id") or os.environ.get("OAUTH_CLIENT_ID")
            if not client_secret:
                client_secret = os.environ.get("oauth_client_secret") or os.environ.get("OAUTH_CLIENT_SECRET")
            if not portal_url:
                portal_url = os.environ.get("portal_url") or os.environ.get("PORTAL_URL")

            if not all([portal_url, client_id, client_secret]):
                return {
                    "status": "error",
                    "error": "portal_url, client_id, and client_secret are required (or set in .env)",
                }
            result = client.connect_oauth2(portal_url, client_id, client_secret)
            return {
                "status": "ok",
                "username": result["username"],
                "portal_url": client.portal_url,
                "auth_method": "oauth2",
                "user_full_name": result.get("user_info", {}).get("fullName", ""),
            }

        else:
            return {
                "status": "error",
                "error": f"Unknown auth_method: {auth_method}. Use 'auto', 'username_password', 'token', 'client_credentials', or 'oauth2'.",
            }

    except ConnectionError as e:
        return {"status": "error", "error": str(e)}
    except Exception as e:
        return {"status": "error", "error": f"Connection failed: {e}"}


@mcp.tool()
def search_content(
    query: str = "*",
    item_type: str | None = None,
    owner: str | None = None,
    max_items: int = 20,
) -> dict[str, Any]:
    """Search for content (items) in the connected ArcGIS Portal.

    Finds web maps, feature services, layers, applications, and more.
    Supports keyword search, type filtering, and owner filtering.

    Args:
        query: Search keywords (e.g. "parcels", "boundary", "roads")
        item_type: Filter by item type (e.g. "Feature Service", "Web Map",
                   "Map Service", "Image Service", "Dashboard")
        owner: Filter by item owner username
        max_items: Maximum items to return (default 20, max 1000)
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    try:
        items = client.search_items(
            query=query,
            item_type=item_type,
            owner=owner,
            max_items=min(max_items, 1000),
        )
        return {
            "status": "ok",
            "count": len(items),
            "items": items,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def get_item_details(item_id: str) -> dict[str, Any]:
    """Get detailed metadata for a specific portal item.

    Returns title, type, owner, tags, snippet, URL, and other properties.
    Useful for understanding what a feature service contains before querying.

    Args:
        item_id: The item's unique ID (found via search_content)
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    try:
        details = client.get_item_details(item_id)
        if not details or "error" in details:
            error_msg = details.get("error", "Item not found") if details else "No response"
            return {"status": "error", "error": error_msg}

        # Extract key fields for readability
        return {
            "status": "ok",
            "id": details.get("id"),
            "title": details.get("title"),
            "type": details.get("type"),
            "owner": details.get("owner"),
            "url": details.get("url"),
            "snippet": details.get("snippet", ""),
            "description": details.get("description", "")[:500] if details.get("description") else "",
            "tags": details.get("tags", []),
            "created": details.get("created"),
            "modified": details.get("modified"),
            "size": details.get("size", 0),
            "num_views": details.get("numViews", 0),
            "access": details.get("access"),
            "content_status": details.get("contentStatus"),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def list_layers(item_id: str) -> dict[str, Any]:
    """List the layers in a feature service or map service item.

    Shows layer names, IDs, geometry types, and feature counts.
    Use this to identify which layer to query before calling query_features.

    Args:
        item_id: The feature service item ID (found via search_content)
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    try:
        # Get item details to find the service URL
        details = client.get_item_details(item_id)
        if not details or "error" in details:
            return {"status": "error", "error": "Item not found or inaccessible"}

        service_url = details.get("url", "")
        if not service_url:
            return {"status": "error", "error": "Item has no service URL (not a service item)"}

        # Query the service root to get layer info
        params = {"f": "json"}
        if client.token:
            params["token"] = client.token

        resp = client._session.get(service_url, params=params, timeout=30)
        service_data = resp.json()

        if "error" in service_data:
            return {"status": "error", "error": service_data["error"].get("message", "Service error")}

        layers = []
        for layer in service_data.get("layers", []):
            layers.append({
                "id": layer.get("id"),
                "name": layer.get("name"),
                "geometry_type": layer.get("geometryType", ""),
                "description": layer.get("description", "")[:200] if layer.get("description") else "",
                "min_scale": layer.get("minScale", 0),
                "max_scale": layer.get("maxScale", 0),
            })

        tables = []
        for table in service_data.get("tables", []):
            tables.append({
                "id": table.get("id"),
                "name": table.get("name"),
            })

        return {
            "status": "ok",
            "item_id": item_id,
            "service_title": details.get("title"),
            "service_url": service_url,
            "service_type": details.get("type"),
            "layer_count": len(layers),
            "table_count": len(tables),
            "layers": layers,
            "tables": tables,
        }

    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def describe_layer(
    service_url: str,
    layer_id: int = 0,
) -> dict[str, Any]:
    """Get detailed metadata for a specific layer.

    Returns field schemas (names, types, domains, constraints), geometry type,
    extent, editing capabilities, relationships, subtypes, and renderer info.
    Use this after list_layers to understand a layer's full schema before
    querying or editing features.

    Args:
        service_url: Feature service or map service URL
            (e.g. "https://host/arcgis/rest/services/MyService/FeatureServer").
            Can also be taken from the list_layers result.
        layer_id: Layer ID within the service (default 0).
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    svc_err = _check_service_url(service_url)
    if svc_err:
        return {"status": "error", "error": svc_err}

    result = client.describe_layer(service_url, layer_id)
    if "error" in result:
        return {"status": "error", "error": result["error"]}
    return {"status": "ok", **result}


@mcp.tool()
def get_gp_task_info(
    gp_service_url: str,
) -> dict[str, Any]:
    """Get metadata for a geoprocessing service or specific task.

    When given a GPServer root URL, lists all available tasks.
    When given a specific task URL, returns the full parameter schema
    including data types, directions, default values, and whether
    parameters are required. Use this before execute_gp_task or
    submit_gp_job to understand what parameters to pass.

    Args:
        gp_service_url: The GP service REST endpoint. Can be:
            - GPServer root: 'https://host/arcgis/rest/services/MyGP/GPServer'
              (lists all tasks)
            - Specific task: 'https://host/arcgis/rest/services/MyGP/GPServer/MyTask'
              (shows parameter schema)
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    result = client.get_gp_task_info(gp_service_url)
    if "error" in result:
        return {"status": "error", "error": result["error"]}
    return {"status": "ok", **result}


@mcp.tool()
def query_features(
    item_id: str,
    layer_id: int = 0,
    where: str = "1=1",
    out_fields: str = "*",
    bbox: str | None = None,
    limit: int = 20,
    offset: int = 0,
    order_by: str | None = None,
) -> dict[str, Any]:
    """Query features from a hosted feature layer.

    Supports attribute filtering (WHERE clause), spatial filtering (bounding box),
    field selection, pagination, and ordering. Results include geometry as GeoJSON.

    Args:
        item_id: The feature service item ID
        layer_id: Layer ID within the service (default 0, the first layer)
        where: SQL WHERE clause for attribute filtering (e.g. "STATUS = 'Active'")
               Use "1=1" for no filter (default).
        out_fields: Comma-separated field names to return (default "*" = all fields).
                    Use specific field names to reduce response size.
        bbox: Bounding box filter as "min_x,min_y,max_x,max_y" in the layer's CRS
        limit: Maximum features to return (default 20, max 2000)
        offset: Offset for pagination (default 0)
        order_by: Field name to sort by (optional)
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    where_err = _validate_where_clause(where)
    if where_err:
        return {"status": "error", "error": where_err}

    try:
        details = client.get_item_details(item_id)
        if not details or "error" in details:
            return {"status": "error", "error": "Item not found or inaccessible"}

        service_url = details.get("url", "")
        if not service_url:
            return {"status": "error", "error": "Item has no service URL"}

        # Build query URL
        query_url = f"{service_url}/{layer_id}/query"

        params: dict[str, Any] = {
            "f": "json",
            "where": where,
            "outFields": out_fields,
            "returnGeometry": "true",
            "outSR": "4326",  # Return geometry in WGS84 for portability
            "resultOffset": offset,
            "resultRecordCount": min(limit, 2000),
        }

        if client.token:
            params["token"] = client.token

        if bbox:
            # Format: xmin,ymin,xmax,ymax, also set spatial relationship
            parts = [float(x.strip()) for x in bbox.split(",")]
            if len(parts) == 4:
                params["geometry"] = ",".join(str(p) for p in parts)
                params["geometryType"] = "esriGeometryEnvelope"
                params["spatialRel"] = "esriSpatialRelIntersects"
                params["inSR"] = "4326"

        if order_by:
            params["orderByFields"] = order_by

        # Execute query
        resp = client._session.get(query_url, params=params, timeout=60)
        result = resp.json()

        if "error" in result:
            return {"status": "error", "error": result["error"].get("message", "Query failed")}

        features = result.get("features", [])
        exceeded = result.get("exceededTransferLimit", False)

        # Simplify output for LLM consumption
        simplified = []
        for f in features:
            attrs = f.get("attributes", {})
            geom = f.get("geometry", {})
            simplified.append({
                "attributes": attrs,
                "geometry": geom,
            })

        return {
            "status": "ok",
            "item_id": item_id,
            "layer_id": layer_id,
            "count": len(simplified),
            "exceeded_transfer_limit": exceeded,
            "features": simplified,
        }

    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def list_users(max_users: int = 100) -> dict[str, Any]:
    """List all users in the connected ArcGIS Portal.

    Shows username, full name, email, role, status, and last login.
    Useful for auditing portal usage and managing user access.

    Args:
        max_users: Maximum users to return (default 100, max 1000)
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    try:
        users = client.list_users(max_users=min(max_users, 1000))

        # Compute summary
        role_counts: dict[str, int] = {}
        for u in users:
            role = u.get("role", "unknown")
            role_counts[role] = role_counts.get(role, 0) + 1

        return {
            "status": "ok",
            "count": len(users),
            "by_role": role_counts,
            "active": sum(1 for u in users if not u.get("disabled")),
            "disabled": sum(1 for u in users if u.get("disabled")),
            "users": users,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def list_groups(max_groups: int = 100) -> dict[str, Any]:
    """List all groups in the connected ArcGIS Portal.

    Shows group title, owner, access level, and member count.
    Useful for understanding organizational content sharing structure.

    Args:
        max_groups: Maximum groups to return (default 100, max 1000)
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    try:
        groups = client.list_groups(max_groups=min(max_groups, 1000))

        # Compute summary
        access_counts: dict[str, int] = {}
        for g in groups:
            access = g.get("access", "unknown")
            access_counts[access] = access_counts.get(access, 0) + 1

        return {
            "status": "ok",
            "count": len(groups),
            "by_access": access_counts,
            "groups": groups,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def portal_health() -> dict[str, Any]:
    """Check the health and status of the connected ArcGIS Portal.

    Returns portal name, version, organization ID, and health check results.
    Requires admin privileges for full health check, returns basic info
    if not admin.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    try:
        # Always try to get portal info (works for all auth levels)
        portal_info = client.get_portal_info()

        # Try admin health check (requires admin)
        health = client.health_check()

        result: dict[str, Any] = {
            "status": "ok",
            "portal_name": portal_info.get("name", "N/A") if portal_info else "N/A",
            "portal_version": portal_info.get("portalVersion", "N/A") if portal_info else "N/A",
            "org_id": portal_info.get("id", "N/A") if portal_info else "N/A",
            "org_name": portal_info.get("organizationName", "N/A") if portal_info else "N/A",
            "user_license_type": portal_info.get("userLicenseType", "N/A") if portal_info else "N/A",
        }

        if health and "error" not in health:
            result["health_check"] = health
            result["health_status"] = "healthy"
        elif "error" in health:
            result["health_check"] = "unavailable (admin privileges may be required)"
            result["health_status"] = "unknown"

        return result
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def server_status() -> dict[str, Any]:
    """Check MCP server status: connection state, version, active portal."""
    client = _get_client()
    return {
        "status": "ok",
        "version": __version__,
        "connected": client.is_connected,
        "portal_url": client.portal_url,
        "username": client.username,
    }


# =========================================================================
# v1.4.0: Content Management (clone, move, service health)
# =========================================================================


@mcp.tool()
def clone_item(
    item_id: str,
    new_title: str = "",
    new_owner: str = "",
    folder: str = "",
) -> dict[str, Any]:
    """Clone an item within the same portal.

    Creates a copy of the item with its data (web maps, apps, etc.).
    The new item gets a new ID but preserves the original's type, tags,
    description, and access level.

    Args:
        item_id: The ID of the item to clone.
        new_title: Title for the clone. Defaults to original title + " (Copy)".
        new_owner: Owner of the clone. Defaults to connected user.
        folder: Folder to place the clone in. Defaults to root.

    Returns:
        Created item info including the new item ID.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    result = client.clone_item(
        item_id=item_id,
        new_title=new_title or None,
        new_owner=new_owner or None,
        folder=folder or None,
    )
    return {"status": "ok", "result": result}


@mcp.tool()
def move_items(
    item_ids: str,
    target_owner: str,
    source_owner: str = "",
) -> dict[str, Any]:
    """Move items from one user to another (reassign ownership).

    Bulk transfer content ownership between portal users. Useful when
    users leave the organization or when consolidating content.

    Args:
        item_ids: Comma-separated item IDs to move.
        target_owner: Username to transfer items to.
        source_owner: Current owner. Defaults to connected user.

    Returns:
        Per-item results with succeeded/failed counts.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    id_list = [iid.strip() for iid in item_ids.split(",") if iid.strip()]
    if not id_list:
        return {"status": "error", "error": "No item IDs provided."}

    result = client.move_items(
        item_ids=id_list,
        target_owner=target_owner,
        source_owner=source_owner or None,
    )
    return {"status": "ok", "result": result}


@mcp.tool()
def check_service_health(
    service_url: str,
    timeout: int = 10,
) -> dict[str, Any]:
    """Check the health of a GIS service endpoint.

    Pings a FeatureServer, MapServer, or ImageServer and returns
    status, response latency, version, and capabilities.

    Args:
        service_url: The service URL to check.
        timeout: Request timeout in seconds (default 10).

    Returns:
        Health status, latency, HTTP status code, and service metadata.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    svc_err = _check_service_url(service_url)
    if svc_err:
        return {"status": "error", "error": svc_err}

    result = client.check_service_health(service_url, timeout=timeout)
    return {"status": "ok", "result": result}


# =========================================================================
# Phase 2, Feature CRUD
# =========================================================================


@mcp.tool()
def add_features(
    service_url: str,
    layer_id: int,
    features: str,
) -> dict[str, Any]:
    """Add new features to a hosted feature layer.

    Args:
        service_url: Feature service URL (e.g. "https://host/arcgis/rest/services/MyService/FeatureServer")
        layer_id: Layer ID within the feature service (e.g. 0)
        features: JSON array of features to add. Each feature is a dict with
                  "attributes" (required) and "geometry" (optional).
                  Example: [{"attributes": {"NAME": "Building A", "STATUS": "Active"},
                             "geometry": {"x": 35.5, "y": 33.9, "spatialReference": {"wkid": 4326}}}]

    Returns:
        addResults array with success/failure per feature.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    svc_err = _check_service_url(service_url)
    if svc_err:
        return {"status": "error", "error": svc_err}

    import json as _json
    try:
        feat_list = _json.loads(features) if isinstance(features, str) else features
    except _json.JSONDecodeError as e:
        return {"status": "error", "error": f"Invalid JSON in features: {e}"}

    result = client.add_features(service_url, layer_id, feat_list)
    return {"status": "ok", "result": result}


@mcp.tool()
def update_features(
    service_url: str,
    layer_id: int,
    features: str,
) -> dict[str, Any]:
    """Update existing features in a hosted feature layer.

    Args:
        service_url: Feature service URL
        layer_id: Layer ID
        features: JSON array of features to update. Each must include OBJECTID in attributes.
                  Example: [{"attributes": {"OBJECTID": 1, "NAME": "Updated Name"}}]

    Returns:
        updateResults array with success/failure per feature.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    svc_err = _check_service_url(service_url)
    if svc_err:
        return {"status": "error", "error": svc_err}

    import json as _json
    try:
        feat_list = _json.loads(features) if isinstance(features, str) else features
    except _json.JSONDecodeError as e:
        return {"status": "error", "error": f"Invalid JSON in features: {e}"}

    result = client.update_features(service_url, layer_id, feat_list)
    return {"status": "ok", "result": result}


@mcp.tool()
def delete_features(
    service_url: str,
    layer_id: int,
    object_ids: str = "",
    where_clause: str = "",
) -> dict[str, Any]:
    """Delete features from a hosted feature layer by OBJECTIDs or WHERE clause.

    Args:
        service_url: Feature service URL
        layer_id: Layer ID
        object_ids: Comma-separated OBJECTID values to delete (e.g. "1,2,3"). Provide either this or where_clause.
        where_clause: SQL WHERE clause to match features for deletion (e.g. "STATUS = 'Inactive'").

    Returns:
        deleteResults array.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    svc_err = _check_service_url(service_url)
    if svc_err:
        return {"status": "error", "error": svc_err}

    if not object_ids and not where_clause:
        return {"status": "error", "error": "Provide either object_ids or where_clause"}

    if where_clause:
        where_err = _validate_where_clause(where_clause)
        if where_err:
            return {"status": "error", "error": where_err}

    result = client.delete_features(
        service_url, layer_id,
        object_ids=object_ids or None,
        where_clause=where_clause or None,
    )
    return {"status": "ok", "result": result}


# =========================================================================
# Phase 2, User / Group Management
# =========================================================================


@mcp.tool()
def get_user_details(username: str) -> dict[str, Any]:
    """Get detailed information about a specific user.

    Args:
        username: The portal username to look up.

    Returns:
        User details: role, privileges, storage usage, last login, etc.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    result = client.get_user_details(username)
    return {"status": "ok", "user": result}


@mcp.tool()
def create_group(
    title: str,
    name: str = "",
    description: str = "",
    access: str = "private",
    is_invitation_only: bool = False,
) -> dict[str, Any]:
    """Create a new group on the portal.

    Args:
        title: Display name for the group.
        name: URL-friendly group name. Defaults to title if not set.
        description: Group description.
        access: Visibility, "private" (group members only), "org" (organization), or "public".
        is_invitation_only: If true, users must be invited to join.

    Returns:
        Group creation result with the new group ID.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    result = client.create_group(
        title=title,
        name=name or None,
        description=description,
        access=access,
        is_invitation_only=is_invitation_only,
    )
    return {"status": "ok", "result": result}


@mcp.tool()
def invite_to_group(
    group_id: str,
    users: str,
    role: str = "member",
    message: str = "",
) -> dict[str, Any]:
    """Invite users to a group.

    Args:
        group_id: The group ID to invite users to.
        users: Comma-separated usernames to invite (e.g. "jsmith,mgarcia").
        role: Role assigned to invited users, "member" (default) or "admin".
        message: Optional invitation message.

    Returns:
        Invitation results per user.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    grp_err = _check_group_ids(group_id)
    if grp_err:
        return {"status": "error", "error": grp_err}

    result = client.invite_to_group(
        group_id=group_id,
        users=users,
        role=role,
        message=message,
    )
    return {"status": "ok", "result": result}


# =========================================================================
# Phase 2, Content Management
# =========================================================================


@mcp.tool()
def update_item(
    item_id: str,
    title: str = "",
    description: str = "",
    snippet: str = "",
    tags: str = "",
    access: str = "",
) -> dict[str, Any]:
    """Update properties of an existing portal item.

    Only the fields you provide will be changed. Leave others empty to skip.

    Args:
        item_id: The item ID to update.
        title: New title.
        description: New description.
        snippet: New summary/snippet.
        tags: Comma-separated tags to set (replaces existing tags).
        access: New access level, "private", "org", or "public".

    Returns:
        Update result.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    owner_err = _check_item_owner(item_id)
    if owner_err:
        return {"status": "error", "error": owner_err}

    result = client.update_item(
        item_id=item_id,
        title=title or None,
        description=description or None,
        snippet=snippet or None,
        tags=tags or None,
        access=access or None,
    )
    return {"status": "ok", "result": result}


@mcp.tool()
def delete_item(item_id: str) -> dict[str, Any]:
    """Delete an item from the portal.

    Args:
        item_id: The item ID to delete. The item owner must match the connected user
                 or the user must have admin delete privileges.

    Returns:
        Delete result.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    owner_err = _check_item_owner(item_id)
    if owner_err:
        return {"status": "error", "error": owner_err}

    result = client.delete_item(item_id)
    return {"status": "ok", "result": result}


@mcp.tool()
def share_item(
    item_id: str,
    everyone: bool = False,
    org: bool = False,
    groups: str = "",
) -> dict[str, Any]:
    """Share or unshare an item with audiences.

    Args:
        item_id: The item ID to share.
        everyone: If true, share publicly with everyone.
        org: If true, share with the entire organization.
        groups: Comma-separated group IDs to share with (e.g. "abc123,def456").

    Returns:
        Sharing result including which groups the item was/wasn't shared with.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    if groups:
        grp_err = _check_group_ids(groups)
        if grp_err:
            return {"status": "error", "error": grp_err}

    result = client.share_item(
        item_id=item_id,
        everyone=everyone,
        org=org,
        groups=groups or None,
    )
    return {"status": "ok", "result": result}


@mcp.tool()
def get_item_data(item_id: str) -> dict[str, Any]:
    """Get the data/content of a portal item.

    Useful for reading web map definitions, web app configurations, feature
    collection data, and other item data payloads.

    Args:
        item_id: The item ID.

    Returns:
        The item data as JSON (e.g. web map definition with basemap, layers, etc.).
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    result = client.get_item_data(item_id)
    return {"status": "ok", "data": result}


# =========================================================================
# Phase 3, Service Publishing
# =========================================================================


@mcp.tool()
def upload_item(
    file_path: str,
    title: str,
    type: str,
    tags: str = "",
    description: str = "",
    snippet: str = "",
    access: str = "private",
    owner: str = "",
) -> dict[str, Any]:
    """Upload a local file to the portal as a new item.

    Supports CSV, Shapefile (zipped), GeoJSON, KML, File Geodatabase,
    Service Definition, and other GIS file formats. After uploading,
    use publish_from_item to publish it as a hosted feature service.

    Args:
        file_path: Local path to the file to upload.
        title: Item title.
        type: ArcGIS item type, 'CSV', 'Shapefile', 'GeoJSON', 'KML',
              'File Geodatabase', 'Service Definition', etc.
        tags: Comma-separated tags for searchability.
        description: Longer description.
        snippet: Short summary (max 250 chars).
        access: 'private', 'org', or 'public'.
        owner: Owner username. Defaults to connected user.

    Returns:
        Item info including the new item ID, use with publish_from_item.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    result = client.upload_file(
        file_path=file_path,
        title=title,
        type_=type,
        tags=tags,
        description=description,
        snippet=snippet,
        access=access,
        owner=owner or None,
    )
    return {"status": "ok", "result": result}


@mcp.tool()
def publish_from_item(
    item_id: str,
    service_type: str = "featureService",
    publish_parameters: str = "",
    owner: str = "",
) -> dict[str, Any]:
    """Publish an uploaded item as a hosted feature service.

    Use this after upload_item to publish a CSV, Shapefile, or other
    uploaded file as a live hosted feature layer.

    Args:
        item_id: The ID of the uploaded item (from upload_item result).
        service_type: 'featureService' (default) or 'mapService'.
        publish_parameters: Optional JSON string for advanced config.
            Example for CSV: '{"layerInfo": {"name": "Parcels", "fields": [...]}}'.
        owner: Owner username. Defaults to connected user.

    Returns:
        Publish result with the new service URL and item details.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    pub_params = None
    if publish_parameters:
        try:
            pub_params = json.loads(publish_parameters)
        except json.JSONDecodeError:
            return {"status": "error", "error": "publish_parameters must be valid JSON"}

    result = client.publish_from_item(
        item_id=item_id,
        service_type=service_type,
        publish_parameters=pub_params,
        owner=owner or None,
    )
    return {"status": "ok", "result": result}


@mcp.tool()
def create_service(
    name: str,
    service_type: str = "Feature Service",
    description: str = "",
    snippet: str = "",
    tags: str = "",
    access: str = "private",
    is_view: bool = False,
    create_parameters: str = "",
    owner: str = "",
) -> dict[str, Any]:
    """Create an empty hosted feature service.

    Creates a new hosted feature service on the portal. Use this when
    you need to set up a new feature layer with a specific schema before
    adding features.

    Args:
        name: Service name.
        service_type: 'Feature Service' or 'Map Service'.
        description: Service description.
        snippet: Short summary.
        tags: Comma-separated tags.
        access: 'private', 'org', or 'public'.
        is_view: If true, creates a hosted feature layer view.
        create_parameters: Optional JSON string for schema definition.
            Example: '{"layers": [{"name": "Buildings", "fields":
            [{"name": "Name", "type": "esriFieldTypeString"}]}]}'
        owner: Owner username. Defaults to connected user.

    Returns:
        Created service info including the new service URL.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    create_params = None
    if create_parameters:
        try:
            create_params = json.loads(create_parameters)
        except json.JSONDecodeError:
            return {"status": "error", "error": "create_parameters must be valid JSON"}

    result = client.create_service(
        name=name,
        service_type=service_type,
        description=description,
        snippet=snippet,
        tags=tags,
        access=access,
        is_view=is_view,
        create_parameters=create_params,
        owner=owner or None,
    )
    return {"status": "ok", "result": result}


# =========================================================================
# Phase 3, Geoprocessing
# =========================================================================


@mcp.tool()
def execute_gp_task(
    gp_service_url: str,
    params: str = "{}",
) -> dict[str, Any]:
    """Execute a synchronous geoprocessing task.

    Submits parameters to a GP service and waits for the result.
    Use for tasks that complete quickly (under ~5 minutes).

    Args:
        gp_service_url: The GP task REST endpoint, e.g.
            'https://server/arcgis/rest/services/MyTool/GPServer/RunAnalysis'.
        params: JSON string of input parameters.
            Example: '{"input_feature": {"url": "..."}, "distance": "100"}'

    Returns:
        Results array, output parameters, and execution messages.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    try:
        gp_params = json.loads(params) if params else {}
    except json.JSONDecodeError:
        return {"status": "error", "error": "params must be valid JSON"}

    result = client.execute_gp_task(gp_service_url, gp_params)
    return {"status": "ok", "result": result}


@mcp.tool()
def submit_gp_job(
    gp_service_url: str,
    params: str = "{}",
) -> dict[str, Any]:
    """Submit an asynchronous geoprocessing job.

    For long-running tasks. Returns a job ID you can poll with
    get_gp_job_status until it completes.

    Args:
        gp_service_url: The GP task REST endpoint.
        params: JSON string of input parameters.

    Returns:
        Job ID and status URL for polling.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    try:
        gp_params = json.loads(params) if params else {}
    except json.JSONDecodeError:
        return {"status": "error", "error": "params must be valid JSON"}

    result = client.submit_gp_job(gp_service_url, gp_params)
    return {"status": "ok", "result": result}


@mcp.tool()
def get_gp_job_status(
    gp_service_url: str,
    job_id: str,
) -> dict[str, Any]:
    """Check the status of an asynchronous geoprocessing job.

    Use after submit_gp_job to poll until the job completes.
    Status values: esriJobSubmitted, esriJobWaiting,
    esriJobExecuting, esriJobSucceeded, esriJobFailed.

    Args:
        gp_service_url: The GP service REST endpoint (same as submit_gp_job).
        job_id: The job ID returned by submit_gp_job.

    Returns:
        Job status, messages, and results (when complete).
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    result = client.get_gp_job_status(gp_service_url, job_id)
    return {"status": "ok", "result": result}


# =========================================================================
# Phase 3, Portal Admin (Enterprise)
# =========================================================================


@mcp.tool()
def portal_system_info() -> dict[str, Any]:
    """Get detailed portal system and version information.

    Requires portal admin privileges. Returns the portal version,
    platform, license type, and system configuration.

    Returns:
        Portal system info: version, platform, license mode, etc.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    result = client.portal_system_info()
    return {"status": "ok", "result": result}


@mcp.tool()
def list_licenses() -> dict[str, Any]:
    """Get portal license information.

    Requires portal admin privileges. Returns license types,
    expiration dates, and assigned user counts.

    Returns:
        License details: types, assignments, expiration dates.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    result = client.list_licenses()
    return {"status": "ok", "result": result}


@mcp.tool()
def portal_usage(
    start_time: str = "",
    end_time: str = "",
    period: str = "1d",
    host_type: str = "portal",
) -> dict[str, Any]:
    """Get portal usage statistics.

    Requires portal admin privileges. Shows active users, API calls,
    storage usage, and service usage over time.

    Args:
        start_time: Start time as epoch milliseconds or ISO string.
                    Defaults to 30 days ago.
        end_time: End time as epoch milliseconds or ISO string.
                  Defaults to now.
        period: Aggregation period, '1d' (daily), '1w' (weekly),
                '1M' (monthly).
        host_type: 'portal' or 'server'.

    Returns:
        Usage metrics with timestamps and values.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    result = client.portal_usage(
        start_time=start_time or None,
        end_time=end_time or None,
        period=period,
        host_type=host_type,
    )
    return {"status": "ok", "result": result}


# =========================================================================
# Phase 3, Batch Operations
# =========================================================================


@mcp.tool()
def batch_delete_items(
    item_ids: str,
    owner: str = "",
) -> dict[str, Any]:
    """Delete multiple items at once.

    More efficient than calling delete_item repeatedly.
    Returns per-item success/failure results.

    Args:
        item_ids: Comma-separated item IDs to delete.
        owner: Owner username. Defaults to connected user.

    Returns:
        Per-item results with succeeded and failed counts.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    ids = [i.strip() for i in item_ids.split(",") if i.strip()]
    if not ids:
        return {"status": "error", "error": "No item IDs provided"}

    # Owner allowlist: filter out items owned by disallowed users
    lists = _load_allowlists()
    skipped: list[dict[str, str]] = []
    if lists["owners"] is not None:
        allowed = lists["owners"]
        filtered = []
        for iid in ids:
            details = client.get_item_details(iid)
            owner = (details or {}).get("owner", "")
            if owner in allowed:
                filtered.append(iid)
            else:
                skipped.append({"item_id": iid, "owner": owner, "error": f"Owner '{owner}' not on allowlist"})
        if not filtered:
            return {"status": "error", "error": "No items passed owner allowlist", "skipped": skipped}
        ids = filtered

    result = client.batch_delete_items(ids, owner=owner or None)
    out: dict[str, Any] = {"status": "ok", "result": result}
    if skipped:
        out["skipped"] = skipped
    return out


@mcp.tool()
def batch_share_items(
    item_ids: str,
    everyone: bool = False,
    org: bool = False,
    groups: str = "",
    owner: str = "",
) -> dict[str, Any]:
    """Share or unshare multiple items at once.

    Applies the same sharing settings to all specified items.
    More efficient than calling share_item repeatedly.

    Args:
        item_ids: Comma-separated item IDs to share.
        everyone: If true, share publicly.
        org: If true, share with the organization.
        groups: Comma-separated group IDs.
        owner: Owner username. Defaults to connected user.

    Returns:
        Per-item results with succeeded and failed counts.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    ids = [i.strip() for i in item_ids.split(",") if i.strip()]
    if not ids:
        return {"status": "error", "error": "No item IDs provided"}

    if groups:
        grp_err = _check_group_ids(groups)
        if grp_err:
            return {"status": "error", "error": grp_err}

    result = client.batch_share_items(
        ids, owner=owner or None, everyone=everyone, org=org, groups=groups or None,
    )
    return {"status": "ok", "result": result}


@mcp.tool()
def batch_update_items(
    item_ids: str,
    title: str = "",
    description: str = "",
    snippet: str = "",
    tags: str = "",
    access: str = "",
    owner: str = "",
) -> dict[str, Any]:
    """Update properties of multiple items at once.

    Applies the same property changes to all specified items.
    Only non-empty parameters are applied.

    Args:
        item_ids: Comma-separated item IDs to update.
        title: New title for all items.
        description: New description.
        snippet: New summary.
        tags: New comma-separated tags (replaces existing).
        access: New access level, 'private', 'org', or 'public'.
        owner: Owner username. Defaults to connected user.

    Returns:
        Per-item results with succeeded and failed counts.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    ids = [i.strip() for i in item_ids.split(",") if i.strip()]
    if not ids:
        return {"status": "error", "error": "No item IDs provided"}

    # Owner allowlist: filter out items owned by disallowed users
    lists = _load_allowlists()
    skipped: list[dict[str, str]] = []
    if lists["owners"] is not None:
        allowed = lists["owners"]
        filtered = []
        for iid in ids:
            details = client.get_item_details(iid)
            owner = (details or {}).get("owner", "")
            if owner in allowed:
                filtered.append(iid)
            else:
                skipped.append({"item_id": iid, "owner": owner, "error": f"Owner '{owner}' not on allowlist"})
        if not filtered:
            return {"status": "error", "error": "No items passed owner allowlist", "skipped": skipped}
        ids = filtered

    result = client.batch_update_items(
        ids, owner=owner or None,
        title=title or None, description=description or None,
        snippet=snippet or None, tags=tags or None, access=access or None,
    )
    out: dict[str, Any] = {"status": "ok", "result": result}
    if skipped:
        out["skipped"] = skipped
    return out


# =========================================================================
# Map Export
# =========================================================================


@mcp.tool()
def export_map_image(
    service_url: str,
    bbox: str = "",
    width: int = 800,
    height: int = 600,
    image_sr: str = "4326",
    format: str = "png",
    dpi: int = 96,
    transparent: bool = False,
    layers: str = "",
    where: str = "",
) -> dict[str, Any]:
    """Export a map image from a MapServer or FeatureServer as JPG/PNG/GIF/PDF/SVG.

    Generates a rendered image of a map service layer. Useful for
    visualizing spatial data, creating thumbnails, or generating
    map snapshots.

    Args:
        service_url: Full URL to MapServer or FeatureServer endpoint
            (e.g. https://services.arcgis.com/.../FeatureServer or MapServer)
        bbox: Bounding box as 'xmin,ymin,xmax,ymax' in WGS84.
            Leave empty to use the service default extent.
        width: Image width in pixels (default 800, max 4096).
        height: Image height in pixels (default 600, max 4096).
        image_sr: Output spatial reference (default '4326' = WGS84).
        format: Image format - png, jpg, gif, pdf, svg (default 'png').
        dpi: Image resolution (default 96).
        transparent: If true, background is transparent.
        layers: Layer visibility, e.g. 'show:0,1' or 'hide:2'.
        where: SQL where clause to filter features.

    Returns:
        Dict with file_path, dimensions, format, extent, and service info.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    svc_err = _check_service_url(service_url)
    if svc_err:
        return {"status": "error", "error": svc_err}

    if where:
        where_err = _validate_where_clause(where)
        if where_err:
            return {"status": "error", "error": where_err}

    result = client.export_map_image(
        service_url=service_url,
        bbox=bbox or None,
        width=width,
        height=height,
        image_sr=image_sr,
        format=format,
        dpi=dpi,
        transparent=transparent,
        layers=layers or None,
        where=where or None,
    )

    if "error" in result:
        return {"status": "error", "error": result["error"]}

    return {
        "status": "ok",
        "file_path": result["file_path"],
        "width": result["width"],
        "height": result["height"],
        "format": result["format"],
        "extent": result.get("extent"),
        "service_url": result["service_url"],
    }


# =========================================================================
# Resources
# =========================================================================


# =========================================================================
# v1.5.0: Relationship & Dependency Analysis Tools
# =========================================================================


@mcp.tool()
def explore_item_relationships(item_id: str) -> dict[str, Any]:
    """Explore all relationships for a portal item.

    Walks both forward and reverse relationships to build a complete
    picture of how items are connected in the portal.

    Args:
        item_id: The item ID to explore relationships for.

    Returns:
        Dict with item info and relationship list.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    try:
        result = client.explore_item_relationships(item_id)
        if "error" in result:
            return {"status": "error", "error": result["error"]}
        return {"status": "ok", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def audit_group_members(group_id: str) -> dict[str, Any]:
    """Audit all members of a portal group with roles and details.

    Paginates through all members and returns username, name, email,
    role, last login, and disabled status for each.

    Args:
        group_id: The group ID to audit.

    Returns:
        Dict with group info and complete member listing.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    try:
        result = client.audit_group_members(group_id)
        if "error" in result:
            return {"status": "error", "error": result["error"]}
        return {"status": "ok", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def scan_service_dependencies(service_item_id: str) -> dict[str, Any]:
    """Scan for all portal items that depend on a given service.

    Searches for web maps, apps, and dashboards that reference the
    service URL, and checks explicit item relationships.

    Args:
        service_item_id: The item ID of the service to scan.

    Returns:
        Dict listing all dependent items.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    try:
        result = client.scan_service_dependencies(service_item_id)
        if "error" in result:
            return {"status": "error", "error": result["error"]}
        return {"status": "ok", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def analyze_item_impact(item_id: str) -> dict[str, Any]:
    """Analyze the impact of deleting or modifying a portal item.

    Combines relationship exploration, dependency scanning, and sharing
    analysis to produce a blast radius assessment with actionable
    recommendation.

    Args:
        item_id: The item ID to analyze for impact.

    Returns:
        Dict with impact assessment including blast radius and recommendation.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    try:
        result = client.analyze_item_impact(item_id)
        if "error" in result:
            return {"status": "error", "error": result["error"]}
        return {"status": "ok", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def get_usage_analytics(
    start_time: str = "",
    end_time: str = "",
) -> dict[str, Any]:
    """Get enhanced portal usage analytics.

    Extends basic portal usage with per-user activity ranking and
    content type distribution breakdown.

    Args:
        start_time: Epoch milliseconds or ISO string (default: 30 days ago).
        end_time: Epoch milliseconds or ISO string (default: now).

    Returns:
        Dict with portal stats, user activity, and content breakdown.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    try:
        result = client.get_usage_analytics(
            start_time=start_time or None,
            end_time=end_time or None,
        )
        if "error" in result:
            return {"status": "error", "error": result["error"]}
        return {"status": "ok", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# =========================================================================
# v1.7.0: Webhooks, Logs, Org Settings, Folders
# =========================================================================


@mcp.tool()
def list_webhooks() -> dict[str, Any]:
    """List all organization webhooks.

    Shows webhook name, URL, active status, and event triggers.
    Requires portal admin privileges.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    try:
        webhooks = client.list_webhooks()
        return {
            "status": "ok",
            "count": len(webhooks),
            "webhooks": webhooks,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def create_webhook(
    name: str,
    hook_url: str,
    change_types: str = "",
    payload_format: str = "json",
    secret: str = "",
    active: bool = True,
    tags: str = "",
) -> dict[str, Any]:
    """Create a new organization webhook.

    Requires portal admin privileges.

    Args:
        name: Webhook name.
        hook_url: URL to receive POST payloads.
        change_types: Comma-separated event triggers (e.g.
            "addItem,deleteItem"). Leave empty for all changes.
        payload_format: "json" or "form".
        secret: Shared secret for HMAC signature verification.
        active: Whether webhook is active on creation.
        tags: Comma-separated tags.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    try:
        ct = [t.strip() for t in change_types.split(",") if t.strip()] or None
        result = client.create_webhook(
            name=name,
            hook_url=hook_url,
            change_types=ct,
            payload_format=payload_format,
            secret=secret,
            active=active,
            tags=tags,
        )
        if "error" in result:
            return {"status": "error", "error": result["error"]}
        return {"status": "ok", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def update_webhook(
    webhook_id: int,
    name: str = "",
    hook_url: str = "",
    change_types: str = "",
    payload_format: str = "",
    active: bool | None = None,
    tags: str = "",
) -> dict[str, Any]:
    """Update an existing organization webhook.

    Only non-empty parameters are applied. Requires admin privileges.

    Args:
        webhook_id: ID of the webhook to update.
        name: New name.
        hook_url: New URL.
        change_types: New comma-separated event triggers.
        payload_format: New format ("json" or "form").
        active: Set True to enable, False to disable.
        tags: New comma-separated tags.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    try:
        ct = [t.strip() for t in change_types.split(",") if t.strip()] or None
        result = client.update_webhook(
            webhook_id=webhook_id,
            name=name,
            hook_url=hook_url,
            change_types=ct,
            payload_format=payload_format,
            active=active,
            tags=tags,
        )
        if "error" in result:
            return {"status": "error", "error": result["error"]}
        return {"status": "ok", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def delete_webhook(webhook_id: int) -> dict[str, Any]:
    """Delete a webhook by ID.

    Requires portal admin privileges.

    Args:
        webhook_id: ID of the webhook to delete.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    try:
        result = client.delete_webhook(webhook_id)
        if "error" in result:
            return {"status": "error", "error": result["error"]}
        return {"status": "ok", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def test_webhook(webhook_id: int) -> dict[str, Any]:
    """Send a test payload to a webhook.

    Sends a sample event to the webhook URL to verify connectivity.
    Requires admin privileges.

    Args:
        webhook_id: ID of the webhook to test.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    try:
        result = client.test_webhook(webhook_id)
        if "error" in result:
            return {"status": "error", "error": result["error"]}
        return {"status": "ok", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def query_logs(
    level: str = "WARNING",
    source: str = "",
    start_time: str = "",
    end_time: str = "",
    max_records: int = 100,
) -> dict[str, Any]:
    """Query portal logs.

    Requires portal admin privileges. Returns log entries filtered by
    level, source, and time range.

    Args:
        level: Minimum log level: DEBUG, INFO, WARNING, SEVERE, CRITICAL.
        source: Filter by source (e.g. "PORTAL", "SERVER").
        start_time: ISO 8601 or epoch ms. Default: last 24 hours.
        end_time: ISO 8601 or epoch ms. Default: now.
        max_records: Max entries to return (default 100, max 1000).
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    try:
        result = client.query_logs(
            level=level,
            source=source,
            start_time=start_time,
            end_time=end_time,
            max_records=min(max_records, 1000),
        )
        if "error" in result:
            return {"status": "error", "error": result["error"]}
        return {"status": "ok", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def clean_logs(start_time: str = "") -> dict[str, Any]:
    """Delete portal logs older than start_time.

    Requires portal admin privileges. Without start_time, deletes all logs.
    Use with caution.

    Args:
        start_time: ISO 8601 or epoch ms. Logs before this time are deleted.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    try:
        result = client.clean_logs(start_time=start_time)
        if "error" in result:
            return {"status": "error", "error": result["error"]}
        return {"status": "ok", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def get_org_settings() -> dict[str, Any]:
    """Get organization settings.

    Returns portal configuration: general settings, security policies,
    sharing defaults, identity store, and more.
    Requires admin privileges.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    try:
        result = client.get_org_settings()
        if "error" in result:
            return {"status": "error", "error": result["error"]}
        return {"status": "ok", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def update_org_settings(
    settings_json: str,
) -> dict[str, Any]:
    """Update organization settings.

    Requires portal admin privileges. Pass a JSON string with the
    settings to update. Only provided keys are changed.

    Args:
        settings_json: JSON string of settings to update. Must match
            the /portals/self/settings schema. Example:
            '{"allowAnonymousAccess": false}'
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    try:
        settings = json.loads(settings_json)
    except json.JSONDecodeError as e:
        return {"status": "error", "error": f"Invalid JSON: {e}"}

    try:
        result = client.update_org_settings(settings)
        if "error" in result:
            return {"status": "error", "error": result["error"]}
        return {"status": "ok", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def create_folder(title: str, owner: str = "") -> dict[str, Any]:
    """Create a content folder.

    Organizes portal items into folders for better management.

    Args:
        title: Folder name.
        owner: Folder owner username. Defaults to connected user.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    try:
        result = client.create_folder(title=title, owner=owner or None)
        if "error" in result:
            return {"status": "error", "error": result["error"]}
        return {"status": "ok", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def list_folders(owner: str = "") -> dict[str, Any]:
    """List content folders for a user.

    Shows folder structure for organizing portal items.

    Args:
        owner: Username to list folders for. Defaults to connected user.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    try:
        folders = client.list_folders(owner=owner or None)
        return {
            "status": "ok",
            "count": len(folders),
            "folders": folders,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


# =========================================================================
# v1.10.0: Group & Folder Lifecycle
# =========================================================================


@mcp.tool()
def delete_folder(folder_id: str, owner: str = "") -> dict[str, Any]:
    """Delete a content folder.

    WARNING: Deleting a folder also deletes all items it contains.

    Args:
        folder_id: The folder ID to delete.
        owner: Folder owner username. Defaults to connected user.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    try:
        result = client.delete_folder(folder_id=folder_id, owner=owner or None)
        if "error" in result:
            return {"status": "error", "error": result["error"]}
        return {"status": "ok", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def update_group(
    group_id: str,
    title: str = "",
    description: str = "",
    access: str = "",
    is_invitation_only: bool | None = None,
) -> dict[str, Any]:
    """Update properties of an existing group.

    Only non-empty parameters are applied. Use this to rename,
    change visibility, or toggle invitation-only mode.

    Args:
        group_id: The group ID to update.
        title: New display name for the group.
        description: New group description.
        access: New visibility: "private", "org", or "public".
        is_invitation_only: If true, users must be invited to join.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    grp_err = _check_group_ids(group_id)
    if grp_err:
        return {"status": "error", "error": grp_err}

    result = client.update_group(
        group_id=group_id,
        title=title or None,
        description=description or None,
        access=access or None,
        is_invitation_only=is_invitation_only,
    )
    if "error" in result:
        return {"status": "error", "error": result["error"]}
    return {"status": "ok", "result": result}


@mcp.tool()
def delete_group(group_id: str) -> dict[str, Any]:
    """Delete a group from the portal.

    Only the group owner or an org administrator can delete a group.

    Args:
        group_id: The ID of the group to delete.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    grp_err = _check_group_ids(group_id)
    if grp_err:
        return {"status": "error", "error": grp_err}

    result = client.delete_group(group_id=group_id)
    if "error" in result:
        return {"status": "error", "error": result["error"]}
    return {"status": "ok", "result": result}


@mcp.tool()
def remove_from_group(group_id: str, users: str) -> dict[str, Any]:
    """Remove users from a group.

    The group owner, group managers, and org administrators can
    remove members. The group owner cannot be removed.

    Args:
        group_id: The group ID to remove users from.
        users: Comma-separated usernames to remove (e.g. "jsmith,mgarcia").
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    grp_err = _check_group_ids(group_id)
    if grp_err:
        return {"status": "error", "error": grp_err}

    result = client.remove_from_group(group_id=group_id, users=users)
    if "error" in result:
        return {"status": "error", "error": result["error"]}
    return {"status": "ok", "result": result}


@mcp.tool()
def list_group_users(group_id: str, max_users: int = 100) -> dict[str, Any]:
    """List users in a specific group.

    Lighter than audit_group_members -- returns just the user list
    without group metadata.

    Args:
        group_id: The group ID to list users for.
        max_users: Maximum users to return (default 100).
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    grp_err = _check_group_ids(group_id)
    if grp_err:
        return {"status": "error", "error": grp_err}

    users = client.list_group_users(group_id=group_id, max_users=max_users)
    return {
        "status": "ok",
        "group_id": group_id,
        "count": len(users),
        "users": users,
    }


@mcp.tool()
def search_users(query: str, max_users: int = 100) -> dict[str, Any]:
    """Search portal users by name, email, or username.

    Returns matching users with their role and status.

    Args:
        query: Search string (matches name, email, username).
        max_users: Maximum results to return (default 100).
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    users = client.search_users(query=query, max_users=max_users)
    return {
        "status": "ok",
        "count": len(users),
        "users": users,
    }


@mcp.resource("arcgis://guide")
def arcgis_rest_guide() -> str:
    """Reference guide for ArcGIS REST API operations."""
    return """# ArcGIS REST API Quick Reference

## Authentication
- Token-based: Add `?token=TOKEN` to any request
- OAuth2 endpoints: `/sharing/rest/oauth2/token`, `/sharing/rest/oauth2/authorize`
- Tokens expire, check `expires_in` and reconnect when needed

## Sharing REST API Endpoints
- `/sharing/rest/search`, Search items (GET, params: q, start, num)
- `/sharing/rest/content/items/{id}`, Get item details
- `/sharing/rest/content/items/{id}/data`, Get item data (web map JSON, etc.)
- `/sharing/rest/content/users/{owner}/items/{id}/update`, Update item properties (POST)
- `/sharing/rest/content/users/{owner}/items/{id}/delete`, Delete item (POST)
- `/sharing/rest/content/users/{owner}/items/{id}/share`, Share/unshare item (POST)
- `/sharing/rest/portals/self`, Organization info
- `/sharing/rest/portals/self/users`, List users
- `/sharing/rest/portals/self/groups`, List groups
- `/sharing/rest/community/self`, Current user info
- `/sharing/rest/community/users/{username}`, User details
- `/sharing/rest/community/createGroup`, Create group (POST)
- `/sharing/rest/community/groups/{id}/invite`, Invite users to group (POST)

## Feature Service Operations
- `{service_url}/{layerId}/query`, Query features
  - where, outFields, returnGeometry, outSR, resultOffset, resultRecordCount
  - geometry, geometryType, spatialRel for spatial filtering
- `{service_url}/{layerId}/addFeatures`, Add features (POST, features=[…])
- `{service_url}/{layerId}/updateFeatures`, Update features (POST, features=[…])
- `{service_url}/{layerId}/deleteFeatures`, Delete features (POST, objectIds or where)

## Common Item Types
- Feature Service, Map Service, Image Service, Scene Service
- Web Map, Web Scene, Dashboard, Experience Builder
- Shapefile, GeoJSON, CSV, KML

## Common ArcGIS Online URLs
- ArcGIS Online: https://www.arcgis.com
- Content: https://www.arcgis.com/home/search.html
- REST Services: https://services.arcgis.com/

## Common Portal URL Patterns
- Enterprise Portal: https://{hostname}/{webadaptor}/home/
- Sharing API: https://{hostname}/{webadaptor}/sharing/rest/
- Admin API: https://{hostname}/{webadaptor}/portaladmin/

## Service Publishing
- `/content/users/{owner}/add`, Upload a file (POST, multipart)
- `/content/users/{owner}/publish`, Publish item as feature service (POST)
- `/content/users/{owner}/createService`, Create hosted feature service (POST)

## Layer Metadata (describe_layer)
- `{service_url}/{layerId}?f=json`, Get full layer schema (fields, types, domains, relationships, extent, renderer)
- Use this before query/add/update to understand the field names, data types, and editing capabilities

## Geoprocessing (get_gp_task_info)
- `{gp_service_url}?f=json`, GPServer root — lists all available tasks
- `{gp_service_url}/{taskName}?f=json`, Specific task — returns parameter schema (name, dataType, direction, defaultValue)
- `{gp_service_url}/execute`, Synchronous GP task (POST)
- `{gp_service_url}/submitJob`, Async GP job submission (POST)
- `{gp_service_url}/jobs/{jobId}`, Check async job status (GET)

## Portal Admin (Enterprise only)
- `/portaladmin/`, System info (GET)
- `/portaladmin/license`, License information (GET)
- `/portaladmin/portalusage`, Usage statistics (GET)
"""


# =========================================================================
# Entry point
# =========================================================================


def _install_guards() -> None:
    """Wrap tool dispatch with read-only, allowlist, and audit guards.

    Modifies ``mcp._tool_manager`` in place so that:
    * ``list_tools`` returns only allowed tools (when MCP_ALLOWED_TOOLS is set).
    * ``call_tool`` rejects disallowed tools, blocks writes in read-only mode,
      and logs every call to the audit file (when MCP_AUDIT_LOG is set).
    """
    from mcp.server.fastmcp.exceptions import ToolError

    original_call = mcp._tool_manager.call_tool
    original_list = mcp._tool_manager.list_tools

    # --- Filtered list_tools ---
    @functools.wraps(original_list)
    def _filtered_list():
        tools = original_list()
        if _TOOL_ALLOWLIST is not None:
            tools = [t for t in tools if t.name in _TOOL_ALLOWLIST]
        return tools

    mcp._tool_manager.list_tools = _filtered_list  # type: ignore[assignment]

    # --- Guarded call_tool ---
    async def _guarded_call(name, arguments, context=None, convert_result=False):
        # 1. Tool allowlist check
        if _TOOL_ALLOWLIST is not None and name not in _TOOL_ALLOWLIST:
            raise ToolError(
                f"Tool '{name}' is not on the allowlist. "
                f"Allowed: {', '.join(sorted(_TOOL_ALLOWLIST))}."
            )

        # 2. Read-only check
        if _READ_ONLY and name in _WRITE_TOOLS:
            raise ToolError(
                f"Read-only mode is active. Tool '{name}' modifies data and is blocked. "
                "Set MCP_READ_ONLY=false or remove --read-only to enable writes."
            )

        # 3. Execute with optional audit logging
        start = _time.monotonic() if _AUDIT_LOG_PATH else None
        try:
            result = await original_call(
                name, arguments, context=context, convert_result=convert_result,
            )
            if _AUDIT_LOG_PATH:
                _write_audit_entry(
                    name, arguments, "ok",
                    (_time.monotonic() - start) * 1000,
                )
            return result
        except Exception as exc:
            if _AUDIT_LOG_PATH:
                _write_audit_entry(
                    name, arguments, "error",
                    (_time.monotonic() - start) * 1000,
                    error=str(exc),
                )
            raise

    mcp._tool_manager.call_tool = _guarded_call  # type: ignore[assignment]


def main() -> None:
    """Run the MCP server via stdio transport."""
    global _READ_ONLY  # noqa: PLW0603

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    # CLI argument parsing
    parser = argparse.ArgumentParser(
        description="ArcGIS Portal MCP Server",
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        default=False,
        help="Block all write/mutating tools (overrides MCP_READ_ONLY env var)",
    )
    args, _ = parser.parse_known_args()

    if args.read_only:
        _READ_ONLY = True

    logger.info("Starting ArcGIS Portal MCP Server v%s", __version__)

    if _READ_ONLY:
        logger.info("Read-only mode ACTIVE — write tools are blocked")
    if _TOOL_ALLOWLIST:
        logger.info(
            "Tool allowlist ACTIVE — %d tools: %s",
            len(_TOOL_ALLOWLIST), ", ".join(sorted(_TOOL_ALLOWLIST)),
        )
    if _AUDIT_LOG_PATH:
        logger.info("Audit logging ACTIVE — %s", _AUDIT_LOG_PATH)

    # Auto-connect from .env if credentials are available
    connected, method = _auto_connect()
    if connected:
        logger.info("Ready, connected to portal via .env credentials (method: %s)", method)
    else:
        logger.info("Ready, waiting for connect_portal tool call")

    # Install security guards (read-only, allowlist, audit) on tool dispatch
    _install_guards()

    mcp.run()


if __name__ == "__main__":
    main()
@mcp.tool()
def list_collaborations() -> dict[str, Any]:
    """List all collaborations the portal participates in.

    Returns collaborations where the portal is host or guest.
    Requires admin privileges.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    try:
        collabs = client.list_collaborations()
        return {"status": "ok", "count": len(collabs), "collaborations": collabs}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def get_collaboration(collaboration_id: str) -> dict[str, Any]:
    """Get details of a specific collaboration.

    Args:
        collaboration_id: The collaboration ID.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    try:
        result = client.get_collaboration(collaboration_id)
        if "error" in result:
            return {"status": "error", "error": result["error"]}
        return {"status": "ok", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def sync_collaboration(collaboration_id: str, workspace_id: str) -> dict[str, Any]:
    """Trigger sync for a collaboration workspace.

    Syncs shared content between collaboration partners.
    Requires admin privileges.

    Args:
        collaboration_id: The collaboration ID.
        workspace_id: The workspace ID within the collaboration.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    try:
        result = client.sync_collaboration(collaboration_id, workspace_id)
        if "error" in result:
            return {"status": "error", "error": result["error"]}
        return {"status": "ok", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def list_roles(return_privileges: bool = True) -> dict[str, Any]:
    """List all organization roles.

    Returns built-in and custom roles with their privilege lists.
    Requires admin privileges.

    Args:
        return_privileges: If true, include each role's privileges (default true).
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    try:
        roles = client.list_roles(return_privileges=return_privileges)
        return {"status": "ok", "count": len(roles), "roles": roles}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def get_role_privileges(role_id: str) -> dict[str, Any]:
    """Get privileges for a specific role.

    Returns the privilege strings assigned to the role.
    Requires admin privileges.

    Args:
        role_id: The role ID (e.g. "iAAAAAAAAAAAAAAA").
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    try:
        result = client.get_role_privileges(role_id)
        if "error" in result:
            return {"status": "error", "error": result["error"]}
        return {"status": "ok", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def list_scheduled_tasks(
    task_type: str = "",
    user_filter: str = "",
    active: bool | None = None,
) -> dict[str, Any]:
    """List all scheduled tasks in the organization.

    Requires admin privileges. Supports filtering by type, user, and active state.

    Args:
        task_type: Filter by task type (e.g. "ExecuteNotebook", "ExecuteSceneCook").
        user_filter: Filter by owner username.
        active: Filter by active state (true/false). Omit for all.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    try:
        tasks = client.list_scheduled_tasks(
            task_type=task_type,
            user_filter=user_filter,
            active=active,
        )
        return {"status": "ok", "count": len(tasks), "tasks": tasks}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def get_user_scheduled_tasks(username: str) -> dict[str, Any]:
    """List scheduled tasks for a specific user.

    Args:
        username: The username to list tasks for.
    """
    client = _require_connected()
    if not client:
        return {"status": "error", "error": "Not connected. Call connect_portal first."}

    try:
        tasks = client.get_user_scheduled_tasks(username)
        return {"status": "ok", "count": len(tasks), "tasks": tasks}
    except Exception as e:
        return {"status": "error", "error": str(e)}
