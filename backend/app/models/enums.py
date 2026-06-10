from enum import StrEnum

from sqlalchemy import Enum as SAEnum


def db_enum(enum_cls: type[StrEnum], length: int = 32) -> SAEnum:
    """Store StrEnum *values* (not names) as short strings — portable across DBs."""
    return SAEnum(
        enum_cls,
        native_enum=False,
        length=length,
        values_callable=lambda obj: [m.value for m in obj],
    )


class Role(StrEnum):
    CLAIMANT = "claimant"
    IMAGING_SPECIALIST = "imaging_specialist"
    MEDICAL_SPECIALIST = "medical_specialist"
    INSURANCE_AGENT = "insurance_agent"


class ClaimState(StrEnum):
    SUBMITTED = "SUBMITTED"
    IMAGING_REVIEW = "IMAGING_REVIEW"
    RETURNED_TO_CLAIMANT = "RETURNED_TO_CLAIMANT"
    SPECIALIST_REVIEW = "SPECIALIST_REVIEW"
    PENDING_FURTHER_TESTING = "PENDING_FURTHER_TESTING"
    ADJUDICATION = "ADJUDICATION"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ClaimAction(StrEnum):
    SUBMIT = "submit"
    IMAGING_COMPLETE = "imaging_complete"
    FORWARD = "forward"
    RETURN_TO_CLAIMANT = "return_to_claimant"
    RESUBMIT = "resubmit"
    SEND_TO_INSURER = "send_to_insurer"
    REQUEST_FURTHER_TESTING = "request_further_testing"
    APPROVE = "approve"
    REJECT = "reject"


class DocumentKind(StrEnum):
    IMAGING = "imaging"
    MEDICAL_RECORD = "medical_record"
    OTHER = "other"


class Modality(StrEnum):
    XRAY = "xray"
    CT = "ct"
    MRI = "mri"


class ArtifactStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


class Recommendation(StrEnum):
    SUPPORTS_CLAIM = "SUPPORTS_CLAIM"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    REQUIRES_FURTHER_TESTING = "REQUIRES_FURTHER_TESTING"


class NotificationStatus(StrEnum):
    LOGGED = "logged"
    SENT = "sent"
    FAILED = "failed"


class AuditEventType(StrEnum):
    CLAIM_SUBMIT = "claim.submit"
    DOCUMENT_UPLOAD = "document.upload"
    WORKFLOW_TRANSITION = "workflow.transition"
    DECISION_APPROVE = "decision.approve"
    DECISION_REJECT = "decision.reject"
    LLM_CALL = "llm_call"
    RAG_RETRIEVAL = "rag_retrieval"
    EMAIL_DRAFTED = "email.drafted"
    EMAIL_SENT = "email.sent"
    AUTH_LOGIN = "auth.login"
    AUTH_FAILED = "auth.failed"
