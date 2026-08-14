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
    assert __version__ == "1.8.0"


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
    """Server should expose exactly 60 tools (53 + 7 new in v1.8.0)."""
    tool_names = mcp._tool_manager._tools.keys()
    assert len(list(tool_names)) == 60


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
# ------------------------------------------------------------------
# v1.4.0: clone_item, move_items, check_service_health
# ------------------------------------------------------------------


def test_clone_item_not_connected():
    """clone_item should return error when not connected."""
    from arcgis_portal_mcp.server import clone_item

    result = clone_item(item_id="abc123")
    assert result["status"] == "error"
    assert "Not connected" in result["error"]


def test_clone_item_success():
    """clone_item should call client.clone_item with correct args."""
    from arcgis_portal_mcp.server import clone_item

    mock_client = MagicMock()
    mock_client.clone_item.return_value = {
        "success": True,
        "itemId": "new123",
        "owner": "testuser",
    }

    with patch("arcgis_portal_mcp.server._require_connected", return_value=mock_client):
        result = clone_item(
            item_id="orig123",
            new_title="My Copy",
            new_owner="testuser",
            folder="Projects",
        )
        assert result["status"] == "ok"
        assert result["result"]["itemId"] == "new123"
        mock_client.clone_item.assert_called_once_with(
            item_id="orig123",
            new_title="My Copy",
            new_owner="testuser",
            folder="Projects",
        )


def test_clone_item_defaults():
    """clone_item should pass None for empty optional args."""
    from arcgis_portal_mcp.server import clone_item

    mock_client = MagicMock()
    mock_client.clone_item.return_value = {"success": True}

    with patch("arcgis_portal_mcp.server._require_connected", return_value=mock_client):
        result = clone_item(item_id="orig123")
        assert result["status"] == "ok"
        mock_client.clone_item.assert_called_once_with(
            item_id="orig123",
            new_title=None,
            new_owner=None,
            folder=None,
        )


def test_move_items_not_connected():
    """move_items should return error when not connected."""
    from arcgis_portal_mcp.server import move_items

    result = move_items(item_ids="a,b", target_owner="user2")
    assert result["status"] == "error"
    assert "Not connected" in result["error"]


def test_move_items_empty_ids():
    """move_items should reject empty item IDs."""
    from arcgis_portal_mcp.server import move_items

    mock_client = MagicMock()
    with patch("arcgis_portal_mcp.server._require_connected", return_value=mock_client):
        result = move_items(item_ids="", target_owner="user2")
        assert result["status"] == "error"
        assert "No item IDs" in result["error"]


def test_move_items_success():
    """move_items should parse comma-separated IDs and call client."""
    from arcgis_portal_mcp.server import move_items

    mock_client = MagicMock()
    mock_client.move_items.return_value = {
        "total": 2,
        "succeeded_count": 2,
        "failed_count": 0,
        "succeeded": ["a", "b"],
        "failed": [],
    }

    with patch("arcgis_portal_mcp.server._require_connected", return_value=mock_client):
        result = move_items(item_ids="a, b", target_owner="user2", source_owner="user1")
        assert result["status"] == "ok"
        assert result["result"]["succeeded_count"] == 2
        mock_client.move_items.assert_called_once_with(
            item_ids=["a", "b"],
            target_owner="user2",
            source_owner="user1",
        )


def test_check_service_health_not_connected():
    """check_service_health should return error when not connected."""
    from arcgis_portal_mcp.server import check_service_health

    result = check_service_health(service_url="https://example.com/arcgis/rest/services/Test/MapServer")
    assert result["status"] == "error"
    assert "Not connected" in result["error"]


def test_check_service_health_success():
    """check_service_health should return health info."""
    from arcgis_portal_mcp.server import check_service_health

    mock_client = MagicMock()
    mock_client.check_service_health.return_value = {
        "status": "ok",
        "available": True,
        "status_code": 200,
        "latency_ms": 150,
        "service_url": "https://example.com/arcgis/rest/services/Test/MapServer",
        "version": 11.2,
        "max_record_count": 1000,
    }

    with patch("arcgis_portal_mcp.server._require_connected", return_value=mock_client):
        result = check_service_health(service_url="https://example.com/arcgis/rest/services/Test/MapServer")
        assert result["status"] == "ok"
        assert result["result"]["available"] is True
        assert result["result"]["latency_ms"] == 150


def test_client_clone_item():
    """clone_item should GET item details, GET data, POST addItem."""
    client = ArcGISClient()
    client._token = "fake-token"
    client._username = "testuser"

    item_details = {
        "title": "Original Map",
        "type": "Web Map",
        "tags": ["gis", "data"],
        "description": "A test map",
        "snippet": "Test snippet",
        "access": "org",
        "owner": "testuser",
    }
    item_data = {"baseMap": {}}

    with patch.object(client, "get_item_details", return_value=item_details), \
         patch.object(client, "get_item_data", return_value=item_data), \
         patch.object(client, "_sharing_request") as mock_req:
        mock_req.return_value = {"success": True, "itemId": "new456"}
        result = client.clone_item("orig123")
        assert result["itemId"] == "new456"
        # Verify addItem was called with correct params
        call_args = mock_req.call_args
        assert "/addItem" in call_args[0][0]
        assert call_args[1]["params"]["title"] == "Original Map (Copy)"
        assert call_args[1]["params"]["type"] == "Web Map"


def test_client_move_items():
    """move_items should POST to /transfer endpoint."""
    client = ArcGISClient()
    client._token = "fake-token"
    client._username = "user1"

    with patch.object(client, "_sharing_request") as mock_req:
        mock_req.return_value = {
            "results": [
                {"itemId": "a", "success": True},
                {"itemId": "b", "success": False, "error": {"message": "Not found"}},
            ]
        }
        result = client.move_items(["a", "b"], "user2")
        assert result["succeeded_count"] == 1
        assert result["failed_count"] == 1
        assert result["failed"][0]["item_id"] == "b"


def test_client_check_service_health_ok():
    """check_service_health should return service info on success."""
    client = ArcGISClient()
    client._token = "fake-token"

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "currentVersion": 11.2,
        "serviceDescription": "Test Service",
        "maxRecordCount": 2000,
        "capabilities": "Query,Editing",
    }

    with patch.object(client._session, "get", return_value=mock_response):
        result = client.check_service_health("https://example.com/arcgis/rest/services/Test/MapServer")
        assert result["available"] is True
        assert result["status_code"] == 200
        assert result["version"] == 11.2
        assert "latency_ms" in result


def test_client_check_service_health_timeout():
    """check_service_health should handle timeouts gracefully."""
    import requests as _requests

    client = ArcGISClient()
    client._token = "fake-token"

    with patch.object(client._session, "get", side_effect=_requests.exceptions.Timeout("Timed out")):
        result = client.check_service_health("https://example.com/arcgis/rest/services/Test/MapServer")
        assert result["available"] is False
        assert "timed out" in result["error"].lower()
        assert "latency_ms" in result


def test_client_check_service_health_connection_error():
    """check_service_health should handle connection errors gracefully."""
    import requests as _requests

    client = ArcGISClient()
    client._token = "fake-token"

    with patch.object(client._session, "get", side_effect=_requests.exceptions.ConnectionError("Refused")):
        result = client.check_service_health("https://example.com/arcgis/rest/services/Test/MapServer")
        assert result["available"] is False
        assert "connection failed" in result["error"].lower()


# ------------------------------------------------------------------
# v1.5.0: explore_item_relationships, audit_group_members,
# scan_service_dependencies, analyze_item_impact, get_usage_analytics
# ------------------------------------------------------------------


def test_explore_item_relationships_not_connected():
    """explore_item_relationships should return error when not connected."""
    from arcgis_portal_mcp.server import explore_item_relationships

    result = explore_item_relationships("some-item-id")
    assert result["status"] == "error"
    assert "Not connected" in result["error"]


def test_audit_group_members_not_connected():
    """audit_group_members should return error when not connected."""
    from arcgis_portal_mcp.server import audit_group_members

    result = audit_group_members("some-group-id")
    assert result["status"] == "error"
    assert "Not connected" in result["error"]


def test_scan_service_dependencies_not_connected():
    """scan_service_dependencies should return error when not connected."""
    from arcgis_portal_mcp.server import scan_service_dependencies

    result = scan_service_dependencies("some-service-id")
    assert result["status"] == "error"
    assert "Not connected" in result["error"]


def test_analyze_item_impact_not_connected():
    """analyze_item_impact should return error when not connected."""
    from arcgis_portal_mcp.server import analyze_item_impact

    result = analyze_item_impact("some-item-id")
    assert result["status"] == "error"
    assert "Not connected" in result["error"]


def test_get_usage_analytics_not_connected():
    """get_usage_analytics should return error when not connected."""
    from arcgis_portal_mcp.server import get_usage_analytics

    result = get_usage_analytics()
    assert result["status"] == "error"
    assert "Not connected" in result["error"]


def test_explore_item_relationships_tool_exists():
    """explore_item_relationships tool should be registered."""
    tool_names = list(mcp._tool_manager._tools.keys())
    assert "explore_item_relationships" in tool_names


def test_audit_group_members_tool_exists():
    """audit_group_members tool should be registered."""
    tool_names = list(mcp._tool_manager._tools.keys())
    assert "audit_group_members" in tool_names


def test_scan_service_dependencies_tool_exists():
    """scan_service_dependencies tool should be registered."""
    tool_names = list(mcp._tool_manager._tools.keys())
    assert "scan_service_dependencies" in tool_names


def test_analyze_item_impact_tool_exists():
    """analyze_item_impact tool should be registered."""
    tool_names = list(mcp._tool_manager._tools.keys())
    assert "analyze_item_impact" in tool_names


def test_get_usage_analytics_tool_exists():
    """get_usage_analytics tool should be registered."""
    tool_names = list(mcp._tool_manager._tools.keys())
    assert "get_usage_analytics" in tool_names


def test_client_explore_item_relationships():
    """explore_item_relationships should return item info and relationships."""
    client = ArcGISClient()
    client._token = "fake-token"

    item_info = {
        "title": "Test Service",
        "type": "Feature Service",
        "owner": "testuser",
    }
    forward_resp = {
        "relatedItems": [
            {"id": "rel1", "title": "Web Map A", "type": "Web Map", "relationshipType": "Map2Service"},
        ],
    }
    reverse_resp = {
        "relatedItems": [
            {"id": "rel2", "title": "Dashboard B", "type": "Dashboard", "relationshipType": "Service2Map"},
        ],
    }

    with patch.object(client, "_sharing_request") as mock_req:
        mock_req.side_effect = [item_info, forward_resp, reverse_resp]
        result = client.explore_item_relationships("item123")

    assert result["item_id"] == "item123"
    assert result["title"] == "Test Service"
    assert result["total_relationships"] == 2
    assert result["relationships"][0]["direction"] == "forward"
    assert result["relationships"][1]["direction"] == "reverse"


def test_client_audit_group_members():
    """audit_group_members should paginate and return member list."""
    client = ArcGISClient()
    client._token = "fake-token"

    group_info = {
        "title": "GIS Team",
        "owner": "admin",
        "description": "GIS team group",
        "access": "org",
        "memberCount": 2,
    }
    users_page = {
        "users": [
            {"username": "user1", "fullName": "User One", "email": "u1@test.com", "role": "org_admin", "lastLogin": 12345, "disabled": False},
            {"username": "user2", "fullName": "User Two", "email": "u2@test.com", "role": "org_user", "lastLogin": 67890, "disabled": False},
        ],
        "nextStart": -1,
        "total": 2,
    }

    with patch.object(client, "_sharing_request") as mock_req:
        mock_req.side_effect = [group_info, users_page]
        result = client.audit_group_members("grp123")

    assert result["group_id"] == "grp123"
    assert result["title"] == "GIS Team"
    assert result["member_count"] == 2
    assert len(result["members"]) == 2
    assert result["members"][0]["username"] == "user1"
    assert result["members"][1]["username"] == "user2"


def test_client_scan_service_dependencies():
    """scan_service_dependencies should find dependent items."""
    client = ArcGISClient()
    client._token = "fake-token"

    item_info = {
        "title": "Parcels Service",
        "type": "Feature Service",
        "url": "https://host/arcgis/rest/services/Parcels/FeatureServer",
    }

    search_data = {
        "total": 1,
        "results": [
            {"id": "webmap1", "title": "Parcel Map", "type": "Web Map", "owner": "user1"},
        ],
    }

    item_data_resp = {
        "baseMap": {"layers": [{"url": "https://host/arcgis/rest/services/Parcels/FeatureServer"}]},
    }

    with patch.object(client, "_sharing_request") as mock_req, \
         patch.object(client, "search_items") as mock_search:
        mock_search.return_value = [{"id": "webmap1", "title": "Parcel Map", "type": "Web Map"}]
        mock_req.side_effect = [item_info, item_data_resp, None]
        result = client.scan_service_dependencies("svc123")

    assert result["service_id"] == "svc123"
    assert result["title"] == "Parcels Service"
    assert result["dependency_count"] >= 1


def test_client_analyze_item_impact_low():
    """analyze_item_impact should return low blast radius for isolated items."""
    client = ArcGISClient()
    client._token = "fake-token"

    item_info = {
        "title": "Isolated Item",
        "type": "CSV",
        "owner": "user1",
    }
    no_relationships = {"item_id": "x", "relationships": [], "total_relationships": 0}
    no_dependencies = {"depended_on_by": [], "dependency_count": 0}
    sharing_info = {"groups": []}

    with patch.object(client, "_sharing_request") as mock_req, \
         patch.object(client, "explore_item_relationships", return_value=no_relationships), \
         patch.object(client, "scan_service_dependencies", return_value=no_dependencies):
        mock_req.side_effect = [item_info, sharing_info]
        result = client.analyze_item_impact("item123")

    assert result["item_id"] == "item123"
    assert result["impact"]["blast_radius"] == "low"
    assert "Safe to delete" in result["impact"]["recommendation"]


def test_client_get_usage_analytics():
    """get_usage_analytics should return portal stats and content breakdown."""
    client = ArcGISClient()
    client._token = "fake-token"

    portal_usage_resp = {"active_users": 10, "storage": 1024}
    search_data = {"total": 5}

    with patch.object(client, "_sharing_request") as mock_req, \
         patch.object(client, "search_items", return_value=[{"owner": "user1"}]):
        mock_req.side_effect = [portal_usage_resp] + [search_data] * 12
        result = client.get_usage_analytics()

    assert "portal_stats" in result
    assert "user_activity" in result
    assert "content_breakdown" in result


# Update the tool count to reflect the 5 new tools
# Add the new tools to the not-connected tool list
def test_tool_returns_not_connected_new_tools():
    """New v1.5.0 tools should return error when not connected."""
    from arcgis_portal_mcp import server as srv

    tool_calls = {
        "explore_item_relationships": (["some-id"], {}),
        "audit_group_members": (["some-group-id"], {}),
        "scan_service_dependencies": (["some-service-id"], {}),
        "analyze_item_impact": (["some-item-id"], {}),
        "get_usage_analytics": ([], {}),
    }

    for tool_name, (args, kwargs) in tool_calls.items():
        func = getattr(srv, tool_name)
        result = func(*args, **kwargs)
        assert result.get("status") == "error", f"{tool_name} should return error status"
        assert "Not connected" in result.get("error", ""), f"{tool_name} error should mention 'Not connected'"


# ======================================================================
# v1.8.0: Collaborations, Roles, Scheduled Tasks
# ======================================================================


def test_client_list_collaborations():
    """list_collaborations should return list of collaborations."""
    client = ArcGISClient()
    client._token = "fake-token"

    with patch.object(client, "_sharing_request", return_value={"collaborations": [{"id": "c1", "name": "Test Collab"}]}):
        result = client.list_collaborations()
    assert len(result) == 1
    assert result[0]["id"] == "c1"


def test_client_list_collaborations_empty():
    """list_collaborations should return empty list on error."""
    client = ArcGISClient()
    client._token = "fake-token"

    with patch.object(client, "_sharing_request", return_value={"error": "fail"}):
        result = client.list_collaborations()
    assert result == []


def test_client_get_collaboration():
    """get_collaboration should return collaboration details."""
    client = ArcGISClient()
    client._token = "fake-token"

    with patch.object(client, "_sharing_request", return_value={"id": "c1", "name": "Test"}):
        result = client.get_collaboration("c1")
    assert result["id"] == "c1"


def test_client_sync_collaboration():
    """sync_collaboration should POST to the sync endpoint."""
    client = ArcGISClient()
    client._token = "fake-token"

    with patch.object(client, "_sharing_request", return_value={"status": "ok"}) as mock:
        result = client.sync_collaboration("c1", "w1")
    mock.assert_called_once()
    assert "sync" in mock.call_args[0][0]


def test_client_list_roles():
    """list_roles should return roles list."""
    client = ArcGISClient()
    client._token = "fake-token"

    with patch.object(client, "_sharing_request", return_value={"roles": [{"id": "r1", "name": "Admin"}]}):
        result = client.list_roles()
    assert len(result) == 1
    assert result[0]["name"] == "Admin"


def test_client_get_role_privileges():
    """get_role_privileges should return privilege list."""
    client = ArcGISClient()
    client._token = "fake-token"

    with patch.object(client, "_sharing_request", return_value={"id": "r1", "privileges": ["portal:user:createGroup"]}):
        result = client.get_role_privileges("r1")
    assert "privileges" in result


def test_client_list_scheduled_tasks():
    """list_scheduled_tasks should return tasks list."""
    client = ArcGISClient()
    client._token = "fake-token"

    with patch.object(client, "_sharing_request", return_value={"tasks": [{"id": "t1", "title": "Notebook Run"}]}):
        result = client.list_scheduled_tasks()
    assert len(result) == 1


def test_client_list_scheduled_tasks_with_filters():
    """list_scheduled_tasks should pass filters to the API."""
    client = ArcGISClient()
    client._token = "fake-token"

    with patch.object(client, "_sharing_request", return_value={"tasks": []}) as mock:
        client.list_scheduled_tasks(task_type="ExecuteNotebook", user_filter="admin", active=True)
    params = mock.call_args[1].get("params", {})
    assert params.get("types") == "ExecuteNotebook"
    assert params.get("userFilter") == "admin"
    assert params.get("active") == "true"


def test_client_get_user_scheduled_tasks():
    """get_user_scheduled_tasks should return tasks for a user."""
    client = ArcGISClient()
    client._token = "fake-token"

    with patch.object(client, "_sharing_request", return_value={"tasks": [{"id": "t1"}]}):
        result = client.get_user_scheduled_tasks("jsmith")
    assert len(result) == 1


# Server tool not-connected tests for v1.8.0 tools
def test_v18_tools_not_connected():
    """v1.8.0 tools should return error when not connected."""
    from arcgis_portal_mcp import server as srv

    tool_calls = {
        "list_collaborations": ([], {}),
        "get_collaboration": (["some-id"], {}),
        "sync_collaboration": (["c1", "w1"], {}),
        "list_roles": ([], {}),
        "get_role_privileges": (["r1"], {}),
        "list_scheduled_tasks": ([], {}),
        "get_user_scheduled_tasks": (["jsmith"], {}),
    }

    for tool_name, (args, kwargs) in tool_calls.items():
        func = getattr(srv, tool_name)
        result = func(*args, **kwargs)
        assert result.get("status") == "error", f"{tool_name} should return error status"
        assert "Not connected" in result.get("error", ""), f"{tool_name} error should mention 'Not connected'"

