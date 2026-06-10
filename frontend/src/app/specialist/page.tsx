"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { PortalShell } from "@/components/portal-shell";
import { formatPercent } from "@/components/specialist/report-meta";
import { MandatoryReviewFlag, RecommendationBadge } from "@/components/specialist/verdict-badge";
import { ReportStatusBadge } from "@/components/status-badge";
import { ApiError, apiFetch } from "@/lib/api-client";
import type { RecommendationQueueItem } from "@/lib/types-specialist";

const POLL_INTERVAL_MS = 5000;
const QUEUE_PATH = "/api/specialist/queue?stage=recommendation";

export default function SpecialistQueuePage() {
  const router = useRouter();
  const [items, setItems] = useState<RecommendationQueueItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    const load = async () => {
      try {
        const queue = await apiFetch<RecommendationQueueItem[]>(QUEUE_PATH);
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
    <PortalShell
      title="Specialist review queue"
      subtitle="Claims awaiting medical specialist review"
    >
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
                Note
              </th>
              <th scope="col" className="px-4 py-3 text-left font-semibold text-slate-600">
                Recommendation
              </th>
              <th scope="col" className="px-4 py-3 text-right font-semibold text-slate-600">
                Confidence
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
                  No claims waiting for specialist review.
                </td>
              </tr>
            ) : null}
            {(items ?? []).map((item) => (
              <tr
                key={item.claim_id}
                onClick={() => router.push(`/specialist/cases/${item.claim_id}`)}
                className="cursor-pointer hover:bg-slate-50"
              >
                <td className="px-4 py-3 font-mono">
                  <Link
                    href={`/specialist/cases/${item.claim_id}`}
                    onClick={(event) => event.stopPropagation()}
                    className="text-blue-700 hover:underline"
                  >
                    {item.claim_ref}
                  </Link>
                </td>
                <td className="px-4 py-3 capitalize text-slate-700">{item.claim_type}</td>
                <td className="px-4 py-3 text-slate-700">{item.claimant}</td>
                <td className="px-4 py-3">
                  <ReportStatusBadge status={item.note_status} />
                </td>
                <td className="px-4 py-3">
                  <RecommendationBadge recommendation={item.recommendation} />
                </td>
                <td className="px-4 py-3 text-right tabular-nums text-slate-700">
                  {formatPercent(item.confidence)}
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
