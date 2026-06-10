import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import COOKIE_NAME, require_role
from app.auth.jwt import create_token
from app.claimguard import audit
from app.config import Settings
from app.models import AuditEvent, Role, User
from tests.conftest import DEMO_PASSWORD, login


@pytest.fixture()
def agent_only_route(app: FastAPI) -> None:
    @app.get("/api/_test/agent-only")
    def agent_only(
        user: User = Depends(require_role(Role.INSURANCE_AGENT)),
    ) -> dict[str, int]:
        return {"user_id": user.id}


def test_login_success_sets_session_cookie_and_me(
    client: TestClient, users: dict[str, User]
) -> None:
    resp = client.post(
        "/api/auth/login", json={"email": "claimant@demo.ca", "password": DEMO_PASSWORD}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "claimant"
    assert body["email"] == "claimant@demo.ca"
    assert body["id"] == users["claimant"].id

    set_cookie = resp.headers["set-cookie"].lower()
    assert set_cookie.startswith(f"{COOKIE_NAME}=")
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "path=/" in set_cookie
    assert COOKIE_NAME in client.cookies

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json() == body


def test_login_failures_identical_detail_and_audited(
    client: TestClient, users: dict[str, User], session: Session
) -> None:
    wrong_password = client.post(
        "/api/auth/login", json={"email": "claimant@demo.ca", "password": "wrong-password"}
    )
    unknown_email = client.post(
        "/api/auth/login", json={"email": "nobody@demo.ca", "password": DEMO_PASSWORD}
    )
    assert wrong_password.status_code == 401
    assert unknown_email.status_code == 401
    assert wrong_password.json()["detail"] == unknown_email.json()["detail"]
    assert COOKIE_NAME not in client.cookies

    events = session.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "auth.failed").order_by(AuditEvent.id)
    ).all()
    assert len(events) == 2
    assert all(e.actor_user_id is None for e in events)
    assert "claimant@demo.ca" in events[0].payload_json
    assert "nobody@demo.ca" in events[1].payload_json

    valid, checked = audit.verify_chain(session)
    assert valid is True
    assert checked == 2


def test_login_success_records_auth_login_with_actor(
    client: TestClient, users: dict[str, User], session: Session
) -> None:
    login(client, "agent@demo.ca")

    event = session.scalar(select(AuditEvent).where(AuditEvent.event_type == "auth.login"))
    assert event is not None
    assert event.actor_user_id == users["insurance_agent"].id
    assert event.actor_role == "insurance_agent"

    valid, checked = audit.verify_chain(session)
    assert valid is True
    assert checked == 1


def test_me_without_cookie_is_401(client: TestClient) -> None:
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Not authenticated"


def test_expired_token_is_401(
    client: TestClient, users: dict[str, User], settings: Settings
) -> None:
    token = create_token(users["claimant"], settings, expires_hours=-1)
    client.cookies.set(COOKIE_NAME, token)
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid or expired session"


def test_tampered_token_is_401(
    client: TestClient, users: dict[str, User], settings: Settings
) -> None:
    forged_settings = settings.model_copy(update={"jwt_secret": "attacker-secret"})
    token = create_token(users["claimant"], forged_settings)
    client.cookies.set(COOKIE_NAME, token)
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid or expired session"


def test_logout_clears_cookie(as_claimant: TestClient) -> None:
    assert as_claimant.get("/api/auth/me").status_code == 200

    resp = as_claimant.post("/api/auth/logout")
    assert resp.status_code == 200
    assert resp.json() == {"status": "logged_out"}
    set_cookie = resp.headers["set-cookie"].lower()
    assert set_cookie.startswith(f"{COOKIE_NAME}=")
    assert "max-age=0" in set_cookie or "expires=" in set_cookie
    assert COOKIE_NAME not in as_claimant.cookies

    assert as_claimant.get("/api/auth/me").status_code == 401


def test_login_rejects_cross_origin(client: TestClient, users: dict[str, User]) -> None:
    resp = client.post(
        "/api/auth/login",
        json={"email": "claimant@demo.ca", "password": DEMO_PASSWORD},
        headers={"Origin": "http://evil.example"},
    )
    assert resp.status_code == 403
    assert COOKIE_NAME not in client.cookies


def test_login_allows_matching_origin(
    client: TestClient, users: dict[str, User], settings: Settings
) -> None:
    resp = client.post(
        "/api/auth/login",
        json={"email": "claimant@demo.ca", "password": DEMO_PASSWORD},
        headers={"Origin": settings.app_origin},
    )
    assert resp.status_code == 200


def test_login_allows_missing_origin(client: TestClient, users: dict[str, User]) -> None:
    resp = client.post(
        "/api/auth/login", json={"email": "claimant@demo.ca", "password": DEMO_PASSWORD}
    )
    assert resp.status_code == 200


def test_require_role_allows_agent(
    agent_only_route: None, as_agent: TestClient, users: dict[str, User]
) -> None:
    resp = as_agent.get("/api/_test/agent-only")
    assert resp.status_code == 200
    assert resp.json() == {"user_id": users["insurance_agent"].id}


def test_require_role_rejects_claimant(agent_only_route: None, as_claimant: TestClient) -> None:
    resp = as_claimant.get("/api/_test/agent-only")
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Insufficient role"


def test_require_role_rejects_anonymous(agent_only_route: None, client: TestClient) -> None:
    resp = client.get("/api/_test/agent-only")
    assert resp.status_code == 401
