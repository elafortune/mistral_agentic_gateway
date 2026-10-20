"""Étape 4 -- expose la `Gateway` elle-même comme un serveur MCP (streamable-http),
pour qu'un agent dans un autre conteneur/réseau Docker s'y connecte comme à
n'importe quel serveur MCP, sans jamais voir directement le serveur en aval.

Authentification : un jeton à portée limitée est transmis via l'en-tête HTTP
`Authorization: Bearer <token>` de la session MCP (pas comme argument d'outil
-- un agument de ce genre serait visible/manipulable par le modèle). Le jeton
lui-même est obtenu via l'outil `_issue_token`, sans contrôle d'identité pour
l'instant : cette étape valide l'isolation réseau/conteneurs, pas encore le
contrôle d'accès de bout en bout (qui a le droit de demander quelle portée)
-- limite assumée et documentée, à traiter avec le vérificateur (Étape 5) et
les scénarios d'attaque (Étape 6).

Étape 5 : `require_human` utilise l'elicitation MCP (`ctx.elicit`) pour
solliciter le client connecté (l'agent) -- c'est lui, pas la passerelle, qui
sait comment joindre un humain (ou le simule, en évaluation automatique).
"""

import inspect
import os

from mcp.server.mcpserver import Context, MCPServer
from pydantic import BaseModel

from result_inspector import ResultInspector
from server import Gateway
from verifier import Verifier


class ApprovalSchema(BaseModel):
    approve: bool


def build_signature(tool) -> inspect.Signature:
    """Signature synthétique à partir du schéma JSON réel de l'outil en amont
    (même technique que `servers/toolkit_mcp_server.py`, mais depuis un schéma
    JSON brut -- ce que renvoie `tools/list` -- plutôt qu'un modèle pydantic."""
    schema = tool.input_schema or {}
    required = set(schema.get("required", []))
    type_map = {"string": str, "integer": int, "number": float, "boolean": bool, "array": list, "object": dict}
    parameters = []
    for prop_name, prop_schema in schema.get("properties", {}).items():
        annotation = type_map.get(prop_schema.get("type"), str)
        default = inspect.Parameter.empty if prop_name in required else None
        parameters.append(
            inspect.Parameter(prop_name, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=annotation, default=default)
        )
    parameters.append(inspect.Parameter("ctx", inspect.Parameter.KEYWORD_ONLY, annotation=Context))
    return inspect.Signature(parameters)


async def elicit_human(ctx: Context, tool_name: str, arguments: dict, note: str) -> bool:
    result = await ctx.elicit(
        message=f"Confirmation humaine requise pour '{tool_name}' avec {arguments}. {note}",
        schema=ApprovalSchema,
    )
    return result.action == "accept" and bool(result.data and result.data.approve)


def make_guarded_wrapper(gw: Gateway, tool_name: str):
    async def wrapper(ctx: Context, **kwargs) -> dict:
        token = (ctx.headers.get("authorization") or "").removeprefix("Bearer ").strip()

        async def elicit_fn(name: str, arguments: dict, note: str) -> bool:
            return await elicit_human(ctx, name, arguments, note)

        return await gw.call_tool_guarded(tool_name, kwargs, session_token=token, elicit_fn=elicit_fn)

    wrapper.__name__ = tool_name
    return wrapper


async def build_frontend(gw: Gateway) -> MCPServer:
    """À appeler après `async with Gateway(...) as gw:` -- construit le serveur
    MCP qui expose les outils réels de l'amont, chacun passé par le garde-fou."""
    tools, alerts = await gw.list_tools_checked()
    for alert in alerts:
        print(f"[gateway] ALERTE INTÉGRITÉ AU DÉMARRAGE: {alert}")

    frontend = MCPServer(name=f"gateway-{gw.server_name}")

    @frontend.add_tool
    def _issue_token(scope: list[str]) -> str:
        """Émet un jeton de session à portée limitée (liste d'outils autorisés)."""
        return gw.tokens.issue(scope=scope)

    for tool in tools:
        wrapper = make_guarded_wrapper(gw, tool.name)
        wrapper.__signature__ = build_signature(tool)
        frontend.add_tool(wrapper, name=tool.name, description=tool.description or "")

    return frontend


async def run(policy_path: str, server_name: str, host: str, port: int, upstream_command: str | None = None, upstream_args: list[str] | None = None, upstream_url: str | None = None) -> None:
    gemini_key = os.environ.get("GEMINI_API_KEY")
    verifier = Verifier(api_key=gemini_key) if gemini_key else None
    if verifier is None:
        print("[gateway] GEMINI_API_KEY absente -- require_verifier échouera fermé (aucun vérificateur)")

    # Couches 1 et 3 désactivables par variable d'environnement -- pour
    # l'ablation (Étape 6/7) : mesurer l'effet de CHAQUE couche seule sur un
    # type d'attaque donné, sans toucher aux autres garde-fous. La couche 4
    # (vérificateur) s'ablate déjà nativement via l'absence de GEMINI_API_KEY.
    result_inspector = None if os.environ.get("DISABLE_RESULT_INSPECTOR") else ResultInspector()
    enable_integrity = not os.environ.get("DISABLE_INTEGRITY")

    async with Gateway(
        policy_path=policy_path,
        server_name=server_name,
        upstream_command=upstream_command,
        upstream_args=upstream_args,
        upstream_url=upstream_url,
        verifier=verifier,
        result_inspector=result_inspector,
        enable_integrity=enable_integrity,
    ) as gw:
        frontend = await build_frontend(gw)
        await frontend.run_streamable_http_async(host=host, port=port)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", required=True)
    parser.add_argument("--server-name", default="upstream")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--upstream-url", default=os.environ.get("UPSTREAM_URL"))
    args = parser.parse_args()

    if not args.upstream_url:
        raise SystemExit("--upstream-url (ou $UPSTREAM_URL) requis pour l'Étape 4 (amont en HTTP)")

    import anyio

    anyio.run(run, args.policy, args.server_name, args.host, args.port, None, None, args.upstream_url)


if __name__ == "__main__":
    main()
