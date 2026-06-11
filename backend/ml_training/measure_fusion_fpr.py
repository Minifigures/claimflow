"""Measure the fusion false-positive rate on clean, resaved real test images.

    uv run python -m ml_training.measure_fusion_fpr --weights-dir weights/ \
        --data-dir ml_training/data/authenticity --limit 500

Runs the full serving-path fusion (`app.ml.imaging.real.RealAnalyzer` — CNN plus
ELA/FFT/metadata heuristics, the exact code reviewers exercise) over the
``label=real`` rows of the authenticity test split and reports what fraction of
clean images land in each verdict band. The number fills the
"clean-resaved-real false positive rate" cell in docs/model-choices.md: these
images went through the same final JPEG resave as the fakes, so this is the
honest "how often do we hassle an innocent claimant" rate, not a softball.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from app.config import Settings
from app.ml.imaging.real import RealAnalyzer


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Fusion FPR on clean resaved real images.")
    parser.add_argument("--weights-dir", type=Path, default=Path("weights"))
    parser.add_argument("--data-dir", type=Path, default=Path("ml_training/data/authenticity"))
    parser.add_argument(
        "--limit", type=int, default=500, help="max real test images to score (0 = all)"
    )
    parser.add_argument(
        "--report", type=Path, default=Path("ml_training/data/fusion_fpr_report.json")
    )
    args = parser.parse_args(argv)

    manifest = args.data_dir / "manifest_auth.csv"
    if not manifest.exists():
        raise SystemExit(f"manifest not found: {manifest}")
    with manifest.open(newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["split"] == "test" and r["label"] == "real"]
    if args.limit:
        rows = rows[: args.limit]
    if not rows:
        raise SystemExit("no real test rows in the manifest")

    analyzer = RealAnalyzer(Settings(model_backend="real", weights_dir=args.weights_dir))

    verdicts: Counter[str] = Counter()
    risks: list[float] = []
    for i, row in enumerate(rows, 1):
        analysis = analyzer.analyze(
            args.data_dir / row["path"], declared_modality=None, dicom_meta=None
        )
        verdicts[analysis.authenticity_verdict] += 1
        risks.append(analysis.authenticity_risk)
        if i % 100 == 0:
            print(f"[fpr] scored {i}/{len(rows)}", flush=True)

    n = len(rows)
    fpr_suspicious = (verdicts["suspicious"] + verdicts["likely_fraudulent"]) / n
    fpr_fraudulent = verdicts["likely_fraudulent"] / n
    report = {
        "n_clean_real_test": n,
        "verdict_counts": dict(verdicts),
        "fpr_to_suspicious_or_worse": round(fpr_suspicious, 4),
        "fpr_to_likely_fraudulent": round(fpr_fraudulent, 4),
        "mean_risk": round(sum(risks) / n, 4),
        "max_risk": round(max(risks), 4),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    print(f"report written: {args.report}")


if __name__ == "__main__":
    main()
