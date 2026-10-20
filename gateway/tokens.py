"""Identifiants à portée limitée -- la passerelle délivre un jeton de session
court, restreint à un sous-ensemble d'outils, plutôt que de laisser l'agent
(ou pire, le serveur MCP en aval) voir des identifiants utilisateur réels.
Aucun *token passthrough* : le serveur MCP en aval ne reçoit jamais ce jeton,
il sert uniquement entre l'agent et la passerelle.
"""

import secrets
import time
from dataclasses import dataclass


@dataclass
class SessionToken:
    token: str
    scope: frozenset[str]
    expires_at: float


class TokenStore:
    def __init__(self, default_ttl_seconds: float = 300) -> None:
        self._tokens: dict[str, SessionToken] = {}
        self._default_ttl = default_ttl_seconds

    def issue(self, scope: list[str], ttl_seconds: float | None = None) -> str:
        token = secrets.token_urlsafe(24)
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        self._tokens[token] = SessionToken(
            token=token, scope=frozenset(scope), expires_at=time.time() + ttl
        )
        return token

    def check(self, token: str, tool_name: str) -> tuple[bool, str]:
        """Renvoie (autorisé, raison)."""
        record = self._tokens.get(token)
        if record is None:
            return False, "jeton inconnu"
        if time.time() > record.expires_at:
            return False, "jeton expiré"
        if tool_name not in record.scope:
            return False, f"outil '{tool_name}' hors de la portée du jeton ({sorted(record.scope)})"
        return True, "ok"

    def revoke(self, token: str) -> None:
        self._tokens.pop(token, None)
