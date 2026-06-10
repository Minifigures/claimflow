"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { ClaimStateBadge } from "@/components/claim-state-badge";
import { ClaimStatusTimeline } from "@/components/claim-status-timeline";
import { DicomBadge, KindChip, ModalityChip } from "@/components/document-chips";
import { DocumentUploader } from "@/components/document-uploader";
import { PortalShell } from "@/components/portal-shell";
import { ReportStatusBadge } from "@/components/status-badge";
import { ApiError, apiFetch } from "@/lib/api-client";
import { formatCurrency, formatDateTime } from "@/lib/format";
import type { ClaimDetail, DocumentOut, Modality, ResubmitOut, TimelineEntry } from "@/lib/types";

/*
 * PRIVACY RULE (deliberate product decision): the claimant view must NEVER
 * render authenticity/fraud fields (authenticity_verdict, authenticity_risk,
 * signals, requires_mandatory_review) even though the owner-scoped API
 * returns them on diagnostic_report. Claimants see modality, report status,
 * and "under review by our imaging team" copy only. No fraud language
 * anywhere in claimant-facing UI.
 */

const POLL_INTERVAL_MS = 2000;

const MODALITY_LABELS: Record<Modality, string> = {
  xray: "X-Ray",
  ct: "CT",
  mri: "MRI",
};

const RESUBMIT_STATES = new Set<ClaimDetail["state"]>([
  "RETURNED_TO_CLAIMANT",
  "PENDING_FURTHER_TESTING",
]);

interface ResubmitPanelProps {
  claimId: number;
  disabled: boolean;
  onResubmitted: () => void;
}

function ResubmitPanel({ claimId, disabled, onResubmitted }: ResubmitPanelProps) {
  const [note, setNote] = useState("");
  const [extraDocs, setExtraDocs] = useState<DocumentOut[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleResubmit = async () => {
    if (note.trim().length === 0) {
      setError("Please add a note describing what you changed or added.");
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      await apiFetch<ResubmitOut>(`/api/claims/${claimId}/resubmit`, {
        method: "POST",
        body: { note: note.trim() },
      });
      setNote("");
      setExtraDocs([]);
      onResubmitted();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Unable to reach the API.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="space-y-3">
      <div>
        <label htmlFor="resubmit-note" className="block text-sm font-medium text-amber-900">
          Your note
        </label>
        <textarea
          id="resubmit-note"
          rows={3}
          value={note}
          disabled={disabled || submitting}
          onChange={(event) => setNote(event.target.value)}
          className="mt-1 block w-full rounded-md border border-amber-300 bg-white px-3 py-2 text-sm text-slate-900 focus:border-amber-500 focus:outline-none focus:ring-1 focus:ring-amber-500 disabled:opacity-50"
          placeholder="Describe what you have added or corrected."
        />
      </div>

      <div>
        <p className="mb-2 text-sm font-medium text-amber-900">Additional documents (optional)</p>
        <DocumentUploader
          claimId={claimId}
          uploaded={extraDocs}
          onUploaded={(doc) => setExtraDocs((prev) => [...prev, doc])}
          disabled={disabled || submitting}
        />
      </div>

      {error ? (
        <p role="alert" className="text-sm text-red-700">
          {error}
        </p>
      ) : null}

      <button
        type="button"
        disabled={disabled || submitting}
        onClick={() => void handleResubmit()}
        className="rounded-md bg-amber-600 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-amber-700 disabled:opacity-50"
      >
        {submitting ? "Resubmitting..." : "Resubmit claim"}
      </button>
    </div>
  );
}

export default function ClaimDetailPage() {
  const params = useParams<{ id: string }>();
  const claimId = Number(params.id);
  const invalidId = !Number.isInteger(claimId) || claimId <= 0;

  const [detail, setDetail] = useState<ClaimDetail | null>(null);
  const [timeline, setTimeline] = useState<TimelineEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const reportStatus = detail?.diagnostic_report?.status ?? null;
  // Poll while the report is still being produced or the claim is freshly
  // (re)submitted; stop once the workflow has moved on.
  const shouldPoll =
    detail === null ||
    detail.state === "SUBMITTED" ||
    detail.diagnostic_report === null ||
    reportStatus === "pending" ||
    reportStatus === "running";

  useEffect(() => {
    if (invalidId) {
      return;
    }

    let active = true;

    const load = async () => {
      try {
        const [claimData, timelineData] = await Promise.all([
          apiFetch<ClaimDetail>(`/api/claims/${claimId}`),
          apiFetch<TimelineEntry[]>(`/api/claims/${claimId}/timeline`),
        ]);
        if (!active) {
          return;
        }
        setDetail(claimData);
        setTimeline(timelineData);
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

  const canResubmit = detail !== null && RESUBMIT_STATES.has(detail.state);

  return (
    <PortalShell title="Claim details" subtitle={detail?.claim_ref ?? "Loading claim..."}>
      <div className="mb-4">
        <Link href="/claimant" className="text-sm text-blue-700 hover:underline">
          Back to your claims
        </Link>
      </div>

      {displayError ? (
        <p
          role="alert"
          className="mb-4 rounded-md border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700"
        >
          {displayError}
        </p>
      ) : null}

      {detail === null && displayError === null ? (
        <div className="rounded-lg border border-slate-200 bg-white px-6 py-16 text-center text-slate-400">
          Loading claim...
        </div>
      ) : null}

      {detail !== null ? (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
          <div className="space-y-6 lg:col-span-2">
            <section className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h2 className="font-mono text-base font-semibold text-slate-900">
                    {detail.claim_ref}
                  </h2>
                  <p className="text-sm capitalize text-slate-500">{detail.claim_type} claim</p>
                </div>
                <ClaimStateBadge state={detail.state} />
              </div>

              <dl className="mt-4 grid grid-cols-1 gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
                <div>
                  <dt className="text-slate-500">Amount claimed</dt>
                  <dd className="tabular-nums text-slate-900">
                    {formatCurrency(detail.amount_claimed)}
                  </dd>
                </div>
                <div>
                  <dt className="text-slate-500">Incident date</dt>
                  <dd className="text-slate-900">{detail.incident_date ?? "Not provided"}</dd>
                </div>
                <div>
                  <dt className="text-slate-500">Procedure code</dt>
                  <dd className="text-slate-900">{detail.procedure_code || "Not provided"}</dd>
                </div>
                <div>
                  <dt className="text-slate-500">Diagnosis code</dt>
                  <dd className="text-slate-900">{detail.diagnosis_code || "Not provided"}</dd>
                </div>
                <div className="sm:col-span-2">
                  <dt className="text-slate-500">Description</dt>
                  <dd className="whitespace-pre-wrap text-slate-900">{detail.description}</dd>
                </div>
                <div>
                  <dt className="text-slate-500">Submitted</dt>
                  <dd className="text-slate-900">{formatDateTime(detail.created_at)}</dd>
                </div>
                <div>
                  <dt className="text-slate-500">Last updated</dt>
                  <dd className="text-slate-900">{formatDateTime(detail.updated_at)}</dd>
                </div>
              </dl>
            </section>

            <section className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
              <h2 className="text-sm font-semibold text-slate-900">Imaging assessment</h2>
              {/*
               * Claimant-safe slice of the diagnostic report: modality and
               * processing status only (see the privacy rule at the top of
               * this file). Verdict/risk/review flags are never rendered.
               */}
              {detail.diagnostic_report === null ? (
                <p className="mt-2 text-sm text-slate-500">
                  Your imaging has not been analyzed yet.
                </p>
              ) : (
                <div className="mt-3 flex flex-wrap items-center gap-3 text-sm">
                  <span className="text-slate-700">
                    Modality:{" "}
                    {detail.diagnostic_report.modality !== null
                      ? MODALITY_LABELS[detail.diagnostic_report.modality]
                      : "Pending"}
                  </span>
                  <ReportStatusBadge status={detail.diagnostic_report.status} />
                  {detail.state !== "APPROVED" && detail.state !== "REJECTED" ? (
                    <span className="text-slate-500">
                      Your imaging is under review by our imaging team.
                    </span>
                  ) : null}
                </div>
              )}
            </section>

            <section className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
              <h2 className="text-sm font-semibold text-slate-900">
                Documents ({detail.documents.length})
              </h2>
              {detail.documents.length === 0 ? (
                <p className="mt-2 text-sm text-slate-500">No documents uploaded yet.</p>
              ) : (
                <ul className="mt-3 space-y-2">
                  {detail.documents.map((doc) => (
                    <li
                      key={doc.id}
                      className="flex flex-wrap items-center gap-2 rounded-md border border-slate-200 bg-slate-50 px-4 py-2.5 text-sm"
                    >
                      <span className="min-w-0 flex-1 truncate text-slate-900">
                        {doc.filename}
                      </span>
                      <KindChip kind={doc.kind} />
                      <ModalityChip modality={doc.modality} />
                      <DicomBadge hasPreview={doc.has_preview} />
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>

          <div>
            <section className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
              <h2 className="mb-4 text-sm font-semibold text-slate-900">Assessment progress</h2>
              {timeline === null ? (
                <p className="text-sm text-slate-400">Loading timeline...</p>
              ) : (
                <ClaimStatusTimeline
                  state={detail.state}
                  entries={timeline}
                  resubmitPanel={
                    canResubmit ? (
                      <ResubmitPanel
                        claimId={detail.id}
                        disabled={false}
                        onResubmitted={() => setRefreshKey((key) => key + 1)}
                      />
                    ) : undefined
                  }
                />
              )}
            </section>
          </div>
        </div>
      ) : null}
    </PortalShell>
  );
}
