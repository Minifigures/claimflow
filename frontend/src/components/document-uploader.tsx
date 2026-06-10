"use client";

import { useState } from "react";

import { DicomBadge, KindChip, ModalityChip } from "@/components/document-chips";
import { UploadDropzone } from "@/components/upload-dropzone";
import { ApiError, apiUpload } from "@/lib/api-client";
import { formatBytes } from "@/lib/format";
import type { DocumentKind, DocumentOut, Modality } from "@/lib/types";

const KIND_OPTIONS: ReadonlyArray<{ value: DocumentKind; label: string }> = [
  { value: "imaging", label: "Imaging" },
  { value: "medical_record", label: "Medical record" },
  { value: "other", label: "Other" },
];

const MODALITY_OPTIONS: ReadonlyArray<{ value: Modality; label: string }> = [
  { value: "xray", label: "X-Ray" },
  { value: "ct", label: "CT" },
  { value: "mri", label: "MRI" },
];

interface StagedFile {
  localId: number;
  file: File;
  kind: DocumentKind;
  modality: Modality;
  uploading: boolean;
  error: string | null;
}

interface DocumentUploaderProps {
  claimId: number;
  uploaded: DocumentOut[];
  onUploaded: (doc: DocumentOut) => void;
  disabled?: boolean;
}

let nextLocalId = 1;

/**
 * Stages dropped/picked files with a per-file kind select (and a modality
 * select shown only for imaging), then uploads each one via
 * POST /api/documents/upload/{claim_id} multipart.
 */
export function DocumentUploader({
  claimId,
  uploaded,
  onUploaded,
  disabled = false,
}: DocumentUploaderProps) {
  const [staged, setStaged] = useState<StagedFile[]>([]);

  const addFiles = (files: File[]) => {
    setStaged((prev) => [
      ...prev,
      ...files.map((file) => ({
        localId: nextLocalId++,
        file,
        kind: "imaging" as DocumentKind,
        modality: "xray" as Modality,
        uploading: false,
        error: null,
      })),
    ]);
  };

  const updateStaged = (localId: number, patch: Partial<StagedFile>) => {
    setStaged((prev) =>
      prev.map((item) => (item.localId === localId ? { ...item, ...patch } : item)),
    );
  };

  const removeStaged = (localId: number) => {
    setStaged((prev) => prev.filter((item) => item.localId !== localId));
  };

  const uploadFile = async (item: StagedFile) => {
    updateStaged(item.localId, { uploading: true, error: null });
    const formData = new FormData();
    formData.append("file", item.file);
    formData.append("kind", item.kind);
    if (item.kind === "imaging") {
      formData.append("modality", item.modality);
    }
    try {
      const doc = await apiUpload<DocumentOut>(`/api/documents/upload/${claimId}`, formData);
      removeStaged(item.localId);
      onUploaded(doc);
    } catch (err) {
      updateStaged(item.localId, {
        uploading: false,
        error: err instanceof ApiError ? err.detail : "Upload failed. Please try again.",
      });
    }
  };

  const anyUploading = staged.some((item) => item.uploading);

  return (
    <div className="space-y-4">
      <UploadDropzone onFiles={addFiles} disabled={disabled || anyUploading} />

      {staged.length > 0 ? (
        <ul className="space-y-2">
          {staged.map((item) => (
            <li
              key={item.localId}
              className="rounded-md border border-slate-200 bg-white px-4 py-3"
            >
              <div className="flex flex-wrap items-center gap-3">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-slate-900">{item.file.name}</p>
                  <p className="text-xs text-slate-500">{formatBytes(item.file.size)}</p>
                </div>
                <label className="flex items-center gap-1.5 text-xs text-slate-600">
                  Kind
                  <select
                    value={item.kind}
                    disabled={disabled || item.uploading}
                    onChange={(event) =>
                      updateStaged(item.localId, { kind: event.target.value as DocumentKind })
                    }
                    className="rounded-md border border-slate-300 px-2 py-1 text-xs text-slate-900 focus:border-blue-600 focus:outline-none"
                  >
                    {KIND_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>
                {item.kind === "imaging" ? (
                  <label className="flex items-center gap-1.5 text-xs text-slate-600">
                    Modality
                    <select
                      value={item.modality}
                      disabled={disabled || item.uploading}
                      onChange={(event) =>
                        updateStaged(item.localId, { modality: event.target.value as Modality })
                      }
                      className="rounded-md border border-slate-300 px-2 py-1 text-xs text-slate-900 focus:border-blue-600 focus:outline-none"
                    >
                      {MODALITY_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : null}
                <button
                  type="button"
                  disabled={disabled || item.uploading}
                  onClick={() => void uploadFile(item)}
                  className="rounded-md bg-blue-700 px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-blue-800 disabled:opacity-50"
                >
                  {item.uploading ? "Uploading..." : "Upload"}
                </button>
                <button
                  type="button"
                  disabled={disabled || item.uploading}
                  onClick={() => removeStaged(item.localId)}
                  className="rounded-md border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-600 transition-colors hover:bg-slate-100 disabled:opacity-50"
                >
                  Remove
                </button>
              </div>
              {item.error ? (
                <p role="alert" className="mt-2 text-xs text-red-700">
                  {item.error}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}

      {uploaded.length > 0 ? (
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Uploaded</p>
          <ul className="mt-2 space-y-2">
            {uploaded.map((doc) => (
              <li
                key={doc.id}
                className="flex flex-wrap items-center gap-2 rounded-md border border-slate-200 bg-slate-50 px-4 py-2.5"
              >
                <span className="min-w-0 flex-1 truncate text-sm text-slate-900">
                  {doc.filename}
                </span>
                <span className="text-xs text-slate-500">{formatBytes(doc.size_bytes)}</span>
                <KindChip kind={doc.kind} />
                <ModalityChip modality={doc.modality} />
                <DicomBadge hasPreview={doc.has_preview} />
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
