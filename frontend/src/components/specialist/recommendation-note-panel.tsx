"use client";

import { BADGE_BASE, ReportStatusBadge } from "@/components/status-badge";
import {
  formatPercent,
  GeneratedByChip,
  RegenerateButton,
  ScoreBar,
} from "@/components/specialist/report-meta";
import { RecommendationBadge } from "@/components/specialist/verdict-badge";
import type { CaseNote, ConsistencyCheckResult } from "@/lib/types-specialist";

const CHECK_LABELS: Record<string, string> = {
  imaging_matches_stated_procedure: "Imaging matches stated procedure",
  imaging_matches_diagnosis_code: "Imaging matches diagnosis code",
  documents_internally_consistent: "Documents internally consistent",
  dates_plausible: "Dates plausible",
  authenticity_concerns: "Authenticity concerns",
};

const RESULT_META: Record<ConsistencyCheckResult, { icon: string; className: string }> = {
  consistent: { icon: "✓", className: "bg-emerald-50 text-emerald-700 ring-emerald-200" },
  inconsistent: { icon: "✕", className: "bg-red-50 text-red-700 ring-red-200" },
  indeterminate: { icon: "?", className: "bg-amber-50 text-amber-700 ring-amber-200" },
  not_applicable: { icon: "—", className: "bg-slate-50 text-slate-500 ring-slate-200" },
};

function checkLabel(check: string): string {
  return CHECK_LABELS[check] ?? check.replaceAll("_", " ");
}

function sourceLabel(source: string): string {
  if (source === "diagnostic_report") {
    return "Diagnostic report";
  }
  if (source === "claim_form") {
    return "Claim form";
  }
  if (source.startsWith("upload:")) {
    return source.slice("upload:".length);
  }
  return source;
}

interface RecommendationNotePanelProps {
  claimId: number;
  note: CaseNote | null;
  onRegenerated: () => void;
}

export function RecommendationNotePanel({
  claimId,
  note,
  onRegenerated,
}: RecommendationNotePanelProps) {
  if (note === null) {
    return (
      <section className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="text-sm font-semibold text-slate-900">Recommendation note</h2>
        <p className="mt-2 text-sm text-slate-500">
          No recommendation note has been generated yet.
        </p>
      </section>
    );
  }

  const payload = note.payload;

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="mr-auto text-sm font-semibold text-slate-900">Recommendation note</h2>
        <ReportStatusBadge status={note.status} />
        <GeneratedByChip generatedBy={note.generated_by} fallbackReason={note.fallback_reason} />
        {note.requires_mandatory_review ? (
          <span className={`${BADGE_BASE} bg-red-50 font-semibold text-red-700 ring-red-200`}>
            Mandatory review
          </span>
        ) : null}
      </div>

      {note.status === "pending" || note.status === "running" ? (
        <p className="mt-4 text-sm text-slate-500">
          Analysis in progress. This panel refreshes automatically.
        </p>
      ) : null}

      {note.status === "failed" ? (
        <div className="mt-4 space-y-3 rounded-md border border-red-200 bg-red-50 p-4">
          <p className="text-sm text-red-700">
            Analysis failed{note.error !== null ? `: ${note.error}` : "."}
          </p>
          <RegenerateButton
            claimId={claimId}
            stage="recommendation"
            onRegenerated={onRegenerated}
          />
        </div>
      ) : null}

      {payload !== null ? (
        <div className="mt-4 space-y-5">
          <div>
            <div className="flex items-center justify-between gap-3">
              <RecommendationBadge recommendation={note.recommendation} />
              <span className="text-sm tabular-nums text-slate-500">
                Confidence {formatPercent(note.confidence)}
              </span>
            </div>
            <div className="mt-1.5">
              <ScoreBar value={note.confidence ?? 0} tone="blue" />
            </div>
          </div>

          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-600">
              Summary
            </h3>
            <p className="mt-2 whitespace-pre-wrap text-sm text-slate-800">{payload.summary}</p>
          </div>

          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-600">
              Supporting findings
            </h3>
            {payload.supporting_findings.length === 0 ? (
              <p className="mt-2 text-sm text-slate-500">No supporting findings cited.</p>
            ) : (
              <ul className="mt-2 space-y-2">
                {payload.supporting_findings.map((finding, index) => (
                  <li
                    key={index}
                    className="rounded-md border border-slate-200 px-3 py-2 text-sm"
                  >
                    <span
                      className={`${BADGE_BASE} bg-slate-100 text-slate-600 ring-slate-200`}
                    >
                      {sourceLabel(finding.source_document)}
                    </span>
                    <p className="mt-1 text-slate-800">{finding.finding}</p>
                    <p className="mt-0.5 text-slate-500">{finding.relevance}</p>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-600">
              Identified gaps
            </h3>
            {payload.identified_gaps.length === 0 ? (
              <p className="mt-2 text-sm text-slate-500">No gaps identified.</p>
            ) : (
              <ul className="mt-2 list-disc space-y-0.5 pl-5 text-sm text-slate-700">
                {payload.identified_gaps.map((gap) => (
                  <li key={gap}>{gap}</li>
                ))}
              </ul>
            )}
          </div>

          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-600">
              Suggested next steps
            </h3>
            {payload.suggested_next_steps.length === 0 ? (
              <p className="mt-2 text-sm text-slate-500">No next steps suggested.</p>
            ) : (
              <ul className="mt-2 list-disc space-y-0.5 pl-5 text-sm text-slate-700">
                {payload.suggested_next_steps.map((step) => (
                  <li key={step}>{step}</li>
                ))}
              </ul>
            )}
          </div>

          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-600">
              Consistency checks
            </h3>
            <div className="mt-2 overflow-hidden rounded-md border border-slate-200">
              <table className="min-w-full divide-y divide-slate-200 text-sm">
                <thead className="bg-slate-50">
                  <tr>
                    <th scope="col" className="px-3 py-2 text-left font-semibold text-slate-600">
                      Check
                    </th>
                    <th scope="col" className="px-3 py-2 text-left font-semibold text-slate-600">
                      Result
                    </th>
                    <th scope="col" className="px-3 py-2 text-left font-semibold text-slate-600">
                      Detail
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {payload.consistency_checks.map((check) => {
                    const meta = RESULT_META[check.result];
                    return (
                      <tr key={check.check}>
                        <td className="px-3 py-2 text-slate-800">{checkLabel(check.check)}</td>
                        <td className="px-3 py-2">
                          <span
                            title={check.result.replaceAll("_", " ")}
                            className={`inline-flex h-5 w-5 items-center justify-center rounded-full text-xs font-bold ring-1 ring-inset ${meta.className}`}
                          >
                            {meta.icon}
                          </span>
                        </td>
                        <td className="px-3 py-2 text-slate-600">{check.detail}</td>
                      </tr>
                    );
                  })}
                  {payload.consistency_checks.length === 0 ? (
                    <tr>
                      <td colSpan={3} className="px-3 py-4 text-center text-slate-500">
                        No consistency checks recorded.
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </div>

          <p className="border-t border-slate-200 pt-3 text-xs text-slate-500">
            {payload.advisory_notice}
          </p>
        </div>
      ) : null}
    </section>
  );
}
