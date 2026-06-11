"""Real stage-1 backend — the trained EfficientNet-B0 pair fused with classical forensics.

Loads ``<weights_dir>/{modality,authenticity}_efficientnet_b0.pt`` plus the
``*_config.json`` written by training (classes, input size, normalization,
calibration temperature). The eval transform mirrors training exactly:
shorter-side resize to ``size*256/224``, center-crop, grayscale replicated to
3 channels, ImageNet normalization.

Authenticity is a fusion, not a single model call: the CNN's calibrated
fake-probability contributes at most ``CNN_FUSION_WEIGHT`` (0.40) of the final
risk; deterministic forensic heuristics (ELA residual localization, FFT
periodicity, metadata consistency) carry the rest, so one overconfident model
can never push a clean image into the fraud band on its own. Verdict bands:
risk < 0.33 authentic, <= 0.66 suspicious, > 0.66 likely_fraudulent. A DICOM
Modality tag that contradicts the CNN's predicted modality is a hard override
to at least suspicious regardless of the fused score.

Honesty caveats (mirrored in the model card): the detector only knows the
tampering families it was trained on, the heuristics are screening signals and
not clinical evidence, and every non-authentic verdict routes to a human.
"""

from __future__ import annotations

import io
import json
import threading
from pathlib import Path

import numpy as np
import timm
import torch
from PIL import Image
from torchvision import transforms

from app.config import Settings
from app.ml.base import ForensicSignal, ImagingAnalysis

ARCH = "efficientnet_b0"
CNN_FUSION_WEIGHT = 0.40  # cap on the authenticity CNN's share of the fused risk
AUTHENTIC_BAND = 0.33
FRAUD_BAND = 0.66

_DICOM_MODALITY_MAP = {"CR": "xray", "DX": "xray", "CT": "ct", "MR": "mri"}
_MAGIC_DICM = b"DICM"

# Heuristic blend within the non-CNN share of the risk.
_FORENSIC_WEIGHTS = {"ela": 0.5, "fft": 0.3, "metadata": 0.2}


class _Net:
    """One loaded classifier: model + transform + calibration, ready for inference."""

    def __init__(self, weights_dir: Path, name: str) -> None:
        config_path = weights_dir / f"{name}_config.json"
        weights_path = weights_dir / f"{name}_{ARCH}.pt"
        config = json.loads(config_path.read_text())
        self.classes: list[str] = list(config["classes"])
        self.temperature = float(config.get("temperature", 1.0))
        size = int(config["input_size"])
        norm = config.get("normalization", {})
        mean = list(norm.get("mean", (0.485, 0.456, 0.406)))
        std = list(norm.get("std", (0.229, 0.224, 0.225)))
        self.transform = transforms.Compose(
            [
                transforms.Resize(int(size * 256 / 224)),
                transforms.CenterCrop(size),
                transforms.Grayscale(num_output_channels=3),
                transforms.ToTensor(),
                transforms.Normalize(mean, std),
            ]
        )
        self.model = timm.create_model(ARCH, pretrained=False, num_classes=len(self.classes))
        state = torch.load(weights_path, map_location="cpu", weights_only=True)
        self.model.load_state_dict(state)
        self.model.eval()

    def probs(self, img: Image.Image) -> dict[str, float]:
        """Temperature-calibrated class probabilities for one grayscale PIL image."""
        x = self.transform(img).unsqueeze(0)
        with torch.no_grad():
            logits = self.model(x)[0] / self.temperature
            p = torch.softmax(logits, dim=0).numpy()
        return {cls: float(p[i]) for i, cls in enumerate(self.classes)}


_nets_lock = threading.Lock()
_nets_cache: dict[tuple[str, str], _Net] = {}


def _load_net(weights_dir: Path, name: str) -> _Net:
    key = (str(weights_dir.resolve()), name)
    with _nets_lock:
        if key not in _nets_cache:
            _nets_cache[key] = _Net(weights_dir, name)
        return _nets_cache[key]


# ------------------------------------------------------------------ image loading


def _is_dicom(path: Path) -> bool:
    with path.open("rb") as fh:
        head = fh.read(132)
    return len(head) >= 132 and head[128:132] == _MAGIC_DICM


def _dicom_to_pil(path: Path) -> Image.Image:
    """Window the DICOM pixel array to uint8 exactly like the intake preview does."""
    import pydicom

    ds = pydicom.dcmread(path, force=True)
    arr = ds.pixel_array.astype("float32")
    lo, hi = float(arr.min()), float(arr.max())
    arr = (arr - lo) / (hi - lo) * 255.0 if hi > lo else arr * 0.0
    arr8 = arr.astype("uint8")
    if arr8.ndim == 3:  # color or multi-frame: analyze the first plane
        arr8 = arr8[..., 0] if arr8.shape[-1] in (3, 4) else arr8[0]
    return Image.fromarray(arr8)


def _load_grayscale(path: Path) -> tuple[Image.Image, bool]:
    if _is_dicom(path):
        return _dicom_to_pil(path).convert("L"), True
    return Image.open(path).convert("L"), False


# ------------------------------------------------------------------ forensic heuristics


def _analysis_array(img: Image.Image, max_side: int = 512) -> np.ndarray:
    """Grayscale uint8 array downscaled for the heuristics (keeps them O(1))."""
    w, h = img.size
    scale = max_side / max(w, h)
    if scale < 1.0:
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.BILINEAR)
    return np.asarray(img, dtype=np.uint8)


def _ela_localization(gray: np.ndarray, grid: int = 16, quality: int = 90) -> tuple[float, str]:
    """Error-level analysis: a spliced/cloned region recompresses differently.

    JPEG-roundtrip the image and compare per-tile mean residuals; a uniform image
    yields uniform residuals (low score), a localized hot region yields a high
    p99-vs-median spread (high score).
    """
    buf = io.BytesIO()
    Image.fromarray(gray).save(buf, format="JPEG", quality=quality)
    buf.seek(0)
    resaved = np.asarray(Image.open(buf).convert("L"), dtype=np.float32)
    residual = np.abs(gray.astype(np.float32) - resaved)

    h, w = residual.shape
    th, tw = max(1, h // grid), max(1, w // grid)
    tiles = [
        float(residual[r : r + th, c : c + tw].mean())
        for r in range(0, h - th + 1, th)
        for c in range(0, w - tw + 1, tw)
    ]
    tiles_arr = np.asarray(tiles)
    p99 = float(np.percentile(tiles_arr, 99))
    med = float(np.median(tiles_arr))
    score = float(np.clip((p99 - med) / (p99 + med + 1e-6), 0.0, 1.0))
    finding = (
        "Error-level residuals localized — a region recompresses unlike its surroundings."
        if score >= 0.5
        else "Error-level analysis residuals uniform across tiles."
    )
    return score, finding


def _fft_periodicity(gray: np.ndarray) -> tuple[float, str]:
    """Resampling/synthesis leaves periodic spectral peaks a natural image lacks.

    Z-score of the strongest off-center peak in the log-magnitude spectrum,
    mapped through a soft threshold so smooth natural spectra stay near zero.
    """
    arr = gray.astype(np.float32)
    arr = arr - float(arr.mean())
    spectrum = np.fft.fftshift(np.abs(np.fft.fft2(arr)))
    log_mag = np.log1p(spectrum)

    h, w = log_mag.shape
    cy, cx = h // 2, w // 2
    yy, xx = np.ogrid[:h, :w]
    # Ignore the DC neighbourhood and the axis cross (dominated by image borders).
    mask = ((yy - cy) ** 2 + (xx - cx) ** 2 > (min(h, w) // 8) ** 2)
    mask &= (np.abs(yy - cy) > 2) | (np.abs(xx - cx) > 2)
    high = log_mag[mask]
    if high.size == 0:
        return 0.0, "Spectrum too small to analyze."
    z = float((high.max() - high.mean()) / (high.std() + 1e-6))
    score = float(np.clip((z - 6.0) / 18.0, 0.0, 1.0))
    finding = (
        "Periodic spectral peaks consistent with resampling or synthetic texture."
        if score >= 0.5
        else "No periodic spectral peaks indicative of resampling."
    )
    return score, finding


def _metadata_consistency(
    dicom_meta: dict | None,
    predicted_modality: str,
    declared_modality: str | None,
    is_dicom: bool,
) -> tuple[float, str, bool]:
    """Score metadata coherence; returns (score, finding, hard_override).

    hard_override fires when the DICOM Modality tag maps to a known modality that
    contradicts either the CNN prediction or the claimant's declared modality —
    the strongest single fraud tells we have, and neither depends on model luck.
    """
    if not dicom_meta:
        if is_dicom:
            return 0.30, "DICOM study carries no acquisition metadata.", False
        return 0.20, "No acquisition metadata available (non-DICOM upload).", False

    tag_modality = _DICOM_MODALITY_MAP.get(str(dicom_meta.get("Modality", "")).upper())
    if tag_modality is not None and declared_modality and tag_modality != declared_modality:
        return (
            0.90,
            f"DICOM Modality tag ({tag_modality}) contradicts the declared "
            f"modality ({declared_modality}) — metadata hard-override.",
            True,
        )
    if tag_modality is not None and tag_modality != predicted_modality:
        return (
            0.90,
            f"DICOM Modality tag ({tag_modality}) disagrees with the model's "
            f"prediction ({predicted_modality}) — metadata hard-override.",
            True,
        )
    if dicom_meta.get("SoftwareVersions") and not dicom_meta.get("Manufacturer"):
        return 0.55, "Software tag present without scanner manufacturer tags.", False
    return 0.05, "DICOM metadata consistent with the predicted acquisition.", False


def _verdict(risk: float) -> str:
    if risk < AUTHENTIC_BAND:
        return "authentic"
    if risk <= FRAUD_BAND:
        return "suspicious"
    return "likely_fraudulent"


# ------------------------------------------------------------------ analyzer


class RealAnalyzer:
    """Trained-CNN stage-1 backend; constructed only when both weight pairs exist."""

    def __init__(self, settings: Settings) -> None:
        self._modality = _load_net(settings.weights_dir, "modality")
        self._authenticity = _load_net(settings.weights_dir, "authenticity")

    def analyze(
        self,
        image_path: Path,
        *,
        declared_modality: str | None,
        dicom_meta: dict | None,
    ) -> ImagingAnalysis:
        img, is_dicom = _load_grayscale(image_path)

        modality_probs = self._modality.probs(img)
        modality = max(modality_probs, key=lambda m: modality_probs[m])
        modality_confidence = modality_probs[modality]

        auth_probs = self._authenticity.probs(img)
        p_fake = auth_probs.get("fake", 0.0)

        gray = _analysis_array(img)
        ela_score, ela_finding = _ela_localization(gray)
        fft_score, fft_finding = _fft_periodicity(gray)
        meta_score, meta_finding, hard_override = _metadata_consistency(
            dicom_meta, modality, declared_modality, is_dicom
        )

        forensic = (
            _FORENSIC_WEIGHTS["ela"] * ela_score
            + _FORENSIC_WEIGHTS["fft"] * fft_score
            + _FORENSIC_WEIGHTS["metadata"] * meta_score
        )
        risk = CNN_FUSION_WEIGHT * p_fake + (1.0 - CNN_FUSION_WEIGHT) * forensic
        if hard_override:
            risk = max(risk, 0.50)  # at least suspicious, whatever the fused score said
        risk = float(np.clip(risk, 0.0, 1.0))

        signals = [
            ForensicSignal(
                name="cnn_authenticity",
                score=round(p_fake, 4),
                finding=f"Authenticity CNN calibrated fake-probability {p_fake:.2f} "
                f"(fusion weight capped at {CNN_FUSION_WEIGHT:.2f}).",
            ),
            ForensicSignal(name="ela", score=round(ela_score, 4), finding=ela_finding),
            ForensicSignal(name="fft", score=round(fft_score, 4), finding=fft_finding),
            ForensicSignal(name="metadata", score=round(meta_score, 4), finding=meta_finding),
        ]

        quality_flags: list[str] = []
        if min(img.size) < 224:
            quality_flags.append("low_resolution")
        if not is_dicom:
            quality_flags.append("non_dicom_upload")

        return ImagingAnalysis(
            modality=modality,
            modality_confidence=round(modality_confidence, 4),
            modality_probs={m: round(p, 4) for m, p in modality_probs.items()},
            authenticity_verdict=_verdict(risk),
            authenticity_risk=round(risk, 4),
            signals=signals,
            quality_flags=quality_flags,
            backend="real",
        )
