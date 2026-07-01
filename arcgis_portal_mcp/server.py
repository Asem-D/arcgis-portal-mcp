"""ArcGIS Portal MCP Server.

An MCP server that gives AI assistants access to ArcGIS Portal and Online
via structured tools. Built on the Model Context Protocol for integration
with Claude Desktop, Cursor, VS Code Copilot, and other MCP clients.

Phase 1 (v0.1): Auth, content search, item details, layer queries, user/group listing.
Phase 2 (v0.2): Feature CRUD, user/group management, content management.
Phase 3 (v1.0): Service publishing, geoprocessing, portal admin, batch operations.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import __version__
from .client import ArcGISClient

logger = logging.getLogger("arcgis-portal-mcp")

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


def _load_env() -> dict[str, str]:
    """Load environment variables from .env file (next to this package).

    Returns a dict of KEY=VALUE pairs found in the .env file.
    Existing environment variables take precedence (not overwritten).
    """
    env_vars: dict[str, str] = {}

    # Look for .env in the package directory (next to server.py)
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        # Also check current working directory
        env_path = Path.cwd() / ".env"

    if env_path.exists():
        logger.info("Loading env from %s", env_path)
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip()
                    # Don't override existing env vars
                    if key not in os.environ:
                        os.environ[key] = value
                    env_vars[key] = os.environ[key]
    else:
        logger.debug("No .env file found")

    return env_vars


def _auto_connect() -> bool:
    """Auto-connect using credentials from .env file.

    Tries in order:
    1. username + password -> generateToken (user-level, full permissions)
    2. client_id + client_secret -> client_credentials (app-level, limited)

    Returns True if connection succeeded, False otherwise.
    """
    env = _load_env()

    portal_url = env.get("portal_url") or os.environ.get("PORTAL_URL")
    username = env.get("username") or os.environ.get("ARCGIS_USERNAME")
    password = env.get("password") or os.environ.get("ARCGIS_PASSWORD")
    client_id = env.get("oauth_client_id") or os.environ.get("OAUTH_CLIENT_ID")
    client_secret = env.get("oauth_client_secret") or os.environ.get("OAUTH_CLIENT_SECRET")

    if not portal_url:
        logger.info("Auto-connect skipped: no portal_url in .env")
        return False

    client = _get_client()

    # Try username/password first (user-level token)
    if username and password:
        try:
            result = client.connect_username_password(portal_url, username, password)
            logger.info(
                "Auto-connected to %s via generateToken (user: %s)",
                portal_url,
                result.get("username", "unknown"),
            )
            return True
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
            return True
        except Exception as e:
            logger.warning("client_credentials auth failed: %s", e)

    logger.info(
        "Auto-connect skipped: no usable credentials in .env "
        "(need username+password or oauth_client_id+oauth_client_secret)"
    )
    return False


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
        if auth_method == "auto":
            # Try auto-connect from .env
            if _auto_connect():
                # Determine which method was used
                env = _load_env()
                has_user = env.get("username") or os.environ.get("ARCGIS_USERNAME")
                method_used = "generateToken (auto)" if has_user else "client_credentials (auto)"
                return {
                    "status": "ok",
                    "username": client.username,
                    "portal_url": client.portal_url,
                    "auth_method": method_used,
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

    try:
        # Get item details for the service URL
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

    if not object_ids and not where_clause:
        return {"status": "error", "error": "Provide either object_ids or where_clause"}

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

    result = client.batch_delete_items(ids, owner=owner or None)
    return {"status": "ok", "result": result}


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

    result = client.batch_update_items(
        ids, owner=owner or None,
        title=title or None, description=description or None,
        snippet=snippet or None, tags=tags or None, access=access or None,
    )
    return {"status": "ok", "result": result}


# =========================================================================
# Resources
# =========================================================================


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

## Geoprocessing
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


def main() -> None:
    """Run the MCP server via stdio transport."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stderr,
    )
    logger.info("Starting ArcGIS Portal MCP Server v%s", __version__)

    # Auto-connect from .env if credentials are available
    if _auto_connect():
        logger.info("Ready, connected to portal via .env credentials")
    else:
        logger.info("Ready, waiting for connect_portal tool call")

    mcp.run()


if __name__ == "__main__":
    main()
