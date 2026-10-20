"""Elicitation humaine -- couche 5 des garde-fous (cf. cadrage).

En évaluation automatique, l'humain réel est remplacé par une règle
déterministe (pas par un second LLM -- l'elicitation MCP est un protocole de
confirmation d'un humain réel, pas un second avis automatisé comme le
vérificateur). Compte les sollicitations pour le tableau sécurité de
l'Étape 7 : combien de fois un humain aurait dû être consulté, et combien de
fois le simulateur a approuvé.

Ce module ne connaît pas MCP -- c'est `agent/agent_stub.py` qui l'appelle
depuis le callback d'elicitation côté client (là où, dans un vrai système,
vivrait l'interface humaine), pas la passerelle.
"""

from dataclasses import dataclass


@dataclass
class SimulatedHuman:
    approve_below: float = 100.0
    solicitations: int = 0
    approvals: int = 0

    def decide(self, arguments: dict) -> bool:
        self.solicitations += 1
        amount = arguments.get("amount")
        approved = amount is None or amount <= self.approve_below
        if approved:
            self.approvals += 1
        return approved
