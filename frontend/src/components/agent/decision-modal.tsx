"use client";

import { useEffect, useState } from "react";

import { ClaimStateBadge } from "@/components/claim-state-badge";
import { BADGE_BASE } from "@/components/status-badge";
import { ApiError, apiFetch } from "@/lib/api-client";
import type { DecisionOut, DecisionValue, DraftEmailOut } from "@/lib/types-agent";

type Phase = "drafting" | "draft_failed" | "editing" | "submitting" | "done";

interface DecisionModalProps {
  claimId: number;
  claimRef: string;
  decision: DecisionValue;
  /** Claimant's preferred_language ("en" | "fr"); drives the French-draft notice. */
  claimantLanguage: string;
  /** Close without recording a decision. */
  onCancel: () => void;
  /** Close after a recorded decision; the caller routes back to the queue. */
  onComplete: () => void;
}

/**
 * The decision modal: drafts the claimant notification email on open, lets the agent
 * edit it freely, then records the decision and dispatches the notification as one
 * atomic backend action (POST /api/agent/cases/{id}/decision).
 */
export function DecisionModal({
  claimId,
  claimRef,
  decision,
  claimantLanguage,
  onCancel,
  onComplete,
}: DecisionModalProps) {
  const [phase, setPhase] = useState<Phase>("drafting");
  const [draftAttempt, setDraftAttempt] = useState(0);
  const [subject, setSubject] = useState("");
  const [bodyText, setBodyText] = useState("");
  const [note, setNote] = useState("");
  const [generatedBy, setGeneratedBy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<DecisionOut | null>(null);

  const approving = decision === "APPROVED";

  // Draft the email on open (and on retry; the retry handler resets the phase).
  useEffect(() => {
    let active = true;

    const draft = async () => {
      try {
        const data = await apiFetch<DraftEmailOut>(`/api/agent/cases/${claimId}/draft-email`, {
          method: "POST",
          body: { decision },
        });
        if (!active) {
          return;
        }
        setSubject(data.subject);
        setBodyText([data.greeting, ...data.body_paragraphs, data.closing].join("\n\n"));
        setGeneratedBy(data.generated_by);
        setPhase("editing");
      } catch (err) {
        if (!active) {
          return;
        }
        setError(err instanceof ApiError ? err.detail : "Unable to reach the API.");
        setPhase("draft_failed");
      }
    };

    void draft();
    return () => {
      active = false;
    };
  }, [claimId, decision, draftAttempt]);

  const handleConfirm = async () => {
    setPhase("submitting");
    setError(null);
    try {
      const data = await apiFetch<DecisionOut>(`/api/agent/cases/${claimId}/decision`, {
        method: "POST",
        body: {
          action: approving ? "approve" : "reject",
          note: note.trim().length > 0 ? note.trim() : null,
          email_subject: subject.trim(),
          email_body_text: bodyText.trim(),
        },
      });
      setResult(data);
      setPhase("done");
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Unable to reach the API.");
      setPhase("editing");
    }
  };

  const submitting = phase === "submitting";
  const confirmDisabled =
    submitting || subject.trim().length === 0 || bodyText.trim().length === 0;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="decision-modal-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 px-4 py-8"
    >
      <div className="max-h-full w-full max-w-2xl overflow-y-auto rounded-lg bg-white shadow-xl">
        <div className="border-b border-slate-200 px-6 py-4">
          <h2 id="decision-modal-title" className="text-base font-semibold text-slate-900">
            {approving ? "Approve claim" : "Reject claim"}{" "}
            <span className="font-mono">{claimRef}</span>
          </h2>
          <p className="mt-0.5 text-sm text-slate-500">
            The decision and the claimant notification are recorded as a single action.
          </p>
        </div>

        {phase === "done" && result !== null ? (
          <div className="px-6 py-8 text-center">
            <div className="flex justify-center">
              <ClaimStateBadge state={result.state} />
            </div>
            <p className="mt-4 text-base font-semibold text-slate-900">
              Notification dispatched to claimant
            </p>
            <p className="mt-2 text-sm text-slate-600">
              Email status:{" "}
              <span
                className={`${BADGE_BASE} ${
                  result.notification_status === "sent"
                    ? "bg-emerald-50 text-emerald-700 ring-emerald-200"
                    : "bg-amber-50 text-amber-700 ring-amber-200"
                }`}
              >
                {result.notification_status.charAt(0).toUpperCase() +
                  result.notification_status.slice(1)}
              </span>
            </p>
            <p className="mt-2 text-sm text-slate-600">
              {result.case_ref !== null
                ? `Indexed as precedent ${result.case_ref}.`
                : "Precedent indexing was unavailable for this case."}
            </p>
            <button
              type="button"
              onClick={onComplete}
              className="mt-6 rounded-md bg-blue-700 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-blue-800"
            >
              Back to queue
            </button>
          </div>
        ) : phase === "drafting" ? (
          <div className="px-6 py-12 text-center">
            <p className="animate-pulse text-sm text-slate-500">
              Drafting the claimant notification email...
            </p>
          </div>
        ) : phase === "draft_failed" ? (
          <div className="px-6 py-8">
            <p
              role="alert"
              className="rounded-md border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700"
            >
              {error ?? "Unable to draft the notification email."}
            </p>
            <div className="mt-4 flex justify-end gap-3">
              <button
                type="button"
                onClick={onCancel}
                className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-100"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => {
                  setPhase("drafting");
                  setError(null);
                  setDraftAttempt((attempt) => attempt + 1);
                }}
                className="rounded-md bg-blue-700 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-blue-800"
              >
                Retry draft
              </button>
            </div>
          </div>
        ) : (
          <div className="space-y-4 px-6 py-5">
            <div className="flex flex-wrap items-center gap-2">
              {generatedBy !== null ? (
                generatedBy === "fallback_template" ? (
                  <span className={`${BADGE_BASE} bg-slate-100 text-slate-600 ring-slate-300`}>
                    Template draft
                  </span>
                ) : (
                  <span className={`${BADGE_BASE} bg-violet-50 text-violet-700 ring-violet-200`}>
                    Drafted by {generatedBy}
                  </span>
                )
              ) : null}
              {claimantLanguage === "fr" ? (
                <span className={`${BADGE_BASE} bg-blue-50 text-blue-700 ring-blue-200`}>
                  Claimant prefers French
                </span>
              ) : null}
            </div>
            {claimantLanguage === "fr" ? (
              <p className="text-xs text-slate-500">
                The draft below is written in French to match the claimant&apos;s language
                preference. Please keep your edits in French.
              </p>
            ) : null}

            <div>
              <label htmlFor="decision-email-subject" className="block text-sm font-medium text-slate-700">
                Email subject
              </label>
              <input
                id="decision-email-subject"
                type="text"
                value={subject}
                disabled={submitting}
                onChange={(event) => setSubject(event.target.value)}
                className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-blue-600 focus:outline-none focus:ring-1 focus:ring-blue-600 disabled:opacity-50"
              />
            </div>

            <div>
              <label htmlFor="decision-email-body" className="block text-sm font-medium text-slate-700">
                Email body
              </label>
              <textarea
                id="decision-email-body"
                rows={12}
                value={bodyText}
                disabled={submitting}
                onChange={(event) => setBodyText(event.target.value)}
                className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 font-mono text-sm text-slate-900 focus:border-blue-600 focus:outline-none focus:ring-1 focus:ring-blue-600 disabled:opacity-50"
              />
            </div>

            <div>
              <label htmlFor="decision-note" className="block text-sm font-medium text-slate-700">
                Decision note (optional)
              </label>
              <textarea
                id="decision-note"
                rows={2}
                value={note}
                disabled={submitting}
                onChange={(event) => setNote(event.target.value)}
                placeholder="Recorded in the case timeline alongside the decision."
                className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-blue-600 focus:outline-none focus:ring-1 focus:ring-blue-600 disabled:opacity-50"
              />
            </div>

            {error !== null ? (
              <p
                role="alert"
                className="rounded-md border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700"
              >
                {error}
              </p>
            ) : null}

            <div className="flex items-center justify-end gap-3 border-t border-slate-100 pt-4">
              <button
                type="button"
                onClick={onCancel}
                disabled={submitting}
                className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-100 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void handleConfirm()}
                disabled={confirmDisabled}
                className={`rounded-md px-4 py-2 text-sm font-semibold text-white transition-colors disabled:opacity-50 ${
                  approving
                    ? "bg-emerald-600 hover:bg-emerald-700"
                    : "bg-red-600 hover:bg-red-700"
                }`}
              >
                {submitting
                  ? "Recording decision..."
                  : approving
                    ? "Confirm approval and notify claimant"
                    : "Confirm rejection and notify claimant"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
