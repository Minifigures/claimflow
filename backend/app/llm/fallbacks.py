"""Deterministic keyless-path generators for every LLM route.

When no Anthropic API key is configured (the assessment's allowed 'mock' path), the
stage wiring calls these pure functions instead of the live model. They return the
exact same pydantic schemas as ``messages.parse`` would, so downstream persistence
and UI code never branch on keyed vs keyless. No LLM, no randomness, no I/O.
"""

from typing import Literal, NamedTuple

from app.llm.schemas import (
    AdjudicationSummaryLLM,
    ClaimantEmailLLM,
    ConsistencyCheck,
    ConsistencyWithHistory,
    DiagnosticReportLLM,
    RecommendationNoteLLM,
    RiskFactor,
    SupportingFinding,
)
from app.ml.base import ImagingAnalysis

_KNOWN_MODALITIES = ("xray", "ct", "mri")

# ---------------------------------------------------------------- stage 1c: diagnostic report


def fallback_diagnostic_report(
    analysis: ImagingAnalysis, *, declared_modality: str | None
) -> DiagnosticReportLLM:
    """Render the stage-1 classifier/forensics output as a draft diagnostic report.

    This path *is* the classifier, so ``modality_agrees_with_classifier`` is always
    True; no clinical findings are asserted and confidence is pinned to 0.0 so the
    imaging specialist owns the full read.
    """
    modality = analysis.modality if analysis.modality in _KNOWN_MODALITIES else "other"
    impression = (
        f"Automated keyless assessment: classifier identified modality "
        f"'{analysis.modality}' with confidence {analysis.modality_confidence:.2f}; "
        f"authenticity verdict: {analysis.authenticity_verdict}. No clinical findings "
        f"are asserted on this path; a licensed imaging specialist must perform the full read."
    )
    if declared_modality is not None and declared_modality != analysis.modality:
        impression += (
            f" Note: declared modality '{declared_modality}' differs from the classifier result."
        )
    return DiagnosticReportLLM(
        modality_assessment=modality,  # type: ignore[arg-type]
        modality_agrees_with_classifier=True,
        anatomical_region="not assessed (keyless path)",
        view=None,
        image_quality="degraded" if analysis.quality_flags else "adequate",
        quality_issues=list(analysis.quality_flags),
        findings=[],
        impression=impression,
        visual_inconsistencies=[s.finding for s in analysis.signals if s.score >= 0.5],
        confidence=0.0,
    )


# ---------------------------------------------------------------- stage 2: recommendation note

_DICOM_NEXT_STEP = (
    "request original DICOM from the imaging provider; re-acquisition if unavailable"
)


def fallback_recommendation(bundle: dict) -> RecommendationNoteLLM:
    """Rule-engine recommendation over the evidence bundle assembled by the stage wiring.

    Expected ``bundle`` keys (all assembled by the caller):

    - ``claim``: dict with ``claim_type``, ``procedure_code``, ``diagnosis_code``,
      ``amount_claimed``, ``incident_date``.
    - ``diagnostic_report``: dict with ``modality``, ``authenticity_verdict``,
      ``authenticity_risk``, ``requires_mandatory_review``, ``impression``;
      or ``None``/absent when stage 1 has not produced a report.
    - ``uploads``: list of dicts with ``filename``, ``kind``, ``text_extract_ok`` (bool).
    - ``modality_for_procedure``: ``str | None``, the modality the caller expects for
      the claimed procedure code (looked up by the caller, not here).

    Rules, in priority order:

    1. report present and (verdict != "authentic" or requires_mandatory_review)
       -> REQUIRES_FURTHER_TESTING.
    2. no diagnostic report -> INSUFFICIENT_EVIDENCE (gap: imaging analysis missing).
    3. expected modality set and != report modality -> INSUFFICIENT_EVIDENCE with an
       inconsistent ``imaging_matches_stated_procedure`` check.
    4. otherwise -> SUPPORTS_CLAIM.
    """
    claim: dict = bundle.get("claim") or {}
    report: dict | None = bundle.get("diagnostic_report")
    uploads: list[dict] = bundle.get("uploads") or []
    expected_modality: str | None = bundle.get("modality_for_procedure")

    verdict = report.get("authenticity_verdict") if report else None
    mandatory = bool(report.get("requires_mandatory_review")) if report else False
    report_modality = report.get("modality") if report else None
    procedure_code = claim.get("procedure_code")
    failed_uploads = [u["filename"] for u in uploads if not u.get("text_extract_ok", False)]

    recommendation: Literal[
        "SUPPORTS_CLAIM", "INSUFFICIENT_EVIDENCE", "REQUIRES_FURTHER_TESTING"
    ]
    identified_gaps: list[str] = []
    if report is not None and (verdict != "authentic" or mandatory):
        recommendation = "REQUIRES_FURTHER_TESTING"
        if verdict != "authentic":
            identified_gaps.append(
                f"imaging authenticity is not established (verdict '{verdict}')"
            )
        if mandatory:
            identified_gaps.append("imaging is flagged for mandatory human review")
        next_steps = [_DICOM_NEXT_STEP]
    elif report is None:
        recommendation = "INSUFFICIENT_EVIDENCE"
        identified_gaps.append("imaging analysis missing: no diagnostic report is available")
        next_steps = ["run imaging analysis on the submitted study before specialist review"]
    elif expected_modality is not None and expected_modality != report_modality:
        recommendation = "INSUFFICIENT_EVIDENCE"
        identified_gaps.append(
            f"submitted imaging modality '{report_modality}' does not match the modality "
            f"'{expected_modality}' expected for procedure {procedure_code}"
        )
        next_steps = [
            "confirm the procedure code with the provider or request imaging of the "
            "expected modality"
        ]
    else:
        recommendation = "SUPPORTS_CLAIM"
        next_steps = ["proceed to specialist review and sign-off"]
    for filename in failed_uploads:
        identified_gaps.append(f"text could not be extracted from upload:{filename}")

    checks = [
        _check_procedure(report, expected_modality, report_modality, procedure_code),
        _check_diagnosis(report),
        _check_documents(uploads, failed_uploads),
        _check_dates(claim),
        _check_authenticity(report, verdict, mandatory),
    ]

    findings = [
        SupportingFinding(
            source_document="claim_form",
            finding=(
                f"{claim.get('claim_type')} claim for procedure {procedure_code} "
                f"(diagnosis {claim.get('diagnosis_code')}), amount claimed "
                f"{claim.get('amount_claimed')}, incident date {claim.get('incident_date')}"
            ),
            relevance="establishes the claimed procedure and claim context",
        )
    ]
    if report is not None:
        findings.append(
            SupportingFinding(
                source_document="diagnostic_report",
                finding=(
                    f"{report_modality} study; authenticity verdict '{verdict}'; "
                    f"impression: {report.get('impression')}"
                ),
                relevance="primary imaging evidence for the claimed procedure",
            )
        )
    for upload in uploads:
        extracted = "text extracted" if upload.get("text_extract_ok") else "text extraction failed"
        findings.append(
            SupportingFinding(
                source_document=f"upload:{upload['filename']}",
                finding=f"{upload.get('kind')} document ({extracted})",
                relevance="supporting documentation submitted with the claim",
            )
        )

    summary = (
        f"Automated keyless rule-engine assessment of this {claim.get('claim_type')} claim "
        f"(procedure {procedure_code}): recommendation {recommendation}. "
        + (
            "No diagnostic report was available, so the imaging evidence could not be "
            "evaluated. "
            if report is None
            else (
                f"The diagnostic report records a {report_modality} study with authenticity "
                f"verdict '{verdict}'. "
            )
        )
        + (
            f"Identified gaps: {'; '.join(identified_gaps)}. "
            if identified_gaps
            else "No gaps were identified by the rule engine. "
        )
        + "This is an advisory rule-based result; the reviewing specialist remains "
        "responsible for the final recommendation."
    )

    return RecommendationNoteLLM(
        recommendation=recommendation,
        confidence=0.0,
        summary=summary,
        supporting_findings=findings,
        identified_gaps=identified_gaps,
        suggested_next_steps=next_steps,
        consistency_checks=checks,
    )


def _check_procedure(
    report: dict | None,
    expected_modality: str | None,
    report_modality: str | None,
    procedure_code: object,
) -> ConsistencyCheck:
    if report is None:
        result, detail = (
            "indeterminate",
            "no diagnostic report available to compare against the stated procedure",
        )
    elif expected_modality is None:
        result, detail = (
            "indeterminate",
            f"no expected modality on file for procedure {procedure_code}",
        )
    elif expected_modality == report_modality:
        result, detail = (
            "consistent",
            f"report modality '{report_modality}' matches the modality expected for "
            f"procedure {procedure_code}",
        )
    else:
        result, detail = (
            "inconsistent",
            f"report modality '{report_modality}' does not match expected modality "
            f"'{expected_modality}' for procedure {procedure_code}",
        )
    return ConsistencyCheck(check="imaging_matches_stated_procedure", result=result, detail=detail)


def _check_diagnosis(report: dict | None) -> ConsistencyCheck:
    detail = (
        "no diagnostic report available"
        if report is None
        else "rule engine cannot map diagnosis codes to imaging content; specialist "
        "judgement required"
    )
    return ConsistencyCheck(
        check="imaging_matches_diagnosis_code", result="indeterminate", detail=detail
    )


def _check_documents(uploads: list[dict], failed_uploads: list[str]) -> ConsistencyCheck:
    if failed_uploads:
        detail = f"text extraction failed for: {', '.join(failed_uploads)}"
    elif not uploads:
        detail = "no supporting uploads to cross-check"
    else:
        detail = "automated cross-document comparison is not available on the keyless path"
    return ConsistencyCheck(
        check="documents_internally_consistent", result="indeterminate", detail=detail
    )


def _check_dates(claim: dict) -> ConsistencyCheck:
    incident_date = claim.get("incident_date")
    detail = (
        f"incident date {incident_date} recorded; no comparison dates are available to "
        "the rule engine"
        if incident_date
        else "no dates are available to the rule engine"
    )
    return ConsistencyCheck(check="dates_plausible", result="indeterminate", detail=detail)


def _check_authenticity(
    report: dict | None, verdict: str | None, mandatory: bool
) -> ConsistencyCheck:
    if report is None:
        result, detail = "indeterminate", "no diagnostic report available"
    elif verdict == "authentic" and not mandatory:
        result, detail = (
            "consistent",
            f"authenticity verdict '{verdict}' (risk {report.get('authenticity_risk')})",
        )
    else:
        result, detail = (
            "inconsistent",
            f"authenticity verdict '{verdict}'"
            + ("; flagged for mandatory human review" if mandatory else ""),
        )
    return ConsistencyCheck(check="authenticity_concerns", result=result, detail=detail)


# ---------------------------------------------------------------- stage 3: adjudication summary

_SIMILAR_CASE_NOTE = "(automated) same modality and procedure family"


def fallback_adjudication(
    specialist_recommendation: str | None,
    history_stats: dict,
    similar_cases: list[dict],
    authenticity_verdict: str | None,
) -> AdjudicationSummaryLLM:
    """Rule-based adjudication summary from history stats and the specialist recommendation.

    ``history_stats`` keys: ``total``, ``approved``, ``rejected``, ``recent_12mo``,
    ``prior_rejections``. SUPPORTS_CLAIM leans approve; anything else (or no
    recommendation) yields no clear lean; a non-authentic imaging verdict always forces
    NO_CLEAR_LEAN plus a high-severity risk factor.
    """
    total = int(history_stats.get("total", 0))
    approved = int(history_stats.get("approved", 0))
    rejected = int(history_stats.get("rejected", 0))
    recent_12mo = int(history_stats.get("recent_12mo", 0))
    prior_rejections = int(history_stats.get("prior_rejections", 0))
    not_authentic = authenticity_verdict is not None and authenticity_verdict != "authentic"

    lean: Literal["LEAN_APPROVE", "LEAN_REJECT", "NO_CLEAR_LEAN"] = (
        "LEAN_APPROVE" if specialist_recommendation == "SUPPORTS_CLAIM" else "NO_CLEAR_LEAN"
    )
    if not_authentic:
        lean = "NO_CLEAR_LEAN"

    risk_factors: list[RiskFactor] = []
    if prior_rejections >= 2:
        risk_factors.append(
            RiskFactor(
                factor="history of rejected claims",
                severity="medium",
                source="claim_history",
            )
        )
    if recent_12mo >= 5:
        risk_factors.append(
            RiskFactor(
                factor="high recent claim frequency",
                severity="medium",
                source="claim_history",
            )
        )
    if not_authentic:
        risk_factors.append(
            RiskFactor(
                factor=f"imaging authenticity not established (verdict '{authenticity_verdict}')",
                severity="high",
                source="diagnostic_report",
            )
        )

    if total == 0:
        consistency = ConsistencyWithHistory(
            assessment="no_history",
            details="claimant has no prior claims on record",
        )
    elif prior_rejections >= 2:
        consistency = ConsistencyWithHistory(
            assessment="minor_discrepancies",
            details=(
                f"{prior_rejections} prior rejection(s) across {total} claim(s); "
                "pattern warrants agent attention"
            ),
        )
    else:
        consistency = ConsistencyWithHistory(
            assessment="consistent",
            details=f"claim pattern is in line with {total} prior claim(s) on record",
        )

    summary = (
        "Automated keyless adjudication summary. Specialist recommendation: "
        f"{specialist_recommendation or 'none recorded'}. Claim history: {total} total "
        f"claim(s) ({approved} approved, {rejected} rejected), {recent_12mo} in the last "
        f"12 months, {prior_rejections} prior rejection(s). {len(similar_cases)} similar "
        f"case(s) retrieved. Recommendation lean: {lean}. The final approve/reject "
        "decision rests solely with the insurance agent."
    )

    return AdjudicationSummaryLLM(
        summary=summary,
        risk_factors=risk_factors,
        consistency_with_history=consistency,
        similar_case_relevance_notes=[_SIMILAR_CASE_NOTE] * len(similar_cases),
        recommendation_lean=lean,
        confidence=0.0,
    )


# ---------------------------------------------------------------- claimant email


class _EmailTemplate(NamedTuple):
    subject: str
    greeting: str
    body: tuple[str, ...]
    closing: str


EMAIL_APPROVED_EN_FORMAL = _EmailTemplate(
    subject="Your claim {claim_ref} has been approved",
    greeting="Dear {first_name},",
    body=(
        "We are pleased to inform you that your {claim_type} claim ({claim_ref}) has "
        "been approved.",
        "Payment will be processed in accordance with the terms of your policy. No "
        "further action is required on your part.",
        "Should you have any questions, please contact our claims team and quote "
        "reference {claim_ref}.",
    ),
    closing="Sincerely,\nThe ClaimFlow Claims Team",
)

EMAIL_APPROVED_EN_PLAIN = _EmailTemplate(
    subject="Good news about your claim {claim_ref}",
    greeting="Hi {first_name},",
    body=(
        "Good news: your {claim_type} claim ({claim_ref}) has been approved.",
        "You do not need to do anything else. Payment will follow under your plan.",
        "Questions? Just reply to this email and mention {claim_ref}.",
    ),
    closing="Thanks,\nThe ClaimFlow Team",
)

EMAIL_REJECTED_EN_FORMAL = _EmailTemplate(
    subject="Decision on your claim {claim_ref}",
    greeting="Dear {first_name},",
    body=(
        "We have completed our review of your {claim_type} claim ({claim_ref}). After "
        "careful consideration, we are unable to approve it at this time.",
        "You may request an appeal within 30 days of this notice. Our team will review "
        "any additional documentation you choose to provide.",
        "Should you have any questions, please contact our claims team and quote "
        "reference {claim_ref}.",
    ),
    closing="Sincerely,\nThe ClaimFlow Claims Team",
)

EMAIL_REJECTED_EN_PLAIN = _EmailTemplate(
    subject="An update on your claim {claim_ref}",
    greeting="Hi {first_name},",
    body=(
        "We have finished reviewing your {claim_type} claim ({claim_ref}). We are not "
        "able to approve it right now.",
        "You can ask us to take another look. You have 30 days from today to start an "
        "appeal, and you can send any extra documents that may help.",
        "Questions? Just reply to this email and mention {claim_ref}.",
    ),
    closing="Thanks,\nThe ClaimFlow Team",
)

EMAIL_APPROVED_FR_FORMAL = _EmailTemplate(
    subject="Votre demande {claim_ref} a été approuvée",
    greeting="Bonjour {first_name},",
    body=(
        "Nous avons le plaisir de vous informer que votre demande de règlement "
        "({claim_type}, dossier {claim_ref}) a été approuvée.",
        "Le paiement sera traité conformément aux modalités de votre contrat. Aucune "
        "autre démarche n'est requise de votre part.",
        "Pour toute question, veuillez communiquer avec notre équipe des réclamations "
        "en mentionnant le numéro de dossier {claim_ref}.",
    ),
    closing="Veuillez agréer nos salutations distinguées,\nL'équipe des réclamations ClaimFlow",
)

EMAIL_APPROVED_FR_PLAIN = _EmailTemplate(
    subject="Bonne nouvelle pour votre demande {claim_ref}",
    greeting="Bonjour {first_name},",
    body=(
        "Bonne nouvelle : votre demande ({claim_type}, dossier {claim_ref}) a été "
        "approuvée.",
        "Vous n'avez rien d'autre à faire. Le paiement suivra selon votre contrat.",
        "Des questions? Répondez à ce courriel en indiquant le numéro {claim_ref}.",
    ),
    closing="Merci,\nL'équipe ClaimFlow",
)

EMAIL_REJECTED_FR_FORMAL = _EmailTemplate(
    subject="Décision concernant votre demande {claim_ref}",
    greeting="Bonjour {first_name},",
    body=(
        "Nous avons terminé l'examen de votre demande de règlement ({claim_type}, "
        "dossier {claim_ref}). Après étude, nous ne pouvons malheureusement pas "
        "l'approuver pour le moment.",
        "Vous disposez de 30 jours à compter du présent avis pour demander une révision "
        "de la décision. Notre équipe examinera tout document supplémentaire que vous "
        "voudrez bien nous transmettre.",
        "Pour toute question, veuillez communiquer avec notre équipe des réclamations "
        "en mentionnant le numéro de dossier {claim_ref}.",
    ),
    closing="Veuillez agréer nos salutations distinguées,\nL'équipe des réclamations ClaimFlow",
)

EMAIL_REJECTED_FR_PLAIN = _EmailTemplate(
    subject="Une mise à jour sur votre demande {claim_ref}",
    greeting="Bonjour {first_name},",
    body=(
        "Nous avons terminé l'étude de votre demande ({claim_type}, dossier "
        "{claim_ref}). Malheureusement, nous ne pouvons pas l'approuver pour le moment.",
        "Vous pouvez nous demander de revoir la décision. Vous avez 30 jours à partir "
        "d'aujourd'hui pour le faire, et vous pouvez nous envoyer tout document qui "
        "pourrait aider.",
        "Des questions? Répondez à ce courriel en indiquant le numéro {claim_ref}.",
    ),
    closing="Merci,\nL'équipe ClaimFlow",
)

_EMAIL_TEMPLATES: dict[tuple[str, str, str], _EmailTemplate] = {
    ("APPROVED", "en", "formal"): EMAIL_APPROVED_EN_FORMAL,
    ("APPROVED", "en", "plain_language"): EMAIL_APPROVED_EN_PLAIN,
    ("REJECTED", "en", "formal"): EMAIL_REJECTED_EN_FORMAL,
    ("REJECTED", "en", "plain_language"): EMAIL_REJECTED_EN_PLAIN,
    ("APPROVED", "fr", "formal"): EMAIL_APPROVED_FR_FORMAL,
    ("APPROVED", "fr", "plain_language"): EMAIL_APPROVED_FR_PLAIN,
    ("REJECTED", "fr", "formal"): EMAIL_REJECTED_FR_FORMAL,
    ("REJECTED", "fr", "plain_language"): EMAIL_REJECTED_FR_PLAIN,
}


def fallback_claimant_email(
    *,
    decision: Literal["APPROVED", "REJECTED"],
    first_name: str,
    language: Literal["en", "fr"],
    tone: Literal["formal", "plain_language"],
    claim_ref: str,
    claim_type: str,
) -> ClaimantEmailLLM:
    """Render one of the eight static claimant-email templates.

    Templates never mention scores, fraud, risk, or medical findings; rejections cite
    the 30-day appeal window without blame language.
    """
    template = _EMAIL_TEMPLATES[(decision, language, tone)]
    slots = {"first_name": first_name, "claim_ref": claim_ref, "claim_type": claim_type}
    return ClaimantEmailLLM(
        subject=template.subject.format(**slots),
        greeting=template.greeting.format(**slots),
        body_paragraphs=[paragraph.format(**slots) for paragraph in template.body],
        closing=template.closing.format(**slots),
    )
