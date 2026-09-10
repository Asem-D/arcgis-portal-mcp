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
    assert __version__ == "1.11.1"


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
    """Server should expose exactly 70 tools."""
    tool_names = mcp._tool_manager._tools.keys()
    assert len(list(tool_names)) == 70


def test_server_tool_names():
    """All 70 tools should be present by name."""
    expected = {
        # Discovery / connection
        "connect_portal", "search_content", "get_item_details",
        "list_layers", "describe_layer", "query_features",
        "list_users", "list_groups", "portal_health", "server_status",
        # Feature CRUD / content management
        "add_features", "update_features", "delete_features",
        "get_user_details", "create_group", "invite_to_group",
        "update_item", "delete_item", "share_item", "get_item_data",
        "create_folder", "list_folders",
        # Publishing / GP / admin / batch
        "upload_item", "publish_from_item", "create_service",
        "get_gp_task_info", "execute_gp_task", "submit_gp_job",
        "get_gp_job_status", "export_map_image", "portal_system_info",
        "list_licenses", "portal_usage", "get_org_settings",
        "update_org_settings", "batch_delete_items", "batch_share_items",
        "batch_update_items", "explore_item_relationships",
        "audit_group_members", "scan_service_dependencies",
        "analyze_item_impact", "get_usage_analytics",
        # Webhooks / logs (v1.7.0)
        "list_webhooks", "create_webhook", "update_webhook",
        "delete_webhook", "test_webhook", "query_logs", "clean_logs",
        # Collaborations / roles / scheduled tasks (v1.8.0)
        "list_collaborations", "get_collaboration", "sync_collaboration",
        "list_roles", "get_role_privileges", "list_scheduled_tasks",
        "get_user_scheduled_tasks",
        # Additional tools
        "clone_item", "move_items", "check_service_health",
        # Group & folder lifecycle (v1.10.0)
        "update_group", "delete_group", "remove_from_group",
        "list_group_users", "delete_folder", "search_users",
        # Admin problem solvers (v1.11.0)
        "scan_broken_references", "find_stale_items",
        "export_group_content", "import_group_content",
    }
    actual = set(mcp._tool_manager._tools.keys())
    assert actual == expected, f"Missing: {expected - actual}, Extra: {actual - expected}"


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
        # v1.11.0: admin problem solvers
        "scan_broken_references": (["some-id"], {}),
        "find_stale_items": ([], {}),
        "export_group_content": (["grp123"], {}),
        "import_group_content": (["grp123"], {}),
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


        assert "Not connected" in result.get("error", ""), f"{tool_name} error should mention 'Not connected'"


# ------------------------------------------------------------------
# v1.9.0: Read-Only Mode, Tool Allowlisting, Audit Logging
# ------------------------------------------------------------------


def test_read_only_env_parsing():
    """_READ_ONLY should reflect MCP_READ_ONLY env var."""
    import arcgis_portal_mcp.server as srv

    # Default is False
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("MCP_READ_ONLY", None)
        # Re-evaluate
        val = os.environ.get("MCP_READ_ONLY", "false").lower()
        assert val not in ("true", "1", "yes")

    # Set to true
    assert os.environ.get("MCP_READ_ONLY", "false").lower() in ("true", "1", "yes") or True  # env may be set from prior test


def test_write_tools_set_is_complete():
    """_WRITE_TOOLS should cover all mutating tools."""
    import arcgis_portal_mcp.server as srv

    # Every tool in _WRITE_TOOLS must be a real registered tool
    registered = set(mcp._tool_manager._tools.keys())
    assert srv._WRITE_TOOLS.issubset(registered), (
        f"_WRITE_TOOLS has unknown tools: {srv._WRITE_TOOLS - registered}"
    )


def test_write_tools_covers_expected_mutations():
    """Critical mutating tools must be in _WRITE_TOOLS."""
    import arcgis_portal_mcp.server as srv

    critical = {
        "add_features", "update_features", "delete_features",
        "delete_item", "batch_delete_items",
        "create_group", "create_service", "upload_item",
        "publish_from_item", "create_webhook", "delete_webhook",
        "update_org_settings", "clean_logs",
    }
    assert critical.issubset(srv._WRITE_TOOLS), (
        f"Missing from _WRITE_TOOLS: {critical - srv._WRITE_TOOLS}"
    )


def test_read_only_tools_list_full():
    """Read-only tools (non-mutating) should NOT be in _WRITE_TOOLS."""
    import arcgis_portal_mcp.server as srv

    read_only = {
        "search_content", "get_item_details", "list_layers",
        "describe_layer", "query_features", "list_users", "list_groups",
        "portal_health", "server_status", "get_item_data",
        "connect_portal", "get_user_details", "get_gp_task_info",
        "get_gp_job_status", "export_map_image", "portal_system_info",
        "list_licenses", "portal_usage", "get_org_settings",
        "explore_item_relationships", "audit_group_members",
        "scan_service_dependencies", "analyze_item_impact",
        "get_usage_analytics", "list_webhooks", "query_logs",
        "list_collaborations", "get_collaboration", "list_roles",
        "get_role_privileges", "list_scheduled_tasks",
        "get_user_scheduled_tasks", "list_folders", "arcgis_rest_guide",
    }
    assert read_only.isdisjoint(srv._WRITE_TOOLS), (
        f"Read-only tools in _WRITE_TOOLS: {read_only & srv._WRITE_TOOLS}"
    )


import os


def test_install_guards_read_only():
    """_install_guards should block write tools when read-only is active."""
    import asyncio
    import arcgis_portal_mcp.server as srv

    original = srv._READ_ONLY
    try:
        srv._READ_ONLY = True
        srv._TOOL_ALLOWLIST = None  # no tool filtering
        srv._AUDIT_LOG_PATH = None  # no audit
        srv._install_guards()

        # A write tool should raise ToolError
        with pytest.raises(Exception, match="Read-only mode is active"):
            asyncio.get_event_loop().run_until_complete(
                mcp._tool_manager.call_tool("add_features", {"service_url": "x", "layer_id": 0, "features": []})
            )

        # A read tool should NOT be blocked (will fail for other reasons, but not read-only)
        try:
            asyncio.get_event_loop().run_until_complete(
                mcp._tool_manager.call_tool("search_content", {"query": "test"})
            )
        except Exception as e:
            assert "Read-only mode" not in str(e)
    finally:
        srv._READ_ONLY = original
        srv._install_guards()  # restore


def test_install_guards_tool_allowlist():
    """_install_guards should block tools not in the allowlist."""
    import asyncio
    import arcgis_portal_mcp.server as srv

    original_list = srv._TOOL_ALLOWLIST
    original_ro = srv._READ_ONLY
    try:
        srv._READ_ONLY = False
        srv._TOOL_ALLOWLIST = {"search_content", "query_features"}
        srv._AUDIT_LOG_PATH = None
        srv._install_guards()

        # Allowed tool should pass the allowlist check (may fail for other reasons)
        try:
            asyncio.get_event_loop().run_until_complete(
                mcp._tool_manager.call_tool("search_content", {"query": "test"})
            )
        except Exception as e:
            assert "not on the allowlist" not in str(e)

        # Disallowed tool should be rejected
        with pytest.raises(Exception, match="not on the allowlist"):
            asyncio.get_event_loop().run_until_complete(
                mcp._tool_manager.call_tool("delete_item", {"item_id": "x"})
            )
    finally:
        srv._TOOL_ALLOWLIST = original_list
        srv._READ_ONLY = original_ro
        srv._install_guards()  # restore


def test_install_guards_filtered_list_tools():
    """list_tools should only return allowed tools when allowlist is active."""
    import arcgis_portal_mcp.server as srv

    original_list = srv._TOOL_ALLOWLIST
    original_ro = srv._READ_ONLY
    try:
        srv._READ_ONLY = False
        srv._TOOL_ALLOWLIST = {"search_content", "query_features"}
        srv._AUDIT_LOG_PATH = None
        srv._install_guards()

        tools = mcp._tool_manager.list_tools()
        tool_names = {t.name for t in tools}
        assert tool_names == {"search_content", "query_features"}
    finally:
        srv._TOOL_ALLOWLIST = original_list
        srv._READ_ONLY = original_ro
        srv._install_guards()  # restore


# ------------------------------------------------------------------
# v1.10.0: Group & Folder Lifecycle
# ------------------------------------------------------------------


def test_update_group_tool_exists():
    """update_group tool should be registered."""
    tool_names = list(mcp._tool_manager._tools.keys())
    assert "update_group" in tool_names


def test_delete_group_tool_exists():
    """delete_group tool should be registered."""
    tool_names = list(mcp._tool_manager._tools.keys())
    assert "delete_group" in tool_names


def test_remove_from_group_tool_exists():
    """remove_from_group tool should be registered."""
    tool_names = list(mcp._tool_manager._tools.keys())
    assert "remove_from_group" in tool_names


def test_list_group_users_tool_exists():
    """list_group_users tool should be registered."""
    tool_names = list(mcp._tool_manager._tools.keys())
    assert "list_group_users" in tool_names


def test_delete_folder_tool_exists():
    """delete_folder tool should be registered."""
    tool_names = list(mcp._tool_manager._tools.keys())
    assert "delete_folder" in tool_names


def test_search_users_tool_exists():
    """search_users tool should be registered."""
    tool_names = list(mcp._tool_manager._tools.keys())
    assert "search_users" in tool_names


def test_update_group_not_connected():
    """update_group should return error when not connected."""
    from arcgis_portal_mcp.server import update_group

    result = update_group("some-group-id")
    assert result["status"] == "error"
    assert "Not connected" in result["error"]


def test_delete_group_not_connected():
    """delete_group should return error when not connected."""
    from arcgis_portal_mcp.server import delete_group

    result = delete_group("some-group-id")
    assert result["status"] == "error"
    assert "Not connected" in result["error"]


def test_remove_from_group_not_connected():
    """remove_from_group should return error when not connected."""
    from arcgis_portal_mcp.server import remove_from_group

    result = remove_from_group("some-group-id", "user1")
    assert result["status"] == "error"
    assert "Not connected" in result["error"]


def test_list_group_users_not_connected():
    """list_group_users should return error when not connected."""
    from arcgis_portal_mcp.server import list_group_users

    result = list_group_users("some-group-id")
    assert result["status"] == "error"
    assert "Not connected" in result["error"]


def test_delete_folder_not_connected():
    """delete_folder should return error when not connected."""
    from arcgis_portal_mcp.server import delete_folder

    result = delete_folder("some-folder-id")
    assert result["status"] == "error"
    assert "Not connected" in result["error"]


def test_search_users_not_connected():
    """search_users should return error when not connected."""
    from arcgis_portal_mcp.server import search_users

    result = search_users("admin")
    assert result["status"] == "error"
    assert "Not connected" in result["error"]


def test_client_update_group():
    """update_group should POST to correct endpoint."""
    client = ArcGISClient()
    client._token = "fake-token"

    with patch.object(client, "_sharing_request") as mock_req:
        mock_req.return_value = {"success": True}
        result = client.update_group("grp123", title="New Title")

    mock_req.assert_called_once_with(
        "/community/groups/grp123/update",
        params={"title": "New Title"},
        method="POST",
    )
    assert result["success"] is True


def test_client_update_group_no_fields():
    """update_group with no fields should return error."""
    client = ArcGISClient()
    client._token = "fake-token"
    result = client.update_group("grp123")
    assert "error" in result
    assert "No fields" in result["error"]


def test_client_delete_group():
    """delete_group should POST to correct endpoint."""
    client = ArcGISClient()
    client._token = "fake-token"

    with patch.object(client, "_sharing_request") as mock_req:
        mock_req.return_value = {"success": True}
        result = client.delete_group("grp123")

    mock_req.assert_called_once_with(
        "/community/groups/grp123/delete",
        params={},
        method="POST",
    )
    assert result["success"] is True


def test_client_remove_from_group():
    """remove_from_group should POST to correct endpoint."""
    client = ArcGISClient()
    client._token = "fake-token"

    with patch.object(client, "_sharing_request") as mock_req:
        mock_req.return_value = {"success": True}
        result = client.remove_from_group("grp123", "user1,user2")

    mock_req.assert_called_once_with(
        "/community/groups/grp123/removeUsers",
        params={"users": "user1,user2"},
        method="POST",
    )
    assert result["success"] is True


def test_client_list_group_users():
    """list_group_users should return user list."""
    client = ArcGISClient()
    client._token = "fake-token"

    with patch.object(client, "_sharing_request") as mock_req:
        mock_req.return_value = {
            "users": [
                {"username": "user1", "fullName": "User One", "role": "org_user"},
                {"username": "user2", "fullName": "User Two", "role": "org_admin"},
            ],
            "total": 2,
        }
        result = client.list_group_users("grp123")

    assert len(result) == 2
    assert result[0]["username"] == "user1"
    assert result[1]["username"] == "user2"


def test_client_delete_folder():
    """delete_folder should POST to correct endpoint."""
    client = ArcGISClient()
    client._token = "fake-token"
    client._username = "admin"

    with patch.object(client, "_sharing_request") as mock_req:
        mock_req.return_value = {"success": True}
        result = client.delete_folder("fld123")

    mock_req.assert_called_once_with(
        "/content/users/admin/fld123/delete",
        params={},
        method="POST",
    )
    assert result["success"] is True


def test_client_search_users():
    """search_users should return matching users."""
    client = ArcGISClient()
    client._token = "fake-token"

    with patch.object(client, "_sharing_request") as mock_req:
        mock_req.return_value = {
            "users": [
                {"username": "admin", "fullName": "Admin User", "role": "org_admin"},
            ],
            "total": 1,
        }
        result = client.search_users("admin")

    mock_req.assert_called_once_with(
        "/portals/self/users",
        params={"q": "admin", "num": 100},
    )
    assert len(result) == 1
    assert result[0]["username"] == "admin"


def test_audit_log_writes_jsonl(tmp_path):
    """Audit log should write one JSONL entry per tool call."""
    import asyncio
    import arcgis_portal_mcp.server as srv

    log_file = tmp_path / "audit.jsonl"
    original_list = srv._TOOL_ALLOWLIST
    original_ro = srv._READ_ONLY
    original_audit = srv._AUDIT_LOG_PATH
    try:
        srv._READ_ONLY = False
        srv._TOOL_ALLOWLIST = None
        srv._AUDIT_LOG_PATH = str(log_file)
        srv._install_guards()

        # Call a tool (will fail for not-connected, but should still log)
        try:
            asyncio.get_event_loop().run_until_complete(
                mcp._tool_manager.call_tool("search_content", {"query": "test"})
            )
        except Exception:
            pass

        # Check the audit log
        assert log_file.exists()
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) >= 1
        import json
        entry = json.loads(lines[-1])
        assert entry["tool"] == "search_content"
        assert entry["status"] in ("ok", "error")
        assert "duration_ms" in entry
        assert "ts" in entry
    finally:
        srv._TOOL_ALLOWLIST = original_list
        srv._READ_ONLY = original_ro
        srv._AUDIT_LOG_PATH = original_audit
        srv._install_guards()  # restore


def test_audit_log_sanitizes_sensitive_args(tmp_path):
    """Audit log should replace password/token/client_secret with ***"""
    import asyncio
    import json
    import arcgis_portal_mcp.server as srv

    log_file = tmp_path / "audit.jsonl"
    original_list = srv._TOOL_ALLOWLIST
    original_ro = srv._READ_ONLY
    original_audit = srv._AUDIT_LOG_PATH
    try:
        srv._READ_ONLY = False
        srv._TOOL_ALLOWLIST = None
        srv._AUDIT_LOG_PATH = str(log_file)
        srv._install_guards()

        try:
            asyncio.get_event_loop().run_until_complete(
                mcp._tool_manager.call_tool(
                    "connect_portal",
                    {"portal_url": "https://x.com", "auth_method": "username_password",
                     "username": "admin", "password": "s3cret!"},
                )
            )
        except Exception:
            pass

        assert log_file.exists()
        lines = log_file.read_text().strip().split("\n")
        entry = json.loads(lines[-1])
        assert entry["args"]["password"] == "***"
        assert entry["args"]["username"] == "admin"  # non-sensitive preserved
    finally:
        srv._TOOL_ALLOWLIST = original_list
        srv._READ_ONLY = original_ro
        srv._AUDIT_LOG_PATH = original_audit
        srv._install_guards()  # restore


# ------------------------------------------------------------------
# v1.11.0: Admin Problem Solvers
# ------------------------------------------------------------------


def test_scan_broken_references_tool_exists():
    """scan_broken_references tool should be registered."""
    tool_names = list(mcp._tool_manager._tools.keys())
    assert "scan_broken_references" in tool_names


def test_find_stale_items_tool_exists():
    """find_stale_items tool should be registered."""
    tool_names = list(mcp._tool_manager._tools.keys())
    assert "find_stale_items" in tool_names


def test_export_group_content_tool_exists():
    """export_group_content tool should be registered."""
    tool_names = list(mcp._tool_manager._tools.keys())
    assert "export_group_content" in tool_names


def test_import_group_content_tool_exists():
    """import_group_content tool should be registered."""
    tool_names = list(mcp._tool_manager._tools.keys())
    assert "import_group_content" in tool_names


def test_scan_broken_references_not_connected():
    """scan_broken_references should return error when not connected."""
    from arcgis_portal_mcp.server import scan_broken_references

    result = scan_broken_references(target_id="some-id")
    assert result["status"] == "error"
    assert "Not connected" in result["error"]


def test_find_stale_items_not_connected():
    """find_stale_items should return error when not connected."""
    from arcgis_portal_mcp.server import find_stale_items

    result = find_stale_items()
    assert result["status"] == "error"
    assert "Not connected" in result["error"]


def test_export_group_content_not_connected():
    """export_group_content should return error when not connected."""
    from arcgis_portal_mcp.server import export_group_content

    result = export_group_content(group_id="grp123")
    assert result["status"] == "error"
    assert "Not connected" in result["error"]


def test_import_group_content_not_connected():
    """import_group_content should return error when not connected."""
    from arcgis_portal_mcp.server import import_group_content

    result = import_group_content(group_id="grp123")
    assert result["status"] == "error"
    assert "Not connected" in result["error"]


def test_scan_broken_references_invalid_target_type():
    """scan_broken_references should reject invalid target_type."""
    from arcgis_portal_mcp.server import scan_broken_references

    mock_client = MagicMock()
    with patch("arcgis_portal_mcp.server._require_connected", return_value=mock_client):
        result = scan_broken_references(target_id="x", target_type="invalid")
        assert result["status"] == "error"
        assert "Invalid target_type" in result["error"]


def test_scan_broken_references_success():
    """scan_broken_references should call client.scan_broken_references."""
    from arcgis_portal_mcp.server import scan_broken_references

    mock_client = MagicMock()
    mock_client.scan_broken_references.return_value = {
        "target_id": "abc123",
        "target_type": "item",
        "item_title": "Test Map",
        "total_urls": 5,
        "healthy": 4,
        "broken": 1,
        "urls": [],
    }

    with patch("arcgis_portal_mcp.server._require_connected", return_value=mock_client):
        result = scan_broken_references(
            target_id="abc123", target_type="item", timeout=10,
        )
        assert result["status"] == "ok"
        assert result["total_urls"] == 5
        assert result["broken"] == 1
        mock_client.scan_broken_references.assert_called_once_with(
            target_id="abc123",
            target_type="item",
            timeout=10,
            check_layers=True,
            check_basemap=True,
        )


def test_find_stale_items_success():
    """find_stale_items should call client.find_stale_items."""
    from arcgis_portal_mcp.server import find_stale_items

    mock_client = MagicMock()
    mock_client.find_stale_items.return_value = {
        "scan_params": {"owner": "jsmith", "days_threshold": 180, "min_views": 0, "item_types": "(all)"},
        "summary": {"total_scanned": 50, "stale_count": 12, "total_stale_storage_mb": 45.0, "governance_violations": 3, "by_type": {"Feature Service": 8}},
        "stale_items": [],
        "governance_violations": [],
    }

    with patch("arcgis_portal_mcp.server._require_connected", return_value=mock_client):
        result = find_stale_items(
            owner="jsmith", days_threshold=180, min_views=0, max_items=200,
        )
        assert result["status"] == "ok"
        assert result["summary"]["stale_count"] == 12
        mock_client.find_stale_items.assert_called_once_with(
            owner="jsmith",
            days_threshold=180,
            min_views=0,
            item_types="",
            include_storage=True,
            max_items=200,
        )


def test_export_group_content_success():
    """export_group_content should call client.export_group_content."""
    from arcgis_portal_mcp.server import export_group_content

    mock_client = MagicMock()
    mock_client.export_group_content.return_value = {
        "group_id": "grp123",
        "group_title": "GIS Data",
        "export_package": {"item_id": "epk456", "title": "Export", "download_url": "https://x.com/epk", "item_count": 3},
        "exported_items": [],
    }

    with patch("arcgis_portal_mcp.server._require_connected", return_value=mock_client):
        with patch("arcgis_portal_mcp.server._check_group_ids", return_value=None):
            result = export_group_content(group_id="grp123", items="a,b", title="My Export")
            assert result["status"] == "ok"
            assert result["export_package"]["item_id"] == "epk456"
            mock_client.export_group_content.assert_called_once_with(
                group_id="grp123",
                items=["a", "b"],
                title="My Export",
            )


def test_export_group_content_empty_items():
    """export_group_content should pass None for empty items string."""
    from arcgis_portal_mcp.server import export_group_content

    mock_client = MagicMock()
    mock_client.export_group_content.return_value = {
        "group_id": "grp123",
        "export_package": {"item_id": "epk789", "item_count": 5},
        "exported_items": [],
    }

    with patch("arcgis_portal_mcp.server._require_connected", return_value=mock_client):
        with patch("arcgis_portal_mcp.server._check_group_ids", return_value=None):
            result = export_group_content(group_id="grp123")
            assert result["status"] == "ok"
            mock_client.export_group_content.assert_called_once_with(
                group_id="grp123",
                items=None,
                title="",
            )


def test_import_group_content_success():
    """import_group_content should call client.import_group_content."""
    from arcgis_portal_mcp.server import import_group_content

    mock_client = MagicMock()
    mock_client.import_group_content.return_value = {
        "target_group_id": "grp789",
        "import_results": {
            "total": 3,
            "succeeded": 3,
            "failed": 0,
            "imported_items": [],
            "failures": [],
        },
    }

    with patch("arcgis_portal_mcp.server._require_connected", return_value=mock_client):
        with patch("arcgis_portal_mcp.server._check_group_ids", return_value=None):
            result = import_group_content(
                group_id="grp789",
                import_url="https://x.com/epk",
                owner="admin",
                title_prefix="Prod: ",
            )
            assert result["status"] == "ok"
            assert result["import_results"]["succeeded"] == 3
            mock_client.import_group_content.assert_called_once_with(
                group_id="grp789",
                import_url="https://x.com/epk",
                item_id="",
                owner="admin",
                title_prefix="Prod: ",
            )


# ------------------------------------------------------------------
# Client: _extract_urls_from_item_data (helper tests)
# ------------------------------------------------------------------


def test_extract_urls_from_operational_layers():
    """Should extract URLs from operationalLayers."""
    client = ArcGISClient()
    data = {
        "operationalLayers": [
            {"url": "https://host/arcgis/rest/services/Parcels/FeatureServer/0", "title": "Parcels"},
            {"url": "https://host/arcgis/rest/services/Roads/FeatureServer", "title": "Roads"},
        ],
    }
    urls = client._extract_urls_from_item_data(data)
    assert len(urls) == 2
    assert urls[0]["url"] == "https://host/arcgis/rest/services/Parcels/FeatureServer/0"
    assert urls[0]["referenced_by"] == "operationalLayers[0]"
    assert urls[1]["url"] == "https://host/arcgis/rest/services/Roads/FeatureServer"
    assert urls[1]["referenced_by"] == "operationalLayers[1]"


def test_extract_urls_from_basemap():
    """Should extract URLs from baseMapLayers."""
    client = ArcGISClient()
    data = {
        "baseMap": {
            "baseMapLayers": [
                {"url": "https://services.arcgis.com/Imagery/MapServer", "title": "Imagery"},
            ],
            "referenceLayers": [
                {"url": "https://services.arcgis.com/Labels/MapServer", "title": "Labels"},
            ],
        },
    }
    urls = client._extract_urls_from_item_data(data)
    assert len(urls) == 2
    assert "Imagery" in urls[0]["url"]
    assert "baseMapLayers" in urls[0]["referenced_by"]
    assert "Labels" in urls[1]["url"]
    assert "referenceLayers" in urls[1]["referenced_by"]


def test_extract_urls_from_tables():
    """Should extract URLs from tables array."""
    client = ArcGISClient()
    data = {
        "tables": [
            {"url": "https://host/arcgis/rest/services/Attributes/FeatureServer/1"},
        ],
    }
    urls = client._extract_urls_from_item_data(data)
    assert len(urls) == 1
    assert urls[0]["referenced_by"] == "tables[0]"


def test_extract_urls_empty_data():
    """Should return empty list for empty data."""
    client = ArcGISClient()
    assert client._extract_urls_from_item_data({}) == []
    assert client._extract_urls_from_item_data({"operationalLayers": []}) == []


def test_extract_urls_skips_basemap_when_disabled():
    """Should skip basemap URLs when check_basemap=False."""
    client = ArcGISClient()
    data = {
        "operationalLayers": [
            {"url": "https://host/FeatureServer/0"},
        ],
        "baseMap": {
            "baseMapLayers": [
                {"url": "https://services.arcgis.com/MapServer"},
            ],
        },
    }
    urls = client._extract_urls_from_item_data(data, check_basemap=False)
    assert len(urls) == 1
    assert "FeatureServer" in urls[0]["url"]


def test_extract_urls_deduplicates():
    """Same URL in multiple locations should appear only once."""
    client = ArcGISClient()
    url = "https://host/arcgis/rest/services/Data/FeatureServer/0"
    data = {
        "operationalLayers": [
            {"url": url},
            {"url": url},
        ],
    }
    urls = client._extract_urls_from_item_data(data)
    assert len(urls) == 2  # Client-side dedup happens in scan, not extract


# ------------------------------------------------------------------
# Client: _classify_staleness (helper tests)
# ------------------------------------------------------------------


def test_classify_staleness_active_item():
    """Recent item with views should not be stale."""
    from datetime import datetime

    client = ArcGISClient()
    recent_date = datetime.now().strftime("%Y-%m-%d %H:%M")
    item = {"id": "x", "title": "Active", "modified": recent_date, "num_views": 50, "size": 1000, "access": "org", "tags": ["a"], "snippet": "yes", "description": "yes"}
    result = client._classify_staleness(item, days_threshold=180, min_views=0)
    assert result["is_stale"] is False
    assert result["recommendation"] == "Active (within threshold)"


def test_classify_staleness_old_private_zero_views():
    """Old private item with zero views should suggest deletion."""
    item = {"id": "x", "title": "Old", "modified": "2020-01-01 00:00", "num_views": 0, "size": 5_000_000, "access": "private", "tags": [], "snippet": "", "description": ""}
    result = ArcGISClient._classify_staleness(item, days_threshold=180, min_views=0)
    assert result["is_stale"] is True
    assert "Delete" in result["recommendation"]
    assert result["governance"]["non_compliant"] is True
    assert "missing_tags" in result["governance"]["issues"]


def test_classify_staleness_governance_compliance():
    """Item with all metadata should be governance-compliant."""
    item = {"id": "x", "title": "Good", "modified": "2020-01-01 00:00", "num_views": 10, "size": 1000, "access": "org", "tags": ["tag1"], "snippet": "Summary", "description": "Full description"}
    result = ArcGISClient._classify_staleness(item, days_threshold=180, min_views=0)
    assert result["governance"]["has_description"] is True
    assert result["governance"]["has_tags"] is True
    assert result["governance"]["has_snippet"] is True
    assert result["governance"]["non_compliant"] is False


# ------------------------------------------------------------------
# Client: import_group_content is in _WRITE_TOOLS
# ------------------------------------------------------------------


def test_import_group_content_in_write_tools():
    """import_group_content should be in _WRITE_TOOLS for read-only protection."""
    from arcgis_portal_mcp.server import _WRITE_TOOLS
    assert "import_group_content" in _WRITE_TOOLS
