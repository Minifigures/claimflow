"""Workflow state machine, the single source of truth for claim transitions.

Every transition mutates the claim, records a Decision row, and appends a
hash-chained audit event in the caller's transaction (caller commits).
"""

from sqlalchemy.orm import Session

from app.claimguard import audit
from app.models import AuditEventType, Claim, ClaimAction, ClaimState, Decision, Role, User

TERMINAL_STATES = {ClaimState.APPROVED, ClaimState.REJECTED}
SYSTEM_ROLE = "system"

TRANSITIONS: dict[tuple[ClaimState, ClaimAction], tuple[ClaimState, frozenset[str]]] = {
    (ClaimState.SUBMITTED, ClaimAction.IMAGING_COMPLETE): (
        ClaimState.IMAGING_REVIEW,
        frozenset({SYSTEM_ROLE}),
    ),
    (ClaimState.IMAGING_REVIEW, ClaimAction.FORWARD): (
        ClaimState.SPECIALIST_REVIEW,
        frozenset({Role.IMAGING_SPECIALIST.value}),
    ),
    (ClaimState.IMAGING_REVIEW, ClaimAction.RETURN_TO_CLAIMANT): (
        ClaimState.RETURNED_TO_CLAIMANT,
        frozenset({Role.IMAGING_SPECIALIST.value}),
    ),
    (ClaimState.RETURNED_TO_CLAIMANT, ClaimAction.RESUBMIT): (
        ClaimState.SUBMITTED,
        frozenset({Role.CLAIMANT.value}),
    ),
    (ClaimState.SPECIALIST_REVIEW, ClaimAction.SEND_TO_INSURER): (
        ClaimState.ADJUDICATION,
        frozenset({Role.MEDICAL_SPECIALIST.value}),
    ),
    (ClaimState.SPECIALIST_REVIEW, ClaimAction.REQUEST_FURTHER_TESTING): (
        ClaimState.PENDING_FURTHER_TESTING,
        frozenset({Role.MEDICAL_SPECIALIST.value}),
    ),
    (ClaimState.PENDING_FURTHER_TESTING, ClaimAction.RESUBMIT): (
        ClaimState.SUBMITTED,
        frozenset({Role.CLAIMANT.value}),
    ),
    (ClaimState.ADJUDICATION, ClaimAction.APPROVE): (
        ClaimState.APPROVED,
        frozenset({Role.INSURANCE_AGENT.value}),
    ),
    (ClaimState.ADJUDICATION, ClaimAction.REJECT): (
        ClaimState.REJECTED,
        frozenset({Role.INSURANCE_AGENT.value}),
    ),
}


class TransitionError(Exception):
    """A workflow action was not permitted; carries a human-readable reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def record_initial_submit(session: Session, claim: Claim, actor: User) -> Decision:
    """Record the SUBMIT decision for a freshly created claim already saved as SUBMITTED."""
    if actor.role is not Role.CLAIMANT or actor.id != claim.claimant_id:
        raise TransitionError("only the claim's claimant can submit it")
    decision = Decision(
        claim_id=claim.id,
        actor_id=actor.id,
        actor_role=actor.role.value,
        action=ClaimAction.SUBMIT,
        from_state=None,
        to_state=ClaimState.SUBMITTED,
    )
    session.add(decision)
    audit.append(
        session,
        AuditEventType.WORKFLOW_TRANSITION,
        claim_id=claim.id,
        actor_user_id=actor.id,
        actor_role=actor.role.value,
        payload={
            "action": ClaimAction.SUBMIT.value,
            "from": None,
            "to": ClaimState.SUBMITTED.value,
        },
    )
    session.flush()
    return decision


def apply_transition(
    session: Session,
    claim: Claim,
    action: ClaimAction,
    *,
    actor: User | None,
    note: str | None = None,
) -> Decision:
    if claim.state in TERMINAL_STATES:
        raise TransitionError(
            f"claim is in terminal state {claim.state.value}; no further actions are allowed"
        )
    transition = TRANSITIONS.get((claim.state, action))
    if transition is None:
        raise TransitionError(f"action {action.value} not allowed from state {claim.state.value}")
    to_state, allowed_roles = transition
    role_value = SYSTEM_ROLE if actor is None else actor.role.value
    if role_value not in allowed_roles:
        raise TransitionError(
            f"role {role_value} may not perform {action.value} from state {claim.state.value}"
        )
    if actor is not None and role_value == Role.CLAIMANT.value and actor.id != claim.claimant_id:
        raise TransitionError(f"only the claim's claimant may perform {action.value}")

    from_state = claim.state
    claim.state = to_state
    decision = Decision(
        claim_id=claim.id,
        actor_id=actor.id if actor is not None else None,
        actor_role=role_value,
        action=action,
        from_state=from_state,
        to_state=to_state,
        note=note,
    )
    session.add(decision)
    audit.append(
        session,
        AuditEventType.WORKFLOW_TRANSITION,
        claim_id=claim.id,
        actor_user_id=actor.id if actor is not None else None,
        actor_role=role_value,
        payload={
            "action": action.value,
            "from": from_state.value,
            "to": to_state.value,
            "note": note,
        },
    )
    session.flush()
    return decision


def allowed_actions(state: ClaimState, role_value: str) -> set[ClaimAction]:
    return {
        action
        for (from_state, action), (_, roles) in TRANSITIONS.items()
        if from_state == state and role_value in roles
    }
