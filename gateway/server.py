"""Passerelle MCP -- proxy entre l'agent et un serveur MCP en aval.

Tout `tools/call` passe par ici : jeton à portée limitée vérifié d'abord (le
serveur en aval ne reçoit jamais l'identité de l'agent), puis politique
(allow/deny/require_verifier/require_human), puis seulement si autorisé,
relais réel vers le serveur MCP en aval.

`require_verifier` (Étape 5) : si un `Verifier` est fourni au constructeur,
un LLM d'un autre fournisseur contrôle l'appel avant exécution réelle. Sans
vérificateur fourni, échec fermé -- explicite, jamais une approbation
silencieuse. Même logique pour `require_human` avec `elicit_fn`, fourni par
appel (c'est le *frontend* MCP, pas la `Gateway`, qui sait comment solliciter
un humain -- cf. `mcp_frontend.py`). Les deux sont optionnels et injectables
exprès : l'évaluation par ablation (Étape 7) doit pouvoir mesurer l'utilité/
la sécurité avec et sans chaque couche.

Expose aussi la passerelle elle-même comme un serveur MCP à part entière
(`mcp_frontend.py`, Étape 4), pour que l'agent s'y connecte comme à n'importe
quel serveur, sans jamais voir directement le serveur en aval.
"""

import sys
import time
from contextlib import AsyncExitStack, nullcontext
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

from integrity import ToolRegistry
from policy import Decision, Policy
from result_inspector import ResultInspector
from tokens import TokenStore
from tracing import init_tracing
from verifier import Verifier

_tracer = init_tracing("mistral-agentic-gateway")

ElicitFn = Callable[[str, dict, str], Awaitable[bool]]


@dataclass
class DecisionLogEntry:
    tool: str
    arguments: dict
    decision: str
    reason: str
    timestamp: float = field(default_factory=time.time)


class Gateway:
    """Connexion à l'amont au choix : stdio (sous-processus local, Étape 3) ou
    streamable-http (serveur distant, Étape 4 -- nécessaire dès que l'amont
    tourne dans un autre conteneur, sur un autre réseau Docker)."""

    def __init__(
        self,
        policy_path: str,
        server_name: str = "upstream",
        upstream_command: str | None = None,
        upstream_args: list[str] | None = None,
        upstream_url: str | None = None,
        verifier: Verifier | None = None,
        result_inspector: ResultInspector | None = None,
        enable_integrity: bool = True,
    ):
        if bool(upstream_url) == bool(upstream_command):
            raise ValueError("fournir soit upstream_url, soit upstream_command/upstream_args, pas les deux")
        self.upstream_command = upstream_command
        self.upstream_args = upstream_args or []
        self.upstream_url = upstream_url
        self.server_name = server_name
        self.policy = Policy.from_yaml(policy_path)
        self.registry = ToolRegistry() if enable_integrity else None
        self.tokens = TokenStore()
        self.verifier = verifier
        self.result_inspector = result_inspector
        self.decision_log: list[DecisionLogEntry] = []

        self._exit_stack = AsyncExitStack()
        self.session: ClientSession | None = None

    async def __aenter__(self) -> "Gateway":
        if self.upstream_url:
            read, write = await self._exit_stack.enter_async_context(streamable_http_client(self.upstream_url))
        else:
            params = StdioServerParameters(command=self.upstream_command, args=self.upstream_args)
            read, write = await self._exit_stack.enter_async_context(stdio_client(params))
        self.session = await self._exit_stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        return self

    async def __aexit__(self, *exc) -> None:
        await self._exit_stack.aclose()

    async def list_tools_checked(self):
        """tools/list relayé, avec vérification d'intégrité sur chaque outil
        annoncé (rug pull/shadowing) ET inspection de sa DESCRIPTION (tool
        poisoning) -- un outil malveillant peut porter des instructions
        cachées dans sa définition dès le premier enregistrement, ce que le
        hash d'intégrité seul ne détecte pas (il ne détecte qu'un changement
        après coup, pas un contenu malveillant présent depuis le début)."""
        result = await self.session.list_tools()
        all_alerts = []
        for t in result.tools:
            if self.registry is not None:
                all_alerts.extend(
                    self.registry.register(self.server_name, t.name, t.description or "", t.input_schema or {})
                )
            if self.result_inspector is not None:
                inspection = self.result_inspector.inspect(t.description or "")
                if inspection.flagged:
                    all_alerts.append(
                        f"TOOL_POISONING: la description de l'outil '{t.name}' (serveur '{self.server_name}') "
                        f"contient des instructions suspectes ({'; '.join(inspection.reasons)})"
                    )
        return result.tools, all_alerts

    def _log(self, tool_name: str, arguments: dict, decision: Decision, reason: str) -> DecisionLogEntry:
        entry = DecisionLogEntry(tool=tool_name, arguments=arguments, decision=decision.value, reason=reason)
        self.decision_log.append(entry)
        print(f"[gateway] {tool_name} -> {decision.value} ({reason})", file=sys.stderr)
        return entry

    async def _call_upstream(self, tool_name: str, arguments: dict) -> dict:
        upstream_result = await self.session.call_tool(tool_name, arguments)
        blocks = [getattr(b, "text", str(b)) for b in upstream_result.content]
        if self.result_inspector is not None:
            sanitized_blocks = []
            for block in blocks:
                inspection = self.result_inspector.inspect(block)
                if inspection.flagged:
                    print(f"[gateway] inspecteur: contenu suspect caviardé dans le résultat de '{tool_name}' ({'; '.join(inspection.reasons)})", file=sys.stderr)
                sanitized_blocks.append(inspection.sanitized_text)
            blocks = sanitized_blocks
        return {"blocked": False, "result": blocks}

    async def call_tool_guarded(
        self, tool_name: str, arguments: dict, session_token: str, elicit_fn: ElicitFn | None = None
    ) -> dict:
        """Point d'entrée unique pour un appel d'outil -- jeton, puis politique,
        puis (si `require_verifier`/`require_human`) le garde-fou correspondant,
        puis seulement si autorisé, relais réel vers le serveur en aval."""
        span_cm = _tracer.start_as_current_span(f"mcp.tools/call {tool_name}") if _tracer else nullcontext()
        with span_cm as span:
            if span is not None:
                span.set_attribute("mcp.server", self.server_name)
                span.set_attribute("tool.name", tool_name)
                span.set_attribute("tool.args", str(arguments))

            def finish(outcome: dict, decision: str, reason: str) -> dict:
                if span is not None:
                    span.set_attribute("policy.decision", decision)
                    span.set_attribute("policy.reason", reason)
                    span.set_attribute("tool.blocked", outcome["blocked"])
                return outcome

            token_ok, token_reason = self.tokens.check(session_token, tool_name)
            if not token_ok:
                self._log(tool_name, arguments, Decision.DENY, f"jeton refusé -- {token_reason}")
                return finish({"blocked": True, "reason": f"jeton refusé: {token_reason}"}, "deny", f"jeton refusé: {token_reason}")

            result = self.policy.evaluate(tool_name, arguments)
            self._log(tool_name, arguments, result.decision, result.reason)

            if result.decision == Decision.ALLOW:
                return finish(await self._call_upstream(tool_name, arguments), "allow", result.reason)

            if result.decision == Decision.REQUIRE_VERIFIER:
                if self.verifier is None:
                    reason = "require_verifier requis mais aucun vérificateur fourni -- échec fermé (couche 4 absente)"
                    return finish({"blocked": True, "reason": reason}, "require_verifier", reason)
                verdict = await self.verifier.verify(tool_name, arguments, result.note)
                self._log(tool_name, arguments, result.decision, f"vérificateur: {verdict.reason}")
                if span is not None:
                    span.set_attribute("verifier.approved", verdict.approved)
                    span.set_attribute("verifier.reason", verdict.reason)
                if not verdict.approved:
                    return finish({"blocked": True, "reason": f"rejeté par le vérificateur: {verdict.reason}"}, "require_verifier", verdict.reason)
                return finish(await self._call_upstream(tool_name, arguments), "require_verifier", verdict.reason)

            if result.decision == Decision.REQUIRE_HUMAN:
                if elicit_fn is None:
                    reason = "require_human requis mais aucune elicitation fournie -- échec fermé (couche 5 absente)"
                    return finish({"blocked": True, "reason": reason}, "require_human", reason)
                approved = await elicit_fn(tool_name, arguments, result.note or "")
                self._log(tool_name, arguments, result.decision, f"elicitation humaine: {'approuvé' if approved else 'refusé'}")
                if span is not None:
                    span.set_attribute("human.approved", approved)
                if not approved:
                    return finish({"blocked": True, "reason": "refusé par l'humain sollicité"}, "require_human", "refusé par l'humain sollicité")
                return finish(await self._call_upstream(tool_name, arguments), "require_human", "approuvé par l'humain sollicité")

            return finish({"blocked": True, "reason": f"refusé par la politique: {result.reason}"}, "deny", result.reason)
