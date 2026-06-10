"""Tests for the tampering fake-generation pipeline (ml_training/datasets/tampering.py)."""

import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

from ml_training.datasets import tampering

SIZE = 256

EXPECTED_ASSETS = ["clean_ct.png", "clean_mri.png", "clean_xray.png", "tampered_xray.png"]


def _rng(seed: int = 0) -> np.random.Generator:
    return np.random.default_rng(seed)


@pytest.fixture()
def xray() -> np.ndarray:
    return tampering.synth_base_image("xray", _rng(7), size=SIZE)


@pytest.fixture()
def donor() -> np.ndarray:
    return tampering.synth_base_image("ct", _rng(8), size=SIZE)


@pytest.mark.parametrize(
    "op_name",
    ["copy_move", "inpaint_removal", "resample_artifacts", "double_jpeg", "final_resave"],
)
def test_single_image_ops_same_shape_uint8_and_modified(op_name: str, xray: np.ndarray) -> None:
    op = getattr(tampering, op_name)
    before = xray.copy()
    out = op(xray, _rng(11))
    assert out.shape == before.shape
    assert out.dtype == np.uint8
    assert not np.array_equal(out, before)
    assert np.array_equal(xray, before)  # input array is never mutated


def test_splice_same_shape_uint8_and_modified(xray: np.ndarray, donor: np.ndarray) -> None:
    before = xray.copy()
    out = tampering.splice(xray, donor, _rng(12))
    assert out.shape == before.shape
    assert out.dtype == np.uint8
    assert not np.array_equal(out, before)
    assert np.array_equal(xray, before)


def test_ops_reject_non_grayscale_input() -> None:
    bad = np.zeros((32, 32, 3), np.uint8)
    with pytest.raises(ValueError):
        tampering.copy_move(bad, _rng(0))


def test_apply_random_tampering_deterministic(xray: np.ndarray, donor: np.ndarray) -> None:
    out1, ops1 = tampering.apply_random_tampering(xray, donor, _rng(99))
    out2, ops2 = tampering.apply_random_tampering(xray, donor, _rng(99))
    assert ops1 == ops2
    assert np.array_equal(out1, out2)
    assert 1 <= len(ops1) <= 2
    assert len(set(ops1)) == len(ops1)
    assert "final_resave" not in ops1
    assert set(ops1) <= set(tampering._TAMPER_OP_NAMES)
    assert not np.array_equal(out1, xray)


def test_apply_random_tampering_varies_across_seeds(
    xray: np.ndarray, donor: np.ndarray
) -> None:
    combos = {
        tuple(tampering.apply_random_tampering(xray, donor, _rng(seed))[1])
        for seed in range(12)
    }
    assert len(combos) > 1
    assert all("final_resave" not in combo for combo in combos)


def test_final_resave_changes_bytes_preserves_shape(xray: np.ndarray) -> None:
    out = tampering.final_resave(xray, _rng(3))
    assert out.shape == xray.shape
    assert out.dtype == np.uint8
    assert out.tobytes() != xray.tobytes()


def test_synth_base_image_distinct_per_kind() -> None:
    images = {
        kind: tampering.synth_base_image(kind, _rng(42), size=SIZE)
        for kind in ("xray", "ct", "mri")
    }
    for img in images.values():
        assert img.shape == (SIZE, SIZE)
        assert img.dtype == np.uint8
    assert not np.array_equal(images["xray"], images["ct"])
    assert not np.array_equal(images["xray"], images["mri"])
    assert not np.array_equal(images["ct"], images["mri"])


def test_synth_base_image_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError):
        tampering.synth_base_image("ultrasound", _rng(0))  # type: ignore[arg-type]


def test_emit_seed_assets_writes_four_loadable_files(tmp_path: Path) -> None:
    written = tampering.emit_seed_assets(tmp_path, size=SIZE)
    assert sorted(p.name for p in written) == EXPECTED_ASSETS
    for path in written:
        assert path.exists()
        assert path.stat().st_size < 300_000
        img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        assert img is not None
        assert img.shape == (SIZE, SIZE)


def test_emit_seed_assets_deterministic(tmp_path: Path) -> None:
    first = tampering.emit_seed_assets(tmp_path / "a", size=128)
    second = tampering.emit_seed_assets(tmp_path / "b", size=128)
    for p1, p2 in zip(first, second, strict=True):
        assert p1.read_bytes() == p2.read_bytes()


def test_emit_seed_assets_cli_main(tmp_path: Path) -> None:
    backend = Path(tampering.__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ml_training.datasets.tampering",
            "--emit-seed-assets",
            "--out-dir",
            str(tmp_path),
            "--size",
            "128",
        ],
        cwd=backend,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    for name in EXPECTED_ASSETS:
        assert (tmp_path / name).exists()
