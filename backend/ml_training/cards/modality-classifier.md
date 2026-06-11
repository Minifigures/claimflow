# Model card — modality classifier

**Model.** EfficientNet-B0 (timm, ImageNet init, standard 3-channel stem; grayscale
inputs replicated 1→3ch in the transform), 3-class head: `ct` / `mri` / `xray`.
Weights: `backend/weights/modality_efficientnet_b0.pt` + `modality_config.json`
(classes, input size, normalization, fitted temperature). Served by
`backend/app/ml/imaging/real.py` under `MODEL_BACKEND=real`.

## Intended use

Routing and cross-checking only: classify the modality of a claimant-uploaded
medical image so the diagnostic-report draft addresses the right study type, and
flag disagreement with the DICOM Modality tag as a fraud signal. The confidence
that gates mandatory human review is the temperature-scaled probability.

**Not** a diagnostic device. No clinical claim is made or implied; every output
is reviewed by an imaging specialist before anything downstream happens.

## Training data

15,000 images from ROCOv2 (`eltorio/ROCOv2-radiology`), 5,000 per class, labels
derived from UMLS CUI tags; only rows matching exactly one modality CUI are kept
(`ml_training/datasets/build_datasets.py`). Single-source by design: drawing all
three classes from one corpus avoids the per-class source confound (resolution,
scanner, compression signatures) that turns stitched-together datasets into
accidental source detectors. Train recipe adds random JPEG-quality re-encoding
(q 60–95) and occasional Gaussian blur on top of geometric augs to kill the
remaining compression/sharpness shortcuts. Split 80/10/10, seed 42.

## Metrics (held-out test split)

| Metric | Value |
|---|---|
| Accuracy | 0.942 (n=1,543) |
| Macro-F1 | 0.943 |
| Per-class precision/recall | ct 0.92/0.93 · mri 0.91/0.92 · xray 1.00/0.98 |
| ECE before → after temperature scaling | 0.030 → 0.016 |
| Fitted temperature | 1.90 |

Most confusion is ct↔mri (76 of the 90 errors); xray is near-perfect. Trained
2026-06-10, 12 epochs, seed 42, best epoch selected by val macro-F1 (0.938 at
epoch 8).

Reproduce: `uv run python -m ml_training.evaluate --weights-dir weights/
--data-dir ml_training/data --report` (full report at
`ml_training/data/eval_report.json`, not committed).

## Caveats and limitations

- **Domain shift is unmeasured.** ROCOv2 is publication-figure radiology; real
  claimant uploads (phone photos of films, portal exports, cropped screenshots)
  look different. The confound-killer augs narrow this gap but do not close it.
- Three classes only; ultrasound, PET, mammography and anything else will be
  forced into the nearest of ct/mri/xray — the calibrated confidence (and the
  mandatory-review gate it feeds) is the only guardrail.
- Calibration was fitted on the validation split of the same corpus; the
  temperature is not guaranteed to transfer under domain shift.
- CUI-derived labels are weak labels; ROCOv2 label noise propagates.
