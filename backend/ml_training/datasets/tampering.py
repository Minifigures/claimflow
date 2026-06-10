"""Tampering pipeline for the authenticity detector — fake generation + demo seed assets.

Every operation takes and returns a 2-D ``uint8`` grayscale array and is deterministic
given an explicit ``numpy.random.Generator``. The dataset builder draws "authentic"
images from ROCOv2 and "tampered" images from :func:`apply_random_tampering`, then
pushes *both* classes through :func:`final_resave` so the detector cannot shortcut on
compression provenance (see the final_resave docstring).

Demo seed assets (synthetic, never used for training):

    uv run python -m ml_training.datasets.tampering --emit-seed-assets
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import cv2
import numpy as np

ModalityKind = Literal["xray", "ct", "mri"]

_SEED_ASSET_DIR = Path(__file__).resolve().parents[2] / "seed-assets"

# ------------------------------------------------------------------ helpers


def _require_gray_u8(img: np.ndarray, name: str = "img") -> None:
    if not isinstance(img, np.ndarray) or img.dtype != np.uint8 or img.ndim != 2:
        raise ValueError(f"{name} must be a 2-D uint8 grayscale array")


def _jpeg_roundtrip(img: np.ndarray, quality: int) -> np.ndarray:
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:  # pragma: no cover - cv2 jpeg encode never fails on valid uint8 input
        raise RuntimeError("JPEG encode failed")
    out = cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)
    if out is None:  # pragma: no cover
        raise RuntimeError("JPEG decode failed")
    return out


def _feathered_paste(canvas: np.ndarray, patch: np.ndarray, top: int, left: int) -> None:
    """Alpha-blend ``patch`` (float32) into ``canvas`` (uint8) with feathered edges."""
    ph, pw = patch.shape
    margin = max(2, min(ph, pw) // 8)
    mask = np.zeros((ph, pw), np.float32)
    mask[margin : ph - margin, margin : pw - margin] = 1.0
    kernel = 2 * margin + 1
    mask = cv2.GaussianBlur(mask, (kernel, kernel), 0)
    region = canvas[top : top + ph, left : left + pw].astype(np.float32)
    blended = region * (1.0 - mask) + patch * mask
    canvas[top : top + ph, left : left + pw] = np.clip(np.rint(blended), 0, 255).astype(np.uint8)


def _non_overlapping_squares(
    h: int, w: int, patch: int, rng: np.random.Generator, attempts: int = 100
) -> tuple[int, int, int, int]:
    """Return (src_y, src_x, dst_y, dst_x) for two non-overlapping patch×patch squares."""
    for _ in range(attempts):
        sy = int(rng.integers(0, h - patch + 1))
        sx = int(rng.integers(0, w - patch + 1))
        dy = int(rng.integers(0, h - patch + 1))
        dx = int(rng.integers(0, w - patch + 1))
        if abs(sy - dy) >= patch or abs(sx - dx) >= patch:
            return sy, sx, dy, dx
    return 0, 0, h - patch, w - patch  # deterministic corner fallback


# ------------------------------------------------------------------ tampering operations


def copy_move(img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Clone a random patch (8-20% of side) to a non-overlapping location.

    Grayscale path: direct paste with a Gaussian-blurred feather mask so the seam is
    soft (``cv2.seamlessClone`` would require a 3-channel conversion for no benefit).
    """
    _require_gray_u8(img)
    h, w = img.shape
    side = min(h, w)
    patch = int(round(side * rng.uniform(0.08, 0.20)))
    patch = max(8, min(patch, side // 3))
    sy, sx, dy, dx = _non_overlapping_squares(h, w, patch, rng)
    cloned = img[sy : sy + patch, sx : sx + patch].astype(np.float32)
    out = img.copy()
    _feathered_paste(out, cloned, dy, dx)
    return out


def splice(img: np.ndarray, donor: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Paste a random region from ``donor`` into ``img`` with a feathered alpha blend."""
    _require_gray_u8(img)
    _require_gray_u8(donor, "donor")
    h, w = img.shape
    dh, dw = donor.shape
    side = min(h, w, dh, dw)
    ph = max(8, int(round(side * rng.uniform(0.10, 0.25))))
    pw = max(8, int(round(side * rng.uniform(0.10, 0.25))))
    sy = int(rng.integers(0, dh - ph + 1))
    sx = int(rng.integers(0, dw - pw + 1))
    dy = int(rng.integers(0, h - ph + 1))
    dx = int(rng.integers(0, w - pw + 1))
    region = donor[sy : sy + ph, sx : sx + pw].astype(np.float32)
    out = img.copy()
    _feathered_paste(out, region, dy, dx)
    return out


def inpaint_removal(img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Erase a random elliptical region via ``cv2.inpaint`` TELEA (simulates removing
    a finding, e.g. painting out a fracture line or lesion)."""
    _require_gray_u8(img)
    h, w = img.shape
    center = (int(rng.integers(w // 6, w - w // 6)), int(rng.integers(h // 6, h - h // 6)))
    axes = (
        max(3, int(rng.integers(w // 24, max(w // 24 + 1, w // 8)))),
        max(3, int(rng.integers(h // 24, max(h // 24 + 1, h // 8)))),
    )
    angle = float(rng.uniform(0.0, 180.0))
    mask = np.zeros((h, w), np.uint8)
    cv2.ellipse(mask, center, axes, angle, 0.0, 360.0, 255, -1)
    return cv2.inpaint(img, mask, 3, cv2.INPAINT_TELEA)


def resample_artifacts(img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Bicubic downscale by 0.4-0.7x then upscale back — GAN-style softness and
    checkerboard/resampling artifacts without changing the image content."""
    _require_gray_u8(img)
    h, w = img.shape
    factor = float(rng.uniform(0.4, 0.7))
    small = cv2.resize(
        img, (max(1, int(w * factor)), max(1, int(h * factor))), interpolation=cv2.INTER_CUBIC
    )
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC)


def double_jpeg(img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Encode high-quality (q 88-95), decode, re-encode low-quality (q 60-75), decode —
    leaves double-quantization artifacts in the DCT histogram."""
    _require_gray_u8(img)
    first = _jpeg_roundtrip(img, int(rng.integers(88, 96)))
    return _jpeg_roundtrip(first, int(rng.integers(60, 76)))


def final_resave(img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Randomized single JPEG re-save (q 70-95) — the CLASS-SHORTCUT KILLER.

    The dataset builder applies this op identically to BOTH classes (authentic and
    tampered) as the very last step. Without it the detector learns the trivial
    shortcut "tampered == re-compressed/processed pixels" instead of the actual
    manipulation evidence, because every tampering op above perturbs compression
    statistics as a side effect. Never include this op in the tampering pool
    (:func:`apply_random_tampering` excludes it by construction).
    """
    _require_gray_u8(img)
    return _jpeg_roundtrip(img, int(rng.integers(70, 96)))


_TAMPER_OP_NAMES: tuple[str, ...] = (
    "copy_move",
    "splice",
    "inpaint_removal",
    "resample_artifacts",
    "double_jpeg",
)


def apply_random_tampering(
    img: np.ndarray, donor: np.ndarray, rng: np.random.Generator
) -> tuple[np.ndarray, list[str]]:
    """Apply 1-2 distinct randomly chosen tampering operations (never ``final_resave``).

    Returns ``(tampered_image, operation_names)`` with names in application order.
    Deterministic for a given rng state, image, and donor.
    """
    _require_gray_u8(img)
    _require_gray_u8(donor, "donor")
    ops: dict[str, Callable[[np.ndarray], np.ndarray]] = {
        "copy_move": lambda im: copy_move(im, rng),
        "splice": lambda im: splice(im, donor, rng),
        "inpaint_removal": lambda im: inpaint_removal(im, rng),
        "resample_artifacts": lambda im: resample_artifacts(im, rng),
        "double_jpeg": lambda im: double_jpeg(im, rng),
    }
    n_ops = int(rng.integers(1, 3))
    indices = rng.choice(len(_TAMPER_OP_NAMES), size=n_ops, replace=False)
    chosen = [_TAMPER_OP_NAMES[int(i)] for i in indices]
    out = img
    for name in chosen:
        out = ops[name](out)
    return out, chosen


# ------------------------------------------------------------------ synthetic bases (tests/demo)


def synth_base_image(
    kind: ModalityKind, rng: np.random.Generator, size: int = 512
) -> np.ndarray:
    """Plausible synthetic grayscale medical-looking image (gradients + ellipses + noise).

    For tests and demo seed assets ONLY — no clinical content. Real detector training
    uses ROCOv2 radiographs; nothing produced here ever enters the training set.
    """
    if kind not in ("xray", "ct", "mri"):
        raise ValueError(f"unknown modality kind: {kind!r}")
    s = size
    c = s // 2
    img = np.zeros((s, s), np.uint8)
    if kind == "xray":
        img[:] = 25
        cv2.ellipse(img, (c, c), (int(s * 0.42), int(s * 0.48)), 0, 0, 360, 115, -1)
        for cx in (int(s * 0.32), int(s * 0.68)):  # lung fields
            cv2.ellipse(
                img, (cx, int(s * 0.48)), (int(s * 0.15), int(s * 0.28)), 0, 0, 360, 70, -1
            )
        cv2.rectangle(img, (int(s * 0.46), int(s * 0.08)), (int(s * 0.54), int(s * 0.92)), 185, -1)
        for i in range(5):  # rib arcs
            y = int(s * (0.28 + 0.11 * i))
            cv2.ellipse(
                img, (c, y), (int(s * 0.36), int(s * 0.07)), 0, 200, 340, 150, max(2, s // 170)
            )
    elif kind == "ct":
        img[:] = 5
        cv2.circle(img, (c, c), int(s * 0.42), 120, -1)  # axial body section
        cv2.circle(img, (c, c), int(s * 0.42), 205, max(3, s // 64))  # fat/skin rim
        cv2.ellipse(
            img,
            (int(s * 0.40), int(s * 0.45)),
            (int(s * 0.13), int(s * 0.18)),
            15,
            0,
            360,
            75,
            -1,
        )
        cv2.ellipse(
            img,
            (int(s * 0.62), int(s * 0.47)),
            (int(s * 0.10), int(s * 0.15)),
            -10,
            0,
            360,
            95,
            -1,
        )
        cv2.circle(img, (c, int(s * 0.74)), max(3, int(s * 0.05)), 230, -1)  # vertebral body
    else:  # mri
        img[:] = 8
        cv2.ellipse(img, (c, c), (int(s * 0.33), int(s * 0.42)), 0, 0, 360, 110, -1)  # brain
        cv2.ellipse(
            img, (c, c), (int(s * 0.33), int(s * 0.42)), 0, 0, 360, 165, max(3, s // 70)
        )  # cortex rim
        for cx in (int(s * 0.44), int(s * 0.56)):  # ventricles
            cv2.ellipse(
                img, (cx, int(s * 0.46)), (max(2, int(s * 0.04)), int(s * 0.10)), 0, 0, 360, 30, -1
            )
        cv2.line(img, (c, int(s * 0.12)), (c, int(s * 0.88)), 40, max(2, s // 256))  # midline
    base = cv2.GaussianBlur(img, (0, 0), max(1.0, s / 170)).astype(np.float32)
    low = rng.normal(0.0, 1.0, (max(1, s // 8), max(1, s // 8))).astype(np.float32)
    base += 10.0 * cv2.resize(low, (s, s), interpolation=cv2.INTER_CUBIC)
    gradient = np.linspace(-1.0, 1.0, s, dtype=np.float32)[:, None]
    base += float(rng.uniform(-12.0, 12.0)) * gradient
    base += rng.normal(0.0, 4.0, (s, s)).astype(np.float32)
    return np.clip(np.rint(base), 0, 255).astype(np.uint8)


# ------------------------------------------------------------------ demo seed assets


def emit_seed_assets(out_dir: Path | None = None, *, size: int = 512) -> list[Path]:
    """Write the four demo assets as PNGs (each well under 300 KB), seeded rng(42).

    clean_{xray,ct,mri}.png: synthetic base + final_resave (same pipeline as the
    authentic class). tampered_xray.png: the clean x-ray base + copy_move + splice
    (CT donor, visibly alien texture) + final_resave — the filename intentionally
    contains 'tampered' because the stub analyzer keys on it for the demo.
    """
    target = _SEED_ASSET_DIR if out_dir is None else out_dir
    target.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(42)
    kinds: tuple[ModalityKind, ...] = ("xray", "ct", "mri")
    bases = {kind: synth_base_image(kind, rng, size=size) for kind in kinds}
    written: list[Path] = []
    for kind in kinds:
        path = target / f"clean_{kind}.png"
        if not cv2.imwrite(str(path), final_resave(bases[kind], rng)):  # pragma: no cover
            raise RuntimeError(f"failed to write {path}")
        written.append(path)
    tampered = copy_move(bases["xray"], rng)
    tampered = splice(tampered, bases["ct"], rng)
    tampered = final_resave(tampered, rng)
    path = target / "tampered_xray.png"
    if not cv2.imwrite(str(path), tampered):  # pragma: no cover
        raise RuntimeError(f"failed to write {path}")
    written.append(path)
    return written


def _main() -> None:
    parser = argparse.ArgumentParser(description="Tampering pipeline utilities.")
    parser.add_argument(
        "--emit-seed-assets",
        action="store_true",
        help="write clean_{xray,ct,mri}.png + tampered_xray.png demo assets",
    )
    parser.add_argument(
        "--out-dir", type=Path, default=None, help="override backend/seed-assets/"
    )
    parser.add_argument("--size", type=int, default=512)
    args = parser.parse_args()
    if args.emit_seed_assets:
        for path in emit_seed_assets(args.out_dir, size=args.size):
            print(path)
    else:
        parser.print_help()


if __name__ == "__main__":
    _main()
