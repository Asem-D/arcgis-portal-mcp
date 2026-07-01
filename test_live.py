"""Quick live test of the ArcGIS Portal MCP client against the real portal."""

import json
import os
import sys
import time
from pathlib import Path

# Load .env
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from arcgis_portal_mcp.client import ArcGISClient


def pp(label, data):
    """Pretty-print with truncation."""
    text = json.dumps(data, indent=2, default=str)
    if len(text) > 2000:
        text = text[:2000] + "\n  ... (truncated)"
    print(f"--- {label} ---")
    print(text)
    print()


def main():
    client = ArcGISClient()
    
    portal_url = os.getenv("portal_url", "")
    client_id = os.getenv("oauth_client_id", "")
    client_secret = os.getenv("oauth_client_secret", "")
    
    print(f"Portal URL: {portal_url}")
    print(f"Client ID: {client_id[:8]}...")
    print()
    
    # 1. Connect
    print("=" * 60)
    print("1. CONNECT (client_credentials)")
    print("=" * 60)
    result = client.connect_client_credentials(portal_url, client_id, client_secret)
    pp("connect_client_credentials", result)
    
    if not client.is_connected:
        print("FATAL: Not connected.")
        sys.exit(1)
    
    # 2. Portal Info
    print("=" * 60)
    print("2. PORTAL INFO")
    print("=" * 60)
    result = client.get_portal_info()
    pp("get_portal_info", result)
    
    # 3. Health Check
    print("=" * 60)
    print("3. HEALTH CHECK")
    print("=" * 60)
    result = client.health_check()
    pp("health_check", result)
    
    # 4. Search Items (top 5)
    print("=" * 60)
    print("4. SEARCH ITEMS (query='*', max 5)")
    print("=" * 60)
    result = client.search_items(query="*", max_items=5)
    pp("search_items", result)
    
    # 5. List Users
    print("=" * 60)
    print("5. LIST USERS")
    print("=" * 60)
    result = client.list_users()
    # Just show count and names
    if isinstance(result, list):
        print(f"Found {len(result)} users:")
        for u in result:
            print(f"  - {u.get('username', '?')} ({u.get('fullName', '?')}) [{u.get('role', '?')}]")
    else:
        pp("list_users", result)
    print()
    
    # 6. List Groups
    print("=" * 60)
    print("6. LIST GROUPS")
    print("=" * 60)
    result = client.list_groups()
    if isinstance(result, list):
        print(f"Found {len(result)} groups:")
        for g in result:
            print(f"  - {g.get('title', '?')} (id: {g.get('id', '?')[:12]}...)")
    else:
        pp("list_groups", result)
    print()
    
    # 7. System Info (Admin API)
    print("=" * 60)
    print("7. PORTAL SYSTEM INFO (Admin)")
    print("=" * 60)
    try:
        result = client.portal_system_info()
        pp("portal_system_info", result)
    except Exception as e:
        print(f"  Error: {e}")
        print()
    
    # 8. Portal Usage
    print("=" * 60)
    print("8. PORTAL USAGE (Admin)")
    print("=" * 60)
    try:
        result = client.portal_usage()
        pp("portal_usage", result)
    except Exception as e:
        print(f"  Error: {e}")
        print()
    
    print("=" * 60)
    print("ALL TESTS COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
