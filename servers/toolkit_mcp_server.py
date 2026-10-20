"""Serveur MCP générique qui ré-expose n'importe quel domaine τ²-bench
(airline/retail/telecom) via le protocole MCP, en réutilisant directement
l'environnement et les outils de τ²-bench -- pas de réimplémentation, juste
un changement de transport (appel Python direct -> tools/call MCP).

Étape 2 du cadrage : ce serveur est utilisé en direct par l'agent (pas encore
de passerelle) pour valider par non-régression que MCP-iser les outils ne
change pas leur comportement.
"""

import argparse
import importlib
import inspect

from pydantic_core import PydanticUndefined

from mcp.server.mcpserver import MCPServer

DOMAIN_ENVIRONMENTS = {
    "airline": "tau2.domains.airline.environment",
    "retail": "tau2.domains.retail.environment",
    "telecom": "tau2.domains.telecom.environment",
}


def load_toolkit(domain: str):
    module = importlib.import_module(DOMAIN_ENVIRONMENTS[domain])
    env = module.get_environment()
    return env.tools


def build_signature(params_model) -> inspect.Signature:
    """Construit une signature Python à partir du modèle pydantic des paramètres
    de l'outil τ²-bench -- nécessaire pour que MCPServer.add_tool() génère le
    bon schéma JSON (il introspecte fn.__signature__, pas un schéma fourni à part)."""
    parameters = []
    for field_name, field in params_model.model_fields.items():
        default = inspect.Parameter.empty if field.is_required() else field.default
        parameters.append(
            inspect.Parameter(
                field_name,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=field.annotation,
                default=default,
            )
        )
    return inspect.Signature(parameters)


def make_wrapper(toolkit, tool_name: str, tool):
    """Une fonction générique par outil, dispatchée vers `toolkit.use_tool()` --
    pas de logique métier réimplémentée, juste le changement de transport."""

    def wrapper(**kwargs):
        result = toolkit.use_tool(tool_name, **kwargs)
        if hasattr(result, "model_dump"):
            return result.model_dump(mode="json")
        if isinstance(result, (list, tuple)) and not result:
            # Le SDK `mcp` aplatit une liste en chaînant le contenu de chaque
            # élément (`_convert_to_content`) -- une liste vide produit donc
            # ZÉRO bloc de contenu, indiscernable d'une absence de réponse
            # pour l'agent (texte "" plutôt que "[]"), trouvé en observant un
            # agent réel boucler sur une recherche sans résultat (Étape 7).
            return "[]"
        return result

    wrapper.__name__ = tool_name
    wrapper.__signature__ = build_signature(tool.params)
    return wrapper


def build_server(domain: str) -> MCPServer:
    toolkit = load_toolkit(domain)
    server = MCPServer(name=f"tau2-{domain}")
    for name, tool in toolkit.get_tools().items():
        description = tool.openai_schema["function"]["description"]
        server.add_tool(make_wrapper(toolkit, name, tool), name=name, description=description)
    return server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--domain", required=True, choices=list(DOMAIN_ENVIRONMENTS))
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    server = build_server(args.domain)
    if args.transport == "stdio":
        server.run(transport="stdio")
    else:
        server.run(transport="streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
