# Agent (stub réseau pour l'Étape 4 -- le raisonnement LangGraph complet est
# une étape ultérieure). Sur agent_net uniquement : ne voit jamais tools_net
# ni le serveur MCP métier directement, seulement la passerelle. Egress
# internet limité à la liste blanche via le proxy `egress_proxy`.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin appuser \
    && apt-get update && apt-get install -y --no-install-recommends curl iputils-ping \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY agent/requirements.txt ./agent-requirements.txt
RUN pip install --no-cache-dir -r agent-requirements.txt

COPY agent/agent_stub.py ./agent/agent_stub.py

USER appuser

ENTRYPOINT ["python", "agent/agent_stub.py"]
