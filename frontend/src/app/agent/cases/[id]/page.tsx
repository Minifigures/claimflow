"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { AdjudicationPanel } from "@/components/agent/adjudication-panel";
import { ClaimHistoryTable } from "@/components/agent/claim-history-table";
import { DecisionModal } from "@/components/agent/decision-modal";
import { DiagnosticPanel } from "@/components/agent/diagnostic-panel";
import { DocumentsRow } from "@/components/agent/documents-row";
import { SpecialistPanel } from "@/components/agent/specialist-panel";
import { StaffTimeline } from "@/components/agent/staff-timeline";
import { ClaimStateBadge } from "@/components/claim-state-badge";
import { PortalShell } from "@/components/portal-shell";
import { BADGE_BASE } from "@/components/status-badge";
import { ApiError, apiFetch } from "@/lib/api-client";
import { formatCurrency, formatDateTime } from "@/lib/format";
import type { DecisionValue, Dossier, RegenerateOut } from "@/lib/types-agent";

const POLL_INTERVAL_MS = 2000;

/** 403/404/409 are terminal for this view; stop polling instead of hammering the API. */
const FATAL_STATUSES = new Set([403, 404, 409]);

export default function AgentCasePage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const claimId = Number(params.id);
  const invalidId = !Number.isInteger(claimId) || claimId <= 0;

  const [dossier, setDossier] = useState<Dossier | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fatal, setFatal] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [regenerating, setRegenerating] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [decisionIntent, setDecisionIntent] = useState<DecisionValue | null>(null);

  const summary = dossier?.adjudication_summary ?? null;
  const summaryStatus = summary?.status ?? null;
  const summaryBusy =
    summary === null || summaryStatus === "pending" || summaryStatus === "running";

  // Poll every 2s while the adjudication summary is still being produced, and
  // keep polling pre-adjudication so a claim advancing while the page is open
  // picks up the decision controls; stop only on terminal states.
  const claimState = dossier?.claim.state ?? null;
  const isTerminal = claimState === "APPROVED" || claimState === "REJECTED";
  const isAdjudication = claimState === "ADJUDICATION";
  const shouldPoll =
    !fatal &&
    !invalidId &&
    (dossier === null || (!isTerminal && !isAdjudication) || (isAdjudication && summaryBusy));

  useEffect(() => {
    if (invalidId) {
      return;
    }

    let active = true;

    const load = async () => {
      try {
        const data = await apiFetch<Dossier>(`/api/agent/cases/${claimId}/dossier`);
        if (!active) {
          return;
        }
        setDossier(data);
        setError(null);
      } catch (err) {
        if (!active) {
          return;
        }
        if (err instanceof ApiError) {
          setError(err.detail);
          if (FATAL_STATUSES.has(err.status)) {
            setFatal(true);
          }
        } else {
          setError("Unable to reach the API.");
        }
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

  const handleRegenerate = async () => {
    setRegenerating(true);
    setActionError(null);
    try {
      await apiFetch<RegenerateOut>(`/api/specialist/cases/${claimId}/regenerate`, {
        method: "POST",
        body: { stage: "adjudication" },
      });
      setRefreshKey((key) => key + 1);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.detail : "Unable to reach the API.");
    } finally {
      setRegenerating(false);
    }
  };

  const displayError = invalidId ? "Invalid claim id." : error;

  const decisionEntry =
    dossier !== null
      ? [...dossier.timeline]
          .reverse()
          .find((entry) => entry.action === "approve" || entry.action === "reject")
      : undefined;
  const latestNotification =
    dossier !== null && dossier.notifications.length > 0
      ? dossier.notifications[dossier.notifications.length - 1]
      : null;

  return (
    <PortalShell title="Case dossier" subtitle={dossier?.claim.claim_ref ?? "Loading case..."}>
      <div className="mb-4">
        <Link href="/agent" className="text-sm text-blue-700 hover:underline">
          Back to adjudication queue
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

      {dossier === null && displayError === null ? (
        <div className="rounded-lg border border-slate-200 bg-white px-6 py-16 text-center text-slate-400">
          Loading dossier...
        </div>
      ) : null}

      {dossier !== null ? (
        <div className="space-y-6">
          {isTerminal ? (
            <section
              className={`rounded-lg border p-6 shadow-sm ${
                dossier.claim.state === "APPROVED"
                  ? "border-emerald-200 bg-emerald-50"
                  : "border-red-200 bg-red-50"
              }`}
            >
              <div className="flex flex-wrap items-center gap-3">
                <ClaimStateBadge state={dossier.claim.state} />
                <p className="text-sm font-semibold text-slate-900">
                  This claim has been decided. The dossier below is read-only.
                </p>
              </div>
              {decisionEntry !== undefined ? (
                <p className="mt-2 text-sm text-slate-700">
                  Decision recorded {formatDateTime(decisionEntry.created_at)}
                  {decisionEntry.note !== null && decisionEntry.note.length > 0
                    ? ` with note: "${decisionEntry.note}"`
                    : "."}
                </p>
              ) : null}
              {latestNotification !== null ? (
                <p className="mt-1 text-sm text-slate-700">
                  Claimant notification &quot;{latestNotification.subject}&quot;
                  {" "}
                  <span
                    className={`${BADGE_BASE} ${
                      latestNotification.status === "sent"
                        ? "bg-emerald-50 text-emerald-700 ring-emerald-200"
                        : "bg-amber-50 text-amber-700 ring-amber-200"
                    }`}
                  >
                    {latestNotification.status.charAt(0).toUpperCase() +
                      latestNotification.status.slice(1)}
                  </span>
                </p>
              ) : null}
              <p className="mt-2 text-xs text-slate-500">
                The complete decision record, including every workflow event and artifact
                provenance, is preserved in the compliance audit trail.
              </p>
            </section>
          ) : null}

          <section className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <h2 className="font-mono text-base font-semibold text-slate-900">
                  {dossier.claim.claim_ref}
                </h2>
                <p className="text-sm capitalize text-slate-500">
                  {dossier.claim.claim_type} claim
                </p>
              </div>
              <ClaimStateBadge state={dossier.claim.state} />
            </div>

            <div className="mt-4 grid grid-cols-1 gap-6 lg:grid-cols-3">
              <dl className="grid grid-cols-1 gap-x-6 gap-y-3 text-sm sm:grid-cols-2 lg:col-span-2">
                <div>
                  <dt className="text-slate-500">Amount claimed</dt>
                  <dd className="tabular-nums text-slate-900">
                    {formatCurrency(dossier.claim.amount_claimed)}
                  </dd>
                </div>
                <div>
                  <dt className="text-slate-500">Incident date</dt>
                  <dd className="text-slate-900">
                    {dossier.claim.incident_date ?? "Not provided"}
                  </dd>
                </div>
                <div>
                  <dt className="text-slate-500">Procedure code</dt>
                  <dd className="font-mono text-slate-900">
                    {dossier.claim.procedure_code || "Not provided"}
                  </dd>
                </div>
                <div>
                  <dt className="text-slate-500">Diagnosis code</dt>
                  <dd className="font-mono text-slate-900">
                    {dossier.claim.diagnosis_code || "Not provided"}
                  </dd>
                </div>
                <div className="sm:col-span-2">
                  <dt className="text-slate-500">Description</dt>
                  <dd className="whitespace-pre-wrap text-slate-900">
                    {dossier.claim.description}
                  </dd>
                </div>
                <div>
                  <dt className="text-slate-500">Submitted</dt>
                  <dd className="text-slate-900">{formatDateTime(dossier.claim.created_at)}</dd>
                </div>
                <div>
                  <dt className="text-slate-500">Last updated</dt>
                  <dd className="text-slate-900">{formatDateTime(dossier.claim.updated_at)}</dd>
                </div>
              </dl>

              <div className="rounded-md border border-slate-200 bg-slate-50 p-4">
                <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Claimant
                </p>
                <p className="mt-2 text-sm font-medium text-slate-900">
                  {dossier.claimant.full_name}
                </p>
                <p className="mt-1 text-sm text-slate-600">
                  Member ID:{" "}
                  <span className="font-mono">
                    {dossier.claimant.member_id ?? "Not on file"}
                  </span>
                </p>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <span
                    className={`${BADGE_BASE} ${
                      dossier.claimant.preferred_language === "fr"
                        ? "bg-blue-50 text-blue-700 ring-blue-200"
                        : "bg-slate-100 text-slate-600 ring-slate-300"
                    }`}
                  >
                    {dossier.claimant.preferred_language === "fr"
                      ? "FR · Français"
                      : "EN · English"}
                  </span>
                  <span className="text-xs text-slate-500">
                    Prefers{" "}
                    {dossier.claimant.preferred_tone === "formal"
                      ? "formal tone"
                      : "plain language"}
                  </span>
                </div>
              </div>
            </div>
          </section>

          <section className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
            <h2 className="mb-3 text-sm font-semibold text-slate-900">
              Documents ({dossier.documents.length})
            </h2>
            <DocumentsRow documents={dossier.documents} />
          </section>

          <DiagnosticPanel report={dossier.diagnostic_report} />
          <SpecialistPanel note={dossier.recommendation_note} />

          {actionError !== null ? (
            <p
              role="alert"
              className="rounded-md border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700"
            >
              {actionError}
            </p>
          ) : null}

          <AdjudicationPanel
            summary={dossier.adjudication_summary}
            onRegenerate={() => void handleRegenerate()}
            regenerating={regenerating}
          />

          <section className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
            <h2 className="mb-3 text-sm font-semibold text-slate-900">Claimant history</h2>
            <ClaimHistoryTable rows={dossier.claim_history} stats={dossier.history_stats} />
          </section>

          <section className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
            <h2 className="mb-4 text-sm font-semibold text-slate-900">Case timeline</h2>
            <StaffTimeline entries={dossier.timeline} />
          </section>

          {isAdjudication ? (
            <div className="sticky bottom-4 z-10 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-200 bg-white/95 p-4 shadow-lg backdrop-blur">
              <p className="text-sm text-slate-600">
                {summaryBusy
                  ? "Analysis in progress... decision controls unlock once the adjudication summary completes."
                  : summaryStatus === "failed"
                    ? "Automated analysis failed. Regenerate it above, or decide based on your manual review."
                    : "Review the dossier, then record the final decision."}
              </p>
              <div className="flex gap-3">
                <button
                  type="button"
                  disabled={summaryBusy || decisionIntent !== null}
                  onClick={() => setDecisionIntent("REJECTED")}
                  className="rounded-md border border-red-300 bg-white px-4 py-2 text-sm font-semibold text-red-700 transition-colors hover:bg-red-50 disabled:opacity-50"
                >
                  Reject claim
                </button>
                <button
                  type="button"
                  disabled={summaryBusy || decisionIntent !== null}
                  onClick={() => setDecisionIntent("APPROVED")}
                  className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-emerald-700 disabled:opacity-50"
                >
                  Approve claim
                </button>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}

      {dossier !== null && decisionIntent !== null ? (
        <DecisionModal
          claimId={dossier.claim.id}
          claimRef={dossier.claim.claim_ref}
          decision={decisionIntent}
          claimantLanguage={dossier.claimant.preferred_language}
          onCancel={() => setDecisionIntent(null)}
          onComplete={() => router.push("/agent")}
        />
      ) : null}
    </PortalShell>
  );
}
