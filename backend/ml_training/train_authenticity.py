"""Train the authenticity detector (fake/real) on the tampering-derived set.

    uv run python -m ml_training.train_authenticity --data-dir ml_training/data/authenticity \
        --epochs 12 --batch-size 32 --out weights/

Uses GEOMETRIC-ONLY train augs (no JPEG-quality jitter, no blur): compression/blur
augmentation would erase the forensic artifacts the detector must learn. Saves
``weights/authenticity_efficientnet_b0.pt`` + ``weights/authenticity_config.json``.
"""

from __future__ import annotations

import argparse

from ml_training.models import add_train_args, run_training, spec_from_args
from ml_training.models.backbone import make_auth_train_transform, make_transforms

AUTHENTICITY_CLASSES = ["fake", "real"]  # alphabetical, must match serving config


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Train the fake/real authenticity detector.")
    add_train_args(parser)
    args = parser.parse_args(argv)
    spec = spec_from_args(
        args,
        name="authenticity",
        classes=AUTHENTICITY_CLASSES,
        manifest_name="manifest_auth.csv",
        label_column="label",
        train_transform=make_auth_train_transform(size=args.input_size),
        eval_transform=make_transforms(train=False, size=args.input_size),
    )
    run_training(spec)


if __name__ == "__main__":
    main()
