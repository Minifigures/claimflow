"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { PortalShell } from "@/components/portal-shell";
import { MandatoryReviewFlag } from "@/components/specialist/verdict-badge";
import { ReportStatusBadge, VerdictBadge } from "@/components/status-badge";
import { ApiError, apiFetch } from "@/lib/api-client";
import type { Modality } from "@/lib/types";
import type { ImagingQueueItem } from "@/lib/types-specialist";

const POLL_INTERVAL_MS = 5000;
const QUEUE_PATH = "/api/specialist/queue?stage=imaging";

const MODALITY_LABELS: Record<Modality, string> = {
  xray: "X-Ray",
  ct: "CT",
  mri: "MRI",
};

function modalityLabel(modality: Modality | null): string {
  return modality !== null ? MODALITY_LABELS[modality] : "Unknown";
}

export default function ImagingQueuePage() {
  const router = useRouter();
  const [items, setItems] = useState<ImagingQueueItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    const load = async () => {
      try {
        const queue = await apiFetch<ImagingQueueItem[]>(QUEUE_PATH);
        if (!active) {
          return;
        }
        setItems(queue);
        setError(null);
        setUpdatedAt(new Date().toLocaleTimeString());
      } catch (err) {
        if (!active) {
          return;
        }
        setError(err instanceof ApiError ? err.detail : "Unable to reach the API.");
      }
    };

    void load();
    const timer = setInterval(() => void load(), POLL_INTERVAL_MS);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);

  return (
    <PortalShell title="Imaging review queue" subtitle="Claims awaiting imaging analysis review">
      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm text-slate-500">
          {updatedAt !== null ? `Last updated ${updatedAt}` : "Loading queue..."}
        </p>
        {error !== null ? (
          <p role="alert" className="text-sm text-red-700">
            {error}
          </p>
        ) : null}
      </div>

      <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
        <table className="min-w-full divide-y divide-slate-200 text-sm">
          <thead className="bg-slate-50">
            <tr>
              <th scope="col" className="px-4 py-3 text-left font-semibold text-slate-600">
                Claim
              </th>
              <th scope="col" className="px-4 py-3 text-left font-semibold text-slate-600">
                Type
              </th>
              <th scope="col" className="px-4 py-3 text-left font-semibold text-slate-600">
                Claimant
              </th>
              <th scope="col" className="px-4 py-3 text-left font-semibold text-slate-600">
                Report
              </th>
              <th scope="col" className="px-4 py-3 text-left font-semibold text-slate-600">
                Modality
              </th>
              <th scope="col" className="px-4 py-3 text-left font-semibold text-slate-600">
                Authenticity
              </th>
              <th scope="col" className="px-4 py-3 text-left font-semibold text-slate-600">
                Mandatory review
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {items === null ? (
              <tr>
                <td colSpan={7} className="px-4 py-12 text-center text-slate-400">
                  Loading...
                </td>
              </tr>
            ) : null}
            {items !== null && items.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-4 py-12 text-center text-slate-500">
                  No claims waiting for imaging review.
                </td>
              </tr>
            ) : null}
            {(items ?? []).map((item) => (
              <tr
                key={item.claim_id}
                onClick={() => router.push(`/imaging/cases/${item.claim_id}`)}
                className="cursor-pointer hover:bg-slate-50"
              >
                <td className="px-4 py-3 font-mono">
                  <Link
                    href={`/imaging/cases/${item.claim_id}`}
                    onClick={(event) => event.stopPropagation()}
                    className="text-blue-700 hover:underline"
                  >
                    {item.claim_ref}
                  </Link>
                </td>
                <td className="px-4 py-3 capitalize text-slate-700">{item.claim_type}</td>
                <td className="px-4 py-3 text-slate-700">{item.claimant}</td>
                <td className="px-4 py-3">
                  <ReportStatusBadge status={item.report_status} />
                </td>
                <td className="px-4 py-3 text-slate-700">{modalityLabel(item.modality)}</td>
                <td className="px-4 py-3">
                  <VerdictBadge verdict={item.authenticity_verdict} />
                </td>
                <td className="px-4 py-3">
                  <MandatoryReviewFlag required={item.requires_mandatory_review} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </PortalShell>
  );
}
