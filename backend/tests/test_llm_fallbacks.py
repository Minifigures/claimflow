"""Tests for the deterministic keyless-path generators in app/llm/fallbacks.py."""

import itertools

import pytest

from app.llm.fallbacks import (
    fallback_adjudication,
    fallback_claimant_email,
    fallback_diagnostic_report,
    fallback_recommendation,
)
from app.llm.schemas import (
    AdjudicationSummaryLLM,
    ClaimantEmailLLM,
    DiagnosticReportLLM,
    RecommendationNoteLLM,
)
from app.ml.base import ForensicSignal, ImagingAnalysis

# ---------------------------------------------------------------- builders


def make_analysis(
    *,
    modality: str = "xray",
    confidence: float = 0.93,
    verdict: str = "authentic",
    risk: float = 0.05,
    signals: list[ForensicSignal] | None = None,
    quality_flags: list[str] | None = None,
) -> ImagingAnalysis:
    return ImagingAnalysis(
        modality=modality,
        modality_confidence=confidence,
        modality_probs={"xray": 0.93, "ct": 0.05, "mri": 0.02},
        authenticity_verdict=verdict,
        authenticity_risk=risk,
        signals=signals or [],
        quality_flags=quality_flags or [],
        backend="stub",
    )


def make_bundle(
    *,
    report: dict | None | str = "default",
    modality_for_procedure: str | None = "mri",
    uploads: list[dict] | None = None,
) -> dict:
    if report == "default":
        report = {
            "modality": "mri",
            "authenticity_verdict": "authentic",
            "authenticity_risk": 0.05,
            "requires_mandatory_review": False,
            "impression": "MRI of the knee, no inconsistencies noted.",
        }
    return {
        "claim": {
            "claim_type": "imaging",
            "procedure_code": "MRI-KNEE-01",
            "diagnosis_code": "M23.2",
            "amount_claimed": 850.0,
            "incident_date": "2026-05-01",
        },
        "diagnostic_report": report,
        "uploads": uploads
        if uploads is not None
        else [{"filename": "knee.dcm", "kind": "imaging", "text_extract_ok": True}],
        "modality_for_procedure": modality_for_procedure,
    }


HISTORY_CLEAN = {"total": 4, "approved": 4, "rejected": 0, "recent_12mo": 1, "prior_rejections": 0}


# ---------------------------------------------------------------- diagnostic report


def test_diagnostic_report_clean_analysis() -> None:
    report = fallback_diagnostic_report(make_analysis(), declared_modality="xray")
    assert isinstance(report, DiagnosticReportLLM)
    assert report.modality_assessment == "xray"
    assert report.modality_agrees_with_classifier is True
    assert report.image_quality == "adequate"
    assert report.quality_issues == []
    assert report.findings == []
    assert report.visual_inconsistencies == []
    assert report.confidence == 0.0
    assert "xray" in report.impression
    assert "0.93" in report.impression
    assert "authentic" in report.impression
    assert "specialist must perform the full read" in report.impression


def test_diagnostic_report_quality_flags_degrade() -> None:
    flags = ["low_resolution", "overexposed"]
    report = fallback_diagnostic_report(
        make_analysis(quality_flags=flags), declared_modality=None
    )
    assert report.image_quality == "degraded"
    assert report.quality_issues == flags


def test_diagnostic_report_signal_threshold() -> None:
    signals = [
        ForensicSignal(name="ela", score=0.9, finding="compression artefacts in corner"),
        ForensicSignal(name="copy_move", score=0.5, finding="duplicated region detected"),
        ForensicSignal(name="noise", score=0.49, finding="noise floor slightly uneven"),
    ]
    report = fallback_diagnostic_report(
        make_analysis(verdict="suspicious", signals=signals), declared_modality=None
    )
    assert report.visual_inconsistencies == [
        "compression artefacts in corner",
        "duplicated region detected",
    ]
    assert "suspicious" in report.impression


def test_diagnostic_report_unknown_modality_maps_to_other() -> None:
    report = fallback_diagnostic_report(
        make_analysis(modality="ultrasound"), declared_modality=None
    )
    assert report.modality_assessment == "other"


# ---------------------------------------------------------------- recommendation


def test_recommendation_tampered_requires_further_testing() -> None:
    bundle = make_bundle(
        report={
            "modality": "mri",
            "authenticity_verdict": "likely_fraudulent",
            "authenticity_risk": 0.9,
            "requires_mandatory_review": True,
            "impression": "inconsistencies noted",
        }
    )
    note = fallback_recommendation(bundle)
    assert isinstance(note, RecommendationNoteLLM)
    assert note.recommendation == "REQUIRES_FURTHER_TESTING"
    assert any("original DICOM" in step for step in note.suggested_next_steps)
    auth = next(c for c in note.consistency_checks if c.check == "authenticity_concerns")
    assert auth.result == "inconsistent"


def test_recommendation_mandatory_review_alone_requires_further_testing() -> None:
    bundle = make_bundle(
        report={
            "modality": "mri",
            "authenticity_verdict": "authentic",
            "authenticity_risk": 0.1,
            "requires_mandatory_review": True,
            "impression": "ok",
        }
    )
    assert fallback_recommendation(bundle).recommendation == "REQUIRES_FURTHER_TESTING"


def test_recommendation_missing_report_insufficient_evidence() -> None:
    note = fallback_recommendation(make_bundle(report=None))
    assert note.recommendation == "INSUFFICIENT_EVIDENCE"
    assert any("imaging analysis missing" in gap for gap in note.identified_gaps)


def test_recommendation_modality_mismatch_insufficient_evidence() -> None:
    bundle = make_bundle(
        report={
            "modality": "xray",
            "authenticity_verdict": "authentic",
            "authenticity_risk": 0.05,
            "requires_mandatory_review": False,
            "impression": "ok",
        },
        modality_for_procedure="mri",
    )
    note = fallback_recommendation(bundle)
    assert note.recommendation == "INSUFFICIENT_EVIDENCE"
    proc = next(
        c for c in note.consistency_checks if c.check == "imaging_matches_stated_procedure"
    )
    assert proc.result == "inconsistent"


def test_recommendation_clean_supports_claim() -> None:
    note = fallback_recommendation(make_bundle())
    assert note.recommendation == "SUPPORTS_CLAIM"
    assert note.confidence == 0.0
    assert note.identified_gaps == []
    assert note.summary
    checks = {c.check for c in note.consistency_checks}
    assert checks == {
        "imaging_matches_stated_procedure",
        "imaging_matches_diagnosis_code",
        "documents_internally_consistent",
        "dates_plausible",
        "authenticity_concerns",
    }
    proc = next(
        c for c in note.consistency_checks if c.check == "imaging_matches_stated_procedure"
    )
    assert proc.result == "consistent"
    auth = next(c for c in note.consistency_checks if c.check == "authenticity_concerns")
    assert auth.result == "consistent"
    dates = next(c for c in note.consistency_checks if c.check == "dates_plausible")
    assert dates.result == "indeterminate"


def test_recommendation_supporting_findings_cite_sources() -> None:
    note = fallback_recommendation(make_bundle())
    sources = {f.source_document for f in note.supporting_findings}
    assert {"claim_form", "diagnostic_report", "upload:knee.dcm"} <= sources


def test_recommendation_failed_extraction_noted() -> None:
    bundle = make_bundle(
        uploads=[{"filename": "referral.pdf", "kind": "medical_record", "text_extract_ok": False}]
    )
    note = fallback_recommendation(bundle)
    assert any("upload:referral.pdf" in gap for gap in note.identified_gaps)
    docs = next(
        c for c in note.consistency_checks if c.check == "documents_internally_consistent"
    )
    assert docs.result == "indeterminate"
    assert "referral.pdf" in docs.detail


# ---------------------------------------------------------------- adjudication


def test_adjudication_supports_claim_leans_approve() -> None:
    summary = fallback_adjudication("SUPPORTS_CLAIM", HISTORY_CLEAN, [], "authentic")
    assert isinstance(summary, AdjudicationSummaryLLM)
    assert summary.recommendation_lean == "LEAN_APPROVE"
    assert summary.risk_factors == []
    assert summary.consistency_with_history.assessment == "consistent"
    assert summary.confidence == 0.0


@pytest.mark.parametrize("rec", [None, "INSUFFICIENT_EVIDENCE", "REQUIRES_FURTHER_TESTING"])
def test_adjudication_non_supporting_no_clear_lean(rec: str | None) -> None:
    summary = fallback_adjudication(rec, HISTORY_CLEAN, [], "authentic")
    assert summary.recommendation_lean == "NO_CLEAR_LEAN"


def test_adjudication_non_authentic_forces_no_clear_lean_and_high_risk() -> None:
    summary = fallback_adjudication("SUPPORTS_CLAIM", HISTORY_CLEAN, [], "suspicious")
    assert summary.recommendation_lean == "NO_CLEAR_LEAN"
    assert any(f.severity == "high" for f in summary.risk_factors)


def test_adjudication_history_risk_factors() -> None:
    stats = {"total": 9, "approved": 4, "rejected": 3, "recent_12mo": 6, "prior_rejections": 3}
    summary = fallback_adjudication("SUPPORTS_CLAIM", stats, [], "authentic")
    factors = {f.factor for f in summary.risk_factors}
    assert "history of rejected claims" in factors
    assert "high recent claim frequency" in factors
    assert all(f.severity == "medium" for f in summary.risk_factors)
    assert summary.consistency_with_history.assessment == "minor_discrepancies"


def test_adjudication_no_history() -> None:
    stats = {"total": 0, "approved": 0, "rejected": 0, "recent_12mo": 0, "prior_rejections": 0}
    summary = fallback_adjudication(None, stats, [], None)
    assert summary.consistency_with_history.assessment == "no_history"
    assert summary.recommendation_lean == "NO_CLEAR_LEAN"
    assert summary.risk_factors == []


def test_adjudication_similar_case_notes_match_count() -> None:
    cases = [{"case_ref": "C-1"}, {"case_ref": "C-2"}, {"case_ref": "C-3"}]
    summary = fallback_adjudication("SUPPORTS_CLAIM", HISTORY_CLEAN, cases, "authentic")
    assert summary.similar_case_relevance_notes == [
        "(automated) same modality and procedure family"
    ] * 3


# ---------------------------------------------------------------- claimant email

EMAIL_COMBOS = list(
    itertools.product(["APPROVED", "REJECTED"], ["en", "fr"], ["formal", "plain_language"])
)

ENGLISH_FILLER = ["Dear ", "Hi ", "Sincerely", "Thank", "approved", "Unfortunately", "review"]
FORBIDDEN_WORDS = ["score", "fraud", "risk", "fraude", "risque"]


def render(decision: str, language: str, tone: str) -> ClaimantEmailLLM:
    return fallback_claimant_email(
        decision=decision,  # type: ignore[arg-type]
        first_name="Camille",
        language=language,  # type: ignore[arg-type]
        tone=tone,  # type: ignore[arg-type]
        claim_ref="CLM-2031",
        claim_type="imagerie" if language == "fr" else "imaging",
    )


@pytest.mark.parametrize(("decision", "language", "tone"), EMAIL_COMBOS)
def test_email_templates_render_and_fill_slots(decision: str, language: str, tone: str) -> None:
    email = render(decision, language, tone)
    assert isinstance(email, ClaimantEmailLLM)
    assert "Camille" in email.greeting
    full_text = " ".join([email.subject, email.greeting, *email.body_paragraphs, email.closing])
    assert "CLM-2031" in full_text
    assert "{" not in full_text and "}" not in full_text
    for word in FORBIDDEN_WORDS:
        assert word not in full_text.lower(), f"forbidden word {word!r} in {decision}/{language}"


@pytest.mark.parametrize("tone", ["formal", "plain_language"])
@pytest.mark.parametrize("decision", ["APPROVED", "REJECTED"])
def test_email_french_has_no_english_filler(decision: str, tone: str) -> None:
    email = render(decision, "fr", tone)
    full_text = " ".join([email.subject, email.greeting, *email.body_paragraphs, email.closing])
    for filler in ENGLISH_FILLER:
        assert filler not in full_text, f"English filler {filler!r} in fr/{decision}/{tone}"


@pytest.mark.parametrize("language", ["en", "fr"])
@pytest.mark.parametrize("tone", ["formal", "plain_language"])
def test_email_rejection_mentions_appeal_window(language: str, tone: str) -> None:
    email = render("REJECTED", language, tone)
    assert "30" in " ".join(email.body_paragraphs)


def test_email_all_eight_templates_distinct() -> None:
    rendered = {
        (d, lg, t): render(d, lg, t).model_dump_json() for d, lg, t in EMAIL_COMBOS
    }
    assert len(set(rendered.values())) == 8


# ---------------------------------------------------------------- determinism


def test_determinism_all_functions() -> None:
    analysis = make_analysis(
        verdict="suspicious",
        signals=[ForensicSignal(name="ela", score=0.8, finding="artefact")],
        quality_flags=["blur"],
    )
    a1 = fallback_diagnostic_report(analysis, declared_modality="ct")
    a2 = fallback_diagnostic_report(analysis, declared_modality="ct")
    assert a1.model_dump() == a2.model_dump()

    bundle = make_bundle()
    r1, r2 = fallback_recommendation(bundle), fallback_recommendation(bundle)
    assert r1.model_dump() == r2.model_dump()

    stats = {"total": 6, "approved": 3, "rejected": 2, "recent_12mo": 5, "prior_rejections": 2}
    cases = [{"case_ref": "C-1"}]
    j1 = fallback_adjudication("SUPPORTS_CLAIM", stats, cases, "suspicious")
    j2 = fallback_adjudication("SUPPORTS_CLAIM", stats, cases, "suspicious")
    assert j1.model_dump() == j2.model_dump()

    e1 = render("REJECTED", "fr", "formal")
    e2 = render("REJECTED", "fr", "formal")
    assert e1.model_dump() == e2.model_dump()
