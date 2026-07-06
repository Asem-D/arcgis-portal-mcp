"""Basic tests for arcgis_portal_mcp package structure."""
import pytest
from arcgis_portal_mcp import __version__
from arcgis_portal_mcp.client import ArcGISClient


def test_version():
    assert __version__ == "1.2.0"


def test_client_init():
    client = ArcGISClient()
    assert not client.is_connected
    assert client.portal_url is None
    assert client.token is None
    assert client.username is None


def test_client_connect_bad_token():
    client = ArcGISClient()
    with pytest.raises(ConnectionError):
        client.connect_token("https://fake.portal.com", "bad-token")


def test_server_tools_count():
    from arcgis_portal_mcp.server import mcp
    tool_names = list(mcp._tool_manager._tools.keys())
    expected = [
        # Phase 1
        "connect_portal", "search_content", "get_item_details",
        "list_layers", "query_features", "list_users", "list_groups",
        "portal_health", "server_status",
        # Phase 2, Feature CRUD
        "add_features", "update_features", "delete_features",
        # Phase 2, User/Group Management
        "get_user_details", "create_group", "invite_to_group",
        # Phase 2, Content Management
        "update_item", "delete_item", "share_item", "get_item_data",
        # Phase 3, Service Publishing
        "upload_item", "publish_from_item", "create_service",
        # Phase 3, Geoprocessing
        "execute_gp_task", "submit_gp_job", "get_gp_job_status",
        # Phase 3, Portal Admin
        "portal_system_info", "list_licenses", "portal_usage",
        # Phase 3, Batch Operations
        "batch_delete_items", "batch_share_items", "batch_update_items",
        # Map Export
        "export_map_image",
    ]
    assert tool_names == expected, f"Expected tools: {expected}, got: {tool_names}"


def test_server_resource_count():
    from arcgis_portal_mcp.server import mcp
    resources = list(mcp._resource_manager._resources.keys())
    assert len(resources) == 1
    assert "arcgis://guide" in resources


def test_where_clause_validation():
    """WHERE clause validation blocks dangerous SQL patterns."""
    from arcgis_portal_mcp.server import _validate_where_clause

    # Safe clauses
    assert _validate_where_clause("") is None
    assert _validate_where_clause("1=1") is None
    assert _validate_where_clause("STATUS = 'Active'") is None
    assert _validate_where_clause("POP > 1000 AND NAME LIKE '%test%'") is None
    assert _validate_where_clause("OBJECTID IN (1, 2, 3)") is None

    # Dangerous patterns
    assert _validate_where_clause("1=1; DROP TABLE users") is not None
    assert _validate_where_clause("1=1 -- comment") is not None
    assert _validate_where_clause("1=1 /* comment */") is not None
    assert _validate_where_clause("DROP TABLE users") is not None
    assert _validate_where_clause("DELETE FROM users") is not None
    assert _validate_where_clause("INSERT INTO users VALUES (1)") is not None
    assert _validate_where_clause("UPDATE users SET role='admin'") is not None
    assert _validate_where_clause("TRUNCATE TABLE users") is not None
