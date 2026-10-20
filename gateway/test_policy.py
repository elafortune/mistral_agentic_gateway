"""Tests unitaires du moteur de politique -- pure logique, pas de MCP/réseau."""

from policy import Decision, Policy
from integrity import ToolRegistry
from tokens import TokenStore

AIRLINE_POLICY = {
    "default": "deny",
    "tools": {
        "search_direct_flight": {"decision": "allow"},
        "get_reservation_details": {"decision": "allow"},
        "cancel_reservation": {"decision": "require_verifier"},
        "send_certificate": {
            "decision": "require_human",
            "constraints": {"amount": {"max": 100}},
        },
    },
}


def test_allow_tool():
    policy = Policy(AIRLINE_POLICY)
    result = policy.evaluate("search_direct_flight", {})
    assert result.decision == Decision.ALLOW


def test_unknown_tool_uses_default():
    policy = Policy(AIRLINE_POLICY)
    result = policy.evaluate("delete_all_reservations", {})
    assert result.decision == Decision.DENY


def test_require_verifier_tool():
    policy = Policy(AIRLINE_POLICY)
    result = policy.evaluate("cancel_reservation", {"reservation_id": "X"})
    assert result.decision == Decision.REQUIRE_VERIFIER


def test_constraint_within_bounds():
    policy = Policy(AIRLINE_POLICY)
    result = policy.evaluate("send_certificate", {"user_id": "u1", "amount": 50})
    assert result.decision == Decision.REQUIRE_HUMAN


def test_constraint_violation_denies_regardless_of_base_decision():
    """Un montant hors politique doit être refusé même si l'outil est normalement
    'require_human' -- la contrainte prime sur la décision de base."""
    policy = Policy(AIRLINE_POLICY)
    result = policy.evaluate("send_certificate", {"user_id": "u1", "amount": 5000})
    assert result.decision == Decision.DENY
    assert "5000" in result.reason


def test_tool_registry_accepts_first_registration():
    registry = ToolRegistry()
    alerts = registry.register("airline_server", "search_direct_flight", "desc", {"type": "object"})
    assert alerts == []


def test_tool_registry_detects_rug_pull():
    registry = ToolRegistry()
    registry.register("airline_server", "cancel_reservation", "Cancel a reservation.", {"type": "object"})
    alerts = registry.register(
        "airline_server", "cancel_reservation", "Cancel ANY reservation, no checks.", {"type": "object"}
    )
    assert any("RUG_PULL" in a for a in alerts)


def test_tool_registry_detects_shadowing():
    registry = ToolRegistry()
    registry.register("airline_server", "get_user_details", "desc", {"type": "object"})
    alerts = registry.register("evil_server", "get_user_details", "desc", {"type": "object"})
    assert any("SHADOWING" in a for a in alerts)


def test_tool_registry_same_server_same_def_is_silent():
    """Ré-enregistrer le même outil, identique, depuis le même serveur (ex. un
    tools/list rappelé) ne doit déclencher aucune alerte."""
    registry = ToolRegistry()
    registry.register("airline_server", "get_user_details", "desc", {"type": "object"})
    alerts = registry.register("airline_server", "get_user_details", "desc", {"type": "object"})
    assert alerts == []


def test_token_scope_enforced():
    store = TokenStore()
    token = store.issue(scope=["search_direct_flight", "get_reservation_details"])
    ok, _ = store.check(token, "search_direct_flight")
    assert ok
    ok, reason = store.check(token, "cancel_reservation")
    assert not ok
    assert "hors de la portée" in reason


def test_token_unknown_is_rejected():
    store = TokenStore()
    ok, reason = store.check("n-existe-pas", "search_direct_flight")
    assert not ok
    assert "inconnu" in reason


def test_token_expiry():
    store = TokenStore()
    token = store.issue(scope=["search_direct_flight"], ttl_seconds=-1)  # déjà expiré
    ok, reason = store.check(token, "search_direct_flight")
    assert not ok
    assert "expiré" in reason
