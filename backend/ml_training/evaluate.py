"""Evaluate the trained imaging models on the held-out test split.

    uv run python -m ml_training.evaluate --weights-dir weights/ \
        --data-dir ml_training/data --report ml_training/data/eval_report.json

``--data-dir`` is the data root: the imaging set is found at ``<data-dir>/imaging``
(or ``<data-dir>`` itself if it directly contains ``manifest.csv``) and the
authenticity set at ``<data-dir>/authenticity`` (or ``<data-dir>`` with
``manifest_auth.csv``). Reports accuracy, macro-F1, per-class precision/recall, the
confusion matrix (printed as a table), and ECE before/after temperature scaling.
Exits nonzero with a clear message if a weights file is missing.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import torch

from ml_training.models import (
    ManifestImageDataset,
    collect_logits,
    confusion_matrix_np,
    macro_f1_from_cm,
    per_class_precision_recall,
    read_manifest,
    resolve_device,
)
from ml_training.models.backbone import ARCH, build_model, make_transforms
from ml_training.models.calibration import ece, softmax

_MODEL_SPECS: tuple[tuple[str, str, str, str], ...] = (
    # (name, data subdir, manifest filename, label column)
    ("modality", "imaging", "manifest.csv", "modality"),
    ("authenticity", "authenticity", "manifest_auth.csv", "label"),
)


def _find_data_dir(data_root: Path, subdir: str, manifest_name: str) -> Path | None:
    for candidate in (data_root / subdir, data_root):
        if (candidate / manifest_name).exists():
            return candidate
    return None


def _format_confusion_matrix(cm: np.ndarray, classes: list[str]) -> str:
    width = max(10, max(len(c) for c in classes) + 6)
    header = " " * width + "".join(f"{'pred:' + c:>{width}}" for c in classes)
    lines = [header]
    for i, cls in enumerate(classes):
        lines.append(f"{'true:' + cls:<{width}}" + "".join(f"{n:>{width}d}" for n in cm[i]))
    return "\n".join(lines)


def evaluate_model(
    name: str,
    weights_path: Path,
    config_path: Path,
    data_dir: Path,
    manifest_name: str,
    label_column: str,
    device: torch.device,
) -> dict[str, object]:
    config = json.loads(config_path.read_text())
    classes: list[str] = list(config["classes"])
    input_size = int(config["input_size"])
    temperature = float(config.get("temperature", 1.0))

    model = build_model(len(classes), pretrained=False)
    state = torch.load(weights_path, map_location="cpu", weights_only=True)
    model.load_state_dict(state)
    model.to(device)

    rows = [r for r in read_manifest(data_dir, manifest_name, label_column) if r.split == "test"]
    if not rows:
        raise SystemExit(f"empty test split in {data_dir / manifest_name}")
    dataset = ManifestImageDataset(
        data_dir, rows, classes, make_transforms(train=False, size=input_size)
    )
    loader = torch.utils.data.DataLoader(dataset, batch_size=32, shuffle=False)
    logits, labels = collect_logits(model, loader, device)

    preds = logits.argmax(axis=1)
    cm = confusion_matrix_np(labels, preds, len(classes))
    precision, recall = per_class_precision_recall(cm)
    ece_before = ece(softmax(logits, 1.0), labels)
    ece_after = ece(softmax(logits, temperature), labels)

    print(f"\n=== {name} (test n={len(labels)}) ===")
    print(_format_confusion_matrix(cm, classes))
    accuracy = float((preds == labels).mean())
    macro_f1 = macro_f1_from_cm(cm)
    print(f"accuracy={accuracy:.4f} macro_f1={macro_f1:.4f}")
    print(f"ece_before={ece_before:.4f} ece_after={ece_after:.4f} (T={temperature:.3f})")

    return {
        "weights": str(weights_path),
        "classes": classes,
        "n_test": int(len(labels)),
        "accuracy": accuracy,
        "macro_f1": macro_f1,
        "per_class": {
            cls: {"precision": float(precision[i]), "recall": float(recall[i])}
            for i, cls in enumerate(classes)
        },
        "confusion_matrix": cm.tolist(),
        "temperature": temperature,
        "ece_before_temperature": ece_before,
        "ece_after_temperature": ece_after,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Evaluate trained models on the test split.")
    parser.add_argument("--weights-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument(
        "--report", type=Path, default=Path("ml_training/data/eval_report.json")
    )
    parser.add_argument("--device", default="auto")
    args = parser.parse_args(argv)

    device = resolve_device(args.device)
    report: dict[str, object] = {"generated_at_utc": datetime.now(UTC).isoformat()}
    missing: list[str] = []

    for name, subdir, manifest_name, label_column in _MODEL_SPECS:
        weights_path = args.weights_dir / f"{name}_{ARCH}.pt"
        config_path = args.weights_dir / f"{name}_config.json"
        if not weights_path.exists() or not config_path.exists():
            missing.append(f"{name}: expected {weights_path} + {config_path}")
            continue
        data_dir = _find_data_dir(args.data_dir, subdir, manifest_name)
        if data_dir is None:
            raise SystemExit(
                f"data for {name!r} not found: looked for {manifest_name} under "
                f"{args.data_dir / subdir} and {args.data_dir}"
            )
        report[name] = evaluate_model(
            name, weights_path, config_path, data_dir, manifest_name, label_column, device
        )

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2))
    print(f"\nreport written: {args.report}")

    if missing:
        print("ERROR: missing trained weights, run the training CLIs first:", file=sys.stderr)
        for line in missing:
            print(f"  - {line}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
