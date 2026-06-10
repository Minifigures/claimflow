from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import db
from app.config import Settings
from app.main import create_app
from app.models import Role, User
from app.auth.passwords import hash_password


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path}/test.sqlite",
        upload_dir=tmp_path / "uploads",
        jwt_secret="test-secret",
        cookie_secure=False,
        email_provider="console",
        model_backend="stub",
        anthropic_api_key="",
        chroma_dir=tmp_path / "chroma",
    )


@pytest.fixture()
def app(settings: Settings):
    return create_app(settings)


@pytest.fixture()
def client(app) -> Generator[TestClient, None, None]:
    with TestClient(app, base_url="http://localhost:3000") as c:
        yield c


@pytest.fixture()
def session(app) -> Generator[Session, None, None]:
    factory = db.get_session_factory()
    s = factory()
    try:
        yield s
    finally:
        s.close()


DEMO_PASSWORD = "demo1234"

DEMO_USERS = [
    ("claimant@demo.ca", Role.CLAIMANT, "Casey Claimant", "MBR-1001"),
    ("imaging@demo.ca", Role.IMAGING_SPECIALIST, "Iris Imaging", None),
    ("specialist@demo.ca", Role.MEDICAL_SPECIALIST, "Sam Specialist", None),
    ("agent@demo.ca", Role.INSURANCE_AGENT, "Avery Agent", None),
]


@pytest.fixture()
def users(session: Session) -> dict[str, User]:
    created: dict[str, User] = {}
    for email, role, name, member_id in DEMO_USERS:
        u = User(
            email=email,
            password_hash=hash_password(DEMO_PASSWORD),
            role=role,
            full_name=name,
            member_id=member_id,
        )
        session.add(u)
        created[role.value] = u
    session.commit()
    return created


def login(client: TestClient, email: str, password: str = DEMO_PASSWORD) -> None:
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text


@pytest.fixture()
def as_claimant(client: TestClient, users) -> TestClient:
    login(client, "claimant@demo.ca")
    return client


@pytest.fixture()
def as_imaging(client: TestClient, users) -> TestClient:
    login(client, "imaging@demo.ca")
    return client


@pytest.fixture()
def as_specialist(client: TestClient, users) -> TestClient:
    login(client, "specialist@demo.ca")
    return client


@pytest.fixture()
def as_agent(client: TestClient, users) -> TestClient:
    login(client, "agent@demo.ca")
    return client
