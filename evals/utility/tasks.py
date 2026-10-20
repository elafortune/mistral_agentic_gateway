"""Étape 7 -- 10 tâches du domaine airline de τ²-bench (`id` = identifiant réel
dans le jeu de 50 tâches), choisies non-adversariales (pas de négociation/
d'insistance multi-tours) et consolidées en UNE instruction unique par tâche
-- scope réduit assumé (option B du cadrage, cf. step_by_step.md Étape 7).

Chaque instruction reprend le contenu réel de `reason_for_call` +
`task_instructions` + `known_info` de la tâche τ²-bench correspondante (pas
inventée), recentrée sur ce que l'action dorée (`evaluation_criteria.actions`)
mesure réellement -- pour éviter qu'un agent qui ferait AUSSI les actions non
mesurées (ex. tâche 9 mentionne aussi deux annulations) ne fasse diverger la
base par rapport à l'environnement doré rejoué par l'évaluateur, et échoue à
tort."""

TASKS = [
    {
        "id": "9",
        "slug": "search_nonstop_mco",
        "instruction": (
            "Je suis Aarav Ahmed (user id aarav_ahmed_6699). Je voudrais savoir "
            "s'il existe un vol direct de JFK à MCO le 2024-05-22, pour un "
            "changement éventuel sur une de mes réservations."
        ),
    },
    {
        "id": "27",
        "slug": "delay_complaint_lookup",
        "instruction": (
            "Je suis Ethan Martin (user id ethan_martin_2396). Mon vol HAT039 "
            "d'ATL à SEA a été retardé. Je veux savoir pourquoi et être indemnisé "
            "(un bon de voyage ou un remboursement sur mon moyen de paiement "
            "d'origine), mais je ne veux PAS annuler ni modifier ma réservation."
        ),
    },
    {
        "id": "36",
        "slug": "bereavement_lookup",
        "instruction": (
            "Je suis Lucas Brown. Ma femme est tragiquement décédée hier. Je "
            "voudrais décaler de 2 jours un vol dans ma réservation EUJUY6, sans "
            "l'annuler."
        ),
    },
    {
        "id": "45",
        "slug": "emergency_cancel_lookup",
        "instruction": (
            "Je suis Sophia Taylor, ma réservation est PEP4E0. Je dois annuler "
            "mon vol en urgence pour une raison familiale et je voudrais un "
            "remboursement complet."
        ),
    },
    {
        "id": "48",
        "slug": "mistake_booking_lookup",
        "instruction": (
            "Je suis Anya Garcia. J'ai réservé ce vol ce matin par erreur "
            "(confirmation 3RK2T9, il y a environ 10 heures) et je voudrais "
            "l'annuler avec un remboursement complet."
        ),
    },
    {
        "id": "13",
        "slug": "transfer_route_change",
        "instruction": (
            "Je suis James Lee, réservation XEWRD9. J'ai un aller-retour ATL-LAX "
            "avec une escale, et je voudrais le changer pour un vol direct "
            "ATL-LAS (Las Vegas), sans réserver un nouveau billet, juste modifier "
            "l'existant. Je peux payer des frais jusqu'à 100$. Si ce changement "
            "n'est pas possible, merci de me transférer à un agent humain."
        ),
    },
    {
        "id": "19",
        "slug": "cancel_with_insurance",
        "instruction": (
            "Je suis Olivia Gonzalez (user id olivia_gonzalez_2305), je réside à "
            "Newark. J'ai une réservation pour un aller-retour vers le Texas, "
            "retour prévu à 15h aujourd'hui. Je voudrais un vol de retour plus "
            "tardif vers Newark (EWR uniquement, pas JFK) aujourd'hui, ou sinon "
            "le premier vol demain. Si ma réservation est en classe économique "
            "de base et ne peut pas être modifiée, j'accepte d'annuler en "
            "utilisant mon assurance voyage car je ne me sens pas bien."
        ),
    },
    {
        "id": "15",
        "slug": "update_cabin_change",
        "instruction": (
            "Je suis Aarav Garcia, réservation M05KNL, vol ATL-PHL à venir. Je "
            "voudrais changer pour le vol le moins cher en classe Economy (pas "
            "Economy de base) le lendemain de la date actuelle. EWR me convient "
            "aussi bien que PHL. Je suis d'accord pour un remboursement sur mon "
            "moyen de paiement d'origine."
        ),
    },
    {
        "id": "20",
        "slug": "book_one_way_constraints",
        "instruction": (
            "Je suis Mia Li (user id mia_li_3668). Je veux voler de New York à "
            "Seattle le 20 mai, aller simple, pas avant 11h (heure de l'Est), en "
            "classe Economy. Je préfère un vol direct, sinon une escale, et si "
            "plusieurs options, le moins cher. J'ai 3 bagages, pas d'assurance. "
            "Je veux payer avec mes deux avoirs (certificates) -- si un seul "
            "peut être utilisé, celui avec le plus gros solde, et le reste avec "
            "ma carte se terminant par 7447."
        ),
    },
    {
        "id": "25",
        "slug": "book_for_friend",
        "instruction": (
            "Je suis Ivan Muller (user id ivan_muller_7015). Je veux réserver un "
            "vol pour mon ami Ivan Smith, déjà dans mon profil (sa date de "
            "naissance aussi). Je veux une réservation strictement identique à "
            "ma réservation actuelle, avec lui comme seul passager. Utilise mon "
            "avoir (certificate) si le prix dépasse 400$, sinon ma carte cadeau "
            "puis ma carte de crédit. Pas de bagage, pas d'assurance."
        ),
    },
]
