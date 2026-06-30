"""ArcGIS Portal MCP Server.

An MCP server that gives AI assistants access to ArcGIS Portal and Online
via structured tools. Built on the Model Context Protocol for integration
with Claude Desktop, Cursor, VS Code Copilot, and other MCP clients.

Phase 1: Auth, content search, item details, layer queries, user/group listing.
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
        "Search content, query feature layers, list users and groups, "
        "and check portal health. Supports both Enterprise Portal and "
        "ArcGIS Online. No dependency on the `arcgis` Python package — "
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

    Reads portal_url, oauth_client_id, oauth_client_secret from .env
    and connects via client_credentials grant.

    Returns True if connection succeeded, False otherwise.
    """
    env = _load_env()

    portal_url = env.get("portal_url") or os.environ.get("PORTAL_URL")
    client_id = env.get("oauth_client_id") or os.environ.get("OAUTH_CLIENT_ID")
    client_secret = env.get("oauth_client_secret") or os.environ.get("OAUTH_CLIENT_SECRET")

    if not all([portal_url, client_id, client_secret]):
        logger.info(
            "Auto-connect skipped: missing credentials in .env "
            "(need portal_url, oauth_client_id, oauth_client_secret)"
        )
        return False

    client = _get_client()
    try:
        result = client.connect_client_credentials(portal_url, client_id, client_secret)
        logger.info(
            "Auto-connected to %s via client_credentials (user: %s)",
            portal_url,
            result.get("username", "unknown"),
        )
        return True
    except Exception as e:
        logger.warning("Auto-connect failed: %s", e)
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
) -> dict[str, Any]:
    """Connect to an ArcGIS Portal or ArcGIS Online instance.

    Supports four authentication methods:
    - auto: Read credentials from .env file and use client_credentials (default)
    - token: Use an existing portal token (quickest for MCP)
    - client_credentials: OAuth2 app-level auth (no browser needed)
    - oauth2: Browser-based OAuth2 (full user permissions, blocks for ~2 min)

    For Enterprise portals with 2FA, use client_credentials or oauth2.
    The .env file should contain: portal_url, oauth_client_id, oauth_client_secret.

    Args:
        portal_url: Base URL of the portal (e.g. https://gis.example.com/portal).
                    Required for token/client_credentials/oauth2; optional for auto
                    (reads from .env).
        auth_method: "auto", "token", "client_credentials", or "oauth2"
        token: Existing portal token (required when auth_method="token")
        client_id: OAuth2 client ID (required for client_credentials/oauth2,
                   optional for auto — reads from .env)
        client_secret: OAuth2 client secret (required for client_credentials/oauth2,
                       optional for auto — reads from .env)
    """
    client = _get_client()

    try:
        if auth_method == "auto":
            # Try auto-connect from .env
            if _auto_connect():
                return {
                    "status": "ok",
                    "username": client.username,
                    "portal_url": client.portal_url,
                    "auth_method": "client_credentials (auto)",
                    "expires_in": client._token_expires - __import__("time").time() if client._token_expires else None,
                }
            else:
                return {
                    "status": "error",
                    "error": (
                        "Auto-connect failed. Ensure .env contains: "
                        "portal_url, oauth_client_id, oauth_client_secret. "
                        "Or specify auth_method='client_credentials' with explicit parameters."
                    ),
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
                "error": f"Unknown auth_method: {auth_method}. Use 'auto', 'token', 'client_credentials', or 'oauth2'.",
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
        layer_id: Layer ID within the service (default 0 — the first layer)
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
            # Format: xmin,ymin,xmax,ymax — also set spatial relationship
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
    Requires admin privileges for full health check — returns basic info
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
# Resources
# =========================================================================


@mcp.resource("arcgis://guide")
def arcgis_rest_guide() -> str:
    """Reference guide for ArcGIS REST API operations."""
    return """# ArcGIS REST API Quick Reference

## Authentication
- Token-based: Add `?token=TOKEN` to any request
- OAuth2 endpoints: `/sharing/rest/oauth2/token`, `/sharing/rest/oauth2/authorize`
- Tokens expire — check `expires_in` and reconnect when needed

## Sharing REST API Endpoints
- `/sharing/rest/search` — Search items (GET, params: q, start, num)
- `/sharing/rest/content/items/{id}` — Get item details
- `/sharing/rest/content/items/{id}/data` — Get item data (web map JSON, etc.)
- `/sharing/rest/portals/self` — Organization info
- `/sharing/rest/portals/self/users` — List users
- `/sharing/rest/portals/self/groups` — List groups
- `/sharing/rest/community/self` — Current user info

## Feature Service Query
- `{service_url}/{layerId}/query` — Query features
  - where: SQL WHERE clause (e.g. "1=1", "STATUS = 'Active'")
  - outFields: Comma-separated fields or "*"
  - returnGeometry: true/false
  - outSR: Spatial reference for output (4326 = WGS84)
  - resultOffset: Pagination offset
  - resultRecordCount: Max records (up to 2000 per request)
  - geometry: Bounding box or geometry for spatial filter
  - geometryType: esriGeometryEnvelope, esriGeometryPoint, etc.
  - spatialRel: esriSpatialRelIntersects, esriSpatialRelContains, etc.

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
        logger.info("Ready — connected to portal via .env credentials")
    else:
        logger.info("Ready — waiting for connect_portal tool call")

    mcp.run()


if __name__ == "__main__":
    main()
