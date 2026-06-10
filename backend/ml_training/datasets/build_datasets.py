"""Build the imaging datasets for the modality classifier and authenticity detector.

Imaging set (modality classifier):

    uv run python -m ml_training.datasets.build_datasets \
        --source rocov2 --per-class 5000 --size 512 --out ml_training/data/imaging

Streams ``eltorio/ROCOv2-radiology`` (never downloads the full 18.6GB archive), maps
each row to a modality via the SAME CUI sets validated by ``probe_rocov2``
(``MODALITY_CUIS`` is imported, not duplicated), keeps rows matching exactly ONE of
ct/mri/xray, and saves grayscale JPEGs (long edge == --size) into
``out/<modality>/<image_id>.jpg`` until the per-class quota is met for ALL classes.
Resumable: existing files are skipped, so an interrupted build continues where it
stopped. ``--limit-stream N`` caps the number of streamed rows (safety valve for
tests/CI). A synthetic source is available for fast CI builds with identical layout:

    uv run python -m ml_training.datasets.build_datasets \
        --source synthetic --per-class 30 --out ml_training/data/imaging-synth

Authenticity set (real/fake detector), built FROM the imaging set:

    uv run python -m ml_training.datasets.build_datasets \
        --authenticity --real-dir ml_training/data/imaging \
        --out ml_training/data/authenticity --seed 42

Both manifests use the same deterministic hash-of-id 80/10/10 split (stable across
runs and machines); the authenticity split is keyed on the SOURCE image id so a source
never straddles splits.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
from collections.abc import Callable, Iterator
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from ml_training.datasets import tampering
from ml_training.datasets.probe_rocov2 import MODALITY_CUIS

MODALITIES: tuple[str, ...] = ("ct", "mri", "xray")
AUTH_LABELS: tuple[str, ...] = ("fake", "real")
PROGRESS_EVERY = 250

# ------------------------------------------------------------------ split + id helpers


def split_for_id(image_id: str) -> str:
    """Deterministic 80/10/10 train/val/test split from a stable hash of the id.

    Stable across runs, machines, and python versions (sha256, not ``hash()``).
    """
    bucket = int(hashlib.sha256(image_id.encode("utf-8")).hexdigest(), 16) % 100
    if bucket < 80:
        return "train"
    if bucket < 90:
        return "val"
    return "test"


def _stable_seed(image_id: str) -> int:
    return int(hashlib.sha256(image_id.encode("utf-8")).hexdigest()[:16], 16)


def _safe_id(image_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", image_id)


def modality_for_cuis(cuis: list[str]) -> str | None:
    """Map a row's CUI list to a modality, mirroring probe_rocov2 exactly.

    Returns the lowercase modality only when the row matches exactly ONE modality
    overall and that modality is one of ct/mri/xray; otherwise ``None``.
    """
    hits = {MODALITY_CUIS[c] for c in cuis if c in MODALITY_CUIS}
    hits = {"XRAY" if h.startswith("XRAY") else h for h in hits}
    if len(hits) != 1:
        return None
    label = next(iter(hits)).lower()
    return label if label in MODALITIES else None


# ------------------------------------------------------------------ imaging set


def _save_grayscale_jpeg(img: Image.Image, dest: Path, size: int) -> None:
    """Convert to grayscale and resize so the long edge equals ``size``."""
    img = img.convert("L")
    w, h = img.size
    scale = size / max(w, h)
    if max(w, h) != size:
        img = img.resize(
            (max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.LANCZOS
        )
    img.save(dest, format="JPEG", quality=95)


def _iter_rocov2(
    limit_stream: int | None,
) -> Iterator[tuple[str, str, Callable[[], Image.Image]]]:
    from datasets import load_dataset  # heavy + network-touching: import lazily

    ds = load_dataset("eltorio/ROCOv2-radiology", split="train", streaming=True)
    for i, row in enumerate(ds):
        if limit_stream is not None and i >= limit_stream:
            return
        cuis = row.get("cui") or []
        if isinstance(cuis, str):
            cuis = [cuis]
        modality = modality_for_cuis(list(cuis))
        if modality is None:
            continue
        image_id = _safe_id(str(row.get("image_id") or f"rocov2_{i:07d}"))
        image = row["image"]
        yield image_id, modality, lambda img=image: img


def _iter_synthetic(
    per_class: int, size: int
) -> Iterator[tuple[str, str, Callable[[], Image.Image]]]:
    for kind in MODALITIES:
        for i in range(per_class):
            image_id = f"synthetic_{kind}_{i:04d}"

            def make(kind: str = kind, image_id: str = image_id) -> Image.Image:
                rng = np.random.default_rng(_stable_seed(image_id))
                return Image.fromarray(tampering.synth_base_image(kind, rng, size=size))  # type: ignore[arg-type]

            yield image_id, kind, make


def write_imaging_manifest(out: Path) -> Path:
    """(Re)generate manifest.csv by scanning the output tree (sorted, deterministic)."""
    manifest = out / "manifest.csv"
    with manifest.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["path", "modality", "split"])
        for modality in MODALITIES:
            for p in sorted((out / modality).glob("*.jpg")):
                writer.writerow([f"{modality}/{p.name}", modality, split_for_id(p.stem)])
    return manifest


def build_imaging_set(
    source: str,
    per_class: int,
    size: int,
    out: Path,
    limit_stream: int | None = None,
) -> Path:
    """Build out/<modality>/<image_id>.jpg until every class hits ``per_class``.

    Resumable: files already on disk count toward quotas and are never re-written.
    Returns the manifest path.
    """
    out.mkdir(parents=True, exist_ok=True)
    have: dict[str, set[str]] = {}
    for modality in MODALITIES:
        (out / modality).mkdir(parents=True, exist_ok=True)
        have[modality] = {p.stem for p in (out / modality).glob("*.jpg")}

    if source == "rocov2":
        rows = _iter_rocov2(limit_stream)
    elif source == "synthetic":
        rows = _iter_synthetic(per_class, size)
    else:
        raise ValueError(f"unknown source: {source!r}")

    saved = 0
    for image_id, modality, get_image in rows:
        if all(len(have[m]) >= per_class for m in MODALITIES):
            break
        if len(have[modality]) >= per_class or image_id in have[modality]:
            continue
        _save_grayscale_jpeg(get_image(), out / modality / f"{image_id}.jpg", size)
        have[modality].add(image_id)
        saved += 1
        if saved % PROGRESS_EVERY == 0:
            counts = {m: len(have[m]) for m in MODALITIES}
            print(f"[build] saved={saved} counts={counts}", flush=True)

    counts = {m: len(have[m]) for m in MODALITIES}
    if any(len(have[m]) < per_class for m in MODALITIES):
        print(f"[build] WARNING: stream ended before quotas met: {counts}", flush=True)
    manifest = write_imaging_manifest(out)
    print(f"[build] done: saved={saved} counts={counts} manifest={manifest}", flush=True)
    return manifest


# ------------------------------------------------------------------ authenticity set


def write_authenticity_manifest(out_dir: Path) -> Path:
    manifest = out_dir / "manifest_auth.csv"
    with manifest.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["path", "label", "source_id", "split"])
        for label in AUTH_LABELS:
            for p in sorted((out_dir / label).glob("*.png")):
                writer.writerow([f"{label}/{p.name}", label, p.stem, split_for_id(p.stem)])
    return manifest


def build_authenticity_set(real_dir: Path, out_dir: Path, rng_seed: int = 42) -> Path:
    """Derive the real/fake detector set from the built real images.

    For each source image, emit exactly one REAL sample (``final_resave`` applied) and
    one FAKE sample (``apply_random_tampering`` then ``final_resave``) into
    ``out_dir/real|fake/<source_id>.png`` (PNG: lossless, so the on-disk pixels are
    exactly the post-resave pixels, with no extra compression step that could differ
    between classes).

    IDENTICAL-FINAL-RESAVE RATIONALE (the class-shortcut killer): every tampering op
    perturbs compression statistics as a side effect, so without a shared final step a
    detector learns the trivial shortcut "fake == re-compressed/processed pixels"
    instead of actual manipulation evidence (clone seams, splice boundaries, inpaint
    smear, resampling softness, double-JPEG ghosts). Both classes therefore pass
    through the SAME randomized single JPEG re-save (``tampering.final_resave``) as the
    very last step, equalizing compression provenance across classes.

    SPLIT IS KEYED ON THE SOURCE IMAGE: manifest_auth.csv (path,label,source_id,split)
    uses ``split_for_id(source_id)``, so the real and fake derivative of one source
    always land in the same split and a source never straddles splits (no
    near-duplicate leakage between train/val/test).

    Resumable and deterministic: per-source rng seeded from (rng_seed, source_id), so
    skipping already-built pairs never shifts the random stream of other pairs.
    """
    sources = sorted(
        p for pattern in ("*.jpg", "*/*.jpg", "*.png", "*/*.png") for p in real_dir.glob(pattern)
    )
    if len(sources) < 2:
        raise SystemExit(f"need at least 2 real images in {real_dir} (found {len(sources)})")
    for label in AUTH_LABELS:
        (out_dir / label).mkdir(parents=True, exist_ok=True)

    built = 0
    for idx, src in enumerate(sources):
        source_id = src.stem
        real_out = out_dir / "real" / f"{source_id}.png"
        fake_out = out_dir / "fake" / f"{source_id}.png"
        if real_out.exists() and fake_out.exists():
            continue
        rng = np.random.default_rng([rng_seed, _stable_seed(source_id)])
        img = cv2.imread(str(src), cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise SystemExit(f"failed to read real image: {src}")
        donor_idx = int(rng.integers(0, len(sources) - 1))
        if donor_idx >= idx:
            donor_idx += 1
        donor = cv2.imread(str(sources[donor_idx]), cv2.IMREAD_GRAYSCALE)
        if donor is None:
            raise SystemExit(f"failed to read donor image: {sources[donor_idx]}")
        real_img = tampering.final_resave(img, rng)
        fake_img, _ops = tampering.apply_random_tampering(img, donor, rng)
        fake_img = tampering.final_resave(fake_img, rng)
        if not cv2.imwrite(str(real_out), real_img):  # pragma: no cover
            raise SystemExit(f"failed to write {real_out}")
        if not cv2.imwrite(str(fake_out), fake_img):  # pragma: no cover
            raise SystemExit(f"failed to write {fake_out}")
        built += 1
        if built % PROGRESS_EVERY == 0:
            print(f"[auth] built={built}/{len(sources)} source pairs", flush=True)

    manifest = write_authenticity_manifest(out_dir)
    print(f"[auth] done: {built} new pairs, {len(sources)} sources, manifest={manifest}", flush=True)
    return manifest


# ------------------------------------------------------------------ CLI


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", choices=("rocov2", "synthetic"), default="rocov2")
    parser.add_argument("--per-class", type=int, default=5000)
    parser.add_argument("--size", type=int, default=512)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--limit-stream", type=int, default=None, help="cap streamed rows (test safety valve)"
    )
    parser.add_argument(
        "--authenticity",
        action="store_true",
        help="build the real/fake set from --real-dir into --out (ignores --source)",
    )
    parser.add_argument(
        "--real-dir", type=Path, default=None, help="imaging dir with built real images"
    )
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    if args.authenticity:
        if args.real_dir is None:
            parser.error("--authenticity requires --real-dir")
        build_authenticity_set(args.real_dir, args.out, args.seed)
    else:
        build_imaging_set(args.source, args.per_class, args.size, args.out, args.limit_stream)


if __name__ == "__main__":
    main()
