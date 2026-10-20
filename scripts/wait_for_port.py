"""Attend qu'un port TCP local accepte les connexions -- utilisé pour
synchroniser le démarrage des composants (amont avant passerelle, etc.) en
dehors de Docker Compose, qui a son propre mécanisme de `healthcheck`/`depends_on`."""
import socket
import sys
import time

host, port, timeout_s = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
deadline = time.time() + timeout_s
while time.time() < deadline:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            sys.exit(0)
    except OSError:
        time.sleep(0.3)
sys.exit(1)
