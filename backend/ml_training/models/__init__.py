"""Shared training loop, manifest dataset, and numpy metrics for both imaging models.

``train_modality`` and ``train_authenticity`` are thin CLIs over :func:`run_training`;
they differ only in classes, manifest name, label column, and train transform.
All metrics are numpy (sklearn is not installed in this environment).
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from torch import nn
from torch.utils.data import DataLoader, Dataset, WeightedRandomSampler

from ml_training.models.backbone import (
    ARCH,
    IMAGENET_MEAN,
    IMAGENET_STD,
    build_model,
)
from ml_training.models.calibration import fit_temperature

# ------------------------------------------------------------------ numpy metrics


def confusion_matrix_np(labels: np.ndarray, preds: np.ndarray, num_classes: int) -> np.ndarray:
    """Rows = true class, cols = predicted class."""
    cm = np.zeros((num_classes, num_classes), dtype=np.int64)
    np.add.at(cm, (labels.astype(np.int64), preds.astype(np.int64)), 1)
    return cm


def per_class_precision_recall(cm: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    diag = np.diag(cm).astype(np.float64)
    pred_totals = cm.sum(axis=0).astype(np.float64)
    true_totals = cm.sum(axis=1).astype(np.float64)
    precision = np.divide(diag, pred_totals, out=np.zeros_like(diag), where=pred_totals > 0)
    recall = np.divide(diag, true_totals, out=np.zeros_like(diag), where=true_totals > 0)
    return precision, recall


def macro_f1_from_cm(cm: np.ndarray) -> float:
    precision, recall = per_class_precision_recall(cm)
    denom = precision + recall
    f1 = np.divide(2 * precision * recall, denom, out=np.zeros_like(denom), where=denom > 0)
    return float(f1.mean())


# ------------------------------------------------------------------ manifest dataset


@dataclass(frozen=True)
class ManifestRow:
    path: str
    label: str
    split: str


def read_manifest(data_dir: Path, manifest_name: str, label_column: str) -> list[ManifestRow]:
    manifest = data_dir / manifest_name
    if not manifest.exists():
        raise SystemExit(f"manifest not found: {manifest}")
    rows: list[ManifestRow] = []
    with manifest.open(newline="") as f:
        for rec in csv.DictReader(f):
            rows.append(ManifestRow(rec["path"], rec[label_column], rec["split"]))
    return rows


class ManifestImageDataset(Dataset[tuple[torch.Tensor, int]]):
    def __init__(
        self,
        data_dir: Path,
        rows: list[ManifestRow],
        classes: list[str],
        transform: object,
    ) -> None:
        self.data_dir = data_dir
        self.rows = rows
        self.class_to_idx = {c: i for i, c in enumerate(classes)}
        self.transform = transform

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, int]:
        row = self.rows[idx]
        img = Image.open(self.data_dir / row.path).convert("L")
        tensor = self.transform(img)  # type: ignore[operator]
        return tensor, self.class_to_idx[row.label]


# ------------------------------------------------------------------ helpers


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def resolve_device(device: str) -> torch.device:
    if device == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return torch.device(device)


def collect_logits(
    model: nn.Module, loader: DataLoader, device: torch.device
) -> tuple[np.ndarray, np.ndarray]:
    model.eval()
    logits: list[np.ndarray] = []
    labels: list[np.ndarray] = []
    with torch.no_grad():
        for x, y in loader:
            out = model(x.to(device))
            logits.append(out.cpu().numpy())
            labels.append(y.numpy())
    if not logits:
        return np.zeros((0, 1), dtype=np.float32), np.zeros((0,), dtype=np.int64)
    return np.concatenate(logits), np.concatenate(labels)


def manifest_sha256(data_dir: Path, manifest_name: str) -> str:
    return hashlib.sha256((data_dir / manifest_name).read_bytes()).hexdigest()


def add_train_args(parser: argparse.ArgumentParser) -> None:
    """Shared CLI surface for train_modality / train_authenticity."""
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--lr-head", type=float, default=3e-4)
    parser.add_argument("--lr-backbone", type=float, default=3e-5)
    parser.add_argument("--freeze-epochs", type=int, default=2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--out", type=Path, default=Path("weights"))
    parser.add_argument("--input-size", type=int, default=224)
    parser.add_argument(
        "--no-pretrained",
        action="store_true",
        help="random init (tests/CI; real runs keep ImageNet init)",
    )
    parser.add_argument("--num-workers", type=int, default=0)


# ------------------------------------------------------------------ training loop


@dataclass
class TrainSpec:
    name: str
    classes: list[str]
    manifest_name: str
    label_column: str
    data_dir: Path
    out_dir: Path
    train_transform: object
    eval_transform: object
    epochs: int = 12
    batch_size: int = 32
    lr_head: float = 3e-4
    lr_backbone: float = 3e-5
    freeze_epochs: int = 2
    seed: int = 42
    device: str = "auto"
    pretrained: bool = True
    input_size: int = 224
    patience: int = 4
    num_workers: int = 0
    extra_config: dict[str, object] = field(default_factory=dict)


def spec_from_args(
    args: argparse.Namespace,
    *,
    name: str,
    classes: list[str],
    manifest_name: str,
    label_column: str,
    train_transform: object,
    eval_transform: object,
) -> TrainSpec:
    return TrainSpec(
        name=name,
        classes=classes,
        manifest_name=manifest_name,
        label_column=label_column,
        data_dir=args.data_dir,
        out_dir=args.out,
        train_transform=train_transform,
        eval_transform=eval_transform,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr_head=args.lr_head,
        lr_backbone=args.lr_backbone,
        freeze_epochs=args.freeze_epochs,
        seed=args.seed,
        device=args.device,
        pretrained=not args.no_pretrained,
        input_size=args.input_size,
        num_workers=args.num_workers,
    )


def run_training(spec: TrainSpec) -> dict[str, object]:
    """Train, select best epoch by val macro-F1, calibrate, and save weights + config.

    Writes ``<name>_efficientnet_b0.pt`` (state_dict) and ``<name>_config.json``.
    """
    set_seed(spec.seed)
    device = resolve_device(spec.device)
    num_classes = len(spec.classes)

    rows = read_manifest(spec.data_dir, spec.manifest_name, spec.label_column)
    train_rows = [r for r in rows if r.split == "train"]
    val_rows = [r for r in rows if r.split == "val"]
    if not train_rows:
        raise SystemExit(f"no train rows in {spec.data_dir / spec.manifest_name}")
    if not val_rows:
        print("[train] WARNING: empty val split; using train rows for validation", flush=True)
        val_rows = train_rows

    class_to_idx = {c: i for i, c in enumerate(spec.classes)}
    train_labels = np.array([class_to_idx[r.label] for r in train_rows], dtype=np.int64)
    class_counts = np.bincount(train_labels, minlength=num_classes).astype(np.float64)
    if (class_counts == 0).any():
        missing = [c for c, n in zip(spec.classes, class_counts) if n == 0]
        raise SystemExit(f"train split has no samples for classes: {missing}")
    sample_weights = torch.as_tensor(1.0 / class_counts[train_labels], dtype=torch.double)
    sampler = WeightedRandomSampler(sample_weights, num_samples=len(train_rows), replacement=True)

    train_ds = ManifestImageDataset(spec.data_dir, train_rows, spec.classes, spec.train_transform)
    val_ds = ManifestImageDataset(spec.data_dir, val_rows, spec.classes, spec.eval_transform)
    train_loader = DataLoader(
        train_ds, batch_size=spec.batch_size, sampler=sampler, num_workers=spec.num_workers
    )
    val_loader = DataLoader(
        val_ds, batch_size=spec.batch_size, shuffle=False, num_workers=spec.num_workers
    )

    model = build_model(num_classes, pretrained=spec.pretrained).to(device)
    head_params = list(model.get_classifier().parameters())
    head_ids = {id(p) for p in head_params}
    backbone_params = [p for p in model.parameters() if id(p) not in head_ids]
    optimizer = torch.optim.AdamW(
        [
            {"params": head_params, "lr": spec.lr_head},
            {"params": backbone_params, "lr": spec.lr_backbone},
        ],
        weight_decay=1e-4,
    )
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=max(1, spec.epochs))
    loss_fn = nn.CrossEntropyLoss()

    best_f1 = -1.0
    best_state: dict[str, torch.Tensor] | None = None
    epochs_without_improvement = 0

    for epoch in range(spec.epochs):
        frozen = epoch < spec.freeze_epochs
        for p in backbone_params:
            p.requires_grad_(not frozen)
        model.train()
        running_loss, seen = 0.0, 0
        for x, y in train_loader:
            x, y = x.to(device), y.to(device)
            optimizer.zero_grad()
            loss = loss_fn(model(x), y)
            loss.backward()
            optimizer.step()
            running_loss += float(loss.item()) * len(y)
            seen += len(y)
        scheduler.step()

        val_logits, val_labels = collect_logits(model, val_loader, device)
        cm = confusion_matrix_np(val_labels, val_logits.argmax(axis=1), num_classes)
        val_f1 = macro_f1_from_cm(cm)
        val_acc = float((val_logits.argmax(axis=1) == val_labels).mean())
        print(
            f"[{spec.name}] epoch {epoch + 1}/{spec.epochs} "
            f"loss={running_loss / max(1, seen):.4f} val_f1={val_f1:.4f} "
            f"val_acc={val_acc:.4f}{' (backbone frozen)' if frozen else ''}",
            flush=True,
        )
        if val_f1 > best_f1:
            best_f1 = val_f1
            best_state = copy.deepcopy(
                {k: v.detach().cpu() for k, v in model.state_dict().items()}
            )
            epochs_without_improvement = 0
        else:
            epochs_without_improvement += 1
            if epochs_without_improvement >= spec.patience:
                print(f"[{spec.name}] early stop at epoch {epoch + 1} (patience)", flush=True)
                break

    assert best_state is not None
    model.load_state_dict(best_state)
    model.to(device)

    val_logits, val_labels = collect_logits(model, val_loader, device)
    temperature = fit_temperature(val_logits, val_labels)
    cm = confusion_matrix_np(val_labels, val_logits.argmax(axis=1), num_classes)
    precision, recall = per_class_precision_recall(cm)
    val_metrics: dict[str, object] = {
        "accuracy": float((val_logits.argmax(axis=1) == val_labels).mean()),
        "macro_f1": macro_f1_from_cm(cm),
        "per_class": {
            cls: {"precision": float(precision[i]), "recall": float(recall[i])}
            for i, cls in enumerate(spec.classes)
        },
        "n_val": int(len(val_labels)),
    }

    spec.out_dir.mkdir(parents=True, exist_ok=True)
    weights_path = spec.out_dir / f"{spec.name}_{ARCH}.pt"
    torch.save(best_state, weights_path)
    config = {
        "arch": ARCH,
        "classes": spec.classes,
        "input_size": spec.input_size,
        "normalization": {"mean": list(IMAGENET_MEAN), "std": list(IMAGENET_STD)},
        "temperature": float(temperature),
        "val_metrics": val_metrics,
        "trained_at_utc": datetime.now(UTC).isoformat(),
        "dataset_manifest_sha256": manifest_sha256(spec.data_dir, spec.manifest_name),
        "seed": spec.seed,
        **spec.extra_config,
    }
    config_path = spec.out_dir / f"{spec.name}_config.json"
    config_path.write_text(json.dumps(config, indent=2))
    print(f"[{spec.name}] saved {weights_path} and {config_path}", flush=True)
    return {
        "weights_path": weights_path,
        "config_path": config_path,
        "val_metrics": val_metrics,
        "temperature": float(temperature),
    }
