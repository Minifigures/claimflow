"""Materialize git-LFS pointer files at container start (Hugging Face Spaces).

The Space build context delivers some binary files as LFS pointers rather than
real bytes (storage-class dependent and not under our control). Anything the
app needs at runtime — seed assets, model weights — is scanned here; pointer
files are replaced with the real content via the hub API, which resolves them
reliably. Local and compose runs have real files on disk, so this is a no-op.

Runs before the seeder in the image CMD chain.
"""

from __future__ import annotations

import os
from pathlib import Path

POINTER_MAGIC = b"version https://git-lfs"
SCAN_DIRS = ("seed-assets", "weights")


def _pointer_files() -> list[Path]:
    found: list[Path] = []
    for scan in SCAN_DIRS:
        root = Path(scan)
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file():
                with path.open("rb") as fh:
                    if fh.read(len(POINTER_MAGIC)) == POINTER_MAGIC:
                        found.append(path)
    return found


def main() -> None:
    pointers = _pointer_files()
    if not pointers:
        print("[bootstrap] all binary assets are materialized; nothing to do", flush=True)
        return

    repo_id = os.environ.get("SPACE_ID")  # injected by Spaces, e.g. "Minifigures/claimflow-api"
    if repo_id is None:
        raise SystemExit(
            f"[bootstrap] {len(pointers)} LFS pointer files found but SPACE_ID is unset; "
            f"cannot resolve: {[str(p) for p in pointers]}"
        )

    # The image pins offline mode for serving; this one bootstrap step needs the hub.
    os.environ.pop("HF_HUB_OFFLINE", None)
    os.environ.pop("TRANSFORMERS_OFFLINE", None)
    from huggingface_hub import hf_hub_download

    for path in pointers:
        print(f"[bootstrap] resolving LFS pointer: {path}", flush=True)
        resolved = hf_hub_download(repo_id=repo_id, repo_type="space", filename=str(path))
        path.write_bytes(Path(resolved).read_bytes())
    print(f"[bootstrap] materialized {len(pointers)} files", flush=True)


if __name__ == "__main__":
    main()
