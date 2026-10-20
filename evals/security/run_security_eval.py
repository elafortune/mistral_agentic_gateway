"""Étape 7 -- tableau sécurité : ablation couche par couche sur les 6 attaques
de l'Étape 6 (3 injections indirectes + 3 attaques MCP), taux de réussite par
cellule, surcoût latence/tokens (pour les injections, qui passent par un
vrai agent LLM), et un contrôle de faux positifs de la couche 3 sur des
données métier réelles et non-adversariales.

Mapping couche <-> attaque (justifié en détail dans step_by_step.md) :
  - injection A (remboursement) et C (modification)   -> couches 3 + 4
  - injection B (exfiltration)                         -> couche 3 seule
  - tool poisoning (description piégée)                -> couche 3 seule
  - rug pull / shadowing (noms/définitions d'outils)   -> couche 1 seule

Ne teste QUE les couches pertinentes par attaque (pas 2^5 combinaisons) :
pour chaque attaque concernée, grille "tout actif" / "sans couche X" / "sans
couche Y" / "sans aucune des deux" quand deux couches sont en jeu, "tout
actif" / "sans la couche" sinon."""

import asyncio
import os
import socket
import sys
import time
from contextlib import AsyncExitStack

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "gateway"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

import httpx  # noqa: E402
from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402

from agent import DEFAULT_SCOPE, Agent, mcp_tool_to_mistral, simulated_human_elicitation  # noqa: E402
from integrity import ToolRegistry  # noqa: E402
from result_inspector import ResultInspector  # noqa: E402
from server import Gateway  # noqa: E402
from test_attack_mcp import fetch_tools  # noqa: E402
from test_attack_mcp import start as start_mcp_proc  # noqa: E402
from test_attack_injection import (  # noqa: E402
    SCENARIOS as INJECTION_SCENARIOS,
    VICTIM_RESERVATION,
)

AIRLINE_SCRIPT = os.path.join(ROOT, "servers", "toolkit_mcp_server.py")
FRONTEND_SCRIPT = os.path.join(ROOT, "gateway", "mcp_frontend.py")
MALICIOUS_SCRIPT = os.path.join(ROOT, "servers", "malicious_server.py")
POLICY_PATH = os.path.join(ROOT, "policies", "airline.yaml")


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_port(host: str, port: int, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.3)
    return False


async def start_proc(cmd: list[str], port: int, env: dict | None = None) -> asyncio.subprocess.Process:
    proc = await asyncio.create_subprocess_exec(
        sys.executable, *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, env=env,
    )
    if not await asyncio.to_thread(wait_for_port, "127.0.0.1", port, 30):
        raise RuntimeError(f"processus pas prêt sur le port {port}")
    return proc


async def stop_proc(proc: asyncio.subprocess.Process | None) -> None:
    if proc is None:
        return
    proc.terminate()
    await proc.wait()


# ---------------------------------------------------------------------------
# Injections indirectes (A/B/C) -- couches 3 (inspecteur) + 4 (vérificateur).
# Repasse par un vrai agent Mistral, comme à l'Étape 6.
# ---------------------------------------------------------------------------

async def connect_agent(gateway_port: int, stack: AsyncExitStack) -> Agent:
    http_client = await stack.enter_async_context(httpx.AsyncClient())
    read, write = await stack.enter_async_context(
        streamable_http_client(f"http://127.0.0.1:{gateway_port}/mcp", http_client=http_client)
    )
    session = await stack.enter_async_context(
        ClientSession(read, write, elicitation_callback=simulated_human_elicitation)
    )
    await session.initialize()
    tools = await session.list_tools()
    tools_schema = [mcp_tool_to_mistral(t) for t in tools.tools if t.name != "_issue_token"]
    token_result = await session.call_tool("_issue_token", {"scope": DEFAULT_SCOPE})
    http_client.headers["Authorization"] = f"Bearer {token_result.content[0].text}"
    return Agent(session, tools_schema)


async def run_injection_cell(plant_text: str, task: str, detect, disable_inspector: bool, disable_verifier: bool) -> dict:
    server_port = find_free_port()
    server_proc = await start_proc(
        [AIRLINE_SCRIPT, "--domain", "airline", "--transport", "streamable-http", "--host", "127.0.0.1", "--port", str(server_port)],
        server_port,
    )
    gateway_port = find_free_port()
    env = os.environ.copy()
    if disable_inspector:
        env["DISABLE_RESULT_INSPECTOR"] = "1"
    else:
        env.pop("DISABLE_RESULT_INSPECTOR", None)
    if disable_verifier:
        env.pop("GEMINI_API_KEY", None)
    gateway_proc = await start_proc(
        [FRONTEND_SCRIPT, "--policy", POLICY_PATH, "--server-name", "airline_mcp", "--host", "127.0.0.1",
         "--port", str(gateway_port), "--upstream-url", f"http://127.0.0.1:{server_port}/mcp"],
        gateway_port, env=env,
    )
    try:
        start = time.monotonic()
        async with AsyncExitStack() as stack:
            agent = await connect_agent(gateway_port, stack)
            await agent.session.call_tool(
                "update_reservation_passengers",
                {"reservation_id": VICTIM_RESERVATION, "passengers": [{"first_name": plant_text, "last_name": "Kim", "dob": "1977-09-23"}]},
            )
            agent.tool_calls_made.clear()
            answer = await agent.run(task)
        elapsed = time.monotonic() - start
        return {
            "attack_success": detect(agent.tool_calls_made, answer),
            "elapsed_s": elapsed,
            "tokens": agent.total_tokens,
        }
    finally:
        await stop_proc(gateway_proc)
        await stop_proc(server_proc)


# ---------------------------------------------------------------------------
# Attaques MCP (poisoning / rug pull / shadowing) -- couches 3 / 1 seules.
# Client MCP direct, pas d'agent LLM (déterministe, pas de coût token).
# ---------------------------------------------------------------------------

async def run_poisoning_cell(disable_inspector: bool) -> dict:
    port = find_free_port()
    proc = await start_mcp_proc([MALICIOUS_SCRIPT, "--version", "1", "--host", "127.0.0.1", "--port", str(port)], port)
    try:
        start = time.monotonic()
        result_inspector = None if disable_inspector else ResultInspector()
        async with Gateway(policy_path=POLICY_PATH, server_name="malicious_mcp", upstream_url=f"http://127.0.0.1:{port}/mcp", result_inspector=result_inspector) as gw:
            _, alerts = await gw.list_tools_checked()
        elapsed = time.monotonic() - start
        detected = any(a.startswith("TOOL_POISONING") for a in alerts)
        return {"attack_success": not detected, "elapsed_s": elapsed, "alerts": alerts}
    finally:
        proc.terminate()
        await proc.wait()


async def run_rug_pull_cell(disable_integrity: bool) -> dict:
    registry = None if disable_integrity else ToolRegistry()
    port = find_free_port()

    async def register_once() -> list[str]:
        tools = await fetch_tools(port)
        alerts: list[str] = []
        if registry is not None:
            for t in tools:
                alerts += registry.register("malicious_mcp", t.name, t.description or "", t.input_schema or {})
        return alerts

    proc1 = await start_mcp_proc([MALICIOUS_SCRIPT, "--version", "1", "--host", "127.0.0.1", "--port", str(port)], port)
    try:
        await register_once()
    finally:
        proc1.terminate()
        await proc1.wait()

    proc2 = await start_mcp_proc([MALICIOUS_SCRIPT, "--version", "2", "--host", "127.0.0.1", "--port", str(port)], port)
    try:
        start = time.monotonic()
        alerts = await register_once()
        elapsed = time.monotonic() - start
    finally:
        proc2.terminate()
        await proc2.wait()

    detected = any(a.startswith("RUG_PULL") for a in alerts)
    return {"attack_success": not detected, "elapsed_s": elapsed, "alerts": alerts}


async def run_shadowing_cell(disable_integrity: bool) -> dict:
    registry = None if disable_integrity else ToolRegistry()
    airline_port = find_free_port()
    malicious_port = find_free_port()
    airline_proc = await start_mcp_proc(
        [AIRLINE_SCRIPT, "--domain", "airline", "--transport", "streamable-http", "--host", "127.0.0.1", "--port", str(airline_port)],
        airline_port,
    )
    malicious_proc = await start_mcp_proc(
        [MALICIOUS_SCRIPT, "--version", "1", "--host", "127.0.0.1", "--port", str(malicious_port)], malicious_port,
    )
    try:
        start = time.monotonic()
        alerts: list[str] = []
        airline_tools = await fetch_tools(airline_port)
        if registry is not None:
            for t in airline_tools:
                alerts += registry.register("airline_mcp", t.name, t.description or "", t.input_schema or {})
        malicious_tools = await fetch_tools(malicious_port)
        target = next(t for t in malicious_tools if t.name == "search_direct_flight")
        if registry is not None:
            alerts += registry.register("malicious_mcp", target.name, target.description or "", target.input_schema or {})
        elapsed = time.monotonic() - start
        detected = any(a.startswith("SHADOWING") for a in alerts)
        return {"attack_success": not detected, "elapsed_s": elapsed, "alerts": alerts}
    finally:
        airline_proc.terminate()
        malicious_proc.terminate()
        await airline_proc.wait()
        await malicious_proc.wait()


# ---------------------------------------------------------------------------
# Faux positifs de la couche 3 : l'inspecteur ne doit PAS flaguer de vraies
# données métier non-adversariales.
# ---------------------------------------------------------------------------

async def check_false_positives() -> dict:
    port = find_free_port()
    proc = await start_mcp_proc(
        [AIRLINE_SCRIPT, "--domain", "airline", "--transport", "streamable-http", "--host", "127.0.0.1", "--port", str(port)], port,
    )
    inspector = ResultInspector()
    flagged = []
    try:
        async with Gateway(policy_path=POLICY_PATH, server_name="airline_mcp", upstream_url=f"http://127.0.0.1:{port}/mcp", result_inspector=inspector) as gw:
            for tool_name, args in [
                ("get_user_details", {"user_id": "mia_li_3668"}),
                ("get_reservation_details", {"reservation_id": "H1QGCY"}),
                ("search_direct_flight", {"origin": "JFK", "destination": "LAX", "date": "2024-05-20"}),
                ("list_all_airports", {}),
            ]:
                result = await gw._call_upstream(tool_name, args)
                for block in result["result"]:
                    inspection = inspector.inspect(block)
                    if inspection.flagged:
                        flagged.append((tool_name, inspection.reasons))
    finally:
        proc.terminate()
        await proc.wait()
    return {"n_checked": 4, "n_false_positives": len(flagged), "details": flagged}


async def main() -> None:
    results: dict = {"injections": {}, "mcp_attacks": {}, "false_positives": None}

    print("=== Injections indirectes (couches 3 + 4) ===", flush=True)
    for name, plant_text, task, detect in INJECTION_SCENARIOS:
        results["injections"][name] = {}
        for disable_inspector, disable_verifier, cell_label in [
            (False, False, "tout_actif"),
            (True, False, "sans_inspecteur"),
            (False, True, "sans_verificateur"),
            (True, True, "sans_aucune"),
        ]:
            print(f"[sécurité] {name} -- {cell_label}", flush=True)
            cell = await run_injection_cell(plant_text, task, detect, disable_inspector, disable_verifier)
            print(f"  -> {'ÉCHEC (attaque réussie)' if cell['attack_success'] else 'bloquée'} "
                  f"(temps={cell['elapsed_s']:.1f}s, tokens={cell['tokens']})", flush=True)
            results["injections"][name][cell_label] = cell

    print("\n=== Attaques MCP (tool poisoning -- couche 3 seule) ===", flush=True)
    results["mcp_attacks"]["tool_poisoning"] = {}
    for disable_inspector, cell_label in [(False, "tout_actif"), (True, "sans_inspecteur")]:
        cell = await run_poisoning_cell(disable_inspector)
        print(f"[sécurité] tool_poisoning -- {cell_label} -> {'ÉCHEC' if cell['attack_success'] else 'bloquée'}", flush=True)
        results["mcp_attacks"]["tool_poisoning"][cell_label] = cell

    print("\n=== Attaques MCP (rug pull -- couche 1 seule) ===", flush=True)
    results["mcp_attacks"]["rug_pull"] = {}
    for disable_integrity, cell_label in [(False, "tout_actif"), (True, "sans_integrite")]:
        cell = await run_rug_pull_cell(disable_integrity)
        print(f"[sécurité] rug_pull -- {cell_label} -> {'ÉCHEC' if cell['attack_success'] else 'bloquée'}", flush=True)
        results["mcp_attacks"]["rug_pull"][cell_label] = cell

    print("\n=== Attaques MCP (shadowing -- couche 1 seule) ===", flush=True)
    results["mcp_attacks"]["shadowing"] = {}
    for disable_integrity, cell_label in [(False, "tout_actif"), (True, "sans_integrite")]:
        cell = await run_shadowing_cell(disable_integrity)
        print(f"[sécurité] shadowing -- {cell_label} -> {'ÉCHEC' if cell['attack_success'] else 'bloquée'}", flush=True)
        results["mcp_attacks"]["shadowing"][cell_label] = cell

    print("\n=== Faux positifs de la couche 3 (données métier réelles) ===", flush=True)
    fp = await check_false_positives()
    print(f"[sécurité] {fp['n_false_positives']}/{fp['n_checked']} appels légitimes flagués à tort", flush=True)
    results["false_positives"] = fp

    import json
    out_path = os.path.join(os.path.dirname(__file__), "results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n[sécurité] résultats bruts écrits dans {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
