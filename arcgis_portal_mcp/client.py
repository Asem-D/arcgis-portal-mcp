"""ArcGIS REST API client.

Raw REST API client for ArcGIS Portal/Online. No dependency on the
`arcgis` Python package, uses requests directly. Handles authentication,
token management, and both Sharing and Admin API endpoints.

Supports:
- Token-based auth (existing portal token)
- Client credentials grant (OAuth2 app-level)
- Authorization code grant (browser-based, user-level)
- Automatic token refresh
- Self-signed certificate handling
"""

from __future__ import annotations

import json
import logging
import os
import urllib.parse
import webbrowser
from datetime import datetime
from html import escape as html_escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Suppress InsecureRequestWarning for self-signed certs (common with Enterprise)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger("arcgis-portal-mcp")


class ArcGISClient:
    """REST API client for ArcGIS Portal and Online.

    Manages authentication and provides methods for both the Sharing REST API
    and the Portal Admin API. Works with both ArcGIS Enterprise Portal and
    ArcGIS Online.
    """

    def __init__(self) -> None:
        self.portal_url: str | None = None
        self.sharing_url: str | None = None
        self._token: str | None = None
        self._token_expires: float | None = None
        self._username: str | None = None
        self._user_info: dict[str, Any] | None = None
        self._session = requests.Session()
        self._session.verify = False  # noqa: S501, Enterprise portals often use self-signed certs
        self._session.timeout = 30

        # Retry with exponential backoff for transient failures
        retry_strategy = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
            connect=1,  # Only 1 retry on connection errors
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    @property
    def is_connected(self) -> bool:
        """Check if we have a valid token."""
        if not self._token or not self._token_expires:
            return False
        return datetime.now().timestamp() < (self._token_expires - 60)

    @property
    def username(self) -> str | None:
        return self._username

    @property
    def token(self) -> str | None:
        if self.is_connected:
            return self._token
        return None

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def connect_token(self, portal_url: str, token: str) -> dict[str, Any]:
        """Connect using an existing portal token.

        Args:
            portal_url: Base URL of the Portal (e.g. https://gis.example.com/portal)
            token: An existing valid token

        Returns:
            User info dict on success, raises on failure.
        """
        self._set_portal_url(portal_url)

        # Validate the token by getting user info
        user_info = self._sharing_request("/community/self", token=token)
        if not user_info or "error" in user_info:
            error_msg = user_info.get("error", "unknown") if user_info else "no response"
            raise ConnectionError(f"Token validation failed: {error_msg}")

        self._token = token
        self._username = user_info.get("username", "unknown")
        self._user_info = user_info
        # Tokens from sharing API don't always include expires, assume long-lived
        self._token_expires = datetime.now().timestamp() + 86400  # 24h fallback

        logger.info("Connected as %s (existing token)", self._username)
        return user_info

    def connect_client_credentials(
        self, portal_url: str, client_id: str, client_secret: str
    ) -> dict[str, Any]:
        """Connect using OAuth2 client_credentials grant (no browser needed).

        App-level token, no user identity. Good for portal info, content
        search, and other non-user-specific operations.

        Args:
            portal_url: Base URL of the Portal
            client_id: OAuth2 app client ID
            client_secret: OAuth2 app client secret

        Returns:
            Dict with token info.
        """
        self._set_portal_url(portal_url)
        token_url = f"{self.sharing_url}/oauth2/token"

        data = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "f": "json",
        }

        resp = self._session.post(token_url, data=data, timeout=30)
        result = resp.json()

        if "error" in result:
            error_msg = result.get("error_description", result["error"])
            raise ConnectionError(f"Client credentials auth failed: {error_msg}")

        token = result["access_token"]
        expires_in = result.get("expires_in", 7200)
        expires_at = datetime.now().timestamp() + expires_in

        self._token = token
        self._token_expires = expires_at
        self._username = "(app-level)"
        self._user_info = {"username": "(app-level)", "client_credentials": True}

        logger.info("Connected via client_credentials (expires in %ds)", expires_in)
        return {
            "token": token,
            "username": "(app-level)",
            "expires_in": expires_in,
            "grant_type": "client_credentials",
        }

    def connect_oauth2(
        self,
        portal_url: str,
        client_id: str,
        client_secret: str,
        redirect_port: int = 9090,
    ) -> dict[str, Any]:
        """Connect via OAuth2 authorization_code flow (browser-based).

        Opens browser for user login, captures the callback, exchanges
        for token. Returns user-level token with full permissions.

        WARNING: This blocks for up to 120 seconds waiting for browser auth.
        Not suitable for MCP tool calls, use for initial setup only.

        Args:
            portal_url: Base URL of the Portal
            client_id: OAuth2 app client ID
            client_secret: OAuth2 app client secret
            redirect_port: Local port for OAuth callback (default 9090)

        Returns:
            Dict with token + user_info.
        """
        self._set_portal_url(portal_url)
        redirect_uri = f"http://localhost:{redirect_port}/callback"

        # Build authorization URL
        auth_params = urllib.parse.urlencode({
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
        })
        auth_url = f"{self.portal_url.rstrip('/')}/sharing/rest/oauth2/authorize?{auth_params}"

        logger.info("Opening browser for OAuth2 login...")
        logger.info("If browser doesn't open, visit: %s", auth_url)

        # Start local HTTP server to capture callback
        server = HTTPServer(("localhost", redirect_port), _OAuthCallbackHandler)
        server.auth_code = None  # type: ignore[attr-defined]
        server.auth_error = None  # type: ignore[attr-defined]
        server.timeout = 120

        webbrowser.open(auth_url)

        logger.info("Waiting for authentication in browser (120s timeout)...")
        while server.auth_code is None and server.auth_error is None:  # type: ignore[attr-defined]
            server.handle_request()

        server.server_close()

        if server.auth_error:  # type: ignore[attr-defined]
            raise ConnectionError(f"OAuth2 error: {server.auth_error}")  # type: ignore[attr-defined]

        if not server.auth_code:  # type: ignore[attr-defined]
            raise ConnectionError("No authorization code received (timeout)")

        # Exchange code for token
        token_url = f"{self.sharing_url}/oauth2/token"
        data = {
            "grant_type": "authorization_code",
            "code": server.auth_code,  # type: ignore[attr-defined]
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "f": "json",
        }

        resp = self._session.post(token_url, data=data, timeout=30)
        result = resp.json()

        if "error" in result:
            error_msg = result.get("error_description", result["error"])
            raise ConnectionError(f"Token exchange failed: {error_msg}")

        token = result["access_token"]
        expires_in = result.get("expires_in", 1209600)  # default 14 days
        expires_at = datetime.now().timestamp() + expires_in

        user_info = self._sharing_request("/community/self", token=token)
        username = user_info.get("username", "unknown") if user_info else "unknown"

        self._token = token
        self._token_expires = expires_at
        self._username = username
        self._user_info = user_info

        logger.info("Connected as %s (OAuth2)", username)
        return {
            "token": token,
            "username": username,
            "expires_in": expires_in,
            "user_info": user_info,
            "grant_type": "authorization_code",
        }

    def connect_username_password(
        self, portal_url: str, username: str, password: str
    ) -> dict[str, Any]:
        """Connect using username + password via generateToken.

        Gets a user-level token with full permissions. Token expires in
        2 hours by default (matching Enterprise Portal default).

        Args:
            portal_url: Base URL of the Portal
            username: ArcGIS Portal username
            password: ArcGIS Portal password

        Returns:
            Dict with token info and user details.
        """
        self._set_portal_url(portal_url)
        token_url = f"{self.sharing_url}/generateToken"

        data = {
            "username": username,
            "password": password,
            "expiration": 120,  # minutes (2 hours)
            "referer": portal_url,
            "f": "json",
        }

        resp = self._session.post(token_url, data=data, timeout=30)
        result = resp.json()

        if "error" in result:
            error_msg = result.get("error", {}).get("description", str(result["error"]))
            raise ConnectionError(f"generateToken failed: {error_msg}")

        token = result["token"]
        expires_in = result.get("expires", 7200)  # seconds
        expires_at = datetime.now().timestamp() + expires_in

        self._token = token
        self._token_expires = expires_at
        self._username = username
        self._user_info = {"username": username}

        logger.info(
            "Connected as %s via generateToken (expires in %ds)",
            username,
            expires_in,
        )
        return {
            "token": token,
            "username": username,
            "expires_in": expires_in,
            "grant_type": "generateToken",
        }

    # ------------------------------------------------------------------
    # Sharing REST API
    # ------------------------------------------------------------------

    def sharing_request(
        self, endpoint: str, params: dict[str, Any] | None = None, method: str = "GET"
    ) -> dict[str, Any] | None:
        """Make a request to the Portal Sharing REST API.

        Args:
            endpoint: Path after /sharing/rest/ (e.g. /search, /portals/self)
            params: Additional query/form parameters
            method: HTTP method (GET or POST)

        Returns:
            JSON response dict, or None on error.
        """
        if not self.portal_url:
            logger.error("Not connected, call connect_* first")
            return None

        return self._sharing_request(endpoint, params=params, method=method)

    def admin_request(
        self, endpoint: str, params: dict[str, Any] | None = None, method: str = "GET"
    ) -> dict[str, Any] | None:
        """Make a request to the Portal Admin API (requires admin privileges).

        Args:
            endpoint: Path after /portaladmin (e.g. /healthCheck, /machines)
            params: Additional parameters
            method: HTTP method

        Returns:
            JSON response dict, or None on error.
        """
        if not self.portal_url:
            logger.error("Not connected, call connect_* first")
            return None

        if not self.is_connected:
            logger.error("Token expired, reconnect first")
            return None

        url = f"{self.portal_url.rstrip('/')}/portaladmin{endpoint}"
        params = dict(params or {})
        params["f"] = "json"
        params["token"] = self._token  # type: ignore[arg-type]

        try:
            if method.upper() == "GET":
                resp = self._session.get(url, params=params, timeout=30)
            else:
                resp = self._session.post(url, data=params, timeout=30)

            resp.raise_for_status()
            data = resp.json()

            if "error" in data:
                logger.warning("Admin API error at %s: %s", endpoint, data["error"])
                return {"error": data["error"]}

            return data
        except requests.exceptions.RequestException as e:
            logger.error("Admin API request failed: %s", e)
            return {"error": str(e)}
        except json.JSONDecodeError:
            logger.error("Admin API returned non-JSON at %s", endpoint)
            return {"error": "Non-JSON response"}

    # ------------------------------------------------------------------
    # Convenience methods
    # ------------------------------------------------------------------

    def get_portal_info(self) -> dict[str, Any] | None:
        """Get portal organization info."""
        return self._sharing_request("/portals/self")

    def search_items(
        self,
        query: str = "*",
        item_type: str | None = None,
        owner: str | None = None,
        max_items: int = 100,
    ) -> list[dict[str, Any]]:
        """Search portal content.

        Args:
            query: Search query string
            item_type: Filter by item type (e.g. "Feature Service")
            owner: Filter by owner username
            max_items: Maximum items to return

        Returns:
            List of item dicts.
        """
        q_parts = []
        if query and query != "*":
            q_parts.append(query)
        if item_type:
            q_parts.append(f'type:"{item_type}"')
        if owner:
            q_parts.append(f'owner:"{owner}"')

        full_query = " AND ".join(q_parts) if q_parts else "*"

        items = []
        start = 1
        page_size = min(max_items, 100)

        while len(items) < max_items:
            data = self._sharing_request(
                "/search",
                params={
                    "q": full_query,
                    "start": start,
                    "num": page_size,
                    "sortField": "modified",
                    "sortOrder": "desc",
                },
            )

            if not data or "error" in data:
                break

            results = data.get("results", [])
            if not results:
                break

            for item in results:
                items.append({
                    "id": item.get("id"),
                    "title": item.get("title"),
                    "type": item.get("type"),
                    "owner": item.get("owner"),
                    "created": _epoch_to_str(item.get("created")),
                    "modified": _epoch_to_str(item.get("modified")),
                    "size": item.get("size", 0),
                    "url": item.get("url"),
                    "snippet": _truncate(item.get("snippet"), 120),
                    "tags": item.get("tags", []),
                    "num_views": item.get("numViews", 0),
                })

            start += page_size
            if start > data.get("total", 0):
                break

        return items[:max_items]

    def get_item_details(self, item_id: str) -> dict[str, Any] | None:
        """Get detailed metadata for a specific item."""
        return self._sharing_request(f"/content/items/{item_id}")

    def get_item_data(self, item_id: str) -> dict[str, Any] | None:
        """Get the data/content of an item (e.g. web map JSON, service definition)."""
        return self._sharing_request(f"/content/items/{item_id}/data")

    def list_users(self, max_users: int = 1000) -> list[dict[str, Any]]:
        """List all portal users."""
        users = []
        start = 1
        page_size = min(max_users, 100)

        while len(users) < max_users:
            data = self._sharing_request(
                "/portals/self/users", params={"start": start, "num": page_size}
            )
            if not data or "error" in data:
                break

            user_list = data.get("users", [])
            if not user_list:
                break

            for u in user_list:
                users.append({
                    "username": u.get("username"),
                    "full_name": u.get("fullName", ""),
                    "email": u.get("email", ""),
                    "role": u.get("role", ""),
                    "level": u.get("level", ""),
                    "disabled": u.get("disabled", False),
                    "last_login": _epoch_to_str(u.get("lastLogin")),
                    "created": _epoch_to_str(u.get("created")),
                    "user_type": u.get("userType", ""),
                })

            start += page_size
            if start > data.get("total", 0):
                break

        return users[:max_users]

    def list_groups(self, max_groups: int = 1000) -> list[dict[str, Any]]:
        """List all portal groups."""
        groups = []
        start = 1
        page_size = min(max_groups, 100)

        while len(groups) < max_groups:
            data = self._sharing_request(
                "/portals/self/groups", params={"start": start, "num": page_size}
            )
            if not data or "error" in data:
                break

            group_list = data.get("groups", [])
            if not group_list:
                break

            for g in group_list:
                groups.append({
                    "id": g.get("id"),
                    "title": g.get("title"),
                    "owner": g.get("owner", ""),
                    "description": _truncate(g.get("description", ""), 120),
                    "access": g.get("access", ""),
                    "member_count": g.get("memberCount", 0),
                    "is_invitation_only": g.get("isInvitationOnly", False),
                    "created": _epoch_to_str(g.get("created")),
                    "modified": _epoch_to_str(g.get("modified")),
                    "tags": g.get("tags", []),
                })

            start += page_size
            if start > data.get("total", 0):
                break

        return groups[:max_groups]

    # ------------------------------------------------------------------
    # Feature Service Operations (Phase 2)
    # ------------------------------------------------------------------

    def add_features(
        self,
        service_url: str,
        layer_id: int,
        features: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Add features to a hosted feature layer.

        Args:
            service_url: Feature service URL (e.g. https://host/arcgis/rest/services/svc/FeatureServer)
            layer_id: Layer ID (e.g. 0)
            features: List of feature dicts with 'attributes' and optionally 'geometry'

        Returns:
            Dict with addResults array.
        """
        url = f"{service_url.rstrip('/')}/{layer_id}/addFeatures"
        data = {
            "features": json.dumps(features),
            "f": "json",
        }
        t = self.token
        if t:
            data["token"] = t
        try:
            resp = self._session.post(url, data=data, timeout=60)
            resp.raise_for_status()
            result = resp.json()
            if "error" in result:
                return {"error": result["error"]}
            return result
        except requests.exceptions.RequestException as e:
            return {"error": str(e)}

    def update_features(
        self,
        service_url: str,
        layer_id: int,
        features: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Update features in a hosted feature layer.

        Args:
            service_url: Feature service URL
            layer_id: Layer ID
            features: List of feature dicts with 'attributes' (must include OBJECTID)

        Returns:
            Dict with updateResults array.
        """
        url = f"{service_url.rstrip('/')}/{layer_id}/updateFeatures"
        data = {
            "features": json.dumps(features),
            "f": "json",
        }
        t = self.token
        if t:
            data["token"] = t
        try:
            resp = self._session.post(url, data=data, timeout=60)
            resp.raise_for_status()
            result = resp.json()
            if "error" in result:
                return {"error": result["error"]}
            return result
        except requests.exceptions.RequestException as e:
            return {"error": str(e)}

    def delete_features(
        self,
        service_url: str,
        layer_id: int,
        object_ids: str | None = None,
        where_clause: str | None = None,
    ) -> dict[str, Any]:
        """Delete features from a hosted feature layer.

        Args:
            service_url: Feature service URL
            layer_id: Layer ID
            object_ids: Comma-separated OBJECTID values (e.g. "1,2,3")
            where_clause: SQL WHERE clause (e.g. "STATUS = 'Inactive'")

        Returns:
            Dict with deleteResults array.
        """
        url = f"{service_url.rstrip('/')}/{layer_id}/deleteFeatures"
        data: dict[str, Any] = {"f": "json"}
        if object_ids:
            data["objectIds"] = object_ids
        elif where_clause:
            data["where"] = where_clause
        else:
            return {"error": "Either objectIds or where clause is required"}
        t = self.token
        if t:
            data["token"] = t
        try:
            resp = self._session.post(url, data=data, timeout=60)
            resp.raise_for_status()
            result = resp.json()
            if "error" in result:
                return {"error": result["error"]}
            return result
        except requests.exceptions.RequestException as e:
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # Content Management (Phase 2)
    # ------------------------------------------------------------------

    def update_item(
        self,
        item_id: str,
        title: str | None = None,
        description: str | None = None,
        snippet: str | None = None,
        tags: str | None = None,
        access: str | None = None,
    ) -> dict[str, Any]:
        """Update item properties on the portal.

        Args:
            item_id: The item ID to update
            title: New title
            description: New description
            snippet: New snippet/summary
            tags: Comma-separated tags
            access: New access level (private, org, public)

        Returns:
            Dict with success status.
        """
        # First get the item to find the owner
        item_info = self.get_item_details(item_id)
        if not item_info or "error" in item_info:
            return {"error": f"Could not retrieve item {item_id}"}
        owner = item_info.get("owner", "")

        data: dict[str, Any] = {}
        if title is not None:
            data["title"] = title
        if description is not None:
            data["description"] = description
        if snippet is not None:
            data["snippet"] = snippet
        if tags is not None:
            data["tags"] = tags
        if access is not None:
            data["access"] = access

        if not data:
            return {"error": "No properties to update"}

        result = self._sharing_request(
            f"/content/users/{owner}/items/{item_id}/update",
            params=data,
            method="POST",
        )
        return result or {"error": "Update failed"}

    def delete_item(self, item_id: str, owner: str | None = None) -> dict[str, Any]:
        """Delete an item from the portal.

        Args:
            item_id: The item ID to delete
            owner: Item owner username. If not provided, looks it up.

        Returns:
            Dict with success status.
        """
        if not owner:
            item_info = self.get_item_details(item_id)
            if not item_info or "error" in item_info:
                return {"error": f"Could not retrieve item {item_id}"}
            owner = item_info.get("owner", "")

        result = self._sharing_request(
            f"/content/users/{owner}/items/{item_id}/delete",
            params={"f": "json"},
            method="POST",
        )
        return result or {"error": "Delete failed"}

    def share_item(
        self,
        item_id: str,
        owner: str | None = None,
        everyone: bool = False,
        org: bool = False,
        groups: str | None = None,
    ) -> dict[str, Any]:
        """Share or unshare an item.

        Args:
            item_id: The item ID to share
            owner: Item owner username
            everyone: Share with everyone (public)
            org: Share with the organization
            groups: Comma-separated group IDs to share with

        Returns:
            Dict with sharing results.
        """
        if not owner:
            item_info = self.get_item_details(item_id)
            if not item_info or "error" in item_info:
                return {"error": f"Could not retrieve item {item_id}"}
            owner = item_info.get("owner", "")

        data: dict[str, Any] = {
            "everyone": str(everyone).lower(),
            "org": str(org).lower(),
        }
        if groups:
            data["groups"] = groups

        result = self._sharing_request(
            f"/content/users/{owner}/items/{item_id}/share",
            params=data,
            method="POST",
        )
        return result or {"error": "Share operation failed"}

    def get_item_data(self, item_id: str) -> dict[str, Any]:
        """Get the data/content of an item (web map JSON, etc.).

        Args:
            item_id: The item ID

        Returns:
            Dict with the item data (e.g., web map JSON, feature collection).
        """
        result = self._sharing_request(f"/content/items/{item_id}/data")
        return result or {"error": "Could not retrieve item data"}

    # ------------------------------------------------------------------
    # User/Group Management (Phase 2)
    # ------------------------------------------------------------------

    def create_group(
        self,
        title: str,
        name: str | None = None,
        description: str = "",
        access: str = "private",
        is_invitation_only: bool = False,
    ) -> dict[str, Any]:
        """Create a new group.

        Args:
            title: Group title (required)
            name: Group name (URL-friendly). Defaults to title.
            description: Group description
            access: Access level, private, org, public
            is_invitation_only: If True, users must be invited to join

        Returns:
            Dict with group creation result.
        """
        data: dict[str, Any] = {
            "title": title,
            "description": description,
            "access": access,
            "isInvitationOnly": str(is_invitation_only).lower(),
        }
        if name:
            data["name"] = name

        result = self._sharing_request(
            "/community/createGroup",
            params=data,
            method="POST",
        )
        return result or {"error": "Group creation failed"}

    def invite_to_group(
        self,
        group_id: str,
        users: str,
        role: str = "member",
        message: str = "",
    ) -> dict[str, Any]:
        """Invite users to a group.

        Args:
            group_id: The group ID
            users: Comma-separated usernames to invite
            role: Role for invited users, member or admin
            message: Invitation message

        Returns:
            Dict with invitation results.
        """
        data: dict[str, Any] = {
            "users": users,
            "role": role,
        }
        if message:
            data["message"] = message

        result = self._sharing_request(
            f"/community/groups/{group_id}/invite",
            params=data,
            method="POST",
        )
        return result or {"error": "Invitation failed"}

    def get_user_details(self, username: str) -> dict[str, Any]:
        """Get detailed information about a specific user.

        Args:
            username: The username to look up

        Returns:
            Dict with user details including role, privileges, storage, etc.
        """
        result = self._sharing_request(f"/community/users/{username}")
        if not result:
            return {"error": "User not found"}
        if "error" in result:
            return result

        # Return a clean subset of user info
        return {
            "status": "ok",
            "username": result.get("username", ""),
            "fullname": result.get("fullName", ""),
            "email": result.get("email", ""),
            "role": result.get("role", ""),
            "role_id": result.get("roleId", ""),
            "privileges": result.get("privileges", []),
            "org_id": result.get("orgId", ""),
            "org_name": result.get("orgName", ""),
            "last_login": _epoch_to_str(result.get("lastLogin")),
            "storage_usage": result.get("storageUsage", 0),
            "storage_quota": result.get("storageQuota", 0),
            "created": _epoch_to_str(result.get("created")),
            "access": result.get("access", ""),
            "mfa_enabled": result.get("mfaEnabled", False),
            "disabled": result.get("disabled", False),
        }

    def health_check(self) -> dict[str, Any]:
        """Perform portal health check (requires admin privileges)."""
        return self.admin_request("/healthCheck") or {"error": "Health check failed"}

    # ------------------------------------------------------------------
    # Service Publishing (Phase 3)
    # ------------------------------------------------------------------

    def upload_file(
        self,
        file_path: str,
        title: str,
        type_: str,
        tags: str = "",
        description: str = "",
        snippet: str = "",
        access: str = "private",
        owner: str | None = None,
    ) -> dict[str, Any]:
        """Upload a file to portal content.

        Supports CSV, Shapefile (zipped), GeoJSON, KML, and other GIS formats.

        Args:
            file_path: Local path to the file to upload.
            title: Item title.
            type_: ArcGIS item type (e.g., 'CSV', 'Shapefile', 'GeoJSON',
                'KML', 'File Geodatabase', 'Service Definition').
            tags: Comma-separated tags.
            description: Item description.
            snippet: Short summary.
            access: private, org, or public.
            owner: Owner username. Defaults to authenticated user.

        Returns:
            Dict with item info (id, item, owner, etc.).
        """
        if not owner:
            owner = self.username
        if not owner:
            return {"error": "No owner specified and not connected as a user."}

        from pathlib import Path

        p = Path(file_path)
        if not p.exists():
            return {"error": f"File not found: {file_path}"
            }

        url = f"{self.sharing_url}/content/users/{owner}/add"
        params: dict[str, Any] = {
            "title": title,
            "type": type_,
            "access": access,
            "f": "json",
        }
        if tags:
            params["tags"] = tags
        if description:
            params["description"] = description
        if snippet:
            params["snippet"] = snippet
        if self.token:
            params["token"] = self.token

        try:
            with open(p, "rb") as fh:
                files = {"file": (p.name, fh)}
                resp = self._session.post(url, data=params, files=files, timeout=120)
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                return {"error": data["error"]}
            return data
        except requests.exceptions.RequestException as e:
            logger.error("Upload failed: %s", e)
            return {"error": str(e)}
        except json.JSONDecodeError:
            return {"error": "Non-JSON response from upload"}

    def publish_from_item(
        self,
        item_id: str,
        service_type: str = "featureService",
        publish_parameters: dict[str, Any] | None = None,
        owner: str | None = None,
    ) -> dict[str, Any]:
        """Publish an uploaded item as a hosted feature service.

        Args:
            item_id: The ID of the uploaded item to publish.
            service_type: 'featureService' or 'mapService'.
            publish_parameters: Optional dict for CSV/Shapefile publish config
                (e.g., layer configuration, output name).
            owner: Owner username.

        Returns:
            Dict with publish result including service URL.
        """
        if not owner:
            owner = self.username
        if not owner:
            return {"error": "No owner specified."}

        data: dict[str, Any] = {
            "itemId": item_id,
            "serviceType": service_type,
        }
        if publish_parameters:
            data["publishParameters"] = json.dumps(publish_parameters)

        result = self._sharing_request(
            f"/content/users/{owner}/publish",
            params=data,
            method="POST",
        )
        return result or {"error": "Publish failed"}

    def create_service(
        self,
        name: str,
        service_type: str = "Feature Service",
        description: str = "",
        snippet: str = "",
        tags: str = "",
        access: str = "private",
        is_view: bool = False,
        create_parameters: dict[str, Any] | None = None,
        owner: str | None = None,
    ) -> dict[str, Any]:
        """Create an empty hosted feature service.

        Args:
            name: Service name.
            service_type: 'Feature Service' or 'Map Service'.
            description: Service description.
            snippet: Short summary.
            tags: Comma-separated tags.
            access: private, org, or public.
            is_view: Create as a hosted feature layer view.
            create_parameters: Optional JSON dict for advanced schema config.
            owner: Owner username.

        Returns:
            Dict with created service info including service URL.
        """
        if not owner:
            owner = self.username
        if not owner:
            return {"error": "No owner specified."}

        data: dict[str, Any] = {
            "name": name,
            "serviceType": service_type,
            "description": description,
            "access": access,
            "isView": str(is_view).lower(),
        }
        if snippet:
            data["snippet"] = snippet
        if tags:
            data["tags"] = tags
        if create_parameters:
            data["createParameters"] = json.dumps(create_parameters)

        result = self._sharing_request(
            f"/content/users/{owner}/createService",
            params=data,
            method="POST",
        )
        return result or {"error": "Service creation failed"}

    # ------------------------------------------------------------------
    # Geoprocessing (Phase 3)
    # ------------------------------------------------------------------

    def execute_gp_task(
        self,
        gp_url: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a synchronous geoprocessing task.

        Args:
            gp_url: The GP service REST endpoint (e.g.,
                'https://server/arcgis/rest/services/MyGP/GPServer/MyTask').
            params: Input parameters as a dict.

        Returns:
            Dict with results (outputs, messages).
        """
        url = gp_url.rstrip("/") + "/execute"
        request_params: dict[str, Any] = {"f": "json"}
        if self.token:
            request_params["token"] = self.token
        if params:
            for k, v in params.items():
                request_params[k] = v if isinstance(v, str) else json.dumps(v)

        try:
            resp = self._session.post(url, data=request_params, timeout=300)
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                return {"error": data["error"]}
            return {
                "status": data.get("executionType", "esriExecutionTypeSynchronous"),
                "results": data.get("results", []),
                "messages": data.get("messages", []),
            }
        except requests.exceptions.RequestException as e:
            logger.error("GP task failed: %s", e)
            return {"error": str(e)}
        except json.JSONDecodeError:
            return {"error": "Non-JSON response from GP service"}

    def submit_gp_job(
        self,
        gp_url: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Submit an asynchronous geoprocessing job.

        Args:
            gp_url: The GP service REST endpoint.
            params: Input parameters as a dict.

        Returns:
            Dict with job ID and status URL for polling.
        """
        url = gp_url.rstrip("/") + "/submitJob"
        request_params: dict[str, Any] = {"f": "json"}
        if self.token:
            request_params["token"] = self.token
        if params:
            for k, v in params.items():
                request_params[k] = v if isinstance(v, str) else json.dumps(v)

        try:
            resp = self._session.post(url, data=request_params, timeout=120)
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                return {"error": data["error"]}
            job_id = data.get("jobId", "")
            return {
                "job_id": job_id,
                "job_status": data.get("jobStatus", "esriJobSubmitted"),
                "status_url": gp_url.rstrip("/") + f"/jobs/{job_id}",
            }
        except requests.exceptions.RequestException as e:
            logger.error("GP job submission failed: %s", e)
            return {"error": str(e)}
        except json.JSONDecodeError:
            return {"error": "Non-JSON response from GP service"}

    def get_gp_job_status(
        self,
        gp_url: str,
        job_id: str,
    ) -> dict[str, Any]:
        """Get the status of an asynchronous geoprocessing job.

        Args:
            gp_url: The GP service REST endpoint.
            job_id: The job ID returned by submit_gp_job.

        Returns:
            Dict with job status, messages, and results (if complete).
        """
        url = gp_url.rstrip("/") + f"/jobs/{job_id}?f=json"
        if self.token:
            url += f"&token={self.token}"

        try:
            resp = self._session.get(url, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                return {"error": data["error"]}
            return {
                "job_id": job_id,
                "job_status": data.get("jobStatus", ""),
                "messages": data.get("messages", []),
                "results": data.get("results", {}),
            }
        except requests.exceptions.RequestException as e:
            logger.error("GP job status check failed: %s", e)
            return {"error": str(e)}
        except json.JSONDecodeError:
            return {"error": "Non-JSON response from GP service"}

    # ------------------------------------------------------------------
    # Portal Admin (Phase 3)
    # ------------------------------------------------------------------

    def portal_system_info(self) -> dict[str, Any]:
        """Get portal system/version information (requires admin access)."""
        result = self.admin_request("")
        if not result:
            return {"error": "Could not retrieve portal system info"}
        if "error" in result:
            return result
        return {
            "status": "ok",
            "portal_version": result.get("currentVersion", ""),
            "full_version": result.get("fullVersion", ""),
            "portal_id": result.get("portalId", ""),
            "name": result.get("name", ""),
            "hosting_server_url": result.get("housingServerVersion", ""),
            "platform": result.get("platform", ""),
            "auth_mode": result.get("authMode", ""),
            "available_languages": result.get("availableLanguages", []),
        }

    def list_licenses(self) -> dict[str, Any]:
        """Get license information (requires admin access).

        Returns:
            Dict with license information for the organization.
        """
        result = self.admin_request("/license")
        if not result:
            return {"error": "Could not retrieve license info"}
        return result

    def portal_usage(
        self,
        start_time: str | None = None,
        end_time: str | None = None,
        period: str = "1d",
        host_type: str = "portal",
    ) -> dict[str, Any]:
        """Get portal usage statistics (requires admin access).

        Args:
            start_time: Start time as epoch ms or ISO string. Defaults to 30 days ago.
            end_time: End time as epoch ms or ISO string. Defaults to now.
            period: Aggregation period, 1d, 1w, 1M (1d recommended).
            host_type: 'portal' or 'server'.

        Returns:
            Dict with usage statistics.
        """
        params: dict[str, Any] = {
            "period": period,
            "hostingServerType": host_type,
        }
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time

        result = self.admin_request("/portalusage", params=params)
        if not result:
            return {"error": "Could not retrieve usage stats"}
        return result

    # ------------------------------------------------------------------
    # Map Export
    # ------------------------------------------------------------------

    def export_map_image(
        self,
        service_url: str,
        bbox: str | None = None,
        width: int = 800,
        height: int = 600,
        image_sr: str = "4326",
        format: str = "png",
        dpi: int = 96,
        transparent: bool = False,
        layers: str | None = None,
        where: str | None = None,
    ) -> dict[str, Any]:
        """Export a map image from a MapServer or FeatureServer.

        Calls the /export endpoint and saves the resulting image to a
        temp file. Works with both ArcGIS Online and Enterprise Portal.

        Args:
            service_url: Full URL to a MapServer or FeatureServer
                (e.g. https://services.arcgis.com/.../MapServer)
            bbox: Bounding box as 'xmin,ymin,xmax,ymax'. If None, uses
                the service's default full extent.
            width: Image width in pixels (default 800).
            height: Image height in pixels (default 600).
            image_sr: Spatial reference for the output image (default 4326).
            format: Image format - 'png', 'jpg', 'gif', 'pdf', 'svg'
                (default 'png').
            dpi: Image DPI (default 96).
            transparent: If true, background is transparent.
            layers: Layer visibility filter, e.g. 'show:0,1' or 'hide:2'.
            where: SQL where clause to filter features (only layers
                that support this).

        Returns:
            Dict with file_path, width, height, format, extent, and URL.
        """
        if not self.is_connected:
            return {"error": "Not connected. Call connect_portal first."}

        # Normalize service URL
        svc = service_url.rstrip("/")
        if not svc.endswith("/MapServer") and not svc.endswith("/FeatureServer"):
            svc = f"{svc}/MapServer"

        # FeatureServer does NOT support /export for map images.
        # Try swapping to MapServer (many services publish both).
        if svc.endswith("/FeatureServer"):
            ms_url = svc.replace("/FeatureServer", "/MapServer")
            try:
                test_resp = self._session.get(
                    f"{ms_url}/export",
                    params={"f": "json", "token": self._token},
                    timeout=15,
                )
                test_data = test_resp.json()
                if "error" not in test_data and "supportedExportMapImageFormats" in test_data:
                    svc = ms_url
                else:
                    # MapServer not available; FeatureServer-only service
                    return {
                        "error": (
                            "This service is a FeatureServer which does not "
                            "support map image export. Use a MapServer URL "
                            "instead, or convert via: "
                            f"{ms_url}"
                        )
                    }
            except Exception:
                return {
                    "error": (
                        "FeatureServer does not support /export. "
                        "A MapServer equivalent was not found at: "
                        f"{ms_url}"
                    )
                }

        export_url = f"{svc}/export"

        params: dict[str, Any] = {
            "f": "json",
            "size": f"{width},{height}",
            "imageSR": image_sr,
            "format": format,
            "dpi": dpi,
            "transparent": str(transparent).lower(),
        }

        if self._token:
            params["token"] = self._token

        if bbox:
            params["bbox"] = bbox
        elif not svc.endswith("/FeatureServer"):
            # Some services require bbox. Try to auto-detect from service metadata.
            try:
                # Fetch metadata WITHOUT token first (public services).
                # Token is org-specific and may be rejected by other servers.
                meta_resp = self._session.get(svc, params={"f": "json"}, timeout=15)
                meta_data = meta_resp.json()
                full_extent = meta_data.get("fullExtent") or meta_data.get("initialExtent")
                if full_extent:
                    params["bbox"] = (
                        f"{full_extent['xmin']},{full_extent['ymin']},"
                        f"{full_extent['xmax']},{full_extent['ymax']}"
                    )
                    sr = full_extent.get("spatialReference", {})
                    if sr.get("wkid"):
                        params["imageSR"] = str(sr["wkid"])
            except Exception:
                pass  # will get a 400 if bbox truly required

        if layers:
            params["layers"] = layers
        if where:
            params["where"] = where

        try:
            resp = self._session.get(export_url, params=params, timeout=60)
            resp.raise_for_status()
            data = resp.json()

            if "error" in data:
                err = data["error"]
                # 498 = invalid token; retry without token for public services
                if isinstance(err, dict) and err.get("code") == 498:
                    params.pop("token", None)
                    resp = self._session.get(export_url, params=params, timeout=60)
                    resp.raise_for_status()
                    data = resp.json()
                else:
                    return {"error": err}

            image_href = data.get("href")
            if not image_href:
                return {"error": "No image URL returned by export endpoint"}

            # Download the actual image
            img_resp = self._session.get(image_href, timeout=60)
            img_resp.raise_for_status()

            # Save to temp file
            import tempfile
            ext = format.lower()
            if ext == "jpg":
                ext = "jpeg"
            suffix = f".{'jpg' if ext == 'jpeg' else ext}"
            tmp = tempfile.NamedTemporaryFile(
                suffix=suffix,
                prefix="arcgis_export_",
                delete=False,
                dir=os.path.join(
                    os.path.dirname(os.path.abspath(__file__)), ".."
                ),
            )
            tmp.write(img_resp.content)
            tmp.close()

            result = {
                "file_path": tmp.name,
                "width": data.get("width", width),
                "height": data.get("height", height),
                "format": format,
                "service_url": svc,
            }

            # Include extent if returned
            extent = data.get("extent")
            if extent:
                result["extent"] = extent

            return result

        except requests.exceptions.RequestException as e:
            return {"error": f"Export request failed: {e}"}
        except json.JSONDecodeError:
            return {"error": "Export endpoint returned non-JSON response"}

    # ------------------------------------------------------------------
    # Batch Operations (Phase 3)
    # ------------------------------------------------------------------

    def batch_delete_items(
        self,
        item_ids: list[str],
        owner: str | None = None,
    ) -> dict[str, Any]:
        """Delete multiple items at once.

        Args:
            item_ids: List of item IDs to delete.
            owner: Owner username. If not provided, looks up each item.

        Returns:
            Dict with per-item results.
        """
        results: dict[str, Any] = {"succeeded": [], "failed": []}
        for item_id in item_ids:
            res = self.delete_item(item_id, owner=owner)
            if res and "error" not in res:
                results["succeeded"].append(item_id)
            else:
                results["failed"].append({
                    "item_id": item_id,
                    "error": res.get("error", "Unknown error") if res else "No response",
                })
        results["total"] = len(item_ids)
        results["succeeded_count"] = len(results["succeeded"])
        results["failed_count"] = len(results["failed"])
        return results

    def batch_share_items(
        self,
        item_ids: list[str],
        owner: str | None = None,
        everyone: bool = False,
        org: bool = False,
        groups: str | None = None,
    ) -> dict[str, Any]:
        """Share or unshare multiple items.

        Args:
            item_ids: List of item IDs to share.
            owner: Owner username.
            everyone: Share with everyone (public).
            org: Share with the organization.
            groups: Comma-separated group IDs.

        Returns:
            Dict with per-item results.
        """
        results: dict[str, Any] = {"succeeded": [], "failed": []}
        for item_id in item_ids:
            res = self.share_item(
                item_id, owner=owner, everyone=everyone, org=org, groups=groups,
            )
            if res and "error" not in res:
                results["succeeded"].append(item_id)
            else:
                results["failed"].append({
                    "item_id": item_id,
                    "error": res.get("error", "Unknown error") if res else "No response",
                })
        results["total"] = len(item_ids)
        results["succeeded_count"] = len(results["succeeded"])
        results["failed_count"] = len(results["failed"])
        return results

    def batch_update_items(
        self,
        item_ids: list[str],
        owner: str | None = None,
        title: str | None = None,
        description: str | None = None,
        snippet: str | None = None,
        tags: str | None = None,
        access: str | None = None,
    ) -> dict[str, Any]:
        """Update properties of multiple items.

        Args:
            item_ids: List of item IDs to update.
            owner: Owner username.
            title: New title (applied to all items).
            description: New description.
            snippet: New snippet/summary.
            tags: New comma-separated tags.
            access: New access level.

        Returns:
            Dict with per-item results.
        """
        results: dict[str, Any] = {"succeeded": [], "failed": []}
        for item_id in item_ids:
            res = self.update_item(
                item_id, owner=owner, title=title, description=description,
                snippet=snippet, tags=tags, access=access,
            )
            if res and "error" not in res:
                results["succeeded"].append(item_id)
            else:
                results["failed"].append({
                    "item_id": item_id,
                    "error": res.get("error", "Unknown error") if res else "No response",
                })
        results["total"] = len(item_ids)
        results["succeeded_count"] = len(results["succeeded"])
        results["failed_count"] = len(results["failed"])
        return results

    # ------------------------------------------------------------------
    # Internal methods
    # ------------------------------------------------------------------

    def _set_portal_url(self, portal_url: str) -> None:
        """Set portal and sharing URLs from base URL."""
        self.portal_url = portal_url.rstrip("/")
        if not self.portal_url.endswith("/rest"):
            self.sharing_url = f"{self.portal_url}/sharing/rest"
        else:
            self.sharing_url = self.portal_url
            self.portal_url = self.portal_url.rsplit("/sharing/rest", 1)[0]

    def _sharing_request(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        token: str | None = None,
        method: str = "GET",
    ) -> dict[str, Any] | None:
        """Internal Sharing API request."""
        url = f"{self.sharing_url}{endpoint}"
        params = dict(params or {})
        params["f"] = "json"

        t = token or self.token
        if t:
            params["token"] = t

        try:
            if method.upper() == "GET":
                resp = self._session.get(url, params=params, timeout=30)
            else:
                resp = self._session.post(url, data=params, timeout=30)

            resp.raise_for_status()
            data = resp.json()

            if "error" in data:
                logger.warning("Sharing API error at %s: %s", endpoint, data["error"])
                return {"error": data["error"]}

            return data
        except requests.exceptions.RequestException as e:
            logger.error("Sharing API request failed: %s", e)
            return {"error": str(e)}
        except json.JSONDecodeError:
            logger.error("Sharing API returned non-JSON at %s", endpoint)
            return {"error": "Non-JSON response"}


# ------------------------------------------------------------------
# OAuth callback handler
# ------------------------------------------------------------------


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler to capture OAuth2 authorization code callback."""

    def do_GET(self) -> None:
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)

        if "code" in params:
            self.server.auth_code = params["code"][0]  # type: ignore[attr-defined]
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h2>Authentication successful!</h2>"
                b"<p>You can close this window.</p></body></html>"
            )
        elif "error" in params:
            self.server.auth_error = params.get("error", ["unknown"])[0]  # type: ignore[attr-defined]
            self.send_response(400)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            msg = (
                f"<html><body><h2>Authentication failed: "
                f"{html_escape(str(self.server.auth_error))}</h2></body></html>"  # type: ignore[attr-defined]
            )
            self.wfile.write(msg.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Suppress HTTP server logs


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _epoch_to_str(epoch_ms: int | float | None) -> str:
    """Convert epoch milliseconds to readable date string."""
    if not epoch_ms:
        return ""
    try:
        return datetime.fromtimestamp(epoch_ms / 1000).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError):
        return str(epoch_ms)


def _truncate(text: str | None, max_len: int) -> str:
    """Truncate text to max_len characters."""
    if not text:
        return ""
    return (text[:max_len] + "...") if len(text) > max_len else text
