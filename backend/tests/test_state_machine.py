from itertools import count

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.claimguard import audit
from app.models import Claim, ClaimAction, ClaimState, Decision, Role, User
from app.workflow.state_machine import (
    SYSTEM_ROLE,
    TERMINAL_STATES,
    TRANSITIONS,
    TransitionError,
    allowed_actions,
    apply_transition,
    record_initial_submit,
)

_ref_counter = count(1)

ALL_ACTOR_ROLES = [r.value for r in Role] + [SYSTEM_ROLE]

ACTION_ACTOR_ROLE: dict[ClaimAction, str] = {
    ClaimAction.SUBMIT: Role.CLAIMANT.value,
    ClaimAction.IMAGING_COMPLETE: SYSTEM_ROLE,
    ClaimAction.FORWARD: Role.IMAGING_SPECIALIST.value,
    ClaimAction.RETURN_TO_CLAIMANT: Role.IMAGING_SPECIALIST.value,
    ClaimAction.RESUBMIT: Role.CLAIMANT.value,
    ClaimAction.SEND_TO_INSURER: Role.MEDICAL_SPECIALIST.value,
    ClaimAction.REQUEST_FURTHER_TESTING: Role.MEDICAL_SPECIALIST.value,
    ClaimAction.APPROVE: Role.INSURANCE_AGENT.value,
    ClaimAction.REJECT: Role.INSURANCE_AGENT.value,
}

INVALID_PAIRS = [
    (state, action)
    for state in ClaimState
    for action in ClaimAction
    if (state, action) not in TRANSITIONS
]


def make_claim(session: Session, claimant: User, state: ClaimState) -> Claim:
    claim = Claim(
        claim_ref=f"CLM-TEST-{next(_ref_counter):05d}",
        claimant_id=claimant.id,
        claim_type="imaging",
        state=state,
    )
    session.add(claim)
    session.flush()
    return claim


def make_other_claimant(session: Session) -> User:
    other = User(
        email="other-claimant@demo.ca",
        password_hash="not-a-real-hash",
        role=Role.CLAIMANT,
        full_name="Olive Other",
        member_id="MBR-2002",
    )
    session.add(other)
    session.flush()
    return other


def actor_for(users: dict[str, User], role_value: str) -> User | None:
    return None if role_value == SYSTEM_ROLE else users[role_value]


def test_full_matrix_invalid_pairs_raise(session: Session, users: dict[str, User]) -> None:
    claimant = users[Role.CLAIMANT.value]
    for state, action in INVALID_PAIRS:
        claim = make_claim(session, claimant, state)
        actor = actor_for(users, ACTION_ACTOR_ROLE[action])
        with pytest.raises(TransitionError):
            apply_transition(session, claim, action, actor=actor)
        assert claim.state == state, f"{state.value} x {action.value} mutated state"


@pytest.mark.parametrize(
    ("state", "action"),
    list(TRANSITIONS),
    ids=[f"{s.value}-{a.value}" for s, a in TRANSITIONS],
)
def test_allowed_transition_succeeds(
    session: Session, users: dict[str, User], state: ClaimState, action: ClaimAction
) -> None:
    expected_state, allowed_roles = TRANSITIONS[(state, action)]
    role_value = next(iter(allowed_roles))
    actor = actor_for(users, role_value)
    claimant = users[Role.CLAIMANT.value]
    claim = make_claim(session, claimant, state)

    decision = apply_transition(session, claim, action, actor=actor, note="ok")

    assert claim.state == expected_state
    assert decision.id is not None
    assert decision.claim_id == claim.id
    assert decision.action == action
    assert decision.from_state == state
    assert decision.to_state == expected_state
    assert decision.note == "ok"
    assert decision.actor_role == role_value
    assert decision.actor_id == (actor.id if actor is not None else None)

    stored = session.scalars(select(Decision).where(Decision.claim_id == claim.id)).all()
    assert len(stored) == 1

    ok, n = audit.verify_chain(session)
    assert ok is True
    assert n == 1


def test_role_enforcement_wrong_roles_raise(session: Session, users: dict[str, User]) -> None:
    claimant = users[Role.CLAIMANT.value]
    for (state, action), (_, allowed_roles) in TRANSITIONS.items():
        for role_value in ALL_ACTOR_ROLES:
            if role_value in allowed_roles:
                continue
            claim = make_claim(session, claimant, state)
            actor = actor_for(users, role_value)
            with pytest.raises(TransitionError):
                apply_transition(session, claim, action, actor=actor)
            assert claim.state == state


def test_claimant_cannot_resubmit_someone_elses_claim(
    session: Session, users: dict[str, User]
) -> None:
    owner = users[Role.CLAIMANT.value]
    other = make_other_claimant(session)
    for state in (ClaimState.RETURNED_TO_CLAIMANT, ClaimState.PENDING_FURTHER_TESTING):
        claim = make_claim(session, owner, state)
        with pytest.raises(TransitionError):
            apply_transition(session, claim, ClaimAction.RESUBMIT, actor=other)
        assert claim.state == state
        decision = apply_transition(session, claim, ClaimAction.RESUBMIT, actor=owner)
        assert claim.state == ClaimState.SUBMITTED
        assert decision.actor_id == owner.id


def test_terminal_states_allow_no_actions(session: Session, users: dict[str, User]) -> None:
    claimant = users[Role.CLAIMANT.value]
    for state in TERMINAL_STATES:
        claim = make_claim(session, claimant, state)
        for action in ClaimAction:
            actor = actor_for(users, ACTION_ACTOR_ROLE[action])
            with pytest.raises(TransitionError):
                apply_transition(session, claim, action, actor=actor)
            assert claim.state == state
        for role_value in ALL_ACTOR_ROLES:
            assert allowed_actions(state, role_value) == set()


def test_record_initial_submit_owner(session: Session, users: dict[str, User]) -> None:
    claimant = users[Role.CLAIMANT.value]
    claim = make_claim(session, claimant, ClaimState.SUBMITTED)

    decision = record_initial_submit(session, claim, claimant)

    assert decision.id is not None
    assert decision.claim_id == claim.id
    assert decision.action == ClaimAction.SUBMIT
    assert decision.from_state is None
    assert decision.to_state == ClaimState.SUBMITTED
    assert decision.actor_id == claimant.id
    assert decision.actor_role == Role.CLAIMANT.value
    assert claim.state == ClaimState.SUBMITTED

    ok, n = audit.verify_chain(session)
    assert ok is True
    assert n == 1


def test_record_initial_submit_rejects_non_owner_and_non_claimant(
    session: Session, users: dict[str, User]
) -> None:
    owner = users[Role.CLAIMANT.value]
    other = make_other_claimant(session)
    claim = make_claim(session, owner, ClaimState.SUBMITTED)
    with pytest.raises(TransitionError):
        record_initial_submit(session, claim, other)
    for role in (Role.IMAGING_SPECIALIST, Role.MEDICAL_SPECIALIST, Role.INSURANCE_AGENT):
        with pytest.raises(TransitionError):
            record_initial_submit(session, claim, users[role.value])
    assert session.scalars(select(Decision)).all() == []


def test_allowed_actions_spot_checks() -> None:
    assert allowed_actions(ClaimState.IMAGING_REVIEW, Role.IMAGING_SPECIALIST.value) == {
        ClaimAction.FORWARD,
        ClaimAction.RETURN_TO_CLAIMANT,
    }
    assert allowed_actions(ClaimState.IMAGING_REVIEW, Role.CLAIMANT.value) == set()
    assert allowed_actions(ClaimState.SUBMITTED, SYSTEM_ROLE) == {ClaimAction.IMAGING_COMPLETE}
    assert allowed_actions(ClaimState.SUBMITTED, Role.CLAIMANT.value) == set()
    assert allowed_actions(ClaimState.ADJUDICATION, Role.INSURANCE_AGENT.value) == {
        ClaimAction.APPROVE,
        ClaimAction.REJECT,
    }
    assert allowed_actions(ClaimState.RETURNED_TO_CLAIMANT, Role.CLAIMANT.value) == {
        ClaimAction.RESUBMIT,
    }
    assert allowed_actions(ClaimState.PENDING_FURTHER_TESTING, Role.CLAIMANT.value) == {
        ClaimAction.RESUBMIT,
    }
