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

# Conditionally suppress InsecureRequestWarning for self-signed certs.
# Only suppressed when TLS verification is explicitly disabled (MCP_TLS_VERIFY=false).
if os.environ.get("MCP_TLS_VERIFY", "true").lower() == "false":
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
        self._auth_method: str | None = None
        self._session = requests.Session()
        # TLS verification: ON by default (MCP_TLS_VERIFY defaults to true).
        # Set MCP_TLS_VERIFY=false in .env for Enterprise portals with self-signed certs.
        self._session.verify = os.environ.get("MCP_TLS_VERIFY", "true").lower() != "false"
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
        self._auth_method = "token"
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
        self._auth_method = "client_credentials"

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
        self._auth_method = "oauth2"

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
        self._auth_method = "generateToken"

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
    # Layer Metadata
    # ------------------------------------------------------------------

    def describe_layer(
        self,
        service_url: str,
        layer_id: int = 0,
    ) -> dict[str, Any]:
        """Get detailed metadata for a specific layer.

        Fetches field schemas, geometry type, extent, editing capabilities,
        supported operations, relationships, and other layer properties
        from the ArcGIS REST API layer endpoint.

        Reference:
            https://developers.arcgis.com/rest/services-reference/enterprise/layer/
            https://developers.arcgis.com/rest/services-reference/enterprise/map-server/layer/

        Args:
            service_url: The feature service or map service URL
                (e.g. 'https://host/arcgis/rest/services/MyService/FeatureServer').
            layer_id: The layer ID within the service (default 0).

        Returns:
            Dict with layer metadata including fields, geometry, extent,
            editing info, and supported operations.
        """
        url = service_url.rstrip("/") + f"/{layer_id}?f=json"
        if self.token:
            url += f"&token={self.token}"

        try:
            resp = self._session.get(url, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                return {"error": data["error"]}

            # Extract structured metadata
            result: dict[str, Any] = {
                "id": data.get("id"),
                "name": data.get("name", ""),
                "type": data.get("type", ""),
                "description": data.get("description", ""),
                "geometry_type": data.get("geometryType", ""),
            }

            # Fields
            fields = data.get("fields", [])
            result["fields"] = [
                {
                    "name": f.get("name", ""),
                    "alias": f.get("alias", ""),
                    "type": f.get("type", ""),
                    "length": f.get("length"),
                    "nullable": f.get("nullable", True),
                    "editable": f.get("editable", True),
                    "default_value": f.get("defaultValue"),
                    "domain": f.get("domain"),
                }
                for f in fields
            ]

            # Edit info
            edit_info = data.get("editingInfo", {})
            result["editing_info"] = {
                "supports_add": data.get("supportsAdd", False),
                "supports_update": data.get("supportsUpdate", False),
                "supports_delete": data.get("supportsDelete", False),
                "supportsrollback_on_failure": data.get(
                    "supportsRollbackOnFailureParameter", False
                ),
                "last_edit_date": _epoch_to_str(
                    edit_info.get("lastEditDate")
                ),
            }

            # Extent
            extent = data.get("extent")
            if extent:
                sr = extent.get("spatialReference", {})
                result["extent"] = {
                    "xmin": extent.get("xmin"),
                    "ymin": extent.get("ymin"),
                    "xmax": extent.get("xmax"),
                    "ymax": extent.get("ymax"),
                    "spatial_reference": sr.get("wkid", sr.get("wkt", "")),
                }

            # Max record count
            result["max_record_count"] = data.get("maxRecordCount", 0)

            # Supports pagination
            result["supports_pagination"] = data.get(
                "supportsPagination", False
            )

            # Drawing info (renderer)
            drawing_info = data.get("drawingInfo")
            if drawing_info:
                renderer = drawing_info.get("renderer", {})
                result["renderer"] = {
                    "type": renderer.get("type", ""),
                    "label": renderer.get("label", ""),
                }

            # Relationships
            relationships = data.get("relationships", [])
            if relationships:
                result["relationships"] = [
                    {
                        "id": r.get("id"),
                        "name": r.get("name", ""),
                        "related_table_id": r.get("relatedTableId"),
                        "cardinality": r.get("cardinality", ""),
                        "key_field": r.get("keyField", ""),
                    }
                    for r in relationships
                ]

            # Subtypes
            subtypes = data.get("subtypes", [])
            if subtypes:
                result["subtypes"] = [
                    {
                        "code": s.get("code"),
                        "name": s.get("name", ""),
                    }
                    for s in subtypes
                ]

            # Domains
            domains = data.get("domains", [])
            if domains:
                result["domains"] = [
                    {
                        "name": d.get("name", ""),
                        "type": d.get("type", ""),
                        "field_name": d.get("fieldName", ""),
                    }
                    for d in domains
                ]

            return result

        except requests.exceptions.RequestException as e:
            logger.error("describe_layer failed: %s", e)
            return {"error": str(e)}
        except json.JSONDecodeError:
            return {"error": "Non-JSON response from service"}

    # ------------------------------------------------------------------
    # Geoprocessing (Phase 3)
    # ------------------------------------------------------------------

    def get_gp_task_info(
        self,
        gp_url: str,
    ) -> dict[str, Any]:
        """Get metadata for a geoprocessing service or specific task.

        When given a GPServer URL (ending in /GPServer), lists all available
        tasks. When given a task-specific URL (ending in /GPServer/TaskName),
        returns the detailed parameter schema for that task.

        Reference:
            https://developers.arcgis.com/rest/services-reference/enterprise/gp-server/
            https://developers.arcgis.com/rest/services-reference/enterprise/gp-task/

        Args:
            gp_url: The GP service REST endpoint. Can be:
                - GPServer root: 'https://host/arcgis/rest/services/MyGP/GPServer'
                - Specific task: 'https://host/arcgis/rest/services/MyGP/GPServer/MyTask'

        Returns:
            Dict with task list or detailed parameter schema.
        """
        url = gp_url.rstrip("/") + "?f=json"
        if self.token:
            url += f"&token={self.token}"

        try:
            resp = self._session.get(url, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                return {"error": data["error"]}

            # Check if this is a task-specific URL or GPServer root
            # GPServer root has a 'tasks' array; task-specific has 'parameters'
            if "parameters" in data:
                # This is a specific GP task
                return self._parse_gp_task(data, gp_url)
            elif "tasks" in data:
                # GPServer root — list all tasks
                tasks = data["tasks"]
                result: dict[str, Any] = {
                    "name": data.get("serviceName", ""),
                    "description": data.get("description", ""),
                    "tasks": [],
                }
                for task in tasks:
                    task_name = task.get("name", "")
                    result["tasks"].append({
                        "name": task_name,
                        "display_name": task.get("displayName", ""),
                        "task_url": gp_url.rstrip("/") + f"/{task_name}",
                    })
                return result
            else:
                return {"error": "Unrecognized GP service response format"}

        except requests.exceptions.RequestException as e:
            logger.error("get_gp_task_info failed: %s", e)
            return {"error": str(e)}
        except json.JSONDecodeError:
            return {"error": "Non-JSON response from GP service"}

    def _parse_gp_task(self, data: dict[str, Any], gp_url: str) -> dict[str, Any]:
        """Parse a single GP task's metadata into a structured dict."""
        result: dict[str, Any] = {
            "name": data.get("name", ""),
            "display_name": data.get("displayName", ""),
            "description": data.get("description", ""),
            "help_url": data.get("helpUrl", ""),
            "execution_type": data.get(
                "executionType", "esriExecutionTypeSynchronous"
            ),
            "category": data.get("category", ""),
            "parameters": [],
        }

        for param in data.get("parameters", []):
            p: dict[str, Any] = {
                "name": param.get("name", ""),
                "display_name": param.get("displayName", ""),
                "data_type": param.get("dataType", ""),
                "direction": param.get("direction", ""),
                "default_value": param.get("defaultValue"),
                "parameter_type": param.get("parameterType", ""),
                "category": param.get("category", ""),
            }
            result["parameters"].append(p)

        return result

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
    # Content Management (v1.4.0)
    # ------------------------------------------------------------------

    def clone_item(
        self,
        item_id: str,
        new_title: str | None = None,
        new_owner: str | None = None,
        folder: str | None = None,
    ) -> dict[str, Any]:
        """Clone an item within the same portal.

        Creates a copy of the item with its data (web maps, apps, etc.).
        The new item gets a new ID but same type, tags, description, and access.

        Args:
            item_id: The ID of the item to clone.
            new_title: Title for the clone. Defaults to original title + " (Copy)".
            new_owner: Owner of the clone. Defaults to connected user.
            folder: Folder to place the clone in. Defaults to root.

        Returns:
            Dict with new item info (id, item, owner, folder, etc.).
        """
        # Get original item details
        item_info = self.get_item_details(item_id)
        if not item_info or "error" in item_info:
            return {"error": f"Could not retrieve item {item_id}: {item_info}"}

        item_type = item_info.get("type", "")
        title = new_title or f"{item_info.get('title', 'Untitled')} (Copy)"
        tags = item_info.get("tags", "")
        if isinstance(tags, list):
            tags = ",".join(tags)
        description = item_info.get("description", "")
        snippet = item_info.get("snippet", "")
        access = item_info.get("access", "private")

        target_owner = new_owner or self.username
        if not target_owner:
            return {"error": "No owner specified and not connected as a user."}

        # Get item data (web map definitions, app configs, etc.)
        item_data = self.get_item_data(item_id)
        data_json = ""
        if item_data and "error" not in item_data:
            import json as _json
            data_json = _json.dumps(item_data)

        # Create the clone
        params: dict[str, Any] = {
            "title": title,
            "type": item_type,
            "access": access,
            "f": "json",
        }
        if tags:
            params["tags"] = tags
        if description:
            params["description"] = description
        if snippet:
            params["snippet"] = snippet
        if folder:
            params["folder"] = folder
        if data_json:
            params["text"] = data_json

        result = self._sharing_request(
            f"/content/users/{target_owner}/addItem",
            params=params,
            method="POST",
        )
        return result or {"error": "Clone failed"}

    def move_items(
        self,
        item_ids: list[str],
        target_owner: str,
        source_owner: str | None = None,
    ) -> dict[str, Any]:
        """Move items from one user to another (reassign ownership).

        Uses the portal sharing API /transfer endpoint.

        Args:
            item_ids: List of item IDs to move.
            target_owner: Username to transfer items to.
            source_owner: Current owner. If not provided, uses connected user.

        Returns:
            Dict with succeeded/failed counts and per-item results.
        """
        if not source_owner:
            source_owner = self.username
        if not source_owner:
            return {"error": "No source owner specified and not connected as a user."}

        import json as _json

        items_payload = [{"itemId": iid, "targetOwner": target_owner} for iid in item_ids]
        result = self._sharing_request(
            f"/content/users/{source_owner}/transfer",
            params={"items": _json.dumps(items_payload)},
            method="POST",
        )

        if not result or "error" in result:
            return {"error": f"Transfer failed: {result}"}

        # Parse results
        transfer_results = result.get("results", [])
        succeeded = []
        failed = []
        for r in transfer_results:
            iid = r.get("itemId", "")
            if r.get("success"):
                succeeded.append(iid)
            else:
                failed.append({"item_id": iid, "error": r.get("error", {}).get("message", "Unknown error")})

        return {
            "total": len(item_ids),
            "succeeded_count": len(succeeded),
            "failed_count": len(failed),
            "succeeded": succeeded,
            "failed": failed,
        }

    def check_service_health(
        self,
        service_url: str,
        timeout: int = 10,
    ) -> dict[str, Any]:
        """Check the health of a GIS service endpoint.

        Pings the service and returns status, latency, and basic metadata.

        Args:
            service_url: The service URL to check (FeatureServer, MapServer, etc.).
            timeout: Request timeout in seconds (default 10).

        Returns:
            Dict with status, latency_ms, status_code, available, and service info.
        """
        import time

        # Normalize URL
        url = service_url.rstrip("/")
        if not url.endswith("?"):
            url += "?f=json"
        else:
            url += "f=json"

        start = time.monotonic()
        try:
            resp = self._session.get(url, timeout=timeout)
            latency_ms = round((time.monotonic() - start) * 1000)
            status_code = resp.status_code

            if status_code != 200:
                return {
                    "status": "error",
                    "available": False,
                    "status_code": status_code,
                    "latency_ms": latency_ms,
                    "error": f"HTTP {status_code}",
                    "service_url": service_url,
                }

            data = resp.json()
            if "error" in data:
                return {
                    "status": "ok",
                    "available": True,
                    "status_code": status_code,
                    "latency_ms": latency_ms,
                    "error": data["error"],
                    "service_url": service_url,
                }

            return {
                "status": "ok",
                "available": True,
                "status_code": status_code,
                "latency_ms": latency_ms,
                "service_url": service_url,
                "service_name": data.get("serviceDescription", ""),
                "service_type": data.get("mapName", ""),
                "version": data.get("currentVersion", ""),
                "max_record_count": data.get("maxRecordCount", 0),
                "has_capabilities": bool(data.get("capabilities", "")),
                "capabilities": data.get("capabilities", ""),
            }
        except requests.exceptions.Timeout:
            latency_ms = round((time.monotonic() - start) * 1000)
            return {
                "status": "error",
                "available": False,
                "status_code": 0,
                "latency_ms": latency_ms,
                "error": "Request timed out",
                "service_url": service_url,
            }
        except requests.exceptions.ConnectionError as e:
            latency_ms = round((time.monotonic() - start) * 1000)
            return {
                "status": "error",
                "available": False,
                "status_code": 0,
                "latency_ms": latency_ms,
                "error": f"Connection failed: {e}",
                "service_url": service_url,
            }
        except Exception as e:
            latency_ms = round((time.monotonic() - start) * 1000)
            return {
                "status": "error",
                "available": False,
                "status_code": 0,
                "latency_ms": latency_ms,
                "error": str(e),
                "service_url": service_url,
            }

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
    # v1.5.0: Relationship & Dependency Analysis Tools
    # ------------------------------------------------------------------

    # ----- Tool 1: Item Relationship Explorer -----

    def explore_item_relationships(self, item_id: str) -> dict[str, Any]:
        """Get all relationships for a portal item, walking both directions.

        Queries the /content/items/{id}/relatedItems endpoint for forward
        relationships, then queries each related item for reverse relationships.

        Args:
            item_id: The ID of the item to explore.

        Returns:
            Dict with item info and its relationships, or error dict.
        """
        try:
            # Get base item details first
            item_info = self._sharing_request(f"/content/items/{item_id}")
            if not item_info or "error" in item_info:
                error_msg = (item_info.get("error", "Item not found")
                             if item_info else "No response")
                return {"error": error_msg}

            relationships = []

            # Forward relationships (items this item relates TO)
            forward = self._sharing_request(
                f"/content/items/{item_id}/relatedItems"
            )
            if forward and "relatedItems" in forward:
                for related in forward["relatedItems"]:
                    relationships.append({
                        "related_item_id": related.get("id", ""),
                        "related_title": related.get("title", ""),
                        "related_type": related.get("type", ""),
                        "relationship_type": related.get("relationshipType", ""),
                        "direction": "forward",
                    })

            # Reverse relationships (items that relate TO this item)
            reverse = self._sharing_request(
                f"/content/items/{item_id}/relatedItems",
                params={"direction": "reverse"},
            )
            if reverse and "relatedItems" in reverse:
                for related in reverse["relatedItems"]:
                    relationships.append({
                        "related_item_id": related.get("id", ""),
                        "related_title": related.get("title", ""),
                        "related_type": related.get("type", ""),
                        "relationship_type": related.get("relationshipType", ""),
                        "direction": "reverse",
                    })

            return {
                "item_id": item_id,
                "title": item_info.get("title", ""),
                "type": item_info.get("type", ""),
                "owner": item_info.get("owner", ""),
                "relationships": relationships,
                "total_relationships": len(relationships),
            }

        except Exception as e:
            return {"error": str(e)}

    # ----- Tool 2: Group Membership Auditor -----

    def audit_group_members(self, group_id: str) -> dict[str, Any]:
        """List all members of a group with their roles and details.

        Paginates through /content/groups/{id}/users to get every member.

        Args:
            group_id: The ID of the group to audit.

        Returns:
            Dict with group info and full member listing, or error dict.
        """
        try:
            # Get group metadata
            group_info = self._sharing_request(f"/content/groups/{group_id}")
            if not group_info or "error" in group_info:
                error_msg = (group_info.get("error", "Group not found")
                             if group_info else "No response")
                return {"error": error_msg}

            # Paginate through all members
            members = []
            start = 1
            num = 100  # max per page

            while True:
                users_resp = self._sharing_request(
                    f"/content/groups/{group_id}/users",
                    params={"start": start, "num": num},
                )

                if not users_resp or "error" in users_resp:
                    break

                batch = users_resp.get("users", [])
                if not batch:
                    break

                for user in batch:
                    members.append({
                        "username": user.get("username", ""),
                        "full_name": user.get("fullName", ""),
                        "email": user.get("email", ""),
                        "role": user.get("role", ""),
                        "last_login": user.get("lastLogin", -1),
                        "disabled": user.get("disabled", False),
                    })

                # Advance pagination
                next_start = users_resp.get("nextStart", 0)
                total = users_resp.get("total", 0)
                if next_start <= 0 or next_start >= total:
                    break
                start = next_start

            return {
                "group_id": group_id,
                "title": group_info.get("title", ""),
                "owner": group_info.get("owner", ""),
                "description": group_info.get("description", ""),
                "access": group_info.get("access", ""),
                "member_count": group_info.get("memberCount", len(members)),
                "members": members,
            }

        except Exception as e:
            return {"error": str(e)}

    # ----- Group Lifecycle (v1.10.0) -----

    def update_group(
        self,
        group_id: str,
        title: str | None = None,
        description: str | None = None,
        access: str | None = None,
        is_invitation_only: bool | None = None,
    ) -> dict[str, Any]:
        """Update group properties.

        Args:
            group_id: The group ID to update.
            title: New group title.
            description: New group description.
            access: New access level (private, org, public).
            is_invitation_only: If True, users must be invited.

        Returns:
            Dict with update result.
        """
        data: dict[str, Any] = {}
        if title is not None:
            data["title"] = title
        if description is not None:
            data["description"] = description
        if access is not None:
            data["access"] = access
        if is_invitation_only is not None:
            data["isInvitationOnly"] = str(is_invitation_only).lower()

        if not data:
            return {"error": "No fields to update"}

        result = self._sharing_request(
            f"/community/groups/{group_id}/update",
            params=data,
            method="POST",
        )
        return result or {"error": "Group update failed"}

    def delete_group(self, group_id: str) -> dict[str, Any]:
        """Delete a group.

        Args:
            group_id: The group ID to delete.

        Returns:
            Dict with delete result.
        """
        result = self._sharing_request(
            f"/community/groups/{group_id}/delete",
            params={},
            method="POST",
        )
        return result or {"error": "Group delete failed"}

    def remove_from_group(self, group_id: str, users: str) -> dict[str, Any]:
        """Remove users from a group.

        Args:
            group_id: The group ID.
            users: Comma-separated usernames to remove.

        Returns:
            Dict with removal results.
        """
        result = self._sharing_request(
            f"/community/groups/{group_id}/removeUsers",
            params={"users": users},
            method="POST",
        )
        return result or {"error": "Remove users failed"}

    def list_group_users(self, group_id: str, max_users: int = 100) -> list[dict[str, Any]]:
        """List users in a specific group.

        Lighter than audit_group_members -- returns just the user list
        without group metadata.

        Args:
            group_id: The group ID.
            max_users: Maximum users to return.

        Returns:
            List of user dicts with username, fullName, role.
        """
        result = self._sharing_request(
            f"/community/groups/{group_id}/users",
            params={"num": max_users},
        )
        if not result or "error" in result:
            return []
        return result.get("users", [])

    # ----- Folder Lifecycle (v1.10.0) -----

    def delete_folder(self, folder_id: str, owner: str | None = None) -> dict[str, Any]:
        """Delete a content folder.

        WARNING: Deleting a folder also deletes all items it contains.

        Args:
            folder_id: The folder ID to delete.
            owner: Folder owner username. Defaults to connected user.

        Returns:
            Dict with delete result.
        """
        user = owner or self.username
        if not user:
            return {"error": "No owner specified and not connected"}
        result = self._sharing_request(
            f"/content/users/{user}/{folder_id}/delete",
            params={},
            method="POST",
        )
        return result or {"error": "Folder delete failed"}

    # ----- User Search (v1.10.0) -----

    def search_users(self, query: str, max_users: int = 100) -> list[dict[str, Any]]:
        """Search portal users by name, email, or username.

        Args:
            query: Search string (matches name, email, username).
            max_users: Maximum results to return.

        Returns:
            List of matching user dicts.
        """
        result = self._sharing_request(
            "/portals/self/users",
            params={"q": query, "num": max_users},
        )
        if not result or "error" in result:
            return []
        return result.get("users", [])

    # ----- Tool 3: Service Dependency Scanner -----

    def scan_service_dependencies(self, service_item_id: str) -> dict[str, Any]:
        """Find all portal items that depend on a given service item.

        Searches for web maps, apps, and dashboards that reference this
        service by URL, and checks explicit item relationships.

        Args:
            service_item_id: The item ID of the service to scan.

        Returns:
            Dict listing all items that depend on this service.
        """
        try:
            # Get the service item details to find its URL
            item_info = self._sharing_request(
                f"/content/items/{service_item_id}"
            )
            if not item_info or "error" in item_info:
                error_msg = (item_info.get("error", "Item not found")
                             if item_info else "No response")
                return {"error": error_msg}

            service_url = item_info.get("url", "")
            service_title = item_info.get("title", "")
            service_type = item_info.get("type", "")

            depended_on_by = []
            seen_ids: set[str] = set()

            # Strategy 1: Search for items referencing this service URL.
            # Extract a searchable portion of the URL (last meaningful segment).
            if service_url:
                url_parts = service_url.rstrip("/").split("/")
                search_term = url_parts[-1] if url_parts else service_url

                search_types = [
                    "Web Map",
                    "Web Mapping Application",
                    "Dashboard",
                    "Web Experience",
                ]
                for item_type in search_types:
                    results = self.search_items(
                        query=f'"{search_term}"',
                        item_type=item_type,
                        max_items=100,
                    )
                    for item in results:
                        item_id = item.get("id", "")
                        if item_id in seen_ids:
                            continue
                        # Verify by checking item data for the service URL
                        item_data = self._sharing_request(
                            f"/content/items/{item_id}/data"
                        )
                        data_str = str(item_data) if item_data else ""
                        if service_url in data_str:
                            seen_ids.add(item_id)
                            depended_on_by.append({
                                "item_id": item_id,
                                "title": item.get("title", ""),
                                "type": item.get("type", ""),
                                "match_method": "url_in_data",
                            })

            # Strategy 2: Check explicit item relationships
            related = self._sharing_request(
                f"/content/items/{service_item_id}/relatedItems"
            )
            if related and "relatedItems" in related:
                for rel in related["relatedItems"]:
                    rel_id = rel.get("id", "")
                    if rel_id not in seen_ids:
                        seen_ids.add(rel_id)
                        depended_on_by.append({
                            "item_id": rel_id,
                            "title": rel.get("title", ""),
                            "type": rel.get("type", ""),
                            "match_method": "related_item",
                        })

            return {
                "service_id": service_item_id,
                "title": service_title,
                "type": service_type,
                "url": service_url,
                "depended_on_by": depended_on_by,
                "dependency_count": len(depended_on_by),
            }

        except Exception as e:
            return {"error": str(e)}

    # ----- Tool 4: Item Impact Analysis -----

    def analyze_item_impact(self, item_id: str) -> dict[str, Any]:
        """Assess the impact of deleting or modifying a portal item.

        Combines relationship exploration, dependency scanning, and sharing
        analysis to produce a blast radius assessment.

        Args:
            item_id: The ID of the item to analyze.

        Returns:
            Dict with impact assessment including blast radius and recommendation.
        """
        try:
            # Get base item details
            item_info = self._sharing_request(f"/content/items/{item_id}")
            if not item_info or "error" in item_info:
                error_msg = (item_info.get("error", "Item not found")
                             if item_info else "No response")
                return {"error": error_msg}

            # Get relationships (forward + reverse)
            relationships = self.explore_item_relationships(item_id)
            direct_deps = relationships.get("relationships", [])

            # Get dependencies (who uses this as a data source)
            scan = self.scan_service_dependencies(item_id)
            reverse_deps = scan.get("depended_on_by", [])

            # Get group sharing info
            sharing = self._sharing_request(
                f"/content/items/{item_id}/share"
            )
            shared_groups = []
            if sharing and "groups" in sharing:
                shared_groups = [
                    {"group_id": g.get("id", ""), "title": g.get("title", "")}
                    for g in sharing["groups"]
                ]

            # Compute blast radius based on downstream impact count
            total_reverse = len(reverse_deps) + len([
                r for r in direct_deps if r.get("direction") == "reverse"
            ])

            if total_reverse == 0 and len(shared_groups) == 0:
                blast_radius = "low"
                recommendation = (
                    "Safe to delete. No items depend on this "
                    "and it is not shared with any group."
                )
            elif total_reverse == 0 and len(shared_groups) > 0:
                blast_radius = "low"
                recommendation = (
                    f"Shared with {len(shared_groups)} group(s) but no "
                    "items depend on it. Removing will revoke group access."
                )
            elif total_reverse <= 3:
                blast_radius = "medium"
                recommendation = (
                    f"{total_reverse} item(s) depend on this. Coordinate "
                    "with owners before removing."
                )
            elif total_reverse <= 10:
                blast_radius = "high"
                recommendation = (
                    f"{total_reverse} items depend on this. High risk of "
                    "breaking maps/apps. Requires migration plan."
                )
            else:
                blast_radius = "critical"
                recommendation = (
                    f"{total_reverse} items depend on this. Critical "
                    "infrastructure item. Do not remove without full "
                    "dependency resolution."
                )

            return {
                "item_id": item_id,
                "title": item_info.get("title", ""),
                "type": item_info.get("type", ""),
                "owner": item_info.get("owner", ""),
                "impact": {
                    "direct_dependencies": direct_deps,
                    "reverse_dependencies": reverse_deps,
                    "shared_with_groups": shared_groups,
                    "total_reverse_dependencies": total_reverse,
                    "blast_radius": blast_radius,
                    "recommendation": recommendation,
                },
            }

        except Exception as e:
            return {"error": str(e)}

    # ----- Tool 5: Usage Analytics (Enhanced) -----

    def get_usage_analytics(
        self,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> dict[str, Any]:
        """Get enhanced portal usage analytics with user and content breakdown.

        Extends the basic portal_usage with per-user activity ranking and
        content type distribution.

        Args:
            start_time: Epoch milliseconds or ISO string for period start.
                        Defaults to 30 days ago.
            end_time: Epoch milliseconds or ISO string for period end.
                      Defaults to now.

        Returns:
            Dict with portal stats, user activity, and content breakdown.
        """
        try:
            # Portal-wide usage stats via admin endpoint
            portal_stats = self.portal_usage(
                start_time=start_time, end_time=end_time
            )

            # Content breakdown by type
            content_breakdown: list[dict[str, Any]] = []
            search_types = [
                "Feature Service",
                "Map Service",
                "Image Service",
                "Web Map",
                "Web Mapping Application",
                "Dashboard",
                "CSV",
                "Shapefile",
                "File Geodatabase",
                "KML",
                "Scene Layer",
                "Web Experience",
            ]
            for item_type in search_types:
                # search_items with max_items=0 would still return items,
                # so we search with max_items=1 to get total count from results
                # Use _sharing_request to get the count directly.
                search_data = self._sharing_request(
                    "/search",
                    params={
                        "q": f'type:"{item_type}"',
                        "start": 1,
                        "num": 1,
                    },
                )
                count = search_data.get("total", 0) if search_data else 0
                if count > 0:
                    content_breakdown.append({
                        "type": item_type,
                        "count": count,
                    })

            # Sort by count descending
            content_breakdown.sort(key=lambda x: x["count"], reverse=True)

            # Top users by content ownership (sample from recent items)
            user_counts: dict[str, int] = {}
            recent = self.search_items(query="*", max_items=100)
            for item in recent:
                owner = item.get("owner", "")
                if owner:
                    user_counts[owner] = user_counts.get(owner, 0) + 1

            user_activity = sorted(
                [{"username": u, "item_count": c}
                 for u, c in user_counts.items()],
                key=lambda x: x["item_count"],
                reverse=True,
            )[:20]

            return {
                "period": {
                    "start": start_time or "30 days ago",
                    "end": end_time or "now",
                },
                "portal_stats": portal_stats,
                "user_activity": user_activity,
                "content_breakdown": content_breakdown,
                "total_content_types": len(content_breakdown),
            }

        except Exception as e:
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # v1.7.0: Webhooks, Logs, Org Settings, Folders
    # ------------------------------------------------------------------

    def list_webhooks(self) -> list[dict[str, Any]]:
        """List all organization webhooks."""
        data = self._sharing_request("/portals/self/webhooks")
        if not data or "error" in data:
            return []
        return data.get("webhooks", [])

    def create_webhook(
        self,
        name: str,
        hook_url: str,
        change_types: list[str] | None = None,
        payload_format: str = "json",
        secret: str = "",
        active: bool = True,
        tags: str = "",
    ) -> dict[str, Any]:
        """Create a new organization webhook."""
        params: dict[str, Any] = {
            "name": name,
            "hookUrl": hook_url,
            "payloadFormat": payload_format,
            "active": str(active).lower(),
        }
        if change_types:
            params["changeTypes"] = ",".join(change_types)
        if secret:
            params["secret"] = secret
        if tags:
            params["tags"] = tags
        return self._sharing_request(
            "/portals/self/webhooks/add", params=params, method="POST"
        ) or {"error": "Create webhook failed"}

    def update_webhook(
        self,
        webhook_id: int,
        name: str = "",
        hook_url: str = "",
        change_types: list[str] | None = None,
        payload_format: str = "",
        active: bool | None = None,
        tags: str = "",
    ) -> dict[str, Any]:
        """Update an existing webhook. Only provided params are changed."""
        params: dict[str, Any] = {"id": webhook_id}
        if name:
            params["name"] = name
        if hook_url:
            params["hookUrl"] = hook_url
        if change_types:
            params["changeTypes"] = ",".join(change_types)
        if payload_format:
            params["payloadFormat"] = payload_format
        if active is not None:
            params["active"] = str(active).lower()
        if tags:
            params["tags"] = tags
        return self._sharing_request(
            f"/portals/self/webhooks/{webhook_id}/update",
            params=params, method="POST",
        ) or {"error": f"Update webhook {webhook_id} failed"}

    def delete_webhook(self, webhook_id: int) -> dict[str, Any]:
        """Delete a webhook by ID."""
        return self._sharing_request(
            f"/portals/self/webhooks/{webhook_id}/delete",
            params={}, method="POST",
        ) or {"error": f"Delete webhook {webhook_id} failed"}

    def test_webhook(self, webhook_id: int) -> dict[str, Any]:
        """Send a test payload to a webhook."""
        return self._sharing_request(
            f"/portals/self/webhooks/{webhook_id}/test",
            params={}, method="POST",
        ) or {"error": f"Test webhook {webhook_id} failed"}

    def query_logs(
        self,
        level: str = "WARNING",
        source: str = "",
        start_time: str = "",
        end_time: str = "",
        max_records: int = 100,
    ) -> dict[str, Any]:
        """Query portal logs. Requires admin privileges."""
        params: dict[str, Any] = {
            "level": level,
            "num": str(min(max_records, 1000)),
        }
        if source:
            params["source"] = source
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        return self.admin_request("/logs/query", params=params) or {
            "error": "Log query failed"
        }

    def clean_logs(self, start_time: str = "") -> dict[str, Any]:
        """Delete portal logs older than start_time."""
        params: dict[str, Any] = {}
        if start_time:
            params["startTime"] = start_time
        return self.admin_request(
            "/logs/clean", params=params, method="POST"
        ) or {"error": "Log cleanup failed"}

    def get_org_settings(self) -> dict[str, Any]:
        """Get organization settings."""
        return self._sharing_request("/portals/self/settings") or {
            "error": "Failed to get org settings"
        }

    def update_org_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        """Update organization settings."""
        params: dict[str, Any] = {"settings": json.dumps(settings)}
        return self._sharing_request(
            "/portals/self/settings/update", params=params, method="POST"
        ) or {"error": "Failed to update org settings"}

    def create_folder(self, title: str, owner: str | None = None) -> dict[str, Any]:
        """Create a content folder."""
        user = owner or self.username
        if not user:
            return {"error": "No owner specified and not connected"}
        return self._sharing_request(
            f"/content/users/{user}/createFolder",
            params={"title": title}, method="POST",
        ) or {"error": f"Create folder '{title}' failed"}

    def list_folders(self, owner: str | None = None) -> list[dict[str, Any]]:
        """List content folders for a user."""
        user = owner or self.username
        if not user:
            return []
        data = self._sharing_request(f"/content/users/{user}")
        if not data or "error" in data:
            return []
        return data.get("folders", [])

    # ------------------------------------------------------------------
    # Collaborations (v1.8.0)
    # ------------------------------------------------------------------

    def list_collaborations(self) -> list[dict[str, Any]]:
        """List all collaborations the portal participates in."""
        data = self._sharing_request("/portals/self/collaborations")
        if not data or "error" in data:
            return []
        return data.get("collaborations", [])

    def get_collaboration(self, collaboration_id: str) -> dict[str, Any]:
        """Get details of a specific collaboration."""
        data = self._sharing_request(
            f"/portals/self/collaborations/{collaboration_id}"
        )
        if not data:
            return {"error": f"Collaboration {collaboration_id} not found"}
        return data

    def sync_collaboration(
        self, collaboration_id: str, workspace_id: str
    ) -> dict[str, Any]:
        """Trigger sync for a collaboration workspace."""
        return self._sharing_request(
            f"/portals/self/collaborations/{collaboration_id}"
            f"/workspaces/{workspace_id}/sync",
            params={}, method="POST",
        ) or {"error": f"Sync failed for workspace {workspace_id}"}

    # ------------------------------------------------------------------
    # Roles & Privileges (v1.8.0)
    # ------------------------------------------------------------------

    def list_roles(self, return_privileges: bool = True) -> list[dict[str, Any]]:
        """List organization roles."""
        params: dict[str, Any] = {"num": 100}
        if return_privileges:
            params["returnPrivileges"] = "true"
        data = self._sharing_request("/portals/self/roles", params=params)
        if not data or "error" in data:
            return []
        return data.get("roles", [])

    def get_role_privileges(self, role_id: str) -> dict[str, Any]:
        """Get privileges for a specific role."""
        data = self._sharing_request(
            f"/portals/self/roles/{role_id}/privileges"
        )
        if not data:
            return {"error": f"Role {role_id} not found"}
        return data

    # ------------------------------------------------------------------
    # Scheduled Tasks (v1.8.0)
    # ------------------------------------------------------------------

    def list_scheduled_tasks(
        self,
        task_type: str = "",
        user_filter: str = "",
        active: bool | None = None,
    ) -> list[dict[str, Any]]:
        """List all scheduled tasks in the organization (admin only)."""
        params: dict[str, Any] = {}
        if task_type:
            params["types"] = task_type
        if user_filter:
            params["userFilter"] = user_filter
        if active is not None:
            params["active"] = str(active).lower()
        data = self._sharing_request(
            "/portals/self/allScheduledTasks", params=params
        )
        if not data or "error" in data:
            return []
        return data.get("tasks", [])

    def get_user_scheduled_tasks(self, username: str) -> list[dict[str, Any]]:
        """List scheduled tasks for a specific user."""
        data = self._sharing_request(
            f"/community/users/{username}/tasks"
        )
        if not data or "error" in data:
            return []
        return data.get("tasks", [])

    # ------------------------------------------------------------------
    # v1.11.0: Admin Problem Solvers
    # ------------------------------------------------------------------

    # --- Helpers ---

    def _extract_urls_from_item_data(
        self,
        data: dict[str, Any],
        check_layers: bool = True,
        check_basemap: bool = True,
    ) -> list[dict[str, Any]]:
        """Extract all service URLs from web map/app JSON.

        Walks the standard ArcGIS web map structure and collects every
        service URL with its location in the JSON hierarchy.

        Args:
            data: Parsed JSON from get_item_data (web map definition).
            check_layers: Include operationalLayers URLs.
            check_basemap: Include baseMap and reference layer URLs.

        Returns:
            List of dicts, each with "url" and "referenced_by" path.
        """
        urls: list[dict[str, Any]] = []

        def _walk_layers(
            layers: list[dict[str, Any]],
            prefix: str,
        ) -> None:
            """Recursively walk layers, including nested folder groups."""
            for i, layer in enumerate(layers):
                url = layer.get("url", "")
                if url:
                    urls.append({
                        "url": url.rstrip("/"),
                        "referenced_by": f"{prefix}[{i}]",
                    })
                for sub_key in ("layers", "featureCollection"):
                    sub = layer.get(sub_key)
                    if isinstance(sub, list):
                        _walk_layers(sub, f"{prefix}[{i}].{sub_key}")
                    elif isinstance(sub, dict):
                        nested = sub.get("layers", [])
                        if isinstance(nested, list):
                            _walk_layers(
                                nested,
                                f"{prefix}[{i}].{sub_key}.layers",
                            )

        if check_layers:
            operational = data.get("operationalLayers", [])
            if isinstance(operational, list):
                _walk_layers(operational, "operationalLayers")

        if check_basemap:
            basemap = data.get("baseMap", {})
            if isinstance(basemap, dict):
                bm_layers = basemap.get("baseMapLayers", [])
                if isinstance(bm_layers, list):
                    _walk_layers(bm_layers, "baseMap.baseMapLayers")
                ref_layers = basemap.get("referenceLayers", [])
                if isinstance(ref_layers, list):
                    _walk_layers(ref_layers, "baseMap.referenceLayers")

        tables = data.get("tables", [])
        if isinstance(tables, list):
            for i, tbl in enumerate(tables):
                url = tbl.get("url", "")
                if url:
                    urls.append({
                        "url": url.rstrip("/"),
                        "referenced_by": f"tables[{i}]",
                    })

        return urls

    def _ping_url(self, url: str, timeout: int = 5) -> dict[str, Any]:
        """Check if a service URL is reachable via HTTP GET.

        Args:
            url: The service URL to check.
            timeout: Request timeout in seconds.

        Returns:
            Dict with url, status, http_status, latency_ms, error.
        """
        import time
        start = time.monotonic()
        try:
            resp = self._session.get(
                url, params={"f": "json"}, timeout=timeout,
                allow_redirects=True,
            )
            latency = int((time.monotonic() - start) * 1000)
            status = "healthy" if resp.status_code < 400 else "unreachable"
            result: dict[str, Any] = {
                "url": url, "status": status,
                "http_status": resp.status_code, "latency_ms": latency,
            }
            if status == "unreachable":
                try:
                    err_data = resp.json()
                    result["error"] = err_data.get(
                        "error", {}
                    ).get("message", resp.reason)
                except Exception:
                    result["error"] = resp.reason or f"HTTP {resp.status_code}"
            return result
        except requests.exceptions.Timeout:
            latency = int((time.monotonic() - start) * 1000)
            return {
                "url": url, "status": "timeout", "http_status": 0,
                "latency_ms": latency, "error": f"No response within {timeout}s",
            }
        except requests.exceptions.ConnectionError as e:
            latency = int((time.monotonic() - start) * 1000)
            return {
                "url": url, "status": "error", "http_status": 0,
                "latency_ms": latency, "error": str(e)[:200],
            }
        except Exception as e:
            latency = int((time.monotonic() - start) * 1000)
            return {
                "url": url, "status": "error", "http_status": 0,
                "latency_ms": latency, "error": str(e)[:200],
            }

    @staticmethod
    def _classify_staleness(
        item: dict[str, Any],
        days_threshold: int,
        min_views: int,
    ) -> dict[str, Any]:
        """Classify a single item for staleness and governance.

        Args:
            item: Item dict (needs id, title, type, owner, modified,
                  num_views, size, access, snippet, tags, description).
            days_threshold: Items older than this are stale.
            min_views: Items with fewer views are stale.

        Returns:
            Dict with staleness classification and governance info.
        """
        import time
        now_ms = time.time() * 1000
        modified_str = item.get("modified", "")
        days_stale = 0
        if modified_str:
            try:
                from datetime import datetime
                dt = datetime.strptime(modified_str, "%Y-%m-%d %H:%M")
                modified_ms = dt.timestamp() * 1000
                days_stale = int((now_ms - modified_ms) / 86_400_000)
            except (ValueError, OSError):
                days_stale = 0

        num_views = item.get("num_views", 0)
        is_stale = days_stale > days_threshold or num_views < min_views

        has_description = bool(item.get("description", ""))
        has_tags = bool(item.get("tags", []))
        has_snippet = bool(item.get("snippet", ""))
        access = item.get("access", "private")

        issues: list[str] = []
        if not has_description:
            issues.append("missing_description")
        if not has_tags:
            issues.append("missing_tags")
        if not has_snippet:
            issues.append("missing_snippet")
        non_compliant = len(issues) > 0

        if not is_stale:
            recommendation = "Active (within threshold)"
        elif access == "private" and num_views == 0 and days_stale > days_threshold * 2:
            recommendation = "Delete (private, zero views, very old)"
        elif access == "private" and num_views == 0:
            recommendation = "Delete (private, zero views)"
        elif access == "org" and num_views < 5 and days_stale > days_threshold:
            recommendation = "Archive (org-shared, low views)"
        else:
            recommendation = "Review (meets staleness criteria)"

        return {
            "item_id": item.get("id", ""),
            "title": item.get("title", ""),
            "type": item.get("type", ""),
            "owner": item.get("owner", ""),
            "modified": modified_str,
            "days_stale": days_stale,
            "num_views": num_views,
            "size_mb": round(item.get("size", 0) / 1_048_576, 1),
            "access": access,
            "is_stale": is_stale,
            "governance": {
                "has_description": has_description,
                "has_tags": has_tags,
                "has_snippet": has_snippet,
                "non_compliant": non_compliant,
                "issues": issues,
            },
            "recommendation": recommendation,
        }

    def _get_items_in_group(self, group_id: str) -> list[dict[str, Any]]:
        """Get all items shared with a group."""
        data = self._sharing_request(
            f"/content/groups/{group_id}",
            params={"num": 100},
        )
        if not data or "error" in data:
            return []
        return data.get("items", [])

    # --- Tool: scan_broken_references ---

    def scan_broken_references(
        self,
        target_id: str,
        target_type: str = "item",
        timeout: int = 5,
        check_layers: bool = True,
        check_basemap: bool = True,
    ) -> dict[str, Any]:
        """Scan item or group for broken service URL references.

        For a single item: reads its JSON data, extracts service URLs,
        requests each to check reachability.
        For a group: iterates all scannable items and checks each one.

        Returns:
            Dict with scan results including healthy/broken URL counts.
        """
        try:
            if target_type == "group":
                return self._scan_broken_references_group(
                    target_id, timeout, check_layers, check_basemap,
                )
            return self._scan_broken_references_item(
                target_id, timeout, check_layers, check_basemap,
            )
        except Exception as e:
            return {"error": str(e)}

    def _scan_broken_references_item(
        self, item_id: str, timeout: int,
        check_layers: bool, check_basemap: bool,
    ) -> dict[str, Any]:
        """Scan a single item for broken service references."""
        item_info = self.get_item_details(item_id)
        if not item_info or "error" in item_info:
            error_msg = (
                item_info.get("error", "Item not found")
                if item_info else "No response"
            )
            return {"error": error_msg}

        item_data = self.get_item_data(item_id)
        if not item_data or "error" in item_data:
            return {"error": f"Could not read item data for {item_id}"}

        url_entries = self._extract_urls_from_item_data(
            item_data, check_layers=check_layers, check_basemap=check_basemap,
        )

        # De-duplicate
        seen_urls: set[str] = set()
        unique_entries: list[dict[str, Any]] = []
        for entry in url_entries:
            if entry["url"] not in seen_urls:
                seen_urls.add(entry["url"])
                unique_entries.append(entry)

        url_results: list[dict[str, Any]] = []
        for entry in unique_entries:
            result = self._ping_url(entry["url"], timeout=timeout)
            result["referenced_by"] = entry["referenced_by"]
            url_results.append(result)

        healthy = sum(1 for r in url_results if r["status"] == "healthy")
        broken = sum(1 for r in url_results if r["status"] != "healthy")

        return {
            "target_id": item_id, "target_type": "item",
            "item_title": item_info.get("title", ""),
            "item_type": item_info.get("type", ""),
            "total_urls": len(url_results),
            "healthy": healthy, "broken": broken,
            "urls": url_results,
        }

    def _scan_broken_references_group(
        self, group_id: str, timeout: int,
        check_layers: bool, check_basemap: bool,
    ) -> dict[str, Any]:
        """Scan all items in a group for broken service references."""
        group_info = self._sharing_request(f"/content/groups/{group_id}")
        if not group_info or "error" in group_info:
            error_msg = (
                group_info.get("error", "Group not found")
                if group_info else "No response"
            )
            return {"error": error_msg}

        items = self._get_items_in_group(group_id)
        scannable_types = {
            "Web Map", "Web Mapping Application", "Dashboard",
            "Web Experience", "StoryMap", "Instant App",
        }

        all_results: list[dict[str, Any]] = []
        total_healthy = 0
        total_broken = 0
        broken_items_summary: list[dict[str, Any]] = []

        for item in items:
            item_type = item.get("type", "")
            if item_type not in scannable_types:
                continue
            item_id = item.get("id", "")
            scan = self._scan_broken_references_item(
                item_id, timeout, check_layers, check_basemap,
            )
            if "error" in scan:
                continue
            total_healthy += scan.get("healthy", 0)
            total_broken += scan.get("broken", 0)
            all_results.append(scan)
            if scan.get("broken", 0) > 0:
                broken_items_summary.append({
                    "item_id": item_id,
                    "title": scan.get("item_title", ""),
                    "type": item_type,
                    "broken_count": scan.get("broken", 0),
                })

        return {
            "target_id": group_id, "target_type": "group",
            "group_title": group_info.get("title", ""),
            "items_scanned": len(all_results),
            "total_urls": total_healthy + total_broken,
            "healthy": total_healthy, "broken": total_broken,
            "broken_items_summary": broken_items_summary,
            "items": all_results,
        }

    # --- Tool: find_stale_items ---

    def find_stale_items(
        self, owner: str = "", days_threshold: int = 180,
        min_views: int = 0, item_types: str = "",
        include_storage: bool = True, max_items: int = 200,
    ) -> dict[str, Any]:
        """Find items that have not been accessed or modified in a while.

        Scans portal content for stale items based on modification date,
        view count, and governance compliance.

        Returns:
            Dict with stale items, summary stats, governance violations.
        """
        try:
            types_list = [
                t.strip() for t in item_types.split(",") if t.strip()
            ] if item_types else []

            all_items: list[dict[str, Any]] = []
            if types_list:
                per_type = max(max_items // len(types_list), 20)
                for t in types_list:
                    results = self.search_items(
                        query="*", item_type=t,
                        owner=owner or None, max_items=per_type,
                    )
                    all_items.extend(results)
            else:
                all_items = self.search_items(
                    query="*", owner=owner or None, max_items=max_items,
                )

            stale_items: list[dict[str, Any]] = []
            governance_violations: list[dict[str, Any]] = []
            type_counts: dict[str, int] = {}
            total_stale_storage = 0.0

            for item in all_items[:max_items]:
                details = self.get_item_details(item.get("id", ""))
                if not details or "error" in details:
                    continue
                merged = {
                    **item,
                    "access": details.get("access", item.get("access", "private")),
                    "description": details.get("description", ""),
                    "snippet": details.get("snippet", ""),
                    "tags": details.get("tags", []),
                    "num_views": details.get("numViews", item.get("num_views", 0)),
                    "size": details.get("size", item.get("size", 0)),
                    "modified": _epoch_to_str(details.get("modified")),
                }
                classification = self._classify_staleness(
                    merged, days_threshold, min_views,
                )
                item_type = merged.get("type", "Unknown")
                type_counts[item_type] = type_counts.get(item_type, 0) + 1
                if classification["is_stale"]:
                    stale_items.append(classification)
                    total_stale_storage += classification["size_mb"]
                if classification["governance"]["non_compliant"]:
                    governance_violations.append({
                        "item_id": classification["item_id"],
                        "title": classification["title"],
                        "type": item_type,
                        "owner": classification["owner"],
                        "access": classification["access"],
                        "issues": classification["governance"]["issues"],
                    })

            stale_items.sort(key=lambda x: x["days_stale"], reverse=True)
            by_type = dict(
                sorted(type_counts.items(), key=lambda x: x[1], reverse=True)
            )
            return {
                "scan_params": {
                    "owner": owner or "(all)",
                    "days_threshold": days_threshold,
                    "min_views": min_views,
                    "item_types": item_types or "(all)",
                },
                "summary": {
                    "total_scanned": len(all_items[:max_items]),
                    "stale_count": len(stale_items),
                    "total_stale_storage_mb": round(total_stale_storage, 1),
                    "governance_violations": len(governance_violations),
                    "by_type": by_type,
                },
                "stale_items": stale_items,
                "governance_violations": governance_violations,
            }
        except Exception as e:
            return {"error": str(e)}

    # --- Tool: export_group_content ---

    def export_group_content(
        self, group_id: str,
        items: list[str] | None = None, title: str = "",
    ) -> dict[str, Any]:
        """Export a group content to an export package (.epk).

        Creates a downloadable package containing the group items and their
        data. Can later be imported via import_group_content.

        Returns:
            Dict with export package info including download URL.
        """
        try:
            group_info = self._sharing_request(f"/content/groups/{group_id}")
            if not group_info or "error" in group_info:
                error_msg = (
                    group_info.get("error", "Group not found")
                    if group_info else "No response"
                )
                return {"error": error_msg}

            params: dict[str, Any] = {}
            if title:
                params["title"] = title
            if items:
                params["items"] = json.dumps(
                    [{"id": item_id} for item_id in items]
                )

            result = self._sharing_request(
                f"/content/groups/{group_id}/export",
                params=params, method="POST",
            )
            if not result or "error" in result:
                error_msg = (
                    result.get("error", "Export failed")
                    if result else "No response from export"
                )
                return {"error": error_msg}

            exported_items = []
            if items:
                for item_id in items:
                    details = self.get_item_details(item_id)
                    if details and "error" not in details:
                        exported_items.append({
                            "item_id": item_id,
                            "title": details.get("title", ""),
                            "type": details.get("type", ""),
                        })
            else:
                group_items = self._get_items_in_group(group_id)
                for item in group_items:
                    exported_items.append({
                        "item_id": item.get("id", ""),
                        "title": item.get("title", ""),
                        "type": item.get("type", ""),
                    })

            return {
                "group_id": group_id,
                "group_title": group_info.get("title", ""),
                "export_package": {
                    "item_id": result.get("itemId", ""),
                    "title": result.get("title", title or "Group Export"),
                    "download_url": result.get("downloadUrl", ""),
                    "item_count": len(exported_items),
                },
                "exported_items": exported_items,
            }
        except Exception as e:
            return {"error": str(e)}

    # --- Tool: import_group_content ---

    def import_group_content(
        self, group_id: str, import_url: str = "",
        item_id: str = "", owner: str = "", title_prefix: str = "",
    ) -> dict[str, Any]:
        """Import content from an export package into a group.

        Imports items from a .epk export package (created by
        export_group_content) into the specified target group.

        Returns:
            Dict with import results including per-item success/failure.
        """
        try:
            group_info = self._sharing_request(f"/content/groups/{group_id}")
            if not group_info or "error" in group_info:
                error_msg = (
                    group_info.get("error", "Target group not found")
                    if group_info else "No response"
                )
                return {"error": error_msg}

            if not import_url and not item_id:
                return {"error": "Either import_url or item_id is required"}

            params: dict[str, Any] = {}
            if import_url:
                params["importUrl"] = import_url
            if item_id:
                params["itemId"] = item_id
            if owner:
                params["owner"] = owner
            if title_prefix:
                params["titlePrefix"] = title_prefix

            result = self._sharing_request(
                f"/content/groups/{group_id}/import",
                params=params, method="POST",
            )
            if not result or "error" in result:
                error_msg = (
                    result.get("error", "Import failed")
                    if result else "No response from import"
                )
                return {"error": error_msg}

            imported = result.get("importedItems", [])
            succeeded = [
                {
                    "original_id": item.get("sourceItemId", ""),
                    "new_id": item.get("itemId", ""),
                    "title": item.get("title", ""),
                    "type": item.get("type", ""),
                    "status": "ok",
                }
                for item in imported
                if not item.get("error")
            ]
            failures = [
                {
                    "original_id": item.get("sourceItemId", ""),
                    "title": item.get("title", ""),
                    "error": item.get("error", "Unknown error"),
                }
                for item in imported
                if item.get("error")
            ]

            return {
                "target_group_id": group_id,
                "target_group_title": group_info.get("title", ""),
                "import_results": {
                    "total": len(imported),
                    "succeeded": len(succeeded),
                    "failed": len(failures),
                    "imported_items": succeeded,
                    "failures": failures,
                },
            }
        except Exception as e:
            return {"error": str(e)}


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
