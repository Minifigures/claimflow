"""RAG layer tests: anonymization, indexing, retrieval, isolation, auditing.

First run downloads the all-MiniLM-L6-v2 model (~90MB) from Hugging Face; allow
roughly 60s for that. Subsequent runs hit the local cache.
"""

import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.claimguard import audit
from app.config import Settings
from app.models import AuditEvent
from app.rag.anonymizer import (
    ALLOWED_METADATA,
    anonymized_metadata,
    build_case_summary,
    make_case_ref,
)
from app.rag.indexer import index_case_document, index_closed_case
from app.rag.retriever import find_similar_cases, get_case_documents

# ---------------------------------------------------------------------------
# anonymizer (pure, no model needed)
# ---------------------------------------------------------------------------


def test_anonymized_metadata_drops_disallowed_keys() -> None:
    meta = anonymized_metadata(
        case_ref="CASE-AB12CD34",
        procedure_code="73721",
        diagnosis_code="S83.2",
        modality="mri",
        claim_type="imaging",
        recommendation="SUPPORTS_CLAIM",
        decision="approved",
        billed_amount=950.0,  # not in allowlist -> dropped
        notes="internal",  # not in allowlist -> dropped
    )
    assert set(meta) <= ALLOWED_METADATA
    assert "billed_amount" not in meta
    assert "notes" not in meta
    assert meta["case_ref"] == "CASE-AB12CD34"


def test_anonymized_metadata_drops_none_values() -> None:
    meta = anonymized_metadata(case_ref="CASE-00000000", modality=None, recommendation=None)
    assert meta == {"case_ref": "CASE-00000000"}


@pytest.mark.parametrize(
    "key",
    ["claimant_id", "member_id", "claim_id", "patient_name", "full_name", "name"],
)
def test_anonymized_metadata_raises_on_identifier_keys(key: str) -> None:
    with pytest.raises(ValueError):
        anonymized_metadata(**{key: "leak", "case_ref": "CASE-00000000"})


def test_make_case_ref_deterministic_and_unlinkable() -> None:
    ref = make_case_ref("CLM-2026-0042")
    assert ref == make_case_ref("CLM-2026-0042")
    assert ref.startswith("CASE-")
    assert len(ref) == len("CASE-") + 8
    assert "CLM" not in ref.removeprefix("CASE-") or ref != "CASE-CLM-2026"
    assert make_case_ref("CLM-2026-0043") != ref


def test_case_summary_contains_no_identifiers() -> None:
    summary = build_case_summary(
        modality="xray",
        claim_type="imaging",
        procedure_code="73560",
        diagnosis_code="S83.2",
        recommendation="SUPPORTS_CLAIM",
        key_findings=["joint effusion", "no fracture"],
        decision="approved",
    )
    # The signature is the anonymization boundary: no member/claim ids, names,
    # or dates can appear because they are never accepted as inputs.
    for identifier in ("MBR-1001", "CLM-2026-0042", "Casey Claimant", "2026-06-10"):
        assert identifier not in summary
    assert "73560" in summary
    assert "S83.2" in summary
    assert "joint effusion" in summary
    assert "\n" not in summary  # one compact paragraph


# ---------------------------------------------------------------------------
# adjudicated cases: index + similarity search
# ---------------------------------------------------------------------------

KNEE_CASES = [
    dict(
        claim_ref="CLM-K1",
        modality="xray",
        claim_type="imaging",
        procedure_code="73560",
        diagnosis_code="S83.2",
        recommendation="SUPPORTS_CLAIM",
        key_findings=["knee joint effusion", "meniscal tear suspected"],
        decision="approved",
    ),
    dict(
        claim_ref="CLM-K2",
        modality="xray",
        claim_type="imaging",
        procedure_code="73562",
        diagnosis_code="M17.11",
        recommendation="SUPPORTS_CLAIM",
        key_findings=["knee osteoarthritis", "joint space narrowing"],
        decision="approved",
    ),
    dict(
        claim_ref="CLM-K3",
        modality="xray",
        claim_type="imaging",
        procedure_code="73560",
        diagnosis_code="S82.1",
        recommendation="INSUFFICIENT_EVIDENCE",
        key_findings=["knee pain reported", "radiograph inconclusive"],
        decision="rejected",
    ),
]

BRAIN_CASES = [
    dict(
        claim_ref="CLM-B1",
        modality="mri",
        claim_type="imaging",
        procedure_code="70551",
        diagnosis_code="G43.9",
        recommendation="SUPPORTS_CLAIM",
        key_findings=["brain MRI shows white matter lesions", "migraine workup"],
        decision="approved",
    ),
    dict(
        claim_ref="CLM-B2",
        modality="mri",
        claim_type="imaging",
        procedure_code="70553",
        diagnosis_code="C71.9",
        recommendation="REQUIRES_FURTHER_TESTING",
        key_findings=["brain mass on MRI", "contrast study recommended"],
        decision="rejected",
    ),
    dict(
        claim_ref="CLM-B3",
        modality="mri",
        claim_type="imaging",
        procedure_code="70551",
        diagnosis_code="S06.0",
        recommendation="SUPPORTS_CLAIM",
        key_findings=["brain MRI after concussion", "no intracranial bleed"],
        decision="approved",
    ),
]

KNEE_QUERY = "knee xray claim with joint effusion and suspected meniscal tear"


def _seed_cases(settings: Settings) -> tuple[set[str], set[str]]:
    knee_refs = {index_closed_case(settings, **case) for case in KNEE_CASES}
    brain_refs = {index_closed_case(settings, **case) for case in BRAIN_CASES}
    return knee_refs, brain_refs


def test_index_and_find_similar_cases_roundtrip(settings: Settings, session: Session) -> None:
    knee_refs, brain_refs = _seed_cases(settings)
    assert len(knee_refs) == 3 and len(brain_refs) == 3

    results = find_similar_cases(settings, session, query=KNEE_QUERY, modality=None, top_k=3)
    session.commit()
    assert results, "expected at least one knee precedent"
    assert {r["case_ref"] for r in results} <= knee_refs | brain_refs
    # Top hit must come from the knee cluster.
    assert results[0]["case_ref"] in knee_refs
    for row in results:
        assert set(row) == {"case_ref", "similarity", "outcome", "summary"}
        assert 0.0 < row["similarity"] <= 1.0
        assert row["outcome"] in {"approved", "rejected"}
    # Results are ordered best-first.
    sims = [r["similarity"] for r in results]
    assert sims == sorted(sims, reverse=True)


def test_identical_text_cosine_similarity_near_one(settings: Settings, session: Session) -> None:
    _seed_cases(settings)
    exact_summary = build_case_summary(
        modality=KNEE_CASES[0]["modality"],
        claim_type=KNEE_CASES[0]["claim_type"],
        procedure_code=KNEE_CASES[0]["procedure_code"],
        diagnosis_code=KNEE_CASES[0]["diagnosis_code"],
        recommendation=KNEE_CASES[0]["recommendation"],
        key_findings=KNEE_CASES[0]["key_findings"],
        decision=KNEE_CASES[0]["decision"],
    )
    results = find_similar_cases(settings, session, query=exact_summary, modality=None, top_k=1)
    session.commit()
    assert results[0]["case_ref"] == make_case_ref("CLM-K1")
    assert results[0]["similarity"] > 0.99


def test_floor_filters_out_nonsense_query(settings: Settings, session: Session) -> None:
    _seed_cases(settings)
    results = find_similar_cases(
        settings,
        session,
        query="zzyzx flurble quux interplanetary banjo recipe",
        modality=None,
        top_k=5,
        floor=0.35,
    )
    session.commit()
    assert results == []  # empty list is a valid result: no precedent shown


def test_modality_prefilter_excludes_other_modality(settings: Settings, session: Session) -> None:
    knee_refs, brain_refs = _seed_cases(settings)
    results = find_similar_cases(
        settings, session, query=KNEE_QUERY, modality="mri", top_k=6, floor=0.0
    )
    session.commit()
    returned = {r["case_ref"] for r in results}
    assert returned <= brain_refs
    assert returned.isdisjoint(knee_refs)


def test_exclude_case_ref_honored(settings: Settings, session: Session) -> None:
    knee_refs, _ = _seed_cases(settings)
    excluded = make_case_ref("CLM-K1")
    assert excluded in knee_refs
    results = find_similar_cases(
        settings,
        session,
        query=KNEE_QUERY,
        modality=None,
        top_k=6,
        floor=0.0,
        exclude_case_ref=excluded,
    )
    session.commit()
    assert excluded not in {r["case_ref"] for r in results}
    assert results  # other cases still returned


# ---------------------------------------------------------------------------
# case documents: chunking + per-claimant isolation
# ---------------------------------------------------------------------------


def test_chunking_2500_chars_yields_3_chunks(settings: Settings) -> None:
    text = "knee imaging report. " * 120  # 2520 chars
    count = index_case_document(
        settings,
        claimant_id=1,
        claim_id=10,
        doc_type="medical_record",
        filename="report.pdf",
        text=text[:2500],
    )
    assert count == 3


def test_per_claimant_isolation(settings: Settings, session: Session) -> None:
    """The critical test: claimant 1 must NEVER see claimant 2's documents."""
    index_case_document(
        settings,
        claimant_id=1,
        claim_id=10,
        doc_type="medical_record",
        filename="claimant1-knee.pdf",
        text="Radiology report: knee xray demonstrates joint effusion and meniscal tear.",
    )
    index_case_document(
        settings,
        claimant_id=2,
        claim_id=20,
        doc_type="medical_record",
        filename="claimant2-knee.pdf",
        text="Radiology report: knee xray demonstrates joint effusion and meniscal tear.",
    )
    results = get_case_documents(
        settings, session, claimant_id=1, query="knee xray joint effusion", top_k=10
    )
    session.commit()
    assert results, "claimant 1 should retrieve their own documents"
    for row in results:
        assert row["claim_id"] == 10
        assert row["filename"] == "claimant1-knee.pdf"
        assert row["doc_type"] == "medical_record"
        assert 0.0 < row["similarity"] <= 1.0
    # And the inverse: claimant 3 (no documents) gets nothing.
    assert get_case_documents(settings, session, claimant_id=3, query="knee xray", top_k=10) == []
    session.commit()


# ---------------------------------------------------------------------------
# audit trail
# ---------------------------------------------------------------------------


def test_retrievals_are_audited_and_chain_valid(settings: Settings, session: Session) -> None:
    _seed_cases(settings)
    index_case_document(
        settings,
        claimant_id=1,
        claim_id=10,
        doc_type="medical_record",
        filename="report.pdf",
        text="knee xray with joint effusion",
    )
    get_case_documents(settings, session, claimant_id=1, query=KNEE_QUERY, top_k=4)
    find_similar_cases(settings, session, query=KNEE_QUERY, modality="xray", top_k=3)
    session.commit()

    rows = session.scalars(select(AuditEvent).where(AuditEvent.event_type == "rag_retrieval")).all()
    assert len(rows) == 2
    valid, checked = audit.verify_chain(session)
    assert valid and checked >= 2

    payloads = [json.loads(row.payload_json) for row in rows]
    collections = {p["collection"] for p in payloads}
    assert collections == {"case_documents", "adjudicated_cases"}
    for row, payload in zip(rows, payloads, strict=True):
        # Query text must never be persisted, only its hash.
        assert KNEE_QUERY not in row.payload_json
        assert "query" not in payload
        assert len(payload["query_sha256"]) == 64
        assert payload["top_k"] in {3, 4}
        assert isinstance(payload["returned"], int)
