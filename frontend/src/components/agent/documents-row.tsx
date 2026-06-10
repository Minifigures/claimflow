import Image from "next/image";

import { DicomBadge, KindChip, ModalityChip } from "@/components/document-chips";
import { formatBytes } from "@/lib/format";
import type { DossierDocument } from "@/lib/types-agent";

/** Horizontal row of document cards: kind/modality chips, imaging preview thumbnails,
 * and direct download links to the stored files. */
export function DocumentsRow({ documents }: { documents: DossierDocument[] }) {
  if (documents.length === 0) {
    return <p className="text-sm text-slate-500">No documents on file for this claim.</p>;
  }

  return (
    <div className="flex gap-3 overflow-x-auto pb-1">
      {documents.map((doc) => (
        <div
          key={doc.id}
          className="w-56 shrink-0 rounded-md border border-slate-200 bg-slate-50 p-3"
        >
          {doc.has_preview ? (
            <Image
              src={`/api/documents/${doc.id}/preview`}
              alt={`Imaging preview for ${doc.filename}`}
              width={208}
              height={112}
              unoptimized
              className="h-28 w-full rounded object-cover ring-1 ring-slate-200"
            />
          ) : (
            <div className="flex h-28 w-full items-center justify-center rounded bg-slate-100 text-xs text-slate-400 ring-1 ring-slate-200">
              No preview
            </div>
          )}
          <p className="mt-2 truncate text-sm text-slate-900" title={doc.filename}>
            {doc.filename}
          </p>
          <div className="mt-1 flex flex-wrap items-center gap-1">
            <KindChip kind={doc.kind} />
            <ModalityChip modality={doc.modality} />
            <DicomBadge hasPreview={doc.has_preview} />
          </div>
          <div className="mt-2 flex items-center justify-between text-xs">
            <span className="text-slate-500">{formatBytes(doc.size_bytes)}</span>
            <a
              href={`/api/documents/${doc.id}/file`}
              className="font-medium text-blue-700 hover:underline"
            >
              Download
            </a>
          </div>
        </div>
      ))}
    </div>
  );
}
