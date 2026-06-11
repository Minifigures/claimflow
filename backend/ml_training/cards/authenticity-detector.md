# Model card — authenticity detector (CNN signal)

**Model.** EfficientNet-B0 (timm, ImageNet init), 2-class head: `fake` / `real`.
Weights: `backend/weights/authenticity_efficientnet_b0.pt` +
`authenticity_config.json`. Served by `backend/app/ml/imaging/real.py` under
`MODEL_BACKEND=real` — never alone: the CNN's calibrated fake-probability is one
signal in a fusion whose weight is capped at 0.40, alongside deterministic
ELA-residual localization, FFT periodicity, and DICOM-metadata consistency.
A DICOM Modality tag contradicting the predicted modality hard-overrides the
verdict to at least `suspicious` regardless of the model.

## Intended use

A tripwire, not a verdict. The fusion buckets images into
`authentic` / `suspicious` / `likely_fraudulent` (bands at 0.33 / 0.66); anything
non-authentic routes to an imaging specialist with per-signal findings. No claim
is auto-rejected on this output.

## Training data

15,000 real/fake pairs derived from clean ROCOv2 images
(`ml_training/datasets/tampering.py` + `build_datasets.py`). Fakes apply 1–2
randomized ops per sample: copy-move, donor splice, inpaint removal, resampling
artifacts, double-JPEG. Two hygiene rules carry the dataset: **both** classes
pass through an identical randomized final JPEG re-save as the last step (so the
detector cannot learn "fake == recompressed"), and splits are keyed on the
source image so real/fake derivatives of one source never straddle splits.
Train augs are geometric-only — compression/blur augs would erase exactly the
forensic evidence being learned. Split 80/10/10, seed 42.

## Metrics (held-out test split)

| Metric | Value |
|---|---|
| Accuracy | 0.696 (n=3,086) |
| Macro-F1 | 0.694 |
| Per-class precision/recall (fake, real) | fake 0.73/0.61 · real 0.67/0.78 |
| ECE before → after temperature scaling | 0.122 → 0.028 |
| Fitted temperature | 2.28 |
| Clean-resaved-real FPR through the full fusion (to suspicious or worse) | 0.298 to suspicious, 0.000 to likely_fraudulent (n=500; mean risk 0.29, max 0.64) |

These numbers are deliberately modest: the identical-final-resave hygiene rule
closes the "fake == recompressed" shortcut, so ~0.70 reflects real manipulation
evidence (a 0.99 would indicate a dataset leak). Fake recall of 0.61 standalone
is why the CNN is one capped signal among four, never the verdict. Trained
2026-06-11, 12 epochs, seed 42, best epoch by val macro-F1 (0.709 at epoch 12).

Reproduce: `uv run python -m ml_training.evaluate` (CNN metrics) and
`uv run python -m ml_training.measure_fusion_fpr` (fusion FPR, serving code path).

## Caveats and limitations — read these first

- **Tampering-family-only detection.** This model detects the five generated
  manipulation families above. Performance on unseen generators — modern
  diffusion inpainting, GAN synthesis, vendor-specific editing tools — is
  **unverified and should be presumed poor**. The public datasets that would
  test this (CTForensics, MedForensics) are unreleased.
- **Domain shift is unmeasured** (same ROCOv2 base-image caveat as the modality
  card).
- Not a clinical or forensic instrument; produces a screening signal for human
  review. A 2025 RSNA study put radiologists near 75% accuracy at spotting
  AI-manipulated images, so the human backstop is real but also fallible — the
  production posture is provenance (PACS audit-trail correlation, C2PA) over
  pixel forensics.
