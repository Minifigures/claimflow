"""Tests for the real (trained-CNN + forensics) stage-1 backend.

Random-initialized EfficientNet-B0 weights stand in for the trained ones: the
contract under test is loading, calibration, transform parity, fusion math, and
the inference-runner integration — not model accuracy. Forensic heuristics are
deterministic, so they are asserted directly on crafted images.
"""

import asyncio
import json
from pathlib import Path

import numpy as np
import pytest
import timm
import torch
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.ml.base import get_analyzer
from app.ml.imaging.real import RealAnalyzer
from app.ml.imaging.stub import StubAnalyzer
from app.models import ArtifactStatus, AuditEvent, Claim, ClaimState, DiagnosticReport, User
from app.services.inference_runner import run_stage1
from tests.test_inference_runner import make_claim_chain

_SPECS = (("modality", ["ct", "mri", "xray"]), ("authenticity", ["fake", "real"]))


@pytest.fixture(scope="module")
def weights_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("real_weights")
    torch.manual_seed(0)
    for name, classes in _SPECS:
        model = timm.create_model("efficientnet_b0", pretrained=False, num_classes=len(classes))
        torch.save(model.state_dict(), out / f"{name}_efficientnet_b0.pt")
        (out / f"{name}_config.json").write_text(
            json.dumps(
                {
                    "arch": "efficientnet_b0",
                    "classes": classes,
                    "input_size": 224,
                    "normalization": {
                        "mean": [0.485, 0.456, 0.406],
                        "std": [0.229, 0.224, 0.225],
                    },
                    "temperature": 1.5,
                }
            )
        )
    return out


@pytest.fixture()
def settings(tmp_path: Path, weights_dir: Path) -> Settings:
    """Shadows the conftest settings so app/session/users fixtures get the real backend."""
    return Settings(
        database_url=f"sqlite:///{tmp_path}/test.sqlite",
        upload_dir=tmp_path / "uploads",
        jwt_secret="test-secret-0123456789abcdef-0123456789",
        cookie_secure=False,
        email_provider="console",
        model_backend="real",
        weights_dir=weights_dir,
        anthropic_api_key="",
        chroma_dir=tmp_path / "chroma",
    )


def _gradient_image(size: int = 320, noise_seed: int = 3) -> np.ndarray:
    """Smooth diagonal gradient + mild noise — a plausible 'clean' radiograph stand-in."""
    rng = np.random.default_rng(noise_seed)
    y, x = np.mgrid[0:size, 0:size].astype(np.float32)
    base = (x + y) / (2 * size) * 200.0 + 20.0
    noisy = base + rng.normal(0.0, 2.0, base.shape)
    return np.clip(noisy, 0, 255).astype(np.uint8)


def write_jpeg(tmp_path: Path, arr: np.ndarray, name: str, quality: int = 85) -> Path:
    path = tmp_path / name
    Image.fromarray(arr).save(path, format="JPEG", quality=quality)
    return path


def write_png(tmp_path: Path, arr: np.ndarray, name: str) -> Path:
    path = tmp_path / name
    Image.fromarray(arr).save(path, format="PNG")
    return path


def _signal(analysis, name: str):
    return next(s for s in analysis.signals if s.name == name)


def test_analyze_satisfies_contract(weights_dir: Path, settings: Settings, tmp_path: Path):
    analyzer = get_analyzer(settings)
    assert isinstance(analyzer, RealAnalyzer)

    image = write_png(tmp_path, _gradient_image(), "knee.png")
    analysis = analyzer.analyze(image, declared_modality="xray", dicom_meta=None)

    assert analysis.backend == "real"
    assert set(analysis.modality_probs) == {"ct", "mri", "xray"}
    assert analysis.modality in analysis.modality_probs
    assert analysis.modality_confidence == max(analysis.modality_probs.values())
    assert abs(sum(analysis.modality_probs.values()) - 1.0) < 0.01
    assert 0.0 <= analysis.authenticity_risk <= 1.0
    bands = {"authentic", "suspicious", "likely_fraudulent"}
    assert analysis.authenticity_verdict in bands
    assert {s.name for s in analysis.signals} == {"cnn_authenticity", "ela", "fft", "metadata"}
    assert all(0.0 <= s.score <= 1.0 for s in analysis.signals)
    assert "non_dicom_upload" in analysis.quality_flags


def test_verdict_bands_match_risk(weights_dir: Path, settings: Settings, tmp_path: Path):
    analyzer = get_analyzer(settings)
    image = write_png(tmp_path, _gradient_image(), "scan.png")
    analysis = analyzer.analyze(image, declared_modality=None, dicom_meta=None)
    risk = analysis.authenticity_risk
    expected = (
        "authentic" if risk < 0.33 else "suspicious" if risk <= 0.66 else "likely_fraudulent"
    )
    assert analysis.authenticity_verdict == expected


def test_metadata_hard_override_forces_at_least_suspicious(
    weights_dir: Path, settings: Settings, tmp_path: Path
):
    analyzer = get_analyzer(settings)
    image = write_png(tmp_path, _gradient_image(), "study.png")

    baseline = analyzer.analyze(image, declared_modality=None, dicom_meta=None)
    # Pick a DICOM tag that maps to a modality the CNN did NOT predict.
    tag_by_modality = {"xray": "CR", "ct": "CT", "mri": "MR"}
    mismatched = next(m for m in tag_by_modality if m != baseline.modality)

    analysis = analyzer.analyze(
        image, declared_modality=None, dicom_meta={"Modality": tag_by_modality[mismatched]}
    )
    assert analysis.authenticity_verdict in {"suspicious", "likely_fraudulent"}
    assert analysis.authenticity_risk >= 0.50
    meta = _signal(analysis, "metadata")
    assert meta.score >= 0.9
    assert "hard-override" in meta.finding


def test_consistent_dicom_metadata_does_not_override(
    weights_dir: Path, settings: Settings, tmp_path: Path
):
    analyzer = get_analyzer(settings)
    image = write_png(tmp_path, _gradient_image(), "study2.png")
    baseline = analyzer.analyze(image, declared_modality=None, dicom_meta=None)
    tag_by_modality = {"xray": "CR", "ct": "CT", "mri": "MR"}

    analysis = analyzer.analyze(
        image,
        declared_modality=None,
        dicom_meta={"Modality": tag_by_modality[baseline.modality], "Manufacturer": "TestScan"},
    )
    meta = _signal(analysis, "metadata")
    assert meta.score <= 0.1
    assert "hard-override" not in meta.finding


def test_ela_flags_spliced_region(weights_dir: Path, settings: Settings, tmp_path: Path):
    analyzer = get_analyzer(settings)
    clean_arr = _gradient_image()
    spliced_arr = clean_arr.copy()
    rng = np.random.default_rng(9)
    # A pasted high-frequency patch recompresses very differently from the gradient.
    spliced_arr[40:120, 60:140] = rng.integers(0, 256, (80, 80), dtype=np.uint8)

    clean = analyzer.analyze(
        write_jpeg(tmp_path, clean_arr, "clean.jpg"), declared_modality=None, dicom_meta=None
    )
    spliced = analyzer.analyze(
        write_jpeg(tmp_path, spliced_arr, "spliced.jpg"), declared_modality=None, dicom_meta=None
    )
    assert _signal(spliced, "ela").score > _signal(clean, "ela").score


def test_fft_flags_periodic_pattern(weights_dir: Path, settings: Settings, tmp_path: Path):
    analyzer = get_analyzer(settings)
    clean_arr = _gradient_image()
    y, x = np.mgrid[0 : clean_arr.shape[0], 0 : clean_arr.shape[1]].astype(np.float32)
    sine = 40.0 * np.sin(2 * np.pi * x / 8.0)  # strong 8px-period resampling-style grid
    periodic_arr = np.clip(clean_arr.astype(np.float32) + sine, 0, 255).astype(np.uint8)

    clean = analyzer.analyze(
        write_png(tmp_path, clean_arr, "clean.png"), declared_modality=None, dicom_meta=None
    )
    periodic = analyzer.analyze(
        write_png(tmp_path, periodic_arr, "periodic.png"), declared_modality=None, dicom_meta=None
    )
    assert _signal(periodic, "fft").score > _signal(clean, "fft").score


def test_low_resolution_flagged(weights_dir: Path, settings: Settings, tmp_path: Path):
    analyzer = get_analyzer(settings)
    image = write_png(tmp_path, _gradient_image(size=128), "tiny.png")
    analysis = analyzer.analyze(image, declared_modality=None, dicom_meta=None)
    assert "low_resolution" in analysis.quality_flags


def test_dicom_pixel_path(weights_dir: Path, settings: Settings, tmp_path: Path):
    import pydicom
    from pydicom.dataset import Dataset, FileMetaDataset
    from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, generate_uid

    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = CTImageStorage
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian

    ds = Dataset()
    ds.file_meta = file_meta
    ds.SOPClassUID = CTImageStorage
    ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
    ds.Modality = "CT"
    ds.Rows = 320
    ds.Columns = 320
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 0
    ds.PixelData = (_gradient_image().astype(np.uint16) * 16).tobytes()
    path = tmp_path / "study.dcm"
    pydicom.dcmwrite(path, ds, enforce_file_format=True)

    analyzer = get_analyzer(settings)
    analysis = analyzer.analyze(path, declared_modality="ct", dicom_meta={"Modality": "CT"})
    assert analysis.backend == "real"
    assert "non_dicom_upload" not in analysis.quality_flags


def test_get_analyzer_degrades_to_stub_when_weights_missing(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path}/test.sqlite",
        upload_dir=tmp_path / "uploads",
        model_backend="real",
        weights_dir=tmp_path / "empty",
        anthropic_api_key="",
        chroma_dir=tmp_path / "chroma",
    )
    assert isinstance(get_analyzer(settings), StubAnalyzer)


def test_run_stage1_with_real_backend(
    weights_dir: Path,
    settings: Settings,
    session: Session,
    users: dict[str, User],
    tmp_path: Path,
):
    """Mirror of the stub happy path with MODEL_BACKEND=real: the trained pair drives
    stage 1 and the audit trail records backend=real."""
    image = write_png(tmp_path, _gradient_image(), "knee_xray.png")
    claim, document, report = make_claim_chain(session, users, image)

    asyncio.run(run_stage1(settings, report.id))
    session.expire_all()

    report = session.get(DiagnosticReport, report.id)
    claim = session.get(Claim, claim.id)
    assert report is not None and claim is not None
    assert report.status == ArtifactStatus.COMPLETE
    assert report.error is None
    assert report.modality in {"ct", "mri", "xray"}
    assert report.modality_confidence is not None and 0.0 <= report.modality_confidence <= 1.0
    assert report.generated_by == "fallback_template"  # keyless stage-1c path
    assert report.requires_mandatory_review is True

    assert report.payload_json is not None
    payload = json.loads(report.payload_json)
    assert payload["classifier"]["modality"] == report.modality
    assert claim.state == ClaimState.IMAGING_REVIEW

    llm_events = session.scalars(
        select(AuditEvent).where(
            AuditEvent.event_type == "llm_call", AuditEvent.claim_id == claim.id
        )
    ).all()
    assert len(llm_events) == 1
    assert json.loads(llm_events[0].payload_json)["backend"] == "real"
