# Passerelle MCP -- Étape 4 : seul composant à cheval sur les deux réseaux
# (agent_net et tools_net). Aucun accès internet direct pour l'instant (le
# vérificateur hétérogène, qui appellera un LLM d'un autre fournisseur, arrive
# à l'Étape 5 -- il faudra alors ajouter cette passerelle à la liste blanche
# d'egress, pas avant).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin appuser

WORKDIR /app

COPY gateway/requirements.txt ./gateway-requirements.txt
RUN pip install --no-cache-dir -r gateway-requirements.txt

COPY gateway/*.py ./gateway/
COPY policies/ ./policies/

USER appuser
WORKDIR /app/gateway
EXPOSE 8100

HEALTHCHECK --interval=2s --timeout=2s --start-period=20s --retries=20 \
    CMD python -c "import socket; socket.create_connection(('127.0.0.1', 8100), timeout=2)" || exit 1

ENTRYPOINT ["python", "mcp_frontend.py", "--host", "0.0.0.0", "--port", "8100"]
