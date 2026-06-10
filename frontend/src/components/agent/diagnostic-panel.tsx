import { ArtifactPanel } from "@/components/agent/artifact-panel";
import { VerdictBadge } from "@/components/status-badge";
import { parseDiagnosticPayload, type DiagnosticReportOut } from "@/lib/types-agent";

const MODALITY_LABELS: Record<string, string> = {
  xray: "X-Ray",
  ct: "CT",
  mri: "MRI",
};

function modalityLabel(modality: string | null): string {
  if (modality === null) {
    return "Unknown";
  }
  return MODALITY_LABELS[modality] ?? modality.toUpperCase();
}

/** Compact stage-1 view: modality, authenticity verdict + risk, and the impression. */
export function DiagnosticPanel({ report }: { report: DiagnosticReportOut | null }) {
  const payload = parseDiagnosticPayload(report?.payload ?? null);

  return (
    <ArtifactPanel
      title="Diagnostic report"
      artifact={report}
      emptyCopy="No diagnostic report has been generated for this claim."
    >
      {report !== null ? (
        <div className="space-y-3 text-sm">
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
            <span className="text-slate-700">
              Modality: <span className="font-medium">{modalityLabel(report.modality)}</span>
            </span>
            <span className="flex items-center gap-2 text-slate-700">
              Authenticity: <VerdictBadge verdict={report.authenticity_verdict} />
              {report.authenticity_risk !== null ? (
                <span className="text-slate-500">
                  risk {Math.round(report.authenticity_risk * 100)}%
                </span>
              ) : null}
            </span>
          </div>
          {payload.impression !== null ? (
            <p className="text-slate-700">
              <span className="text-slate-500">Impression:</span> {payload.impression}
            </p>
          ) : (
            <p className="text-slate-500">No impression recorded.</p>
          )}
        </div>
      ) : null}
    </ArtifactPanel>
  );
}
