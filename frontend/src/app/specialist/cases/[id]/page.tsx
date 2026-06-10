"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { PortalShell } from "@/components/portal-shell";
import { ActionBar } from "@/components/specialist/action-bar";
import { CaseHeader } from "@/components/specialist/case-header";
import { DiagnosticReportPanel } from "@/components/specialist/diagnostic-report-panel";
import { ImagePanel } from "@/components/specialist/image-panel";
import { RecommendationNotePanel } from "@/components/specialist/recommendation-note-panel";
import { ApiError, apiFetch } from "@/lib/api-client";
import type { CaseDetail, ReturnOut, SendToInsurerOut } from "@/lib/types-specialist";

const POLL_INTERVAL_MS = 2000;

export default function SpecialistCasePage() {
  const router = useRouter();
  const params = useParams<{ id: string }>();
  const claimId = Number(params.id);
  const invalidId = !Number.isInteger(claimId) || claimId <= 0;

  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  // Poll while any stage-1 report or stage-2 note is still being produced.
  const shouldPoll =
    detail === null ||
    [...detail.diagnostic_reports, ...detail.recommendation_notes].some(
      (artifact) => artifact.status === "pending" || artifact.status === "running",
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
  const latestNote = detail?.recommendation_notes[0] ?? null;

  const handleSendToInsurer = async () => {
    await apiFetch<SendToInsurerOut>(`/api/specialist/cases/${claimId}/send-to-insurer`, {
      method: "POST",
    });
    router.push("/specialist");
  };

  const handleRequestFurtherTesting = async (note: string) => {
    await apiFetch<ReturnOut>(`/api/specialist/cases/${claimId}/request-further-testing`, {
      method: "POST",
      body: { note },
    });
    router.push("/specialist");
  };

  return (
    <PortalShell title="Specialist review" subtitle={detail?.claim_ref ?? "Loading case..."}>
      <div className="mb-4">
        <Link href="/specialist" className="text-sm text-blue-700 hover:underline">
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
            <div className="space-y-4">
              <RecommendationNotePanel
                claimId={detail.id}
                note={latestNote}
                onRegenerated={() => setRefreshKey((key) => key + 1)}
              />

              <details className="rounded-lg border border-slate-200 bg-white shadow-sm">
                <summary className="cursor-pointer select-none px-6 py-4 text-sm font-semibold text-slate-900">
                  Stage 1 diagnostic report
                </summary>
                <div className="border-t border-slate-200 [&>section]:rounded-none [&>section]:border-0 [&>section]:shadow-none">
                  <DiagnosticReportPanel
                    claimId={detail.id}
                    report={latestReport}
                    allowRegenerate={false}
                    onRegenerated={() => setRefreshKey((key) => key + 1)}
                  />
                </div>
              </details>
            </div>
          </div>

          <ActionBar
            primaryLabel="Send to insurance company"
            primaryConfirmPrompt="Send this case to the insurance company for adjudication?"
            onPrimary={handleSendToInsurer}
            secondaryLabel="Request further testing"
            modalTitle="Request further testing"
            modalDescription="The claimant will be notified by email with the reason you provide."
            noteLabel="What additional evidence is needed"
            notePlaceholder="Describe the additional tests or documents required."
            onSecondary={handleRequestFurtherTesting}
          />
        </div>
      ) : null}
    </PortalShell>
  );
}
