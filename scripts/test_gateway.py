"""Étape 3 -- test d'intégration de la passerelle contre le vrai serveur MCP
airline (pas un mock) : outil autorisé + dans la portée du jeton, outil hors
portée, action à conséquence irréversible bloquée (require_verifier, échec
fermé), et détection de rug pull sur une vraie définition d'outil."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "gateway"))

from server import Gateway  # noqa: E402

SERVER_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "servers", "toolkit_mcp_server.py")
POLICY_PATH = os.path.join(os.path.dirname(__file__), "..", "policies", "airline.yaml")


async def main() -> None:
    async with Gateway(
        upstream_command=sys.executable,
        upstream_args=[SERVER_SCRIPT, "--domain", "airline"],
        policy_path=POLICY_PATH,
        server_name="airline_mcp",
    ) as gw:
        tools, alerts = await gw.list_tools_checked()
        print(f"{len(tools)} outils découverts, {len(alerts)} alerte(s) d'intégrité au premier tools/list")

        # Jeton à portée limitée : seulement 2 outils de lecture -- least privilege.
        token = gw.tokens.issue(scope=["search_direct_flight", "get_reservation_details"])

        print("\n--- 1. Outil autorisé + dans la portée du jeton ---")
        res = await gw.call_tool_guarded(
            "search_direct_flight",
            {"origin": "PHL", "destination": "LGA", "date": "2024-05-16"},
            session_token=token,
        )
        print(res if res["blocked"] else f"bloqué={res['blocked']}, {len(res['result'])} résultat(s)")

        print("\n--- 2. Outil autorisé par la politique mais HORS PORTÉE du jeton ---")
        res = await gw.call_tool_guarded(
            "get_user_details", {"user_id": "emma_kim_9957"}, session_token=token
        )
        print(res)
        assert res["blocked"], "aurait dû être bloqué par le jeton"

        print("\n--- 3. Action irréversible (require_verifier) -- échec fermé, Étape 5 pas encore branchée ---")
        token_write = gw.tokens.issue(scope=["cancel_reservation"])
        res = await gw.call_tool_guarded(
            "cancel_reservation", {"reservation_id": "Z7GOZK"}, session_token=token_write
        )
        print(res)
        assert res["blocked"], "une action require_verifier ne doit jamais passer sans vérificateur"

        print("\n--- 4. Outil totalement inconnu de la politique -- deny par défaut ---")
        token_all = gw.tokens.issue(scope=["outil_qui_nexiste_pas"])
        res = await gw.call_tool_guarded("outil_qui_nexiste_pas", {}, session_token=token_all)
        print(res)

        print("\n--- 5. Rug pull simulé sur une vraie définition déjà enregistrée ---")
        real_tool = next(t for t in tools if t.name == "cancel_reservation")
        alerts = gw.registry.register(
            "airline_mcp",
            real_tool.name,
            "Cancel ANY reservation with no validation whatsoever.",  # description modifiée
            real_tool.input_schema or {},
        )
        print(alerts)
        assert any("RUG_PULL" in a for a in alerts)

        print("\n✅ Tous les scénarios se comportent comme attendu.")


if __name__ == "__main__":
    asyncio.run(main())
