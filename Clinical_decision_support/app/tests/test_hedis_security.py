"""API security: tokens, authentication, role-based authorization, segregation of duties and PHI minimization."""

import time

import pytest
from fastapi.testclient import TestClient

from app.hedis import store
from app.main import create_app
from app.security import rbac, tokens
from app.vbc_poc import repository as poc_repo
from app.vbc_poc import service as vbc
from app.vbc_poc.golden import GOLDEN_AS_OF
from frontend import access_control as ac
from frontend.admin_service import DEFAULT_PERMISSIONS, HEDIS_MODULES

PASSWORD = "Passw0rd!x"
USERS = {"analyst": "HEDIS Analyst", "lead": "HEDIS Lead", "lead2": "HEDIS Lead", "stars": "Stars / Contract", "exec": "Executive",
         "auditor": "Auditor", "doc": "Physician / APP", "ops": "Operations Admin", "coder": "Coding Specialist"}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv(poc_repo.STATE_ENV, str(tmp_path / "poc.json"))
    monkeypatch.setenv("HEDIS_STATE", str(tmp_path / "hedis.json"))
    monkeypatch.setenv("HEDIS_OUTBOX", str(tmp_path / "outbox"))
    monkeypatch.setenv(rbac.ACCESS_STORE_ENV, str(tmp_path / "access.json"))
    monkeypatch.setenv(tokens.SECRET_ENV, "unit-test-secret-0123456789abcdefghijklmnopqrstuv")
    monkeypatch.setattr(vbc, "today", lambda: GOLDEN_AS_OF)
    monkeypatch.setattr("app.vbc_poc.metrics.today", lambda: GOLDEN_AS_OF)
    access = ac.empty_store()
    for username, role in USERS.items():
        ac.create_user(access, username, username.title(), "", role, PASSWORD)
    ac.save_store(rbac.store_path(), access)
    yield


@pytest.fixture
def client():
    return TestClient(create_app())


def login(client, username):
    response = client.post("/hedis/auth/token", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


def test_token_round_trip_and_expiry():
    token = tokens.issue("lead", "HEDIS Lead", ttl=60, now=1_000_000)
    claims = tokens.verify(token, now=1_000_030)
    assert claims["sub"] == "lead" and claims["role"] == "HEDIS Lead" and claims["iss"] == tokens.ISSUER
    with pytest.raises(tokens.TokenError, match="expired"):
        tokens.verify(token, now=1_000_061)


@pytest.mark.parametrize("mutate", [
    lambda t: t[:-2] + ("AA" if not t.endswith("AA") else "BB"),  # signature
    lambda t: t.split(".")[0] + "." + tokens._b64(b'{"sub":"lead","role":"Super Admin","iss":"nuucare-hedis","exp":9999999999}') + "." + t.split(".")[2],
    lambda t: "not-a-token",
])
def test_tampered_tokens_are_rejected(mutate):
    with pytest.raises(tokens.TokenError):
        tokens.verify(mutate(tokens.issue("lead", "HEDIS Lead")))


def test_short_secret_is_refused(monkeypatch):
    monkeypatch.setenv(tokens.SECRET_ENV, "too-short")
    with pytest.raises(RuntimeError):
        tokens.issue("lead", "HEDIS Lead")


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def test_every_route_needs_a_token(client):
    for path in ("/hedis/registry", "/hedis/supplemental", "/hedis/reconciliation", "/hedis/agents", "/hedis/ncqa", "/hedis/audit"):
        assert client.get(path).status_code == 401
    assert client.get("/hedis/registry", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_bad_password_counts_toward_lockout(client):
    for _ in range(ac.MAX_FAILED_ATTEMPTS):
        assert client.post("/hedis/auth/token", json={"username": "lead", "password": "wrong-pass1"}).status_code == 401
    assert client.post("/hedis/auth/token", json={"username": "lead", "password": PASSWORD}).status_code == 401  # locked


def test_deactivated_account_loses_access_immediately(client):
    headers = login(client, "analyst")
    assert client.get("/hedis/registry", headers=headers).status_code == 200
    access = ac.load_store(rbac.store_path())
    ac.update_user(access, "analyst", "ops", active=False)
    ac.save_store(rbac.store_path(), access)
    assert client.get("/hedis/registry", headers=headers).status_code == 401


def test_role_change_invalidates_old_tokens(client):
    headers = login(client, "analyst")
    access = ac.load_store(rbac.store_path())
    ac.update_user(access, "analyst", "ops", role="Executive")
    ac.save_store(rbac.store_path(), access)
    assert client.get("/hedis/registry", headers=headers).status_code == 401


def test_temporary_password_must_be_changed_first(client):
    access = ac.load_store(rbac.store_path())
    ac.set_password(access, "stars", "Temp0rary!x", actor="ops", temporary=True)
    ac.save_store(rbac.store_path(), access)
    assert client.post("/hedis/auth/token", json={"username": "stars", "password": "Temp0rary!x"}).status_code == 403


# ---------------------------------------------------------------------------
# Authorization, segregation of duties, PHI
# ---------------------------------------------------------------------------


def test_least_privilege_matrix():
    signers = {role for role, perms in DEFAULT_PERMISSIONS.items() if perms["Supplemental Submission"]["Approve"]}
    lockers = {role for role, perms in DEFAULT_PERMISSIONS.items() if perms["HEDIS Registry"]["Approve"]}
    assert signers == lockers == {"HEDIS Lead", "Super Admin"}
    assert not any(DEFAULT_PERMISSIONS[role][m]["Edit"] for role in ("Executive", "Auditor") for m in HEDIS_MODULES)
    assert not any(DEFAULT_PERMISSIONS["Coding Specialist"][m]["View"] for m in HEDIS_MODULES)
    admins = {role for role, perms in DEFAULT_PERMISSIONS.items() if perms["AI Agent Console"]["Admin"]}
    assert admins == {"Operations Admin", "Super Admin"}


def test_forbidden_actions_are_403_and_logged(client):
    executive = login(client, "exec")
    assert client.post("/hedis/registry/snapshots", headers=executive).status_code == 403
    assert client.post("/hedis/supplemental/batches", headers=executive, json={"payer": "ATH"}).status_code == 403
    assert client.get("/hedis/supplemental", headers=login(client, "coder")).status_code == 403
    denials = [e for e in store.load()["audit"] if e["action"] == "rbac.denied"]
    assert len(denials) == 3 and "Exec (Executive)" in {e["actor"] for e in denials}


def test_segregation_of_duties_through_the_api(client):
    analyst, lead, lead2 = login(client, "analyst"), login(client, "lead"), login(client, "lead2")
    batch = client.post("/hedis/supplemental/batches", headers=lead, json={"payer": "ATH"}).json()
    assert client.post(f"/hedis/supplemental/batches/{batch['batch_id']}/sign", headers=analyst).status_code == 403  # no Approve
    assert client.post(f"/hedis/supplemental/batches/{batch['batch_id']}/sign", headers=lead).status_code == 403  # builder
    assert client.post(f"/hedis/supplemental/batches/{batch['batch_id']}/sign", headers=lead2).status_code == 200
    assert client.post(f"/hedis/supplemental/batches/{batch['batch_id']}/submit", headers=lead2).status_code == 200
    snapshot = client.post("/hedis/registry/snapshots", headers=lead, json={"note": "interim"})
    assert snapshot.status_code == 200
    assert client.post("/hedis/registry/lock", headers=lead).status_code == 403  # publisher cannot lock
    assert client.post("/hedis/registry/lock", headers=lead2).status_code == 200


def test_phi_is_masked_by_role(client):
    analyst = client.get("/hedis/registry", headers=login(client, "analyst")).json()["rows"]
    executive = client.get("/hedis/registry", headers=login(client, "exec")).json()["rows"]
    assert any(row["name"] == "Maria Alvarez" for row in analyst)
    assert all(row["name"].startswith("Member ") and row["dob"] is None for row in executive)
    log = store.load()["access_log"]
    assert {entry["detail"] for entry in log} == {"identified", "masked"}


def test_me_lists_effective_permissions(client):
    me = client.get("/hedis/me", headers=login(client, "stars")).json()
    assert "reconciliation:dispute" in me["permissions"] and "supplemental:sign" not in me["permissions"] and me["phi"] is False


def test_agent_approval_respects_the_underlying_permission(client):
    lead, lead2, stars = login(client, "lead"), login(client, "lead2"), login(client, "stars")
    batch = client.post("/hedis/supplemental/batches", headers=lead, json={"payer": "CGH"}).json()
    client.post(f"/hedis/supplemental/batches/{batch['batch_id']}/sign", headers=lead2)
    client.post(f"/hedis/supplemental/batches/{batch['batch_id']}/submit", headers=lead2)
    ack = client.post(f"/hedis/supplemental/batches/{batch['batch_id']}/simulated-ack", headers=lead).text
    assert client.post(f"/hedis/supplemental/batches/{batch['batch_id']}/ack", headers=lead, json={"text": ack}).status_code == 200
    assert client.post("/hedis/agents/rejection_resolver/run", headers=login(client, "analyst")).json()["proposed"] == 10
    proposal = next(p for p in client.get("/hedis/agents", headers=lead).json()["proposals"] if p["status"] == "PROPOSED")
    # Stars / Contract may approve agent proposals in general, but not a supplemental correction (needs Supplemental Edit)
    assert client.post(f"/hedis/agents/proposals/{proposal['proposal_id']}/decision", headers=stars,
                       json={"decision": "approve"}).status_code == 403
    approved = client.post(f"/hedis/agents/proposals/{proposal['proposal_id']}/decision", headers=lead, json={"decision": "approve"})
    assert approved.status_code == 200 and approved.json()["decided_by"] == "Lead (HEDIS Lead)"


def test_only_ops_can_flip_kill_switches(client):
    assert client.put("/hedis/agents/tiers/L1", headers=login(client, "lead"), json={"enabled": False}).status_code == 403
    assert client.put("/hedis/agents/tiers/L1", headers=login(client, "ops"), json={"enabled": False, "reason": "drill"}).json()["L1"] is False


def test_attestation_needs_the_named_role(client):
    body = {"note": "Roadmap reviewed against current systems on 2026-10-20"}
    assert client.post("/hedis/ncqa/attestations/IS7-4", headers=login(client, "auditor"), json=body).status_code == 403
    assert client.post("/hedis/ncqa/attestations/IS7-4", headers=login(client, "lead"), json=body).status_code == 200


def test_audit_endpoint_reports_chain_integrity(client):
    lead = login(client, "lead")
    client.post("/hedis/registry/snapshots", headers=lead)
    audit = client.get("/hedis/audit", headers=login(client, "auditor")).json()
    assert audit["chain_intact"] is True and audit["events"][-1]["action"] == "registry.snapshot_published"


def test_token_expiry_is_enforced_by_the_api(client, monkeypatch):
    token = tokens.issue("analyst", "HEDIS Analyst", ttl=1, now=time.time() - 10)
    assert client.get("/hedis/registry", headers={"Authorization": f"Bearer {token}"}).status_code == 401
