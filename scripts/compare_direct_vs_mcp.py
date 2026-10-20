"""Étape 2 -- comparaison de non-régression : rejoue l'action de référence
réelle d'une tâche τ²-bench (task 19, domaine airline : cancel_reservation
sur Z7GOZK) une fois en appel direct (comme le fait l'agent natif de
τ²-bench), une fois via MCP -- et compare l'état final.

Volontairement PAS un rejeu de l'agent LLM complet : ça réintroduirait le
non-déterminisme déjà démontré au projet SWE-bench (même code, résultat
parfois différent). Ici on isole exactement ce que cette étape doit prouver
-- MCP change-t-il le résultat d'une séquence d'actions fixe ? -- sans bruit
de raisonnement LLM.
"""

import asyncio
import os
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from tau2.domains.airline.environment import get_environment

SERVER_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "servers", "toolkit_mcp_server.py")
RESERVATION_ID = "Z7GOZK"  # action de référence de la tâche 19


def run_direct() -> dict:
    """Chemin actuel de τ²-bench : appel Python direct sur le toolkit."""
    env = get_environment()  # état pristine, chargé depuis le fichier source
    env.tools.use_tool("cancel_reservation", reservation_id=RESERVATION_ID)
    result = env.tools.use_tool("get_reservation_details", reservation_id=RESERVATION_ID)
    return result.model_dump(mode="json")


async def run_via_mcp() -> dict:
    """Même séquence, mais via mon serveur MCP -- même environnement pristine
    de départ (get_environment() est appelé à l'identique côté serveur)."""
    params = StdioServerParameters(command=sys.executable, args=[SERVER_SCRIPT, "--domain", "airline"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await session.call_tool("cancel_reservation", {"reservation_id": RESERVATION_ID})
            result = await session.call_tool("get_reservation_details", {"reservation_id": RESERVATION_ID})
            # Le contenu MCP est une liste de blocs texte JSON (un par élément
            # si le résultat est structuré) -- ici un seul objet Reservation,
            # donc un seul bloc.
            import json

            return json.loads(result.content[0].text)


def main() -> None:
    direct_result = run_direct()
    mcp_result = asyncio.run(run_via_mcp())

    print("--- Résultat direct ---")
    print(direct_result)
    print("\n--- Résultat via MCP ---")
    print(mcp_result)

    if direct_result == mcp_result:
        print("\n✅ IDENTIQUE -- non-régression confirmée sur cette action.")
    else:
        print("\n❌ DIFFÉRENT -- régression détectée, à investiguer.")
        diff_keys = [k for k in direct_result if direct_result.get(k) != mcp_result.get(k)]
        print("Champs différents:", diff_keys)


if __name__ == "__main__":
    main()
