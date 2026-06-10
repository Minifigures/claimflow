"""Streaming dry-run against ROCOv2: estimate per-modality counts from CUI tags.

Validates the dataset-plan premise (CT/X-ray/MRI derivable from `cui`) before
committing to the 18.6GB download. Streams a sample, never the full set.

Usage: uv run python -m ml_training.datasets.probe_rocov2 [--sample 3000]
Writes: ml_training/data/cui_probe.json
"""

import argparse
import json
from collections import Counter
from pathlib import Path

MODALITY_CUIS = {
    "C0040405": "CT",
    "C0024485": "MRI",
    "C1306645": "XRAY_plain",
    "C0034571": "XRAY_radiography",
    "C0041618": "ULTRASOUND",
    "C0032743": "PET",
    "C0002978": "ANGIOGRAPHY",
    "C0026606": "MAMMOGRAPHY",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=3000)
    args = parser.parse_args()

    from datasets import load_dataset

    ds = load_dataset("eltorio/ROCOv2-radiology", split="train", streaming=True)

    cui_counts: Counter[str] = Counter()
    modality_counts: Counter[str] = Counter()
    multi_modality = 0
    no_modality = 0
    seen = 0

    for row in ds.take(args.sample):
        seen += 1
        cuis = row.get("cui") or []
        if isinstance(cuis, str):
            cuis = [cuis]
        cui_counts.update(cuis)
        hits = {MODALITY_CUIS[c] for c in cuis if c in MODALITY_CUIS}
        hits = {"XRAY" if h.startswith("XRAY") else h for h in hits}
        if not hits:
            no_modality += 1
        elif len(hits) > 1:
            multi_modality += 1
        else:
            modality_counts[next(iter(hits))] += 1

    out = {
        "sampled": seen,
        "single_modality_counts": dict(modality_counts),
        "multi_modality_rows": multi_modality,
        "no_modality_match_rows": no_modality,
        "top_30_cuis": cui_counts.most_common(30),
        "projection": {
            label: round(count / seen * 79789)
            for label, count in modality_counts.items()
        },
    }
    out_path = Path(__file__).resolve().parents[1] / "data" / "cui_probe.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))
    print(f"\nwritten: {out_path}")
    ct = out["projection"].get("CT", 0)
    mri = out["projection"].get("MRI", 0)
    xray = out["projection"].get("XRAY", 0)
    verdict = "VIABLE" if min(ct, mri, xray) >= 5000 else "CHECK FALLBACK"
    print(f"verdict: {verdict} (projected per-class: CT={ct} MRI={mri} XRAY={xray}; need >=5000 each)")


if __name__ == "__main__":
    main()
