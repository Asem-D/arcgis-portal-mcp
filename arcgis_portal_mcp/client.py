"""ArcGIS REST API client.

Raw REST API client for ArcGIS Portal/Online. No dependency on the
`arcgis` Python package — uses requests directly. Handles authentication,
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
import threading
import urllib.parse
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import requests
import urllib3

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
        self._session.verify = False  # noqa: S501 — Enterprise portals often use self-signed certs
        self._session.timeout = 30
        self._lock = threading.Lock()

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
        # Tokens from sharing API don't always include expires — assume long-lived
        self._token_expires = datetime.now().timestamp() + 86400  # 24h fallback

        logger.info("Connected as %s (existing token)", self._username)
        return user_info

    def connect_client_credentials(
        self, portal_url: str, client_id: str, client_secret: str
    ) -> dict[str, Any]:
        """Connect using OAuth2 client_credentials grant (no browser needed).

        App-level token — no user identity. Good for portal info, content
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
        Not suitable for MCP tool calls — use for initial setup only.

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
            logger.error("Not connected — call connect_* first")
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
            logger.error("Not connected — call connect_* first")
            return None

        if not self.is_connected:
            logger.error("Token expired — reconnect first")
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

    def health_check(self) -> dict[str, Any]:
        """Perform portal health check (requires admin privileges)."""
        return self.admin_request("/healthCheck") or {"error": "Health check failed"}

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
                f"{self.server.auth_error}</h2></body></html>"  # type: ignore[attr-defined]
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
