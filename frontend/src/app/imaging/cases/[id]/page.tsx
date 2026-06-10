"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { PortalShell } from "@/components/portal-shell";
import { ActionBar } from "@/components/specialist/action-bar";
import { CaseHeader } from "@/components/specialist/case-header";
import { DiagnosticReportPanel } from "@/components/specialist/diagnostic-report-panel";
import { ImagePanel } from "@/components/specialist/image-panel";
import { ApiError, apiFetch } from "@/lib/api-client";
import type { CaseDetail, ForwardOut, ReturnOut } from "@/lib/types-specialist";

const POLL_INTERVAL_MS = 2000;

export default function ImagingCasePage() {
  const router = useRouter();
  const params = useParams<{ id: string }>();
  const claimId = Number(params.id);
  const invalidId = !Number.isInteger(claimId) || claimId <= 0;

  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  // Poll while any report is still being produced; the queue endpoints carry
  // the rest of the workflow forward.
  const shouldPoll =
    detail === null ||
    detail.diagnostic_reports.some(
      (report) => report.status === "pending" || report.status === "running",
    );

  useEffect(() => {
    if (invalidId) {
      return;
    }

    let active = true;

    const load = async () => {
      try {
        const data = await apiFetch<CaseDetail>(`/api/specialist/cases/${claimId}`);
        if (!active) {
          return;
        }
        setDetail(data);
        setError(null);
      } catch (err) {
        if (!active) {
          return;
        }
        setError(err instanceof ApiError ? err.detail : "Unable to reach the API.");
      }
    };

    void load();
    if (!shouldPoll) {
      return () => {
        active = false;
      };
    }
    const timer = setInterval(() => void load(), POLL_INTERVAL_MS);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [claimId, invalidId, shouldPoll, refreshKey]);

  const displayError = invalidId ? "Invalid claim id." : error;
  const latestReport = detail?.diagnostic_reports[0] ?? null;

  const handleForward = async () => {
    await apiFetch<ForwardOut>(`/api/specialist/cases/${claimId}/forward`, { method: "POST" });
    router.push("/imaging");
  };

  const handleReturn = async (note: string) => {
    await apiFetch<ReturnOut>(`/api/specialist/cases/${claimId}/return`, {
      method: "POST",
      body: { note },
    });
    router.push("/imaging");
  };

  return (
    <PortalShell title="Imaging review" subtitle={detail?.claim_ref ?? "Loading case..."}>
      <div className="mb-4">
        <Link href="/imaging" className="text-sm text-blue-700 hover:underline">
          Back to the queue
        </Link>
      </div>

      {displayError !== null ? (
        <p
          role="alert"
          className="mb-4 rounded-md border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700"
        >
          {displayError}
        </p>
      ) : null}

      {detail === null && displayError === null ? (
        <div className="rounded-lg border border-slate-200 bg-white px-6 py-16 text-center text-slate-400">
          Loading case...
        </div>
      ) : null}

      {detail !== null ? (
        <div className="space-y-6">
          <CaseHeader detail={detail} />

          <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-2">
            <ImagePanel documents={detail.documents} />
            <DiagnosticReportPanel
              claimId={detail.id}
              report={latestReport}
              allowRegenerate
              onRegenerated={() => setRefreshKey((key) => key + 1)}
            />
          </div>

          <ActionBar
            primaryLabel="Forward to medical specialist"
            primaryConfirmPrompt="Forward this case to the medical specialist?"
            onPrimary={handleForward}
            secondaryLabel="Return to claimant"
            modalTitle="Return to claimant"
            modalDescription="The claimant will be notified by email with the reason you provide."
            noteLabel="Reason for returning"
            notePlaceholder="Explain what is missing or needs to be corrected."
            onSecondary={handleReturn}
          />
        </div>
      ) : null}
    </PortalShell>
  );
}
