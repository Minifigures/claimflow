# Seed asset provenance

`clean_xray.png`, `clean_ct.png`, `clean_mri.png`, and the base image inside
`tampered_xray.dcm` are radiology images from the ROCOv2 dataset
(Pelka et al., `eltorio/ROCOv2-radiology`, CC BY-NC-SA 4.0), used here as
non-commercial demo fixtures. `tampered_xray.dcm` additionally carries
deliberate copy-move + splice + double-JPEG manipulation and a contradicting
DICOM Modality tag — it exists to demonstrate the authenticity tripwire and is
NOT a real study.
