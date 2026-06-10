"use client";

import { useEffect, useState } from "react";

import { PortalShell } from "@/components/portal-shell";
import { ReportStatusBadge, VerdictBadge } from "@/components/status-badge";
import { ApiError, apiFetch } from "@/lib/api-client";
import type { QueueItem } from "@/lib/types";

const POLL_INTERVAL_MS = 2000;
const QUEUE_PATH = "/api/specialist/queue?stage=imaging";

const MODALITY_LABELS: Record<string, string> = {
  xray: "X-Ray",
  ct: "CT",
  mri: "MRI",
};

function modalityLabel(modality: string | null): string {
  if (modality === null) {
    return "Unknown";
  }
  return MODALITY_LABELS[modality] ?? modality.toUpperCase();
}

export default function ImagingPortalPage() {
  const [items, setItems] = useState<QueueItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    const load = async () => {
      try {
        const queue = await apiFetch<QueueItem[]>(QUEUE_PATH);
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
          {updatedAt ? `Last updated ${updatedAt}` : "Loading queue..."}
        </p>
        {error ? (
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
            {items !== null && items.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-12 text-center text-slate-500">
                  No claims waiting for imaging review.
                </td>
              </tr>
            ) : null}
            {items === null ? (
              <tr>
                <td colSpan={6} className="px-4 py-12 text-center text-slate-400">
                  Loading...
                </td>
              </tr>
            ) : null}
            {(items ?? []).map((item) => (
              <tr key={item.claim_id} className="hover:bg-slate-50">
                <td className="px-4 py-3 font-mono text-slate-900">{item.claim_ref}</td>
                <td className="px-4 py-3 text-slate-700">{item.claim_type}</td>
                <td className="px-4 py-3">
                  <ReportStatusBadge status={item.report_status} />
                </td>
                <td className="px-4 py-3 text-slate-700">{modalityLabel(item.modality)}</td>
                <td className="px-4 py-3">
                  <VerdictBadge verdict={item.authenticity_verdict} />
                </td>
                <td className="px-4 py-3">
                  {item.requires_mandatory_review ? (
                    <span className="inline-flex items-center rounded-full bg-red-50 px-2 py-0.5 text-xs font-semibold text-red-700 ring-1 ring-inset ring-red-200">
                      Required
                    </span>
                  ) : (
                    <span className="text-slate-400">Not required</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </PortalShell>
  );
}
