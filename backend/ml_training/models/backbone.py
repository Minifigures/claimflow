"""EfficientNet-B0 backbone + transform recipes for both imaging models.

Grayscale strategy: the model keeps the standard 3-channel stem (so ImageNet
pretrained weights load unchanged); grayscale inputs are replicated 1->3ch inside the
transform (``Grayscale(num_output_channels=3)``).

Two train recipes:

- MODALITY (``make_transforms(train=True)``): geometric augs PLUS the source-confound
  killers (random JPEG-quality re-encode at q 60-95 and occasional gaussian blur),
  so the classifier cannot key on per-source compression/sharpness signatures.
- AUTHENTICITY (``AUTH_TRAIN_TRANSFORM`` / ``make_auth_train_transform``): geometric
  augs ONLY. JPEG/blur augs would erase exactly the forensic artifacts (double-JPEG
  ghosts, splice seams, resampling softness) the detector must learn.
"""

from __future__ import annotations

import io
import random

import timm
from PIL import Image
from torch import nn
from torchvision import transforms

ARCH = "efficientnet_b0"
IMAGENET_MEAN: tuple[float, float, float] = (0.485, 0.456, 0.406)
IMAGENET_STD: tuple[float, float, float] = (0.229, 0.224, 0.225)


def build_model(num_classes: int, pretrained: bool) -> nn.Module:
    """timm efficientnet_b0 with a fresh ``num_classes`` head (standard 3ch input)."""
    return timm.create_model(ARCH, pretrained=pretrained, num_classes=num_classes)


class JpegQualityJitter:
    """Re-encode the PIL image as JPEG at a random quality in [lo, hi] (default 60-95).

    Custom transform (torchvision has no JPEG aug for PIL inputs): in-memory re-encode
    via ``io.BytesIO`` so no temp files. Uses python's ``random`` (seeded by the shared
    ``set_seed``) for determinism.
    """

    def __init__(self, quality: tuple[int, int] = (60, 95)) -> None:
        if not (1 <= quality[0] <= quality[1] <= 100):
            raise ValueError(f"invalid quality range: {quality}")
        self.quality = quality

    def __call__(self, img: Image.Image) -> Image.Image:
        q = random.randint(self.quality[0], self.quality[1])
        buf = io.BytesIO()
        img.convert("L").save(buf, format="JPEG", quality=q)
        buf.seek(0)
        return Image.open(buf).convert("L")

    def __repr__(self) -> str:
        return f"{type(self).__name__}(quality={self.quality})"


def _to_3ch_tensor() -> list[object]:
    return [
        transforms.Grayscale(num_output_channels=3),
        transforms.ToTensor(),
        transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
    ]


def make_transforms(train: bool, size: int = 224) -> transforms.Compose:
    """MODALITY-model transforms (train recipe includes the confound killers)."""
    if not train:
        return transforms.Compose(
            [
                transforms.Resize(int(size * 256 / 224)),
                transforms.CenterCrop(size),
                *_to_3ch_tensor(),
            ]
        )
    return transforms.Compose(
        [
            transforms.RandomResizedCrop(size, scale=(0.8, 1.0)),
            transforms.RandomRotation(10),
            transforms.RandomHorizontalFlip(),
            transforms.ColorJitter(brightness=0.2, contrast=0.2),
            JpegQualityJitter((60, 95)),
            transforms.RandomApply(
                [transforms.GaussianBlur(kernel_size=5, sigma=(0.1, 1.5))], p=0.2
            ),
            *_to_3ch_tensor(),
        ]
    )


def make_auth_train_transform(size: int = 224) -> transforms.Compose:
    """AUTHENTICITY-model train transforms: geometric only (no JPEG/blur/photometric).

    Compression/blur augs would destroy the forensic evidence the detector learns.
    """
    return transforms.Compose(
        [
            transforms.RandomResizedCrop(size, scale=(0.8, 1.0)),
            transforms.RandomRotation(10),
            transforms.RandomHorizontalFlip(),
            *_to_3ch_tensor(),
        ]
    )


AUTH_TRAIN_TRANSFORM = make_auth_train_transform()
