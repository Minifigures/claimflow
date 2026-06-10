import { ArtifactPanel } from "@/components/agent/artifact-panel";
import { LeanBadge } from "@/components/agent/lean-badge";
import { OutcomeBadge } from "@/components/agent/outcome-badge";
import { BADGE_BASE } from "@/components/status-badge";
import {
  formatConfidence,
  parseAdjudicationPayload,
  type AdjudicationSummaryOut,
  type HistoryAssessment,
  type RiskSeverity,
} from "@/lib/types-agent";

const SEVERITY_STYLES: Record<RiskSeverity, string> = {
  low: "bg-slate-100 text-slate-600 ring-slate-300",
  medium: "bg-amber-50 text-amber-700 ring-amber-200",
  high: "bg-red-50 text-red-700 ring-red-200",
};

const ASSESSMENT_META: Record<HistoryAssessment, { label: string; className: string }> = {
  consistent: {
    label: "Consistent with history",
    className: "border-emerald-200 bg-emerald-50 text-emerald-800",
  },
  minor_discrepancies: {
    label: "Minor discrepancies with history",
    className: "border-amber-200 bg-amber-50 text-amber-800",
  },
  major_discrepancies: {
    label: "Major discrepancies with history",
    className: "border-red-200 bg-red-50 text-red-800",
  },
  no_history: {
    label: "No prior history",
    className: "border-slate-200 bg-slate-50 text-slate-700",
  },
};

const DEFAULT_ADVISORY =
  "Advisory analysis only. The final approve/reject decision rests solely with the insurance agent.";

interface AdjudicationPanelProps {
  summary: AdjudicationSummaryOut | null;
  onRegenerate: () => void;
  regenerating: boolean;
}

/** Stage-3 view, the centerpiece of the dossier: summary text, severity-colored risk
 * factors, history-consistency callout, precedent table, lean + confidence, and the
 * advisory-notice footer. */
export function AdjudicationPanel({ summary, onRegenerate, regenerating }: AdjudicationPanelProps) {
  const payload = parseAdjudicationPayload(summary?.payload ?? null);
  const consistency = payload.consistency_with_history;
  const consistencyMeta = consistency !== null ? ASSESSMENT_META[consistency.assessment] : null;

  return (
    <ArtifactPanel
      title="Adjudication summary"
      artifact={summary}
      emptyCopy="The adjudication summary has not been generated yet."
      onRegenerate={onRegenerate}
      regenerating={regenerating}
      highlighted
    >
      {summary !== null ? (
        <div className="space-y-4 text-sm">
          <div className="flex flex-wrap items-center gap-3">
            <LeanBadge lean={summary.recommendation_lean} />
            <span className="text-slate-500">
              Confidence {formatConfidence(summary.confidence)}
            </span>
          </div>

          {payload.summary !== null ? (
            <p className="text-slate-700">{payload.summary}</p>
          ) : (
            <p className="text-slate-500">No summary text recorded.</p>
          )}

          <div>
            <p className="font-medium text-slate-700">Risk factors</p>
            {payload.risk_factors.length === 0 ? (
              <p className="mt-1 text-slate-500">No risk factors flagged.</p>
            ) : (
              <ul className="mt-2 space-y-2">
                {payload.risk_factors.map((risk) => (
                  <li key={`${risk.factor}-${risk.source}`} className="flex items-start gap-2">
                    <span className={`${BADGE_BASE} shrink-0 ${SEVERITY_STYLES[risk.severity]}`}>
                      {risk.severity.charAt(0).toUpperCase() + risk.severity.slice(1)}
                    </span>
                    <span className="text-slate-700">
                      {risk.factor}{" "}
                      <span className="text-xs text-slate-400">({risk.source})</span>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {consistency !== null && consistencyMeta !== null ? (
            <div className={`rounded-md border px-4 py-3 ${consistencyMeta.className}`}>
              <p className="font-semibold">{consistencyMeta.label}</p>
              {consistency.details.length > 0 ? (
                <p className="mt-1">{consistency.details}</p>
              ) : null}
            </div>
          ) : null}

          <div>
            <p className="font-medium text-slate-700">Similar case outcomes</p>
            {payload.similar_case_outcomes.length === 0 ? (
              <p className="mt-1 rounded-md border border-dashed border-slate-300 bg-slate-50 px-4 py-3 text-slate-500">
                No sufficiently similar precedent found.
              </p>
            ) : (
              <div className="mt-2 overflow-hidden rounded-md border border-slate-200">
                <table className="min-w-full divide-y divide-slate-200 text-sm">
                  <thead className="bg-slate-50">
                    <tr>
                      <th scope="col" className="px-3 py-2 text-left font-semibold text-slate-600">
                        Case
                      </th>
                      <th scope="col" className="px-3 py-2 text-left font-semibold text-slate-600">
                        Similarity
                      </th>
                      <th scope="col" className="px-3 py-2 text-left font-semibold text-slate-600">
                        Outcome
                      </th>
                      <th scope="col" className="px-3 py-2 text-left font-semibold text-slate-600">
                        Relevance
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {payload.similar_case_outcomes.map((item) => (
                      <tr key={item.case_ref}>
                        <td className="px-3 py-2 font-mono text-slate-900">{item.case_ref}</td>
                        <td className="px-3 py-2 tabular-nums text-slate-700">
                          {Math.round(item.similarity * 100)}%
                        </td>
                        <td className="px-3 py-2">
                          <OutcomeBadge outcome={item.outcome} />
                        </td>
                        <td className="px-3 py-2 text-slate-600">{item.relevance_note}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <p className="border-t border-slate-100 pt-3 text-xs italic text-slate-500">
            {payload.advisory_notice ?? DEFAULT_ADVISORY}
          </p>
        </div>
      ) : null}
    </ArtifactPanel>
  );
}
