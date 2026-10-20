"""Test direct du serveur MCP airline : liste les outils, appelle
`search_direct_flight` (READ, sans effet de bord) pour vérifier que le
serveur répond correctement en conditions réelles."""

import asyncio
import os
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

SERVER_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "servers", "toolkit_mcp_server.py")


async def main() -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=[SERVER_SCRIPT, "--domain", "airline"],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print(f"{len(tools.tools)} outils exposés :")
            for t in tools.tools:
                print(f"  - {t.name}: {t.description[:70]}")

            print("\n--- Appel réel : search_direct_flight ---")
            result = await session.call_tool(
                "search_direct_flight",
                {"origin": "PHL", "destination": "LGA", "date": "2024-05-16"},
            )
            print("is_error:", result.is_error)
            print("content blocks:", len(result.content))
            for block in result.content:
                text = getattr(block, "text", str(block))
                print("  content:", text[:500])
            print("structured_content:", result.structured_content)


if __name__ == "__main__":
    asyncio.run(main())
