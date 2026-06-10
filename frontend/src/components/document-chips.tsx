import { BADGE_BASE } from "@/components/status-badge";
import type { DocumentKind, Modality } from "@/lib/types";

const KIND_LABELS: Record<DocumentKind, string> = {
  imaging: "Imaging",
  medical_record: "Medical record",
  other: "Other",
};

const MODALITY_LABELS: Record<Modality, string> = {
  xray: "X-Ray",
  ct: "CT",
  mri: "MRI",
};

export function KindChip({ kind }: { kind: DocumentKind }) {
  return (
    <span className={`${BADGE_BASE} bg-slate-100 text-slate-700 ring-slate-200`}>
      {KIND_LABELS[kind] ?? kind}
    </span>
  );
}

export function ModalityChip({ modality }: { modality: Modality | null }) {
  if (modality === null) {
    return null;
  }
  return (
    <span className={`${BADGE_BASE} bg-blue-50 text-blue-700 ring-blue-200`}>
      {MODALITY_LABELS[modality] ?? modality}
    </span>
  );
}

/** Shown when the backend generated a preview, which only happens for DICOM files. */
export function DicomBadge({ hasPreview }: { hasPreview: boolean }) {
  if (!hasPreview) {
    return null;
  }
  return (
    <span className={`${BADGE_BASE} bg-emerald-50 text-emerald-700 ring-emerald-200`}>DICOM</span>
  );
}
