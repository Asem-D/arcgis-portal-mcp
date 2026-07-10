"""Tests for arcgis-portal-mcp."""

from unittest.mock import MagicMock, patch

import pytest

from arcgis_portal_mcp import __version__
from arcgis_portal_mcp.client import (
    ArcGISClient,
    _epoch_to_str,
    _truncate,
)
from arcgis_portal_mcp.server import _validate_where_clause, mcp

# ------------------------------------------------------------------
# Version, client init, connection basics
# ------------------------------------------------------------------


def test_version():
    """Version should match pyproject.toml."""
    assert __version__ == "1.2.0"


def test_client_init():
    """Client should initialize with sensible defaults."""
    client = ArcGISClient()
    assert client.is_connected is False
    assert client.token is None
    assert client.username is None
    assert client.portal_url is None
    assert client.sharing_url is None


def test_client_connect_bad_token():
    """Connecting with a bad token should raise ConnectionError."""
    client = ArcGISClient()
    # Mock the sharing request to simulate a rejected token
    with patch.object(client, "_sharing_request", return_value={"error": "Invalid token"}):
        with pytest.raises(ConnectionError, match="Token validation failed"):
            client.connect_token("https://example.com/portal", "bad-token-12345")


# ------------------------------------------------------------------
# Server tool/resource counts (must stay in sync with README)
# ------------------------------------------------------------------


def test_server_tools_count():
    """Server should expose exactly 34 tools (as documented in README)."""
    tool_names = mcp._tool_manager._tools.keys()
    assert len(list(tool_names)) == 34


def test_server_resource_count():
    """Server should expose exactly 1 resource."""
    resource_names = mcp._resource_manager._resources.keys()
    assert len(list(resource_names)) == 1


# ------------------------------------------------------------------
# WHERE clause validation (SQL injection protection)
# ------------------------------------------------------------------


def test_where_clause_rejects_injection():
    """WHERE clause with injection patterns should be rejected."""
    bad_clauses = [
        "1=1; DROP TABLE parcels",
        "x = 1 -- comment",
        "x = 1 /* comment */",
        "x = 1; DELETE FROM users",
        "x = 1; UPDATE users SET role='admin'",
        "x = 1; TRUNCATE logs",
        "x = 1; INSERT INTO logs VALUES (1)",
        "x = 1; ALTER TABLE users ADD admin INT",
        "x = 1; CREATE TABLE hack (id INT)",
        "x = 1; EXEC xp_cmdshell('dir')",
    ]
    for clause in bad_clauses:
        assert _validate_where_clause(clause) is not None, f"Should reject: {clause}"


def test_where_clause_allows_safe():
    """Simple WHERE clauses should be allowed."""
    safe_clauses = [
        "",
        "1=1",
        "STATUS = 'Active'",
        "POPULATION > 1000",
        "NAME LIKE '%Central%'",
        "TYPE IN ('Park', 'School')",
        "AREA >= 500 AND TYPE = 'Commercial'",
    ]
    for clause in safe_clauses:
        assert _validate_where_clause(clause) is None, f"Should allow: {clause}"


# ------------------------------------------------------------------
# Client helpers: _epoch_to_str, _truncate
# ------------------------------------------------------------------


def test_epoch_to_str_normal():
    """Epoch milliseconds should convert to readable date string."""
    # 2024-01-15 12:00:00 UTC = 1705317600000 ms
    result = _epoch_to_str(1705317600000)
    assert "2024" in result
    assert ":" in result  # Contains time separator


def test_epoch_to_str_none():
    """None should return empty string."""
    assert _epoch_to_str(None) == ""


def test_epoch_to_str_zero():
    """Zero should return empty string (falsy)."""
    assert _epoch_to_str(0) == ""


def test_epoch_to_str_invalid():
    """Invalid value should return string representation."""
    result = _epoch_to_str(-1)
    assert isinstance(result, str)


def test_truncate_short():
    """Short text should not be truncated."""
    assert _truncate("hello", 10) == "hello"


def test_truncate_exact():
    """Text at max length should not be truncated."""
    assert _truncate("hello", 5) == "hello"


def test_truncate_long():
    """Long text should be truncated with ellipsis."""
    result = _truncate("hello world", 5)
    assert result == "hello..."
    assert len(result) == 8  # 5 chars + "..."


def test_truncate_none():
    """None should return empty string."""
    assert _truncate(None, 10) == ""


def test_truncate_empty():
    """Empty string should return empty string."""
    assert _truncate("", 10) == ""


# ------------------------------------------------------------------
# v1.2.0 tools: server_status (works without connection)
# ------------------------------------------------------------------


def test_server_status_unconnected():
    """server_status should return version and connected=False when not connected."""
    from arcgis_portal_mcp.server import server_status

    result = server_status()
    assert result["status"] == "ok"
    assert result["version"] == __version__
    assert result["connected"] is False
    assert result["portal_url"] is None
    assert result["username"] is None


# ------------------------------------------------------------------
# v1.2.0 tools: describe_layer, get_gp_task_info (require connection)
# ------------------------------------------------------------------


def test_describe_layer_not_connected():
    """describe_layer should return error when not connected."""
    from arcgis_portal_mcp.server import describe_layer

    result = describe_layer("https://example.com/FeatureServer", 0)
    assert result["status"] == "error"
    assert "Not connected" in result["error"]


def test_get_gp_task_info_not_connected():
    """get_gp_task_info should return error when not connected."""
    from arcgis_portal_mcp.server import get_gp_task_info

    result = get_gp_task_info("https://example.com/GPServer")
    assert result["status"] == "error"
    assert "Not connected" in result["error"]


# ------------------------------------------------------------------
# v1.2.0 tools: batch operations (require connection)
# ------------------------------------------------------------------


def test_batch_delete_items_not_connected():
    """batch_delete_items should return error when not connected."""
    from arcgis_portal_mcp.server import batch_delete_items

    result = batch_delete_items("abc,def")
    assert result["status"] == "error"
    assert "Not connected" in result["error"]


def test_batch_share_items_not_connected():
    """batch_share_items should return error when not connected."""
    from arcgis_portal_mcp.server import batch_share_items

    result = batch_share_items("abc,def")
    assert result["status"] == "error"
    assert "Not connected" in result["error"]


def test_batch_update_items_not_connected():
    """batch_update_items should return error when not connected."""
    from arcgis_portal_mcp.server import batch_update_items

    result = batch_update_items("abc,def")
    assert result["status"] == "error"
    assert "Not connected" in result["error"]


# ------------------------------------------------------------------
# v1.2.0 tools: export_map_image, get_item_data (require connection)
# ------------------------------------------------------------------


def test_export_map_image_not_connected():
    """export_map_image should return error when not connected."""
    from arcgis_portal_mcp.server import export_map_image

    result = export_map_image("https://example.com/MapServer")
    assert result["status"] == "error"
    assert "Not connected" in result["error"]


def test_get_item_data_not_connected():
    """get_item_data should return error when not connected."""
    from arcgis_portal_mcp.server import get_item_data

    result = get_item_data("some-item-id")
    assert result["status"] == "error"
    assert "Not connected" in result["error"]


def test_export_map_image_rejects_bad_where():
    """export_map_image should reject WHERE clauses with SQL injection."""
    from arcgis_portal_mcp.server import export_map_image

    # Patch _require_connected to return a mock client
    with patch("arcgis_portal_mcp.server._require_connected") as mock_req:
        mock_client = MagicMock()
        mock_req.return_value = mock_client
        result = export_map_image(
            "https://example.com/MapServer",
            where="1=1; DROP TABLE parcels",
        )
        assert result["status"] == "error"
        assert "dangerous SQL" in result["error"]
        mock_client.export_map_image.assert_not_called()


def test_add_features_invalid_json():
    """add_features should return error for invalid JSON."""
    from arcgis_portal_mcp.server import add_features

    with patch("arcgis_portal_mcp.server._require_connected") as mock_req:
        mock_req.return_value = MagicMock()
        result = add_features("https://example.com/FeatureServer", 0, "not json!")
        assert result["status"] == "error"
        assert "Invalid JSON" in result["error"]


def test_update_features_invalid_json():
    """update_features should return error for invalid JSON."""
    from arcgis_portal_mcp.server import update_features

    with patch("arcgis_portal_mcp.server._require_connected") as mock_req:
        mock_req.return_value = MagicMock()
        result = update_features("https://example.com/FeatureServer", 0, "not json!")
        assert result["status"] == "error"
        assert "Invalid JSON" in result["error"]


# ------------------------------------------------------------------
# Client: _parse_gp_task helper
# ------------------------------------------------------------------


def test_parse_gp_task_basic():
    """_parse_gp_task should extract parameters and metadata."""
    client = ArcGISClient()
    data = {
        "name": "BufferAnalysis",
        "displayName": "Buffer Analysis",
        "description": "Buffers input features",
        "helpUrl": "https://example.com/help",
        "executionType": "esriExecutionTypeSynchronous",
        "category": "Analysis",
        "parameters": [
            {
                "name": "input",
                "displayName": "Input Features",
                "dataType": "GPFeatureLayerRecordSet",
                "direction": "esriGPParameterDirectionInput",
                "defaultValue": None,
                "parameterType": "esriGPParameterTypeRequired",
                "category": "",
            },
            {
                "name": "distance",
                "displayName": "Distance",
                "dataType": "GPLinearUnit",
                "direction": "esriGPParameterDirectionInput",
                "defaultValue": {
                    "distance": 100,
                    "units": "Meters",
                },
                "parameterType": "esriGPParameterTypeOptional",
                "category": "",
            },
        ],
    }
    result = client._parse_gp_task(data, "https://example.com/GPServer/BufferAnalysis")

    assert result["name"] == "BufferAnalysis"
    assert result["display_name"] == "Buffer Analysis"
    assert result["description"] == "Buffers input features"
    assert result["help_url"] == "https://example.com/help"
    assert result["execution_type"] == "esriExecutionTypeSynchronous"
    assert result["category"] == "Analysis"
    assert len(result["parameters"]) == 2

    # Check first parameter
    p1 = result["parameters"][0]
    assert p1["name"] == "input"
    assert p1["display_name"] == "Input Features"
    assert p1["data_type"] == "GPFeatureLayerRecordSet"
    assert p1["direction"] == "esriGPParameterDirectionInput"
    assert p1["default_value"] is None
    assert p1["parameter_type"] == "esriGPParameterTypeRequired"

    # Check second parameter
    p2 = result["parameters"][1]
    assert p2["name"] == "distance"
    assert p2["default_value"] == {"distance": 100, "units": "Meters"}
    assert p2["parameter_type"] == "esriGPParameterTypeOptional"


def test_parse_gp_task_empty():
    """_parse_gp_task should handle empty input gracefully."""
    client = ArcGISClient()
    result = client._parse_gp_task({}, "https://example.com/GPServer/Task")
    assert result["name"] == ""
    assert result["display_name"] == ""
    assert result["parameters"] == []


def test_parse_gp_task_no_params():
    """_parse_gp_task should handle task with no parameters field."""
    client = ArcGISClient()
    data = {
        "name": "SimpleTask",
        "displayName": "Simple Task",
    }
    result = client._parse_gp_task(data, "https://example.com/GPServer/SimpleTask")
    assert result["name"] == "SimpleTask"
    assert result["parameters"] == []


# ------------------------------------------------------------------
# Client: batch operations (unit tests with mocked requests)
# ------------------------------------------------------------------


def test_batch_delete_items_result_structure():
    """batch_delete_items should return succeeded/failed with counts."""
    client = ArcGISClient()
    # Mock delete_item to return success for first, error for second
    client.delete_item = MagicMock(side_effect=[{"success": True}, {"error": "Not found"}])
    client._token = "test-token"
    client._token_expires = 9999999999999

    result = client.batch_delete_items(["id1", "id2"], owner="testuser")
    assert result["total"] == 2
    assert result["succeeded_count"] == 1
    assert result["failed_count"] == 1
    assert "id1" in result["succeeded"]
    assert result["failed"][0]["item_id"] == "id2"


def test_batch_share_items_result_structure():
    """batch_share_items should return succeeded/failed with counts."""
    client = ArcGISClient()
    client.share_item = MagicMock(return_value={"results": [{}]})
    client._token = "test-token"
    client._token_expires = 9999999999999

    result = client.batch_share_items(["id1", "id2"], everyone=True)
    assert result["total"] == 2
    assert result["succeeded_count"] == 2
    assert result["failed_count"] == 0


def test_batch_update_items_result_structure():
    """batch_update_items should return succeeded/failed with counts."""
    client = ArcGISClient()
    client.update_item = MagicMock(side_effect=[{"success": True}, {"error": "Forbidden"}])
    client._token = "test-token"
    client._token_expires = 9999999999999

    result = client.batch_update_items(["id1", "id2"], title="New Title")
    assert result["total"] == 2
    assert result["succeeded_count"] == 1
    assert result["failed_count"] == 1


# ------------------------------------------------------------------
# Client: connect_portal with username_password auth
# ------------------------------------------------------------------


def test_connect_portal_username_password_method():
    """connect_portal should support username_password auth method."""
    client = ArcGISClient()
    # Mock the HTTP response for generateToken and community/self
    mock_token_resp = MagicMock()
    mock_token_resp.json.return_value = {
        "token": "fake-user-token",
        "expires": 9999999999999,
    }
    mock_token_resp.raise_for_status = MagicMock()

    mock_self_resp = MagicMock()
    mock_self_resp.json.return_value = {
        "username": "testuser",
        "fullName": "Test User",
        "email": "test@example.com",
        "role": "org_user",
        "privileges": [],
    }
    mock_self_resp.raise_for_status = MagicMock()

    mock_session = MagicMock()
    mock_session.post.return_value = mock_token_resp
    mock_session.get.return_value = mock_self_resp
    client._session = mock_session

    result = client.connect_username_password(
        portal_url="https://gis.example.com/portal",
        username="testuser",
        password="testpass123",
    )

    assert result["username"] == "testuser"
    assert client.is_connected
    assert client.username == "testuser"


def test_connect_portal_username_password_bad_credentials():
    """connect_portal with invalid credentials should raise ConnectionError."""
    client = ArcGISClient()

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"error": {"code": 400, "message": "Invalid username or password."}}
    mock_resp.raise_for_status = MagicMock()

    mock_session = MagicMock()
    mock_session.post.return_value = mock_resp
    client._session = mock_session

    with pytest.raises(ConnectionError, match="generateToken failed"):
        client.connect_username_password(
            portal_url="https://gis.example.com/portal",
            username="baduser",
            password="badpass",
        )


# ------------------------------------------------------------------
# Client: connect_portal auto-detect logic
# ------------------------------------------------------------------


def test_connect_portal_tool_username_password():
    """connect_portal server tool should dispatch to connect_username_password."""
    from arcgis_portal_mcp.server import connect_portal

    with patch("arcgis_portal_mcp.server._get_client") as mock_get:
        mock_client = MagicMock()
        mock_client.connect_username_password.return_value = {
            "username": "testuser",
            "expires_in": 7200,
        }
        mock_get.return_value = mock_client

        result = connect_portal(
            portal_url="https://gis.example.com/portal",
            auth_method="username_password",
            username="testuser",
            password="testpass",
        )

        assert result["status"] == "ok"
        assert result["username"] == "testuser"
        mock_client.connect_username_password.assert_called_once()


# ------------------------------------------------------------------
# Server: tool registration (v1.2.0 additions)
# ------------------------------------------------------------------


def test_describe_layer_tool_exists():
    """describe_layer tool should be registered."""
    tool_names = list(mcp._tool_manager._tools.keys())
    assert "describe_layer" in tool_names


def test_get_gp_task_info_tool_exists():
    """get_gp_task_info tool should be registered."""
    tool_names = list(mcp._tool_manager._tools.keys())
    assert "get_gp_task_info" in tool_names


def test_batch_delete_items_tool_exists():
    """batch_delete_items tool should be registered."""
    tool_names = list(mcp._tool_manager._tools.keys())
    assert "batch_delete_items" in tool_names


def test_batch_share_items_tool_exists():
    """batch_share_items tool should be registered."""
    tool_names = list(mcp._tool_manager._tools.keys())
    assert "batch_share_items" in tool_names


def test_batch_update_items_tool_exists():
    """batch_update_items tool should be registered."""
    tool_names = list(mcp._tool_manager._tools.keys())
    assert "batch_update_items" in tool_names


def test_export_map_image_tool_exists():
    """export_map_image tool should be registered."""
    tool_names = list(mcp._tool_manager._tools.keys())
    assert "export_map_image" in tool_names


def test_get_item_data_tool_exists():
    """get_item_data tool should be registered."""
    tool_names = list(mcp._tool_manager._tools.keys())
    assert "get_item_data" in tool_names


def test_server_status_tool_exists():
    """server_status tool should be registered."""
    tool_names = list(mcp._tool_manager._tools.keys())
    assert "server_status" in tool_names


def test_portal_usage_tool_exists():
    """portal_usage tool should be registered."""
    tool_names = list(mcp._tool_manager._tools.keys())
    assert "portal_usage" in tool_names


def test_connect_portal_tool_exists():
    """connect_portal tool should be registered."""
    tool_names = list(mcp._tool_manager._tools.keys())
    assert "connect_portal" in tool_names


# ------------------------------------------------------------------
# Server: tools requiring connection return proper error when offline
# ------------------------------------------------------------------


def test_tool_returns_not_connected():
    """All tools that require a connection should return a clear error when offline."""
    from arcgis_portal_mcp import server as srv

    # Map tool name -> (positional_args, keyword_args)
    # connect_portal with explicit token succeeds without prior connection, so skip it
    tool_calls = {
        "search_content": ([], {}),
        "get_item_details": (["some-id"], {}),
        "get_item_data": (["some-id"], {}),
        "list_layers": (["some-id"], {}),
        "describe_layer": (["https://x.com/FeatureServer"], {}),
        "list_users": ([], {}),
        "list_groups": ([], {}),
        "get_user_details": (["someuser"], {}),
        "invite_to_group": (["gid", "user1"], {}),
        "create_group": (["Test Group"], {}),
        "update_item": (["some-id"], {}),
        "delete_item": (["some-id"], {}),
        "share_item": (["some-id"], {}),
        "upload_item": (["/tmp/f.csv", "Title", "CSV"], {}),
        "publish_from_item": (["some-id"], {}),
        "create_service": (["svc-name"], {}),
        "get_gp_task_info": (["https://x.com/GPServer"], {}),
        "export_map_image": (["https://x.com/MapServer"], {}),
        "portal_system_info": ([], {}),
        "list_licenses": ([], {}),
        "portal_usage": ([], {}),
        "add_features": (["https://x.com/FeatureServer", 0, "[]"], {}),
        "update_features": (["https://x.com/FeatureServer", 0, "[]"], {}),
        "delete_features": (["https://x.com/FeatureServer", 0], {}),
        "query_features": (["some-id"], {}),
        "execute_gp_task": (["https://x.com/GPServer/Task"], {}),
        "submit_gp_job": (["https://x.com/GPServer/Task"], {}),
        "get_gp_job_status": (["https://x.com/GPServer/Task", "job-123"], {}),
        "batch_delete_items": (["id1,id2"], {}),
        "batch_share_items": (["id1,id2"], {}),
        "batch_update_items": (["id1,id2"], {}),
    }

    for tool_name, (args, kwargs) in tool_calls.items():
        func = getattr(srv, tool_name)
        result = func(*args, **kwargs)
        assert result.get("status") == "error", f"{tool_name} should return error status"
        assert "Not connected" in result.get("error", ""), f"{tool_name} error should mention 'Not connected'"
