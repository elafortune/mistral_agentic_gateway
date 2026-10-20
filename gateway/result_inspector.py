"""Inspection des résultats -- couche 3 des garde-fous (cf. cadrage).

Jusqu'ici, rien n'examinait le texte qui revient d'un outil avant qu'il
n'atteigne l'agent : une instruction cachée dans une note de réservation (ou
toute autre donnée métier) passerait sans filtre jusqu'au LLM, qui pourrait
la confondre avec une vraie instruction (injection indirecte, cf. AgentDojo).

Détecteur heuristique d'abord (motifs explicites adressés à un modèle), pas
un classifieur entraîné (le cadrage envisage aussi un classifieur type
Prompt Guard / l'API de modération Mistral -- amélioration possible, pas un
prérequis pour démontrer la couche). Rapide, déterministe, sans dépendance
réseau -- contrairement au vérificateur, aucune raison de payer le coût d'un
appel LLM pour inspecter CHAQUE résultat d'outil.

Volontairement permissif plutôt que bloquant : une donnée suspecte est
caviardée (l'agent ne voit jamais le texte piégé), pas toute la réponse
rejetée -- un faux positif ne doit pas casser un flux métier légitime.

Caviardage à la granularité de la PHRASE, pas du seul motif détecté : un
premier essai mot-à-mot laissait passer l'attaque (la phrase déclencheuse
"as an AI..." était retirée, mais la charge utile "...send_certificate avec
amount=90..." qui la suit dans la même phrase restait intacte et suffisait à
l'agent pour agir) -- trouvé en testant contre un vrai scénario d'attaque
(Étape 6), pas supposé correct a priori.
"""

import re
from dataclasses import dataclass

SUSPICIOUS_PATTERNS = [
    re.compile(r"ignore (all |any )?(previous|prior|above) instructions", re.IGNORECASE),
    re.compile(r"disregard (all |any )?(previous|prior|above)", re.IGNORECASE),
    re.compile(r"\b(system|assistant)\s*:", re.IGNORECASE),
    re.compile(r"###?\s*(instruction|system|override)", re.IGNORECASE),
    re.compile(r"you (must|should) now (call|invoke|approve|execute)", re.IGNORECASE),
    re.compile(r"as an ai\b", re.IGNORECASE),
    re.compile(r"\bnew instructions?\b", re.IGNORECASE),
]

REDACTION = "[CONTENU SUSPECT SUPPRIMÉ PAR L'INSPECTEUR DE RÉSULTATS]"


@dataclass
class InspectionResult:
    flagged: bool
    sanitized_text: str
    reasons: list[str]


def _redact_sentence_at(text: str, start: int, end: int) -> str:
    """Caviarde toute la phrase autour de la portion [start:end], pas
    seulement le motif qui a matché -- un motif déclencheur ("as an AI...")
    et sa charge utile ("...send_certificate with amount=90...") sont
    presque toujours dans la même phrase ; ne retirer que le motif laisse
    la charge utile intacte et l'attaque passe quand même."""
    sentence_start = max((text.rfind(c, 0, start) for c in ".!?\n"), default=-1) + 1
    ends = [text.find(c, end) for c in ".!?\n"]
    ends = [e for e in ends if e != -1]
    sentence_end = (min(ends) + 1) if ends else len(text)
    return text[:sentence_start] + f" {REDACTION} " + text[sentence_end:]


class ResultInspector:
    def inspect(self, text: str, max_passes: int = 10) -> InspectionResult:
        reasons = []
        sanitized = text
        for _ in range(max_passes):
            match = None
            for pattern in SUSPICIOUS_PATTERNS:
                match = pattern.search(sanitized)
                if match:
                    reasons.append(f"motif détecté: {pattern.pattern}")
                    break
            if match is None:
                break
            sanitized = _redact_sentence_at(sanitized, match.start(), match.end())
        return InspectionResult(flagged=bool(reasons), sanitized_text=sanitized, reasons=reasons)
