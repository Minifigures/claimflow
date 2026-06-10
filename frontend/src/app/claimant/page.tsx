"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ClaimStateBadge } from "@/components/claim-state-badge";
import { PortalShell } from "@/components/portal-shell";
import { ApiError, apiFetch } from "@/lib/api-client";
import { formatCurrency, formatDateTime } from "@/lib/format";
import type { ClaimOut } from "@/lib/types";

export default function ClaimantPortalPage() {
  const [claims, setClaims] = useState<ClaimOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    const load = async () => {
      try {
        const data = await apiFetch<ClaimOut[]>("/api/claims");
        if (!active) {
          return;
        }
        setClaims(data);
        setError(null);
      } catch (err) {
        if (!active) {
          return;
        }
        setError(err instanceof ApiError ? err.detail : "Unable to reach the API.");
      }
    };

    void load();
    return () => {
      active = false;
    };
  }, []);

  return (
    <PortalShell title="Your claims" subtitle="Track and manage your medical claims">
      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm text-slate-500">
          {claims === null ? "Loading claims..." : `${claims.length} claim(s)`}
        </p>
        <Link
          href="/claimant/new"
          className="rounded-md bg-blue-700 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-blue-800"
        >
          New claim
        </Link>
      </div>

      {error ? (
        <p role="alert" className="mb-4 text-sm text-red-700">
          {error}
        </p>
      ) : null}

      {claims !== null && claims.length === 0 ? (
        <div className="rounded-lg border border-dashed border-slate-300 bg-white px-6 py-16 text-center">
          <p className="text-base font-medium text-slate-700">No claims yet.</p>
          <p className="mt-1 text-sm text-slate-500">
            Start a new claim to submit your medical documents for assessment.
          </p>
          <Link
            href="/claimant/new"
            className="mt-4 inline-block rounded-md bg-blue-700 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-blue-800"
          >
            Start a claim
          </Link>
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
          <table className="min-w-full divide-y divide-slate-200 text-sm">
            <thead className="bg-slate-50">
              <tr>
                <th scope="col" className="px-4 py-3 text-left font-semibold text-slate-600">
                  Reference
                </th>
                <th scope="col" className="px-4 py-3 text-left font-semibold text-slate-600">
                  Type
                </th>
                <th scope="col" className="px-4 py-3 text-right font-semibold text-slate-600">
                  Amount
                </th>
                <th scope="col" className="px-4 py-3 text-left font-semibold text-slate-600">
                  Status
                </th>
                <th scope="col" className="px-4 py-3 text-left font-semibold text-slate-600">
                  Updated
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {claims === null ? (
                <tr>
                  <td colSpan={5} className="px-4 py-12 text-center text-slate-400">
                    Loading...
                  </td>
                </tr>
              ) : null}
              {(claims ?? []).map((claim) => (
                <tr key={claim.id} className="hover:bg-slate-50">
                  <td className="px-4 py-3 font-mono">
                    <Link
                      href={`/claimant/claims/${claim.id}`}
                      className="text-blue-700 hover:underline"
                    >
                      {claim.claim_ref}
                    </Link>
                  </td>
                  <td className="px-4 py-3 capitalize text-slate-700">{claim.claim_type}</td>
                  <td className="px-4 py-3 text-right tabular-nums text-slate-700">
                    {formatCurrency(claim.amount_claimed)}
                  </td>
                  <td className="px-4 py-3">
                    <ClaimStateBadge state={claim.state} />
                  </td>
                  <td className="px-4 py-3 text-slate-500">{formatDateTime(claim.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </PortalShell>
  );
}
