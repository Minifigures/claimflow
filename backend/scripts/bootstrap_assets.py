"""Verify runtime binary assets at container start; re-fetch any that are invalid.

Hugging Face Space builds can deliver repo binaries in surprising states
(git-LFS pointers, xet pointers, partial materialization) depending on storage
class — outside our control and version-dependent. Instead of guessing storage
formats, every asset the app needs is validated by its content magic; anything
invalid is downloaded through the hub API, which resolves storage correctly.
Local and compose runs have real files on disk, so this is a no-op.

Runs before the seeder in the image CMD chain.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable


def _png(data: bytes) -> bool:
    return data.startswith(b"\x89PNG\r\n\x1a\n")


def _dicom(data: bytes) -> bool:
    return len(data) >= 132 and data[128:132] == b"DICM"


def _torch_zip(data: bytes) -> bool:
    return data.startswith(b"PK")


def _safetensors(data: bytes) -> bool:
    # 8-byte little-endian header length followed by a JSON header.
    return len(data) > 8 and data[8:9] == b"{"


REQUIRED: dict[str, Callable[[bytes], bool]] = {
    "seed-assets/clean_ct.png": _png,
    "seed-assets/clean_mri.png": _png,
    "seed-assets/clean_xray.png": _png,
    "seed-assets/tampered_xray.dcm": _dicom,
    "weights/modality_efficientnet_b0.pt": _torch_zip,
    "weights/authenticity_efficientnet_b0.pt": _torch_zip,
    "weights/all-MiniLM-L6-v2/model.safetensors": _safetensors,
}


def _invalid_assets() -> list[str]:
    bad: list[str] = []
    for rel, check in REQUIRED.items():
        path = Path(rel)
        if not path.is_file():
            print(f"[bootstrap] MISSING: {rel}", flush=True)
            bad.append(rel)
            continue
        with path.open("rb") as fh:
            head = fh.read(160)
        if not check(head):
            print(
                f"[bootstrap] INVALID: {rel} size={path.stat().st_size} "
                f"head={head[:24].hex()} text={head[:24]!r}",
                flush=True,
            )
            bad.append(rel)
    return bad


def _llm_lane_probe() -> None:
    """Log (never fail on) the live-LLM lane state: key presence and one ping."""
    key = os.environ.get("GEMINI_API_KEY", "")
    print(f"[bootstrap] GEMINI_API_KEY present={bool(key)} len={len(key)}", flush=True)
    if not key:
        return
    try:
        import httpx

        response = httpx.post(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-2.5-flash:generateContent",
            headers={"x-goog-api-key": key},
            json={
                "contents": [{"role": "user", "parts": [{"text": "ping"}]}],
                "generationConfig": {"maxOutputTokens": 16},
            },
            timeout=20,
        )
        print(f"[bootstrap] gemini ping status={response.status_code}", flush=True)
    except Exception as exc:
        print(f"[bootstrap] gemini ping failed: {exc!r}", flush=True)


def main() -> None:
    # Code fingerprints: lets the logs prove which build is actually running.
    import hashlib

    for code_file in ("app/ml/imaging/real.py", "scripts/seed.py"):
        digest = hashlib.sha256(Path(code_file).read_bytes()).hexdigest()[:12]
        print(f"[bootstrap] code {code_file} sha={digest}", flush=True)

    _llm_lane_probe()
    bad = _invalid_assets()
    if not bad:
        print("[bootstrap] all required assets valid; nothing to do", flush=True)
        return

    repo_id = os.environ.get("SPACE_ID")  # injected by Spaces, e.g. "Minifigures/claimflow-api"
    if repo_id is None:
        raise SystemExit(
            f"[bootstrap] {len(bad)} invalid assets and SPACE_ID unset; cannot resolve: {bad}"
        )

    # The image pins offline mode for serving; this one bootstrap step needs the hub.
    os.environ.pop("HF_HUB_OFFLINE", None)
    os.environ.pop("TRANSFORMERS_OFFLINE", None)
    from huggingface_hub import hf_hub_download

    for rel in bad:
        print(f"[bootstrap] fetching from hub: {rel}", flush=True)
        resolved = hf_hub_download(
            repo_id=repo_id, repo_type="space", filename=rel, force_download=True
        )
        Path(rel).write_bytes(Path(resolved).read_bytes())

    still_bad = _invalid_assets()
    if still_bad:
        raise SystemExit(f"[bootstrap] assets still invalid after hub fetch: {still_bad}")
    print(f"[bootstrap] repaired {len(bad)} assets from the hub", flush=True)


if __name__ == "__main__":
    main()
