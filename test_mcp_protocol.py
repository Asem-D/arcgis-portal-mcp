"""Test the MCP server via stdio protocol - exercises the full tool chain."""

import asyncio
import json
import os
import sys
from pathlib import Path

# Ensure .env is loaded
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


async def main():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "arcgis_portal_mcp.server"],
        cwd=str(Path(__file__).parent),
    )

    print("Starting MCP server via stdio...")
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            # Initialize
            print("Initializing session...")
            init_result = await session.initialize()
            print(f"Server initialized: {init_result}")
            print()

            # List tools
            print("=" * 60)
            print("TOOLS AVAILABLE")
            print("=" * 60)
            tools = await session.list_tools()
            for tool in tools.tools:
                print(f"  {tool.name}: {tool.description[:80]}...")
            print(f"\nTotal: {len(tools.tools)} tools")
            print()

            # Test 1: connect_portal (auto)
            print("=" * 60)
            print("TEST 1: connect_portal (auto)")
            print("=" * 60)
            result = await session.call_tool("connect_portal", {"auth_method": "auto"})
            for block in result.content:
                if hasattr(block, "text"):
                    print(block.text)
            print()

            # Test 2: portal_health
            print("=" * 60)
            print("TEST 2: portal_health")
            print("=" * 60)
            result = await session.call_tool("portal_health", {})
            for block in result.content:
                if hasattr(block, "text"):
                    print(block.text)
            print()

            # Test 3: search_content
            print("=" * 60)
            print("TEST 3: search_content")
            print("=" * 60)
            result = await session.call_tool("search_content", {"query": "*", "max_results": 3})
            for block in result.content:
                if hasattr(block, "text"):
                    text = block.text[:2000]
                    print(text)
            print()

            # Test 4: list_users
            print("=" * 60)
            print("TEST 4: list_users")
            print("=" * 60)
            result = await session.call_tool("list_users", {})
            for block in result.content:
                if hasattr(block, "text"):
                    text = block.text[:2000]
                    print(text)
            print()

            # Test 5: list_groups
            print("=" * 60)
            print("TEST 5: list_groups")
            print("=" * 60)
            result = await session.call_tool("list_groups", {})
            for block in result.content:
                if hasattr(block, "text"):
                    text = block.text[:2000]
                    print(text)
            print()

            # Test 6: server_status
            print("=" * 60)
            print("TEST 6: server_status")
            print("=" * 60)
            result = await session.call_tool("server_status", {})
            for block in result.content:
                if hasattr(block, "text"):
                    text = block.text[:2000]
                    print(text)
            print()

            print("=" * 60)
            print("ALL MCP PROTOCOL TESTS COMPLETE")
            print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
