from datetime import datetime, timedelta

import pytest

import frontend.access_control as access
from frontend.access_control import (
    AuthError,
    assign_record,
    authenticate,
    can,
    can_view_page,
    create_user,
    empty_store,
    fallback_chain,
    hash_password,
    load_store,
    needs_bootstrap,
    reroute_assignments,
    resolve_assignee,
    restore_assignments,
    save_role_permissions,
    save_store,
    set_fallback,
    set_password,
    update_user,
    verify_password,
)

PASSWORD = "Secure123"


@pytest.fixture(autouse=True)
def fast_hashing(monkeypatch):
    monkeypatch.setattr(access, "PBKDF2_ITERATIONS", 1000)


def _store():
    store = empty_store()
    create_user(store, "admin", "Ada Admin", "", "Super Admin", PASSWORD)
    create_user(store, "coder1", "Casey Coder", "", "Coding Specialist", PASSWORD)
    create_user(store, "coder2", "Corey Coder", "", "Coding Specialist", PASSWORD)
    create_user(store, "lead", "Lee Lead", "", "Operations Admin", PASSWORD)
    return store


def test_password_hash_round_trip_and_salting():
    first, second = hash_password(PASSWORD), hash_password(PASSWORD)
    assert first != second
    assert verify_password(PASSWORD, first) and not verify_password("wrong123", first)


def test_password_policy_and_username_rules():
    store = empty_store()
    with pytest.raises(ValueError, match="at least"):
        create_user(store, "short", "Short Pw", "", "RCM Analyst", "abc1")
    with pytest.raises(ValueError, match="letters and numbers"):
        create_user(store, "letters", "Letters Only", "", "RCM Analyst", "abcdefghij")
    with pytest.raises(ValueError, match="Username"):
        create_user(store, "x", "Bad Name", "", "RCM Analyst", PASSWORD)


def test_bootstrap_required_until_a_super_admin_exists():
    store = empty_store()
    assert needs_bootstrap(store)
    create_user(store, "admin", "Ada Admin", "", "Super Admin", PASSWORD)
    assert not needs_bootstrap(store)


def test_store_round_trip_keeps_users_and_hashes(tmp_path):
    store = _store()
    path = tmp_path / "access.json"
    save_store(path, store)
    loaded = load_store(path)
    assert [user["username"] for user in loaded["users"]] == ["admin", "coder1", "coder2", "lead"]
    assert PASSWORD not in path.read_text(encoding="utf-8")


def test_authenticate_success_failure_and_lockout():
    store = _store()
    assert authenticate(store, "Coder1", PASSWORD)["last_login"]
    now = datetime(2026, 10, 17, 9, 0, 0)
    for _ in range(access.MAX_FAILED_ATTEMPTS):
        with pytest.raises(AuthError, match="Invalid username or password"):
            authenticate(store, "coder1", "wrong-pass1", now=now)
    with pytest.raises(AuthError, match="locked"):
        authenticate(store, "coder1", PASSWORD, now=now)
    later = now + timedelta(minutes=access.LOCKOUT_MINUTES + 1)
    assert authenticate(store, "coder1", PASSWORD, now=later)["failed_attempts"] == 0


def test_unknown_user_gets_the_same_generic_error():
    with pytest.raises(AuthError, match="Invalid username or password"):
        authenticate(_store(), "nobody", PASSWORD)


def test_inactive_user_cannot_sign_in():
    store = _store()
    update_user(store, "coder1", "admin", active=False)
    with pytest.raises(AuthError, match="deactivated"):
        authenticate(store, "coder1", PASSWORD)


def test_permissions_follow_role_and_page_mapping():
    store = _store()
    coder = access.get_user(store, "coder1")
    assert can(store, coder, "Codes", "Approve")
    assert not can(store, coder, "RCM Workspace", "View")
    assert can_view_page(store, coder, "codes") and not can_view_page(store, coder, "billing")
    assert not can_view_page(store, coder, "super_admin")
    assert can_view_page(store, access.get_user(store, "admin"), "super_admin")


def test_role_permission_edits_persist_and_super_admin_is_locked():
    store = _store()
    save_role_permissions(store, "Coding Specialist", [
        {"Module": "RCM Workspace", "View": True, "Edit": False, "Approve": False, "Admin": False},
    ], actor="admin")
    assert can(store, access.get_user(store, "coder1"), "RCM Workspace", "View")
    assert can(store, access.get_user(store, "coder1"), "Codes", "Edit")
    with pytest.raises(ValueError):
        save_role_permissions(store, "Super Admin", [], actor="admin")
    assert any(entry["action"] == "role.permissions" for entry in store["audit"])


def test_last_super_admin_cannot_be_removed():
    store = _store()
    with pytest.raises(ValueError, match="Super Admin"):
        update_user(store, "admin", "admin", active=False)
    with pytest.raises(ValueError, match="Super Admin"):
        update_user(store, "admin", "admin", role="RCM Analyst")


def test_fallback_rejects_self_and_loops():
    store = _store()
    set_fallback(store, "coder1", "coder2", actor="admin")
    with pytest.raises(ValueError, match="own fallback"):
        set_fallback(store, "coder1", "coder1", actor="admin")
    with pytest.raises(ValueError, match="loop"):
        set_fallback(store, "coder2", "coder1", actor="admin")
    set_fallback(store, "coder2", "lead", actor="admin")
    assert fallback_chain(store, "coder1") == ["coder2", "lead"]


def test_resolve_assignee_walks_the_chain_past_unavailable_users():
    store = _store()
    set_fallback(store, "coder1", "coder2", actor="admin")
    set_fallback(store, "coder2", "lead", actor="admin")
    assert resolve_assignee(store, "coder1") == "coder1"
    update_user(store, "coder1", "admin", out_of_office=True)
    assert resolve_assignee(store, "coder1") == "coder2"
    update_user(store, "coder2", "admin", active=False)
    assert resolve_assignee(store, "coder1") == "lead"
    update_user(store, "lead", "admin", out_of_office=True)
    assert resolve_assignee(store, "coder1") is None


def test_assigning_to_an_unavailable_user_routes_to_fallback():
    store = _store()
    set_fallback(store, "coder1", "coder2", actor="admin")
    update_user(store, "coder1", "admin", out_of_office=True)
    record = {"id": "PT-1"}
    assert assign_record(store, record, "coder1", actor="admin") == "coder2"
    assert record["rerouted_from"] == "coder1"
    assert record["assignment_history"][-1]["to"] == "coder2"


def test_reroute_and_restore_work_when_user_is_away_and_returns():
    store = _store()
    set_fallback(store, "coder1", "coder2", actor="admin")
    records = [{"id": "PT-1", "assigned_to": "coder1"}, {"id": "PT-2", "assigned_to": "coder2"}]
    update_user(store, "coder1", "admin", out_of_office=True)
    moved = reroute_assignments(store, records, "coder1", actor="admin", reason="Out of office")
    assert [r["id"] for r in moved] == ["PT-1"]
    assert records[0]["assigned_to"] == "coder2" and records[0]["rerouted_from"] == "coder1"
    update_user(store, "coder1", "admin", out_of_office=False)
    returned = restore_assignments(store, records, "coder1", actor="admin")
    assert [r["id"] for r in returned] == ["PT-1"]
    assert records[0]["assigned_to"] == "coder1" and "rerouted_from" not in records[0]
    assert {"work.reroute", "work.restore"} <= {entry["action"] for entry in store["audit"]}


def test_reroute_with_no_available_fallback_goes_to_unassigned_queue():
    store = _store()
    update_user(store, "coder1", "admin", active=False)
    records = [{"id": "PT-1", "assigned_to": "coder1"}]
    reroute_assignments(store, records, "coder1", actor="admin", reason="Deactivated")
    assert records[0]["assigned_to"] is None and records[0]["rerouted_from"] == "coder1"


def test_admin_set_passwords_are_temporary_until_user_changes_them():
    store = _store()
    create_user(store, "newbie", "New Person", "", "RCM Analyst", PASSWORD, actor="admin", must_change_password=True)
    assert access.get_user(store, "newbie")["must_change_password"] is True
    with pytest.raises(ValueError, match="different"):
        set_password(store, "newbie", PASSWORD, actor="newbie")
    set_password(store, "newbie", "MyOwnPass789", actor="newbie")
    assert access.get_user(store, "newbie")["must_change_password"] is False
    set_password(store, "newbie", "AdminReset111", actor="admin", temporary=True)
    assert access.get_user(store, "newbie")["must_change_password"] is True


def test_password_reset_clears_lockout():
    store = _store()
    user = access.get_user(store, "coder1")
    user["failed_attempts"], user["locked_until"] = 5, "2999-01-01 00:00:00"
    set_password(store, "coder1", "NewPass456", actor="admin")
    assert authenticate(store, "coder1", "NewPass456")["username"] == "coder1"
