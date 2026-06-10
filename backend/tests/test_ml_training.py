"""Fast CPU tests for the ml_training workstream (synthetic source only, no network)."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

import numpy as np
import pytest
import torch

from ml_training.datasets.build_datasets import (
    MODALITIES,
    build_authenticity_set,
    build_imaging_set,
    split_for_id,
)
from ml_training.evaluate import main as evaluate_main
from ml_training.models.backbone import build_model, make_transforms
from ml_training.models.calibration import ece, fit_temperature
from ml_training.train_authenticity import main as train_authenticity_main
from ml_training.train_modality import main as train_modality_main

PER_CLASS = 12
IMG_SIZE = 96  # small synthetic images keep the smoke train well under the CPU budget


def _read_rows(manifest: Path) -> list[dict[str, str]]:
    with manifest.open(newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def data_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("mltrain")
    build_imaging_set("synthetic", PER_CLASS, IMG_SIZE, root / "imaging")
    build_authenticity_set(root / "imaging", root / "authenticity", rng_seed=42)
    return root


@pytest.fixture(scope="module")
def weights_dir(data_root: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("weights")
    common = [
        "--epochs", "1", "--batch-size", "8", "--no-pretrained",
        "--input-size", str(IMG_SIZE), "--freeze-epochs", "0", "--seed", "42",
        "--device", "cpu", "--out", str(out),
    ]
    train_modality_main(["--data-dir", str(data_root / "imaging"), *common])
    train_authenticity_main(["--data-dir", str(data_root / "authenticity"), *common])
    return out


# ------------------------------------------------------------------ dataset build


def test_manifest_counts_and_deterministic_split(data_root: Path) -> None:
    rows = _read_rows(data_root / "imaging" / "manifest.csv")
    assert len(rows) == PER_CLASS * len(MODALITIES)
    counts = Counter(r["modality"] for r in rows)
    assert counts == {m: PER_CLASS for m in MODALITIES}
    for row in rows:
        stem = Path(row["path"]).stem
        assert row["split"] == split_for_id(stem)  # split is a pure function of the id
        assert (data_root / "imaging" / row["path"]).exists()
    by_split = Counter(r["split"] for r in rows)
    assert set(by_split) <= {"train", "val", "test"}
    assert by_split["train"] > by_split["val"] > 0 and by_split["test"] > 0
    # every class has train rows (WeightedRandomSampler precondition)
    train_classes = {r["modality"] for r in rows if r["split"] == "train"}
    assert train_classes == set(MODALITIES)


def test_build_is_stable_across_fresh_runs(data_root: Path, tmp_path: Path) -> None:
    build_imaging_set("synthetic", PER_CLASS, IMG_SIZE, tmp_path / "imaging")
    first = (data_root / "imaging" / "manifest.csv").read_text()
    second = (tmp_path / "imaging" / "manifest.csv").read_text()
    assert first == second  # ids, modalities, and splits identical across runs


def test_build_is_resumable_no_dupes(data_root: Path) -> None:
    imaging = data_root / "imaging"
    files_before = sorted(p.name for p in imaging.glob("*/*.jpg"))
    mtimes = {p: p.stat().st_mtime_ns for p in imaging.glob("*/*.jpg")}
    build_imaging_set("synthetic", PER_CLASS, IMG_SIZE, imaging)  # rerun on same out dir
    files_after = sorted(p.name for p in imaging.glob("*/*.jpg"))
    assert files_before == files_after
    assert all(p.stat().st_mtime_ns == t for p, t in mtimes.items())  # nothing re-written
    assert len(_read_rows(imaging / "manifest.csv")) == PER_CLASS * len(MODALITIES)


def test_authenticity_set_source_keyed_split(data_root: Path) -> None:
    rows = _read_rows(data_root / "authenticity" / "manifest_auth.csv")
    counts = Counter(r["label"] for r in rows)
    assert counts["real"] == counts["fake"] == PER_CLASS * len(MODALITIES)
    splits_per_source: dict[str, set[str]] = {}
    for row in rows:
        assert (data_root / "authenticity" / row["path"]).exists()
        assert row["split"] == split_for_id(row["source_id"])
        splits_per_source.setdefault(row["source_id"], set()).add(row["split"])
    # a source image never straddles splits (its real+fake pair share one split)
    assert all(len(s) == 1 for s in splits_per_source.values())
    assert len(splits_per_source) == PER_CLASS * len(MODALITIES)


# ------------------------------------------------------------------ model + transforms


def test_backbone_forward_pass_logits_shape() -> None:
    model = build_model(num_classes=3, pretrained=False).eval()
    with torch.no_grad():
        logits = model(torch.randn(2, 3, IMG_SIZE, IMG_SIZE))
    assert logits.shape == (2, 3)


def test_eval_transform_replicates_grayscale_to_3ch(data_root: Path) -> None:
    from PIL import Image

    from ml_training.models.backbone import IMAGENET_MEAN, IMAGENET_STD

    sample = next(iter((data_root / "imaging" / "ct").glob("*.jpg")))
    tensor = make_transforms(train=False, size=IMG_SIZE)(Image.open(sample).convert("L"))
    assert tensor.shape == (3, IMG_SIZE, IMG_SIZE)
    # channels are replicated before Normalize; undo per-channel norm to compare
    mean = torch.tensor(IMAGENET_MEAN).view(3, 1, 1)
    std = torch.tensor(IMAGENET_STD).view(3, 1, 1)
    raw = tensor * std + mean
    assert torch.allclose(raw[0], raw[1], atol=1e-6) and torch.allclose(raw[1], raw[2], atol=1e-6)


# ------------------------------------------------------------------ training + evaluate


def test_training_smoke_writes_weights_and_config(weights_dir: Path) -> None:
    for name, classes in (("modality", ["ct", "mri", "xray"]), ("authenticity", ["fake", "real"])):
        weights = weights_dir / f"{name}_efficientnet_b0.pt"
        assert weights.exists()
        state = torch.load(weights, map_location="cpu", weights_only=True)
        assert isinstance(state, dict) and len(state) > 0
        config = json.loads((weights_dir / f"{name}_config.json").read_text())
        assert config["classes"] == classes
        assert isinstance(config["temperature"], float)
        assert config["input_size"] == IMG_SIZE
        assert config["seed"] == 42
        assert len(config["dataset_manifest_sha256"]) == 64
        assert "macro_f1" in config["val_metrics"]
        assert config["normalization"]["mean"] and config["normalization"]["std"]


def test_evaluate_writes_report_with_confusion_matrices(
    weights_dir: Path, data_root: Path, tmp_path: Path
) -> None:
    report_path = tmp_path / "eval_report.json"
    evaluate_main(
        [
            "--weights-dir", str(weights_dir),
            "--data-dir", str(data_root),
            "--report", str(report_path),
            "--device", "cpu",
        ]
    )
    report = json.loads(report_path.read_text())
    modality_cm = report["modality"]["confusion_matrix"]
    assert len(modality_cm) == 3 and all(len(row) == 3 for row in modality_cm)
    auth_cm = report["authenticity"]["confusion_matrix"]
    assert len(auth_cm) == 2 and all(len(row) == 2 for row in auth_cm)
    for name in ("modality", "authenticity"):
        assert 0.0 <= report[name]["accuracy"] <= 1.0
        assert isinstance(report[name]["ece_before_temperature"], float)
        assert isinstance(report[name]["ece_after_temperature"], float)
        assert set(report[name]["per_class"]) == set(report[name]["classes"])


def test_evaluate_missing_weights_exits_nonzero(data_root: Path, tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        evaluate_main(
            [
                "--weights-dir", str(tmp_path / "empty"),
                "--data-dir", str(data_root),
                "--report", str(tmp_path / "report.json"),
            ]
        )
    assert excinfo.value.code not in (0, None)


# ------------------------------------------------------------------ calibration


def test_ece_perfect_predictions_near_zero() -> None:
    labels = np.array([0, 1, 2, 0, 1, 2])
    probs = np.eye(3)[labels]  # fully confident, fully correct
    assert ece(probs, labels, bins=15) < 1e-9


def test_fit_temperature_returns_positive_float() -> None:
    rng = np.random.default_rng(0)
    labels = rng.integers(0, 3, size=64)
    logits = np.eye(3)[labels] * 5.0 + rng.normal(0, 0.5, size=(64, 3))
    temperature = fit_temperature(logits.astype(np.float32), labels)
    assert isinstance(temperature, float) and temperature > 0.0
