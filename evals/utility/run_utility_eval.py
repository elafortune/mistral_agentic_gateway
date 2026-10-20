"""Étape 7 -- tableau utilité : pass^1 / pass^k sur 3 configurations (sans
passerelle / avec passerelle / avec passerelle + vérificateur), k essais.

Ne rejoue PAS le harnais multi-tours complet de τ²-bench (simulateur
d'utilisateur, orchestrateur) -- scope réduit assumé, cf. `tasks.py` et
`step_by_step.md`. Réutilise en revanche le VRAI scoring déterministe de
τ²-bench (`EnvironmentEvaluator.calculate_reward`) : rejeu des actions dorées
de la tâche sur un environnement vierge, comparaison de hash de base avec la
trajectoire réelle de notre propre agent (`agent/agent.py`) -- même principe
que la non-régression de l'Étape 2, généralisé à 10 tâches.
"""

import asyncio
import json
import os
import socket
import sys
import time
from contextlib import AsyncExitStack

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, os.path.join(ROOT, "gateway"))
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.dirname(__file__))

from agent import build_agent  # noqa: E402
from tasks import TASKS  # noqa: E402

from tau2.data_model.message import (  # noqa: E402
    AssistantMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from tau2.evaluator.evaluator import EnvironmentEvaluator  # noqa: E402
from tau2.registry import registry  # noqa: E402

AIRLINE_SCRIPT = os.path.join(ROOT, "servers", "toolkit_mcp_server.py")
FRONTEND_SCRIPT = os.path.join(ROOT, "gateway", "mcp_frontend.py")
POLICY_PATH = os.path.join(ROOT, "policies", "airline.yaml")
CONFIGS = ["sans_passerelle", "avec_passerelle", "avec_passerelle_verifier"]
K = 3

TAU2_TASKS = {t.id: t for t in registry.get_tasks_loader("airline")()}
ENV_CONSTRUCTOR = registry.get_env_constructor("airline")


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


def to_tau2_trajectory(messages: list[dict]) -> list:
    """Convertit l'historique Mistral/OpenAI de `agent.py` en trajectoire
    τ²-bench (`tau2.data_model.message`), seul format accepté par
    `EnvironmentEvaluator.calculate_reward`."""
    traj = []
    for m in messages:
        role = m["role"]
        if role == "system":
            traj.append(SystemMessage(role="system", content=m.get("content") or ""))
        elif role == "user":
            traj.append(UserMessage(role="user", content=m.get("content") or ""))
        elif role == "assistant":
            raw_tool_calls = m.get("tool_calls")
            tool_calls = None
            if raw_tool_calls:
                tool_calls = []
                for tc in raw_tool_calls:
                    args = tc["function"]["arguments"]
                    if isinstance(args, str):
                        args = json.loads(args or "{}")
                    tool_calls.append(ToolCall(id=tc["id"], name=tc["function"]["name"], arguments=args, requestor="assistant"))
            traj.append(AssistantMessage(role="assistant", content=m.get("content") or "", tool_calls=tool_calls))
        elif role == "tool":
            traj.append(ToolMessage(id=m["tool_call_id"], role="tool", content=m.get("content") or "", requestor="assistant"))
        else:
            raise ValueError(f"rôle de message inattendu: {role}")
    return traj


async def run_one_trial(task: dict, config: str) -> tuple[float, int, list]:
    """Démarre un serveur airline (et une passerelle si besoin) FRAIS pour
    isoler chaque essai (base en mémoire, pas de contamination entre essais
    ni entre tâches), fait tourner l'agent, puis note avec le scoring réel
    de τ²-bench."""
    airline_port = find_free_port()
    airline_proc = await start_proc(
        [AIRLINE_SCRIPT, "--domain", "airline", "--transport", "streamable-http", "--host", "127.0.0.1", "--port", str(airline_port)],
        airline_port,
    )
    frontend_proc = None
    try:
        mcp_url = f"http://127.0.0.1:{airline_port}/mcp"
        use_gateway = config != "sans_passerelle"
        if use_gateway:
            frontend_port = find_free_port()
            fenv = os.environ.copy()
            if config == "avec_passerelle":
                fenv.pop("GEMINI_API_KEY", None)  # force l'échec fermé du vérificateur (Étape 5)
            fenv["UPSTREAM_URL"] = mcp_url
            frontend_proc = await start_proc(
                [FRONTEND_SCRIPT, "--policy", POLICY_PATH, "--host", "127.0.0.1", "--port", str(frontend_port)],
                frontend_port, env=fenv,
            )
            mcp_url = f"http://127.0.0.1:{frontend_port}/mcp"

        start = time.monotonic()
        async with AsyncExitStack() as stack:
            agent = await build_agent(stack, mcp_url=mcp_url, use_gateway=use_gateway)
            # 15 et pas le défaut de 8 (pensé pour les attaques Étape 6, une
            # seule action) -- certaines tâches ici demandent plusieurs
            # recherches exploratoires avant l'action finale (ex. tâche 19).
            await agent.run(task["instruction"], max_steps=15)
        elapsed = time.monotonic() - start

        traj = to_tau2_trajectory(agent.messages)
        tau2_task = TAU2_TASKS[task["id"]]
        # strict_replay=False : notre serveur MCP (Étape 2) appelle
        # `toolkit.use_tool()` directement, en contournant le dispatcher
        # `Environment.get_response()` de τ²-bench -- un appel d'outil en
        # échec produit donc un texte d'erreur différent en direct qu'au
        # rejeu interne de `calculate_reward` (texte générique "Error
        # executing tool X"). Le hash de base (le vrai signal) n'est pas
        # affecté, seule l'égalité texte annexe que `strict_replay=True`
        # exige en plus -- documentée par τ²-bench pour ce cas précis.
        reward_info = EnvironmentEvaluator.calculate_reward(ENV_CONSTRUCTOR, tau2_task, traj, strict_replay=False)
        return reward_info.reward, elapsed, agent.tool_calls_made
    finally:
        await stop_proc(frontend_proc)
        await stop_proc(airline_proc)


async def main() -> None:
    results: dict[str, dict[str, list[dict]]] = {t["slug"]: {c: [] for c in CONFIGS} for t in TASKS}

    for task in TASKS:
        for config in CONFIGS:
            for trial in range(K):
                print(f"[eval] tâche={task['slug']} config={config} essai={trial + 1}/{K}", flush=True)
                try:
                    reward, elapsed, tool_calls = await run_one_trial(task, config)
                except Exception as exc:  # noqa: BLE001 -- on veut continuer les autres essais même si un échoue
                    print(f"[eval]   ERREUR: {exc!r}", flush=True)
                    reward, elapsed, tool_calls = 0.0, None, []
                print(f"[eval]   reward={reward} temps={elapsed} outils={tool_calls}", flush=True)
                results[task["slug"]][config].append(
                    {"reward": reward, "elapsed_s": elapsed, "tool_calls": tool_calls}
                )

    out_path = os.path.join(os.path.dirname(__file__), "results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\n[eval] résultats bruts écrits dans {out_path}")

    print("\n=== Tableau utilité (pass^1 / pass^{K}) ===".replace("{K}", str(K)))
    header = f"{'tâche':<28}" + "".join(f"{c:<28}" for c in CONFIGS)
    print(header)
    for task in TASKS:
        row = f"{task['slug']:<28}"
        for config in CONFIGS:
            runs = results[task["slug"]][config]
            rewards = [r["reward"] for r in runs]
            pass1 = sum(rewards) / len(rewards) if rewards else float("nan")
            passk = 1.0 if rewards and all(r == 1.0 for r in rewards) else 0.0
            row += f"{f'{pass1:.2f} / {passk:.0f}':<28}"
        print(row)

    print("\n=== Moyennes globales ===")
    for config in CONFIGS:
        all_rewards = [r["reward"] for t in TASKS for r in results[t["slug"]][config]]
        pass1_global = sum(all_rewards) / len(all_rewards) if all_rewards else float("nan")
        passk_global = sum(
            1.0 if all(r["reward"] == 1.0 for r in results[t["slug"]][config]) else 0.0 for t in TASKS
        ) / len(TASKS)
        print(f"{config:<28} pass^1={pass1_global:.2f}  pass^{K}={passk_global:.2f}")


if __name__ == "__main__":
    asyncio.run(main())
