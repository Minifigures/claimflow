"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api-client";

interface ActionBarProps {
  /** Primary forward-progress action (confirm-style: click, then confirm inline). */
  primaryLabel: string;
  primaryConfirmPrompt: string;
  onPrimary: () => Promise<void>;
  /** Secondary action requiring a reason (modal with a mandatory textarea). */
  secondaryLabel: string;
  modalTitle: string;
  modalDescription: string;
  noteLabel: string;
  notePlaceholder: string;
  onSecondary: (note: string) => Promise<void>;
}

function errorDetail(err: unknown): string {
  return err instanceof ApiError ? err.detail : "Unable to reach the API.";
}

/**
 * Sticky bottom action bar shared by both specialist case views. Both actions
 * are confirm-style; 409 (and any other ApiError) detail is surfaced inline.
 */
export function ActionBar({
  primaryLabel,
  primaryConfirmPrompt,
  onPrimary,
  secondaryLabel,
  modalTitle,
  modalDescription,
  noteLabel,
  notePlaceholder,
  onSecondary,
}: ActionBarProps) {
  const [confirmingPrimary, setConfirmingPrimary] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState<"primary" | "secondary" | null>(null);
  const [barError, setBarError] = useState<string | null>(null);
  const [modalError, setModalError] = useState<string | null>(null);

  const busy = submitting !== null;

  const handlePrimary = async () => {
    setSubmitting("primary");
    setBarError(null);
    try {
      await onPrimary();
    } catch (err) {
      setBarError(errorDetail(err));
      setConfirmingPrimary(false);
    } finally {
      setSubmitting(null);
    }
  };

  const handleSecondary = async () => {
    if (note.trim().length === 0) {
      setModalError("A reason is required.");
      return;
    }
    setSubmitting("secondary");
    setModalError(null);
    try {
      await onSecondary(note.trim());
    } catch (err) {
      setModalError(errorDetail(err));
    } finally {
      setSubmitting(null);
    }
  };

  return (
    <>
      <div className="sticky bottom-0 z-10 -mx-6 mt-8 border-t border-slate-200 bg-white/95 px-6 py-4 backdrop-blur">
        <div className="flex flex-wrap items-center gap-3">
          {confirmingPrimary ? (
            <>
              <span className="text-sm text-slate-700">{primaryConfirmPrompt}</span>
              <button
                type="button"
                disabled={busy}
                onClick={() => void handlePrimary()}
                className="rounded-md bg-blue-700 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-blue-800 disabled:opacity-50"
              >
                {submitting === "primary" ? "Submitting..." : "Confirm"}
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  setConfirmingPrimary(false);
                  setBarError(null);
                }}
                className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-100 disabled:opacity-50"
              >
                Cancel
              </button>
            </>
          ) : (
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setConfirmingPrimary(true);
                setBarError(null);
              }}
              className="rounded-md bg-blue-700 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-blue-800 disabled:opacity-50"
            >
              {primaryLabel}
            </button>
          )}

          <button
            type="button"
            disabled={busy}
            onClick={() => {
              setModalOpen(true);
              setModalError(null);
            }}
            className="rounded-md border border-amber-300 bg-amber-50 px-4 py-2 text-sm font-semibold text-amber-800 transition-colors hover:bg-amber-100 disabled:opacity-50"
          >
            {secondaryLabel}
          </button>

          {barError !== null ? (
            <p role="alert" className="text-sm text-red-700">
              {barError}
            </p>
          ) : null}
        </div>
      </div>

      {modalOpen ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={modalTitle}
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/50 p-6"
        >
          <div className="w-full max-w-lg rounded-lg border border-slate-200 bg-white p-6 shadow-lg">
            <h2 className="text-base font-semibold text-slate-900">{modalTitle}</h2>
            <p className="mt-1 text-sm text-slate-500">{modalDescription}</p>

            <label
              htmlFor="action-bar-note"
              className="mt-4 block text-sm font-medium text-slate-700"
            >
              {noteLabel}
            </label>
            <textarea
              id="action-bar-note"
              rows={4}
              value={note}
              disabled={busy}
              onChange={(event) => setNote(event.target.value)}
              placeholder={notePlaceholder}
              className="mt-1 block w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:opacity-50"
            />

            {modalError !== null ? (
              <p role="alert" className="mt-2 text-sm text-red-700">
                {modalError}
              </p>
            ) : null}

            <div className="mt-5 flex justify-end gap-3">
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  setModalOpen(false);
                  setModalError(null);
                }}
                className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-100 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={busy || note.trim().length === 0}
                onClick={() => void handleSecondary()}
                className="rounded-md bg-amber-600 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-amber-700 disabled:opacity-50"
              >
                {submitting === "secondary" ? "Submitting..." : "Confirm"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
