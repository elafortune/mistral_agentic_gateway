"""Étape 6 -- attaques propres à MCP contre de VRAIS serveurs (pas les tests
unitaires en mémoire de l'Étape 3) : tool poisoning, rug pull, shadowing.
Scoring déterministe : la bonne alerte doit apparaître, avec le bon type."""

import asyncio
import os
import socket
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "gateway"))

from contextlib import AsyncExitStack  # noqa: E402

from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402

from integrity import ToolRegistry  # noqa: E402
from result_inspector import ResultInspector  # noqa: E402
from server import Gateway  # noqa: E402

MALICIOUS_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "servers", "malicious_server.py")
AIRLINE_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "servers", "toolkit_mcp_server.py")
POLICY_PATH = os.path.join(os.path.dirname(__file__), "..", "policies", "airline.yaml")


def wait_for_port(host: str, port: int, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.3)
    return False


async def start(cmd: list[str], port: int) -> asyncio.subprocess.Process:
    proc = await asyncio.create_subprocess_exec(
        sys.executable, *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    if not await asyncio.to_thread(wait_for_port, "127.0.0.1", port, 30):
        raise RuntimeError(f"processus pas prêt sur le port {port}")
    return proc


async def fetch_tools(port: int) -> list:
    async with AsyncExitStack() as stack:
        read, write = await stack.enter_async_context(streamable_http_client(f"http://127.0.0.1:{port}/mcp"))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        result = await session.list_tools()
        return result.tools


async def test_tool_poisoning() -> None:
    print("=== 1. Tool poisoning (description piégée dès le premier enregistrement) ===")
    proc = await start([MALICIOUS_SCRIPT, "--version", "1", "--host", "127.0.0.1", "--port", "9500"], 9500)
    try:
        async with Gateway(
            policy_path=POLICY_PATH, server_name="malicious_mcp",
            upstream_url="http://127.0.0.1:9500/mcp", result_inspector=ResultInspector(),
        ) as gw:
            _, alerts = await gw.list_tools_checked()
            print(f"  alertes: {alerts}")
            assert any(a.startswith("TOOL_POISONING") for a in alerts), "le tool poisoning aurait dû être détecté"
            print("  ✅ détecté")
    finally:
        proc.terminate()
        await proc.wait()


async def test_rug_pull() -> None:
    print("\n=== 2. Rug pull (même nom d'outil, définition changée entre deux vraies connexions) ===")
    registry = ToolRegistry()

    proc1 = await start([MALICIOUS_SCRIPT, "--version", "1", "--host", "127.0.0.1", "--port", "9501"], 9501)
    try:
        tools = await fetch_tools(9501)
        alerts = []
        for t in tools:
            alerts += registry.register("malicious_mcp", t.name, t.description or "", t.input_schema or {})
        print(f"  1re connexion (v1) -- alertes: {alerts}")
        assert not alerts, "le premier enregistrement ne doit déclencher aucune alerte"
    finally:
        proc1.terminate()
        await proc1.wait()

    proc2 = await start([MALICIOUS_SCRIPT, "--version", "2", "--host", "127.0.0.1", "--port", "9501"], 9501)
    try:
        tools = await fetch_tools(9501)
        alerts = []
        for t in tools:
            alerts += registry.register("malicious_mcp", t.name, t.description or "", t.input_schema or {})
        print(f"  2e connexion (v2, même serveur, description changée) -- alertes: {alerts}")
        assert any(a.startswith("RUG_PULL") for a in alerts), "le rug pull aurait dû être détecté"
        print("  ✅ détecté")
    finally:
        proc2.terminate()
        await proc2.wait()


async def test_shadowing() -> None:
    print("\n=== 3. Shadowing (deux VRAIS serveurs différents annoncent le même nom d'outil) ===")
    registry = ToolRegistry()

    airline_proc = await start(
        [AIRLINE_SCRIPT, "--domain", "airline", "--transport", "streamable-http", "--host", "127.0.0.1", "--port", "9502"],
        9502,
    )
    malicious_proc = await start([MALICIOUS_SCRIPT, "--version", "1", "--host", "127.0.0.1", "--port", "9503"], 9503)
    try:
        airline_tools = await fetch_tools(9502)
        alerts = []
        for t in airline_tools:
            alerts += registry.register("airline_mcp", t.name, t.description or "", t.input_schema or {})
        assert not alerts, "le serveur légitime ne doit déclencher aucune alerte à lui seul"

        malicious_tools = await fetch_tools(9503)
        target = next(t for t in malicious_tools if t.name == "search_direct_flight")
        alerts = registry.register("malicious_mcp", target.name, target.description or "", target.input_schema or {})
        print(f"  outil homonyme annoncé par 'malicious_mcp' -- alertes: {alerts}")
        assert any(a.startswith("SHADOWING") for a in alerts), "le shadowing aurait dû être détecté"
        print("  ✅ détecté")
    finally:
        airline_proc.terminate()
        malicious_proc.terminate()
        await airline_proc.wait()
        await malicious_proc.wait()


async def main() -> None:
    await test_tool_poisoning()
    await test_rug_pull()
    await test_shadowing()
    print("\n✅ Les trois attaques MCP sont détectées contre de vrais serveurs.")


if __name__ == "__main__":
    asyncio.run(main())
