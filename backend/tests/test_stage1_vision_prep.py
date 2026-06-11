"""Vision-prep regression tests: DICOM uploads must encode, and prep failures
must degrade to the deterministic fallback rather than failing stage 1.

The original bug only manifested in keyed environments (the keyless path skips
image prep entirely), which is exactly how it escaped every local run.
"""

import base64
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.config import Settings
from app.llm.stages.stage1_diagnostic import _encode_image_b64, generate_diagnostic_report
from app.ml.imaging.stub import StubAnalyzer

SEED_DICOM = Path(__file__).resolve().parents[1] / "seed-assets" / "tampered_xray.dcm"


def test_encode_image_b64_handles_dicom() -> None:
    encoded = _encode_image_b64(SEED_DICOM)
    assert len(encoded) > 1000
    assert base64.standard_b64decode(encoded)[:2] == b"\xff\xd8"  # JPEG magic


def test_keyed_stage1_with_unreadable_image_falls_back(
    settings: Settings, session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keyed path + an image PIL cannot read -> deterministic fallback, stage completes."""
    keyed = settings.model_copy(update={"gemini_api_key": "test-key"})
    broken = tmp_path / "broken.dcm"
    broken.write_bytes(b"version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 1\n")

    analysis = StubAnalyzer().analyze(broken, declared_modality="xray", dicom_meta=None)
    stage = generate_diagnostic_report(
        keyed,
        session,
        claim_id=1,
        image_path=broken,
        image_media_type="application/dicom",
        analysis=analysis,
        declared_modality="xray",
    )
    assert stage.generated_by == "fallback_template"
    # LLMUnavailableError maps to the keyless fallback reason label.
    assert stage.fallback_reason == "no_api_key"
    assert stage.payload["classifier"]["modality"] == analysis.modality
