"use client";

import { useState } from "react";

import { DicomBadge, KindChip, ModalityChip } from "@/components/document-chips";
import type { CaseDocument } from "@/lib/types-specialist";

/**
 * Imaging documents render inline: DICOM uploads always have a server-side
 * PNG preview, so anything without one is a browser-renderable PNG/JPEG and
 * the original file is shown directly.
 */
function imageSrc(doc: CaseDocument): string {
  return doc.has_preview ? `/api/documents/${doc.id}/preview` : `/api/documents/${doc.id}/file`;
}

interface ImagePanelProps {
  documents: CaseDocument[];
}

export function ImagePanel({ documents }: ImagePanelProps) {
  const [enlarged, setEnlarged] = useState<CaseDocument | null>(null);

  const imaging = documents.filter((doc) => doc.kind === "imaging");
  const supporting = documents.filter((doc) => doc.kind !== "imaging");

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
      <h2 className="text-sm font-semibold text-slate-900">
        Imaging documents ({imaging.length})
      </h2>

      {imaging.length === 0 ? (
        <p className="mt-2 text-sm text-slate-500">No imaging documents attached.</p>
      ) : (
        <ul className="mt-3 space-y-4">
          {imaging.map((doc) => (
            <li key={doc.id} className="overflow-hidden rounded-md border border-slate-200">
              <button
                type="button"
                onClick={() => setEnlarged(doc)}
                title="Click to enlarge"
                className="block w-full bg-slate-950 focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                {/* Same-origin API route with cookie auth; next/image's optimizer cannot fetch it. */}
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={imageSrc(doc)}
                  alt={`Imaging document ${doc.filename}`}
                  className="mx-auto max-h-80 w-auto transition-transform duration-150 hover:scale-[1.02]"
                />
              </button>
              <div className="flex flex-wrap items-center gap-2 border-t border-slate-200 bg-slate-50 px-3 py-2 text-sm">
                <span className="min-w-0 flex-1 truncate text-slate-900">{doc.filename}</span>
                <ModalityChip modality={doc.modality} />
                <DicomBadge hasPreview={doc.has_preview} />
                <a
                  href={`/api/documents/${doc.id}/file`}
                  download
                  className="text-blue-700 hover:underline"
                >
                  Download
                </a>
              </div>
            </li>
          ))}
        </ul>
      )}

      <h2 className="mt-6 text-sm font-semibold text-slate-900">
        Supporting documents ({supporting.length})
      </h2>
      {supporting.length === 0 ? (
        <p className="mt-2 text-sm text-slate-500">No supporting documents attached.</p>
      ) : (
        <ul className="mt-3 space-y-2">
          {supporting.map((doc) => (
            <li
              key={doc.id}
              className="flex flex-wrap items-center gap-2 rounded-md border border-slate-200 bg-slate-50 px-4 py-2.5 text-sm"
            >
              <span className="min-w-0 flex-1 truncate text-slate-900">{doc.filename}</span>
              <KindChip kind={doc.kind} />
              <a
                href={`/api/documents/${doc.id}/file`}
                download
                className="text-blue-700 hover:underline"
              >
                Download
              </a>
            </li>
          ))}
        </ul>
      )}

      {enlarged !== null ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={`Enlarged view of ${enlarged.filename}`}
          onClick={() => setEnlarged(null)}
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/85 p-6"
        >
          <button
            type="button"
            onClick={() => setEnlarged(null)}
            className="absolute right-4 top-4 rounded-md bg-white/10 px-3 py-1.5 text-sm font-medium text-white hover:bg-white/20"
          >
            Close
          </button>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={imageSrc(enlarged)}
            alt={`Enlarged imaging document ${enlarged.filename}`}
            className="max-h-full max-w-full scale-100 transition-transform duration-200"
          />
        </div>
      ) : null}
    </section>
  );
}
