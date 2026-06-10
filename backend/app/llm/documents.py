"""Document handling for the LLM stages.

PDF text extraction, untrusted-content wrapping (claimant uploads are data, never
instructions), and deterministic context-bundle assembly for stages 2 and 3 with hard
character caps and explicit truncation notes.
"""

import json
from pathlib import Path

from pypdf import PdfReader

PER_DOC_CHAR_CAP = 50_000
BUNDLE_CHAR_CAP = 120_000

# A scanned (image-only) PDF extracts to almost nothing; below this per-page average we
# report the file as an unextractable scan instead of feeding garbage to the model.
MIN_AVG_CHARS_PER_PAGE = 50

# The ONLY claim-form fields that may reach the model. Everything else on the claim
# (names, emails, member ids, addresses) is PII and stays out of every prompt.
CLAIM_FORM_ALLOWLIST: tuple[str, ...] = (
    "claim_type",
    "procedure_code",
    "diagnosis_code",
    "incident_date",
    "amount_claimed",
)

_CLOSE_TAG = "</untrusted_document>"
_CLOSE_TAG_ESCAPED = "</untrusted-document>"
_TRUNCATION_MARKER = "\n[truncated]"


def extract_pdf_text(path: Path) -> tuple[str, bool]:
    """Extract per-page text from a PDF.

    Returns ``(text, ok)``. ``ok`` is False for an "unextractable_scan": a PDF whose
    pages average fewer than MIN_AVG_CHARS_PER_PAGE extracted characters.
    """
    reader = PdfReader(str(path))
    pages = [page.extract_text() or "" for page in reader.pages]
    text = "\n".join(pages).strip()
    if not pages:
        return "", False
    avg = sum(len(p) for p in pages) / len(pages)
    return text, avg >= MIN_AVG_CHARS_PER_PAGE


def wrap_untrusted(name: str, text: str) -> str:
    """Wrap claimant-supplied text so it cannot break out of its untrusted envelope.

    Any literal closing tag inside the content is defanged (underscore -> hyphen) so the
    only real ``</untrusted_document>`` is the one this function appends; null bytes are
    stripped; the name is escaped so it cannot smuggle attributes or tags.
    """
    safe_name = (
        name.replace("\x00", "").replace('"', "'").replace("<", "(").replace(">", ")")
    )
    safe_text = text.replace("\x00", "").replace(_CLOSE_TAG, _CLOSE_TAG_ESCAPED)
    return f'<untrusted_document name="{safe_name}">\n{safe_text}\n{_CLOSE_TAG}'


def _truncate(text: str, cap: int) -> tuple[str, bool]:
    """Cut ``text`` to at most ``cap`` chars, ending with an explicit marker."""
    if len(text) <= cap:
        return text, False
    body = text.removesuffix(_TRUNCATION_MARKER)
    keep = max(cap - len(_TRUNCATION_MARKER), 0)
    return body[:keep] + _TRUNCATION_MARKER, True


def _json_section(tag: str, payload: dict) -> str:
    return f"<{tag}>\n{json.dumps(payload, indent=2, default=str, sort_keys=True)}\n</{tag}>"


def assemble_stage2_bundle(
    claim_fields: dict,
    diagnostic_report: dict,
    uploads: list[tuple[str, str]],
) -> tuple[str, list[str]]:
    """Build the stage-2 evidence bundle.

    Returns ``(bundle_text, truncation_notes)``. Claim-form fields are filtered through
    a strict allowlist (no PII reaches the model), the human-approved diagnostic report
    is embedded as JSON, and each upload is wrapped as untrusted content. Docs are capped
    at PER_DOC_CHAR_CAP, then trimmed oldest-first until the bundle fits BUNDLE_CHAR_CAP.
    """
    truncation_notes: list[str] = []

    allowed = {k: claim_fields[k] for k in CLAIM_FORM_ALLOWLIST if k in claim_fields}
    assert set(allowed) <= set(CLAIM_FORM_ALLOWLIST)  # belt-and-suspenders PII guard

    docs: list[tuple[str, str]] = []
    for doc_name, doc_text in uploads:
        capped, truncated = _truncate(doc_text, PER_DOC_CHAR_CAP)
        if truncated:
            truncation_notes.append(
                f"{doc_name}: truncated to {PER_DOC_CHAR_CAP} chars (per-document cap)"
            )
        docs.append((doc_name, capped))

    def render() -> str:
        wrapped = "\n\n".join(wrap_untrusted(n, t) for n, t in docs)
        return "\n\n".join(
            [
                _json_section("claim_form", allowed),
                _json_section("diagnostic_report", diagnostic_report),
                f"<uploaded_documents>\n{wrapped}\n</uploaded_documents>",
            ]
        )

    bundle = render()
    i = 0  # uploads arrive oldest-first; trim from the front
    while len(bundle) > BUNDLE_CHAR_CAP and i < len(docs):
        doc_name, doc_text = docs[i]
        excess = len(bundle) - BUNDLE_CHAR_CAP
        new_cap = max(len(doc_text) - excess, 0)
        new_text, _ = _truncate(doc_text, new_cap)
        if len(new_text) < len(doc_text):
            docs[i] = (doc_name, new_text)
            truncation_notes.append(
                f"{doc_name}: truncated further to fit bundle cap ({BUNDLE_CHAR_CAP} chars)"
            )
            bundle = render()
        if new_cap == 0 or len(new_text) >= len(doc_text):
            i += 1  # this doc cannot shrink further; move to the next oldest

    return bundle, truncation_notes


def _history_table(history_rows: list[dict]) -> str:
    if not history_rows:
        return "(no prior claims on record)"
    columns = ("date", "type", "procedure", "amount", "outcome")
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in history_rows:
        lines.append("| " + " | ".join(str(row.get(c, "")) for c in columns) + " |")
    return "\n".join(lines)


def _similar_cases_list(similar_cases: list[dict]) -> str:
    if not similar_cases:
        return "(no similar cases retrieved)"
    lines = []
    for idx, case in enumerate(similar_cases, start=1):
        lines.append(
            f"{idx}. case_ref: {case.get('case_ref', '')} | outcome: {case.get('outcome', '')}\n"
            f"   summary: {case.get('summary', '')}"
        )
    return "\n".join(lines)


def assemble_stage3_context(
    specialist_note: dict,
    diagnostic_report: dict,
    history_rows: list[dict],
    similar_cases: list[dict],
    claimant_docs: list[tuple[str, str]],
) -> str:
    """Build the stage-3 adjudication context: human-approved artifacts as JSON, the
    claimant history as a markdown table, retrieved precedents as a numbered list, and
    claimant documents wrapped as untrusted content."""
    wrapped_docs = (
        "\n\n".join(wrap_untrusted(n, t) for n, t in claimant_docs)
        if claimant_docs
        else "(no claimant documents)"
    )
    return "\n\n".join(
        [
            _json_section("specialist_note", specialist_note),
            _json_section("diagnostic_report", diagnostic_report),
            f"<claimant_history>\n{_history_table(history_rows)}\n</claimant_history>",
            f"<similar_cases>\n{_similar_cases_list(similar_cases)}\n</similar_cases>",
            f"<claimant_documents>\n{wrapped_docs}\n</claimant_documents>",
        ]
    )
