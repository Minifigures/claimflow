"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { LeanBadge } from "@/components/agent/lean-badge";
import { RecommendationBadge } from "@/components/agent/recommendation-badge";
import { PortalShell } from "@/components/portal-shell";
import { ReportStatusBadge } from "@/components/status-badge";
import { ApiError, apiFetch } from "@/lib/api-client";
import { formatConfidence, type AgentQueueItem } from "@/lib/types-agent";

const POLL_INTERVAL_MS = 5000;
const QUEUE_PATH = "/api/agent/queue";

export default function AgentPortalPage() {
  const router = useRouter();
  const [items, setItems] = useState<AgentQueueItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    const load = async () => {
      try {
        const queue = await apiFetch<AgentQueueItem[]>(QUEUE_PATH);
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
    <PortalShell title="Adjudication queue" subtitle="Claims awaiting the final decision">
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
                Claimant
              </th>
              <th scope="col" className="px-4 py-3 text-left font-semibold text-slate-600">
                Summary
              </th>
              <th scope="col" className="px-4 py-3 text-left font-semibold text-slate-600">
                Specialist rec.
              </th>
              <th scope="col" className="px-4 py-3 text-left font-semibold text-slate-600">
                Lean
              </th>
              <th scope="col" className="px-4 py-3 text-left font-semibold text-slate-600">
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
                <td colSpan={8} className="px-4 py-12 text-center text-slate-400">
                  Loading...
                </td>
              </tr>
            ) : null}
            {items !== null && items.length === 0 ? (
              <tr>
                <td colSpan={8} className="px-4 py-12 text-center text-slate-500">
                  No claims awaiting adjudication.
                </td>
              </tr>
            ) : null}
            {(items ?? []).map((item) => (
              <tr
                key={item.claim_id}
                onClick={() => router.push(`/agent/cases/${item.claim_id}`)}
                className="cursor-pointer hover:bg-slate-50"
              >
                <td className="px-4 py-3 font-mono">
                  <Link
                    href={`/agent/cases/${item.claim_id}`}
                    onClick={(event) => event.stopPropagation()}
                    className="text-blue-700 hover:underline"
                  >
                    {item.claim_ref}
                  </Link>
                </td>
                <td className="px-4 py-3 capitalize text-slate-700">{item.claim_type}</td>
                <td className="px-4 py-3 text-slate-700">{item.claimant}</td>
                <td className="px-4 py-3">
                  <ReportStatusBadge status={item.summary_status} />
                </td>
                <td className="px-4 py-3">
                  <RecommendationBadge recommendation={item.specialist_recommendation} />
                </td>
                <td className="px-4 py-3">
                  <LeanBadge lean={item.recommendation_lean} />
                </td>
                <td className="px-4 py-3 tabular-nums text-slate-700">
                  {formatConfidence(item.confidence)}
                </td>
                <td className="px-4 py-3">
                  {item.requires_mandatory_review === true ? (
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
