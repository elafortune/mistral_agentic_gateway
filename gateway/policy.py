"""Moteur de politique -- couche 2 des garde-fous (cf. cadrage).

Politique déclarative en YAML : par outil, une décision (allow/deny/
require_verifier/require_human) et des contraintes optionnelles sur les
arguments (borne min/max). Pure logique, aucune dépendance MCP/réseau --
testable unitairement sans lancer quoi que ce soit.
"""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yaml


class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_VERIFIER = "require_verifier"
    REQUIRE_HUMAN = "require_human"


@dataclass
class PolicyResult:
    decision: Decision
    reason: str
    note: str | None = None


class Policy:
    def __init__(self, config: dict) -> None:
        self.default = Decision(config.get("default", "deny"))
        self.tools: dict = config.get("tools", {})

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Policy":
        with open(path, encoding="utf-8") as f:
            return cls(yaml.safe_load(f))

    def evaluate(self, tool_name: str, arguments: dict) -> PolicyResult:
        rule = self.tools.get(tool_name)
        if rule is None:
            return PolicyResult(
                self.default, f"aucune règle explicite pour '{tool_name}' -- décision par défaut"
            )

        constraints = rule.get("constraints", {})
        for arg_name, bounds in constraints.items():
            value = arguments.get(arg_name)
            if value is None:
                continue
            if "max" in bounds and value > bounds["max"]:
                return PolicyResult(
                    Decision.DENY,
                    f"argument '{arg_name}'={value} dépasse le maximum autorisé ({bounds['max']})",
                )
            if "min" in bounds and value < bounds["min"]:
                return PolicyResult(
                    Decision.DENY,
                    f"argument '{arg_name}'={value} sous le minimum autorisé ({bounds['min']})",
                )

        decision = Decision(rule.get("decision", self.default.value))
        return PolicyResult(decision, f"règle explicite pour '{tool_name}'", note=rule.get("note"))
