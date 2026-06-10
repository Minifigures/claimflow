"""PII scrubbing for the adjudicated-cases collection.

Closed cases are stored as anonymized clinical summaries: no names, no member or
claim identifiers, no dates. The metadata allowlist plus the forbidden-key check
defend against future call sites accidentally leaking identifiers into Chroma.
"""

from __future__ import annotations

import hashlib

ALLOWED_METADATA = frozenset(
    {
        "case_ref",
        "procedure_code",
        "diagnosis_code",
        "modality",
        "claim_type",
        "recommendation",
        "decision",
    }
)

# Hard-forbidden identifier keys: passing any of these raises instead of dropping,
# so a leaking call site fails loudly in tests rather than silently storing PII.
_FORBIDDEN_KEYS = frozenset({"claimant_id", "member_id", "claim_id", "user_id", "patient_id"})
_FORBIDDEN_FRAGMENTS = ("name",)  # full_name, patient_name, first_name, ...


def build_case_summary(
    *,
    modality: str | None,
    claim_type: str,
    procedure_code: str,
    diagnosis_code: str,
    recommendation: str | None,
    key_findings: list[str],
    decision: str,
) -> str:
    """One compact, PII-free paragraph describing a closed case.

    Deliberately takes no names, identifiers, or dates, the signature is the
    anonymization boundary.
    """
    modality_phrase = f"{modality} imaging" if modality else "no imaging modality recorded"
    findings_phrase = "; ".join(key_findings) if key_findings else "none recorded"
    recommendation_phrase = recommendation if recommendation else "none recorded"
    return (
        f"Closed {claim_type} claim with {modality_phrase}, "
        f"procedure code {procedure_code}, diagnosis code {diagnosis_code}. "
        f"Specialist recommendation: {recommendation_phrase}. "
        f"Key findings: {findings_phrase}. "
        f"Final decision: {decision}."
    )


def make_case_ref(claim_ref: str) -> str:
    """Deterministic case reference, unlinkable without the original claim_ref."""
    digest = hashlib.sha256(claim_ref.encode("utf-8")).hexdigest()
    return "CASE-" + digest[:8].upper()


def anonymized_metadata(**kwargs: object) -> dict[str, object]:
    """Filter metadata down to the allowlist.

    Drops any key not in ALLOWED_METADATA (and None values, which Chroma rejects).
    Raises ValueError for identifier-like keys so leaks fail loudly.
    """
    for key in kwargs:
        lowered = key.lower()
        if lowered in _FORBIDDEN_KEYS or any(frag in lowered for frag in _FORBIDDEN_FRAGMENTS):
            raise ValueError(f"identifier-like metadata key not allowed: {key!r}")
    return {
        key: value for key, value in kwargs.items() if key in ALLOWED_METADATA and value is not None
    }
