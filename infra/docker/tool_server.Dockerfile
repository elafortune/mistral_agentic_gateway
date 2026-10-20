# Serveur MCP métier (τ²-bench ré-exposé) -- Étape 4 : conteneur durci, sur
# tools_net uniquement, aucun accès internet (pure logique métier, pas besoin
# d'appeler un LLM ni quoi que ce soit d'externe).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin appuser \
    && apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY servers/requirements.txt ./servers-requirements.txt
RUN pip install --no-cache-dir -r servers-requirements.txt \
    && git clone --depth 1 https://github.com/sierra-research/tau2-bench /opt/tau2-bench \
    && pip install --no-cache-dir -e /opt/tau2-bench \
    && pip install --no-cache-dir websockets \
    && apt-get purge -y git && apt-get autoremove -y \
    && chown -R appuser:appuser /opt/tau2-bench

COPY servers/toolkit_mcp_server.py ./servers/toolkit_mcp_server.py

USER appuser
EXPOSE 8000

HEALTHCHECK --interval=2s --timeout=2s --start-period=20s --retries=20 \
    CMD python -c "import socket; socket.create_connection(('127.0.0.1', 8000), timeout=2)" || exit 1

ENTRYPOINT ["python", "servers/toolkit_mcp_server.py", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8000"]
CMD ["--domain", "airline"]
