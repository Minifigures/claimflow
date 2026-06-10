"""Pydantic output contracts for every LLM route — used by messages.parse() structured
outputs on the keyed path and produced verbatim by the deterministic fallbacks on the
keyless path (the assessment's allowed 'mock')."""

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------- stage 1c: diagnostic report


class Finding(BaseModel):
    description: str
    location: str | None = None
    severity: Literal["normal", "minor", "moderate", "significant"]
    confidence: float = Field(ge=0.0, le=1.0)


class DiagnosticReportLLM(BaseModel):
    """LLM-authored fields only — authenticity facts are system-injected post-parse so
    the model can never overrule the forensics layer."""

    modality_assessment: Literal["xray", "ct", "mri", "other"]
    modality_agrees_with_classifier: bool
    anatomical_region: str
    view: str | None = None
    image_quality: Literal["adequate", "degraded", "non_diagnostic"]
    quality_issues: list[str]
    findings: list[Finding]
    impression: str
    visual_inconsistencies: list[str]
    confidence: float = Field(ge=0.0, le=1.0)


DIAGNOSTIC_DISCLAIMER = (
    "Draft generated for specialist review. Not a diagnosis. A licensed imaging "
    "specialist must review, edit, and approve before this report is used."
)

# ---------------------------------------------------------------- stage 2: recommendation note


class SupportingFinding(BaseModel):
    source_document: str  # "diagnostic_report" | "claim_form" | "upload:<filename>"
    finding: str
    relevance: str


class ConsistencyCheck(BaseModel):
    check: Literal[
        "imaging_matches_stated_procedure",
        "imaging_matches_diagnosis_code",
        "documents_internally_consistent",
        "dates_plausible",
        "authenticity_concerns",
    ]
    result: Literal["consistent", "inconsistent", "indeterminate", "not_applicable"]
    detail: str


class RecommendationNoteLLM(BaseModel):
    recommendation: Literal[
        "SUPPORTS_CLAIM", "INSUFFICIENT_EVIDENCE", "REQUIRES_FURTHER_TESTING"
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str
    supporting_findings: list[SupportingFinding]
    identified_gaps: list[str]
    suggested_next_steps: list[str]
    consistency_checks: list[ConsistencyCheck]


ADVISORY_NOTICE = (
    "Advisory analysis only. The reviewing specialist remains responsible for the "
    "recommendation sent to the insurer."
)

# ---------------------------------------------------------------- stage 3: adjudication summary


class RiskFactor(BaseModel):
    factor: str
    severity: Literal["low", "medium", "high"]
    source: str


class ConsistencyWithHistory(BaseModel):
    assessment: Literal[
        "consistent", "minor_discrepancies", "major_discrepancies", "no_history"
    ]
    details: str


class SimilarCaseOutcome(BaseModel):
    """case_ref / similarity / outcome are system-supplied from retrieval post-parse —
    the model only authors relevance_note, so precedents cannot be fabricated."""

    case_ref: str
    similarity: float = Field(ge=0.0, le=1.0)
    outcome: str
    relevance_note: str


class AdjudicationSummaryLLM(BaseModel):
    summary: str
    risk_factors: list[RiskFactor]
    consistency_with_history: ConsistencyWithHistory
    similar_case_relevance_notes: list[str]  # one per retrieved case, in order
    recommendation_lean: Literal["LEAN_APPROVE", "LEAN_REJECT", "NO_CLEAR_LEAN"]
    confidence: float = Field(ge=0.0, le=1.0)


ADJUDICATION_NOTICE = (
    "Advisory analysis only. The final approve/reject decision rests solely with the "
    "insurance agent."
)

# ---------------------------------------------------------------- claimant email


class ClaimantEmailLLM(BaseModel):
    subject: str
    greeting: str
    body_paragraphs: list[str] = Field(min_length=1, max_length=4)
    closing: str


# ---------------------------------------------------------------- common result envelope


class StageResult(BaseModel):
    """What every stage function returns regardless of keyed/keyless path."""

    payload: dict
    generated_by: str  # model id or "fallback_template"
    prompt_version: str
    fallback_reason: str | None = None  # no_api_key | refusal | llm_truncated | llm_error
    requires_mandatory_review: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
