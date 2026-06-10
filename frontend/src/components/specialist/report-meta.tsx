"use client";

import { useState } from "react";

import { BADGE_BASE } from "@/components/status-badge";
import { ApiError, apiFetch } from "@/lib/api-client";
import type { RegenerateOut, RegenerateStage } from "@/lib/types-specialist";

export function formatPercent(value: number | null): string {
  if (value === null) {
    return "—";
  }
  return `${Math.round(value * 100)}%`;
}

const BAR_TONES = {
  blue: "bg-blue-600",
  emerald: "bg-emerald-600",
  amber: "bg-amber-500",
  red: "bg-red-600",
} as const;

export type BarTone = keyof typeof BAR_TONES;

/** Horizontal 0..1 score bar used for confidence and forensic-signal scores. */
export function ScoreBar({ value, tone = "blue" }: { value: number; tone?: BarTone }) {
  const clamped = Math.min(Math.max(value, 0), 1);
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
      <div
        className={`h-full rounded-full ${BAR_TONES[tone]}`}
        style={{ width: `${Math.round(clamped * 100)}%` }}
      />
    </div>
  );
}

/**
 * Provenance chip: amber "Deterministic fallback" when the artifact came from
 * the keyless/fallback path, otherwise a neutral chip with the model id.
 */
export function GeneratedByChip({
  generatedBy,
  fallbackReason,
}: {
  generatedBy: string;
  fallbackReason: string | null;
}) {
  if (fallbackReason !== null) {
    return (
      <span
        title={`Fallback reason: ${fallbackReason}`}
        className={`${BADGE_BASE} bg-amber-50 text-amber-700 ring-amber-200`}
      >
        Deterministic fallback
      </span>
    );
  }
  return (
    <span className={`${BADGE_BASE} bg-slate-100 font-mono text-slate-600 ring-slate-200`}>
      {generatedBy}
    </span>
  );
}

interface RegenerateButtonProps {
  claimId: number;
  stage: RegenerateStage;
  onRegenerated: () => void;
}

/** Re-runs the latest FAILED artifact of a stage; ApiError detail shown inline. */
export function RegenerateButton({ claimId, stage, onRegenerated }: RegenerateButtonProps) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleRegenerate = async () => {
    setSubmitting(true);
    setError(null);
    try {
      await apiFetch<RegenerateOut>(`/api/specialist/cases/${claimId}/regenerate`, {
        method: "POST",
        body: { stage },
      });
      onRegenerated();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Unable to reach the API.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="space-y-2">
      <button
        type="button"
        disabled={submitting}
        onClick={() => void handleRegenerate()}
        className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-100 disabled:opacity-50"
      >
        {submitting ? "Requesting..." : "Regenerate analysis"}
      </button>
      {error !== null ? (
        <p role="alert" className="text-sm text-red-700">
          {error}
        </p>
      ) : null}
    </div>
  );
}
