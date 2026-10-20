"""Étape 1 -- premier test de traçabilité : un vrai appel LLM (Mistral) et un
appel MCP factice, tous deux instrumentés en spans OpenTelemetry (conventions
sémantiques GenAI + attributs custom de l'architecture du projet), exportés
vers Langfuse auto-hébergé via OTLP/HTTP.
"""

import base64
import os
import time

import httpx
from dotenv import load_dotenv
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

load_dotenv()

LANGFUSE_URL = "http://localhost:3000"
PUBLIC_KEY = os.environ["LANGFUSE_INIT_PROJECT_PUBLIC_KEY"]
SECRET_KEY = os.environ["LANGFUSE_INIT_PROJECT_SECRET_KEY"]

auth = base64.b64encode(f"{PUBLIC_KEY}:{SECRET_KEY}".encode()).decode()

exporter = OTLPSpanExporter(
    endpoint=f"{LANGFUSE_URL}/api/public/otel/v1/traces",
    headers={"Authorization": f"Basic {auth}", "x-langfuse-ingestion-version": "4"},
)

provider = TracerProvider(resource=Resource.create({"service.name": "mistral-agentic-gateway-smoketest"}))
# SimpleSpanProcessor (pas Batch) pour ce test ponctuel -- envoi immédiat,
# pas d'attente d'un flush de batch qui pourrait ne jamais se déclencher
# avant la fin du script.
provider.add_span_processor(SimpleSpanProcessor(exporter))
trace.set_tracer_provider(provider)
tracer = trace.get_tracer("smoketest")


def call_mistral(prompt: str) -> tuple[str, dict]:
    """Un vrai appel LLM, instrumenté avec les conventions sémantiques GenAI d'OTel."""
    with tracer.start_as_current_span("chat mistral-small-latest") as span:
        span.set_attribute("gen_ai.system", "mistral_ai")
        span.set_attribute("gen_ai.request.model", "mistral-small-latest")
        span.set_attribute("gen_ai.operation.name", "chat")

        t0 = time.time()
        resp = httpx.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.environ['MISTRAL_API_KEY']}"},
            json={
                "model": "mistral-small-latest",
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        elapsed = time.time() - t0

        usage = data.get("usage", {})
        span.set_attribute("gen_ai.usage.input_tokens", usage.get("prompt_tokens", 0))
        span.set_attribute("gen_ai.usage.output_tokens", usage.get("completion_tokens", 0))
        span.set_attribute("gen_ai.response.model", data.get("model", ""))
        span.set_attribute("duration_s", elapsed)

        content = data["choices"][0]["message"]["content"]
        return content, usage


def call_fake_mcp_tool(server: str, tool: str, args: dict) -> dict:
    """Appel MCP factice -- pas encore de vrai serveur (Étape 2) -- juste pour
    valider les attributs custom prévus dans l'architecture (mcp.server,
    tool.name, policy.decision...) avant de brancher la vraie passerelle."""
    with tracer.start_as_current_span(f"mcp.tools/call {tool}") as span:
        span.set_attribute("mcp.server", server)
        span.set_attribute("tool.name", tool)
        span.set_attribute("tool.args", str(args))
        span.set_attribute("policy.decision", "allow")  # factice pour l'instant
        span.set_attribute("session.scope", "demo-session-1")
        time.sleep(0.05)  # simule la latence réseau vers le serveur MCP
        result = {"status": "ok", "order_id": "ORD-12345"}
        span.set_attribute("tool.result_status", result["status"])
        return result


def main() -> None:
    with tracer.start_as_current_span("conversation utilisateur (smoketest)") as root:
        root.set_attribute("agent.role", "primary")
        root.set_attribute("session.scope", "demo-session-1")

        answer, usage = call_mistral("Réponds en un mot : quelle est la capitale de la France ?")
        print("Réponse Mistral:", answer, "| usage:", usage)

        result = call_fake_mcp_tool("orders", "get_order_status", {"order_id": "ORD-12345"})
        print("Résultat MCP factice:", result)

    # SimpleSpanProcessor envoie déjà de façon synchrone, mais on force un
    # flush explicite pour être certain que tout est parti avant la fin du script.
    provider.force_flush()
    print("\nTrace envoyée à Langfuse -- vérifier sur http://localhost:3000")


if __name__ == "__main__":
    main()
