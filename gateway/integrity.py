"""Intégrité des outils -- couche 1 des garde-fous (cf. cadrage).

Hash la définition de chaque outil à son premier enregistrement ; toute
définition différente reçue ensuite sous le même nom est un **rug pull**
(l'outil a changé après approbation). Le même nom annoncé par deux serveurs
différents est du **shadowing** (un outil homonyme potentiellement
malveillant). Dans les deux cas, on ne bloque pas silencieusement -- on
renvoie des alertes explicites que l'appelant (le proxy) décide quoi en
faire.
"""

import hashlib
import json
from dataclasses import dataclass


def hash_tool_def(name: str, description: str, input_schema: dict) -> str:
    """Hash canonique (ordre des clés fixé) d'une définition d'outil."""
    canonical = json.dumps(
        {"name": name, "description": description, "input_schema": input_schema},
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass
class ToolRecord:
    server_name: str
    definition_hash: str


class ToolRegistry:
    """Registre en mémoire des définitions d'outils vues, par nom."""

    def __init__(self) -> None:
        self._records: dict[str, ToolRecord] = {}

    def register(self, server_name: str, name: str, description: str, input_schema: dict) -> list[str]:
        """Enregistre ou vérifie un outil. Renvoie une liste d'alertes (vide si RAS)."""
        alerts = []
        new_hash = hash_tool_def(name, description, input_schema)
        existing = self._records.get(name)

        if existing is None:
            self._records[name] = ToolRecord(server_name=server_name, definition_hash=new_hash)
            return alerts

        if existing.definition_hash != new_hash:
            alerts.append(
                f"RUG_PULL: la définition de l'outil '{name}' a changé depuis son enregistrement "
                f"initial (serveur '{existing.server_name}')"
            )
        if existing.server_name != server_name:
            alerts.append(
                f"SHADOWING: l'outil '{name}' est déjà enregistré par le serveur "
                f"'{existing.server_name}', nouvelle annonce depuis '{server_name}'"
            )
        return alerts

    def known_tools(self) -> list[str]:
        return list(self._records.keys())
