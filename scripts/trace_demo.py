"""Étape 8 -- paye la dette de traçabilité ouverte depuis l'Étape 1 (span MCP
factice jamais rebranché sur la vraie passerelle) : rejoue deux scénarios
réels à travers la passerelle et l'agent maintenant instrumentés en
OpenTelemetry (`gateway/tracing.py`), pour produire les deux captures
Langfuse attendues par le cadrage :

1. une trace complète de conversation (action autorisée, vérificateur réel) ;
2. une action bloquée par la politique, avec la couche responsable explicite
   dans les attributs du span (`policy.decision`, `policy.reason`).
"""

import asyncio
import os
import socket
import sys
import time
from contextlib import AsyncExitStack

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "gateway"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

from agent import build_agent  # noqa: E402
from tracing import flush  # noqa: E402

AIRLINE_SCRIPT = os.path.join(ROOT, "servers", "toolkit_mcp_server.py")
FRONTEND_SCRIPT = os.path.join(ROOT, "gateway", "mcp_frontend.py")
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


async def run_scenario(label: str, task: str, with_verifier: bool) -> None:
    print(f"\n=== {label} ===")
    airline_port = find_free_port()
    airline_proc = await start_proc(
        [AIRLINE_SCRIPT, "--domain", "airline", "--transport", "streamable-http", "--host", "127.0.0.1", "--port", str(airline_port)],
        airline_port,
    )
    gateway_port = find_free_port()
    env = os.environ.copy()
    if not with_verifier:
        env.pop("GEMINI_API_KEY", None)
    env["UPSTREAM_URL"] = f"http://127.0.0.1:{airline_port}/mcp"
    gateway_proc = await start_proc(
        [FRONTEND_SCRIPT, "--policy", POLICY_PATH, "--server-name", "airline_mcp", "--host", "127.0.0.1", "--port", str(gateway_port)],
        gateway_port, env=env,
    )
    try:
        async with AsyncExitStack() as stack:
            agent = await build_agent(stack, mcp_url=f"http://127.0.0.1:{gateway_port}/mcp", use_gateway=True)
            answer = await agent.run(task, max_steps=10)
        print(f"réponse finale: {answer[:300]}")
    finally:
        gateway_proc.terminate()
        airline_proc.terminate()
        await gateway_proc.wait()
        await airline_proc.wait()
        flush()


async def main() -> None:
    await run_scenario(
        "1. Conversation complète, autorisée (vérificateur réel actif)",
        "Je suis Olivia Gonzalez (user id olivia_gonzalez_2305), je réside à Newark. "
        "J'ai une réservation pour un aller-retour vers le Texas, retour prévu à 15h "
        "aujourd'hui. Je voudrais un vol de retour plus tardif vers Newark (EWR "
        "uniquement) aujourd'hui, ou sinon le premier vol demain. Si ma réservation "
        "est en classe économique de base et ne peut pas être modifiée, j'accepte "
        "d'annuler en utilisant mon assurance voyage car je ne me sens pas bien.",
        with_verifier=True,
    )
    await run_scenario(
        "2. Action bloquée par la politique (couche 4 absente -- échec fermé)",
        "Annule la réservation Z7GOZK.",
        with_verifier=False,
    )
    print("\nTraces envoyées à Langfuse -- vérifier sur http://localhost:3000")


if __name__ == "__main__":
    asyncio.run(main())
