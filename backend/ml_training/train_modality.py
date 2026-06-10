"""Train the modality classifier (ct/mri/xray) on the built imaging set.

    uv run python -m ml_training.train_modality --data-dir ml_training/data/imaging \
        --epochs 12 --batch-size 32 --out weights/

Uses the full modality train recipe (geometric + JPEG-quality jitter + blur, the
source-confound killers) so the model cannot key on per-source compression signatures.
Saves ``weights/modality_efficientnet_b0.pt`` + ``weights/modality_config.json``.
"""

from __future__ import annotations

import argparse

from ml_training.models import add_train_args, run_training, spec_from_args
from ml_training.models.backbone import make_transforms

MODALITY_CLASSES = ["ct", "mri", "xray"]  # alphabetical, must match serving config


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Train the ct/mri/xray modality classifier.")
    add_train_args(parser)
    args = parser.parse_args(argv)
    spec = spec_from_args(
        args,
        name="modality",
        classes=MODALITY_CLASSES,
        manifest_name="manifest.csv",
        label_column="modality",
        train_transform=make_transforms(train=True, size=args.input_size),
        eval_transform=make_transforms(train=False, size=args.input_size),
    )
    run_training(spec)


if __name__ == "__main__":
    main()
