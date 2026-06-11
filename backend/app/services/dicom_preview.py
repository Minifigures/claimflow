"""DICOM intake: PHI de-identification at rest, safe metadata, PNG preview.

Uploaded studies are rewritten in place with every denylisted PHI tag blanked
before anything else reads them; only a small allowlisted metadata dict (with
the study date truncated to a year) is ever persisted alongside the document.
"""

from pathlib import Path

import pydicom
from PIL import Image
from pydicom.datadict import tag_for_keyword

PHI_DENYLIST: tuple[str, ...] = (
    "PatientName",
    "PatientID",
    "PatientBirthDate",
    "OtherPatientIDs",
    "OtherPatientNames",
    "PatientAddress",
    "PatientTelephoneNumbers",
    "InstitutionName",
    "InstitutionAddress",
    "ReferringPhysicianName",
    "PerformingPhysicianName",
    "OperatorsName",
    "AccessionNumber",
)

_SAFE_STR_FIELDS = ("Modality", "Manufacturer", "ManufacturerModelName", "SoftwareVersions")
_SAFE_INT_FIELDS = ("Rows", "Columns")

_MAGIC_PNG = b"\x89PNG\r\n\x1a\n"
_MAGIC_JPEG = b"\xff\xd8\xff"
_MAGIC_PDF = b"%PDF-"
_MAGIC_DICM = b"DICM"


def sniff_kind(path: Path, mime: str) -> str:
    """Classify a stored upload by magic bytes.

    The client extension and mime (passed for logging/diagnostics only) are never
    trusted alone. Returns 'dicom' | 'png' | 'jpeg' | 'pdf'.
    """
    with path.open("rb") as fh:
        head = fh.read(132)
    if len(head) >= 132 and head[128:132] == _MAGIC_DICM:
        return "dicom"
    if head.startswith(_MAGIC_PNG):
        return "png"
    if head.startswith(_MAGIC_JPEG):
        return "jpeg"
    if head.startswith(_MAGIC_PDF):
        return "pdf"
    raise ValueError("unsupported file type")


def process_dicom(path: Path, *, rewrite: bool = True) -> tuple[dict, str | None]:
    """De-identify a DICOM file *in place*, extract safe metadata, render a preview.

    Every denylisted PHI tag present is blanked and the file rewritten on disk
    before returning, so the study is de-identified at rest. Returns
    (meta, preview_path); preview_path is None when pixel data is missing or
    unreadable. Raises ValueError for non-DICOM input.

    ``rewrite=False`` skips the at-rest rewrite for callers whose input is
    synthetic and PHI-free by construction (the demo seeder); metadata and
    preview extraction behave identically.
    """
    try:
        ds = pydicom.dcmread(path, force=True)
    except Exception as exc:
        raise ValueError("not a valid DICOM file") from exc
    if len(ds) == 0 or "TransferSyntaxUID" not in ds.file_meta:
        raise ValueError("not a valid DICOM file")

    for keyword in PHI_DENYLIST:
        tag = tag_for_keyword(keyword)
        if tag is not None and tag in ds:
            ds[tag].value = ""

    meta: dict[str, object] = {}
    for keyword in _SAFE_STR_FIELDS:
        value = ds.get(keyword)
        if value is not None and str(value):
            meta[keyword] = str(value)
    for keyword in _SAFE_INT_FIELDS:
        value = ds.get(keyword)
        if value is not None:
            meta[keyword] = int(value)
    study_date = str(ds.get("StudyDate") or "")
    if study_date:
        meta["StudyDate"] = study_date[:4]  # year only — never persist the full date
    pixel_spacing = ds.get("PixelSpacing")
    if pixel_spacing is not None:
        meta["PixelSpacing"] = str(pixel_spacing)

    if rewrite:
        ds.save_as(path, enforce_file_format=True)  # rewrite: de-identified at rest
        with path.open("rb") as fh:
            head = fh.read(132)
        if len(head) < 132 or head[128:132] != _MAGIC_DICM:
            import logging

            logging.getLogger("claimflow.dicom").error(
                "post-rewrite verification failed for %s: size=%d head=%s",
                path,
                path.stat().st_size,
                head[:16].hex(),
            )

    preview = path.with_suffix(".png")
    try:
        arr = ds.pixel_array.astype("float32")
    except Exception:
        return meta, None
    lo, hi = float(arr.min()), float(arr.max())
    arr = (arr - lo) / (hi - lo) * 255.0 if hi > lo else arr * 0.0
    arr8 = arr.astype("uint8")
    if arr8.ndim == 3:  # color or multi-frame: preview the first plane
        arr8 = arr8[..., 0] if arr8.shape[-1] in (3, 4) else arr8[0]
    Image.fromarray(arr8).save(preview)
    return meta, str(preview)
