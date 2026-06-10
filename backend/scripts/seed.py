"""Idempotent demo seeder: demo users plus deterministic PII-free claim history.

Run with `uv run python -m scripts.seed` or import `seed_database` for tests/startup.
"""

import random
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import db
from app.auth.passwords import hash_password
from app.config import Settings
from app.main import create_app
from app.models import ClaimHistory, Modality, Role, User

DEMO_PASSWORD = "demo1234"

HISTORY_SEED = 42
HISTORY_COUNTS = {"MBR-1001": 25, "MBR-1002": 15}
SERVICE_WINDOW = (date(2023, 1, 1), date(2025, 12, 1))

CLAIM_TYPES = ("imaging", "physio", "dental", "prescription")
MODALITIES = tuple(m.value for m in Modality)
PROCEDURE_CODES = {
    "imaging": ("IMG-201", "IMG-205", "IMG-310"),
    "physio": ("PHY-110", "PHY-204", "PHY-330"),
    "dental": ("DEN-303", "DEN-115", "DEN-220"),
    "prescription": ("RX-405", "RX-112", "RX-518"),
}
DIAGNOSIS_CODES = ("M54.5", "S82.1", "M25.51", "K08.9", "J45.9", "G43.0")
NOTE_LINES = (
    "Receipt resubmitted after the first copy was illegible.",
    "Provider invoice matched the plan fee schedule.",
    "Pre-authorization was on file before the date of service.",
    "Amount exceeded the annual category maximum.",
    "Duplicate of an earlier submission; original was paid.",
)


@dataclass(frozen=True)
class DemoUser:
    email: str
    role: Role
    full_name: str
    member_id: str | None = None
    preferred_language: str = "en"


DEMO_USERS: tuple[DemoUser, ...] = (
    DemoUser("claimant@demo.ca", Role.CLAIMANT, "Casey Claimant", "MBR-1001"),
    DemoUser("claimant2@demo.ca", Role.CLAIMANT, "Camille Tremblay", "MBR-1002", "fr"),
    DemoUser("imaging@demo.ca", Role.IMAGING_SPECIALIST, "Iris Imaging"),
    DemoUser("specialist@demo.ca", Role.MEDICAL_SPECIALIST, "Sam Specialist"),
    DemoUser("agent@demo.ca", Role.INSURANCE_AGENT, "Avery Agent"),
)


@dataclass
class SeedSummary:
    users_created: list[str] = field(default_factory=list)
    users_skipped: list[str] = field(default_factory=list)
    history_inserted: int = 0
    history_counts: dict[str, int] = field(default_factory=dict)


def _history_rows(member_id: str, count: int, rng: random.Random) -> list[ClaimHistory]:
    start, end = SERVICE_WINDOW
    span_days = (end - start).days
    rows: list[ClaimHistory] = []
    for _ in range(count):
        claim_type = rng.choice(CLAIM_TYPES)
        modality = rng.choice(MODALITIES) if claim_type == "imaging" else None
        date_of_service = start + timedelta(days=rng.randrange(span_days + 1))
        rows.append(
            ClaimHistory(
                member_id=member_id,
                claim_type=claim_type,
                procedure_code=rng.choice(PROCEDURE_CODES[claim_type]),
                diagnosis_code=rng.choice(DIAGNOSIS_CODES),
                modality=modality,
                billed_amount=round(rng.uniform(80.0, 4500.0), 2),
                outcome="approved" if rng.random() < 0.75 else "rejected",
                date_of_service=date_of_service,
                decided_at=date_of_service + timedelta(days=rng.randint(7, 45)),
                notes=rng.choice(NOTE_LINES) if rng.random() < 0.25 else None,
            )
        )
    return rows


def seed_database(session: Session) -> SeedSummary:
    summary = SeedSummary()
    for spec in DEMO_USERS:
        if session.scalar(select(User.id).where(User.email == spec.email)) is not None:
            summary.users_skipped.append(spec.email)
            continue
        session.add(
            User(
                email=spec.email,
                password_hash=hash_password(DEMO_PASSWORD),
                role=spec.role,
                full_name=spec.full_name,
                member_id=spec.member_id,
                preferred_language=spec.preferred_language,
            )
        )
        summary.users_created.append(spec.email)

    existing = session.scalar(select(func.count()).select_from(ClaimHistory)) or 0
    if existing == 0:
        rng = random.Random(HISTORY_SEED)
        rows: list[ClaimHistory] = []
        for member_id, count in HISTORY_COUNTS.items():
            rows.extend(_history_rows(member_id, count, rng))
        session.add_all(rows)
        summary.history_inserted = len(rows)

    session.commit()
    summary.history_counts = {
        member_id: count
        for member_id, count in session.execute(
            select(ClaimHistory.member_id, func.count())
            .group_by(ClaimHistory.member_id)
            .order_by(ClaimHistory.member_id)
        ).all()
    }
    return summary


def main() -> None:
    settings = Settings()
    create_app(settings)
    factory = db.get_session_factory()
    with factory() as session:
        summary = seed_database(session)

    print("ClaimFlow demo seed")
    print(f"{'user':<24}status")
    for email in summary.users_created:
        print(f"{email:<24}created")
    for email in summary.users_skipped:
        print(f"{email:<24}skipped (already exists)")
    counts = ", ".join(f"{member}={count}" for member, count in summary.history_counts.items())
    print(f"claim_history rows: {counts} (inserted this run: {summary.history_inserted})")
    print(f"all demo users: {DEMO_PASSWORD}")


if __name__ == "__main__":
    main()
