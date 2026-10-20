#!/bin/bash
# Démarre la stack Langfuse en chargeant .env de façon robuste
# (set -a/source, pas export $(grep...) qui a déjà échoué silencieusement une fois).
cd "$(dirname "$0")/.."
set -a
source .env
set +a
docker compose -f infra/docker-compose.langfuse.yml up -d
