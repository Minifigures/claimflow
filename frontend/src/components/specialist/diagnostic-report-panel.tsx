"use client";

import { BADGE_BASE, ReportStatusBadge, VerdictBadge } from "@/components/status-badge";
import {
  formatPercent,
  GeneratedByChip,
  RegenerateButton,
  ScoreBar,
} from "@/components/specialist/report-meta";
import type { Modality } from "@/lib/types";
import type { CaseReport, FindingSeverity, ImageQuality } from "@/lib/types-specialist";

const MODALITY_LABELS: Record<Modality, string> = {
  xray: "X-Ray",
  ct: "CT",
  mri: "MRI",
};

const SEVERITY_STYLES: Record<FindingSeverity, string> = {
  normal: "bg-slate-100 text-slate-600 ring-slate-200",
  minor: "bg-blue-50 text-blue-700 ring-blue-200",
  moderate: "bg-amber-50 text-amber-700 ring-amber-200",
  significant: "bg-red-50 text-red-700 ring-red-200",
};

const QUALITY_LABELS: Record<ImageQuality, string> = {
  adequate: "Adequate",
  degraded: "Degraded",
  non_diagnostic: "Non-diagnostic",
};

function riskTone(risk: number): "emerald" | "amber" | "red" {
  if (risk >= 0.7) {
    return "red";
  }
  if (risk >= 0.35) {
    return "amber";
  }
  return "emerald";
}

interface DiagnosticReportPanelProps {
  claimId: number;
  report: CaseReport | null;
  /** Stage-1 regenerate is imaging-specialist-only; hide the button elsewhere. */
  allowRegenerate: boolean;
  onRegenerated: () => void;
}

export function DiagnosticReportPanel({
  claimId,
  report,
  allowRegenerate,
  onRegenerated,
}: DiagnosticReportPanelProps) {
  if (report === null) {
    return (
      <section className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="text-sm font-semibold text-slate-900">Diagnostic report</h2>
        <p className="mt-2 text-sm text-slate-500">No diagnostic report has been generated yet.</p>
      </section>
    );
  }

  const payload = report.payload;

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="mr-auto text-sm font-semibold text-slate-900">Diagnostic report</h2>
        <ReportStatusBadge status={report.status} />
        <GeneratedByChip
          generatedBy={report.generated_by}
          fallbackReason={report.fallback_reason}
        />
        {report.requires_mandatory_review ? (
          <span className={`${BADGE_BASE} bg-red-50 font-semibold text-red-700 ring-red-200`}>
            Mandatory review
          </span>
        ) : null}
      </div>

      {report.status === "pending" || report.status === "running" ? (
        <p className="mt-4 text-sm text-slate-500">
          Analysis in progress. This panel refreshes automatically.
        </p>
      ) : null}

      {report.status === "failed" ? (
        <div className="mt-4 space-y-3 rounded-md border border-red-200 bg-red-50 p-4">
          <p className="text-sm text-red-700">
            Analysis failed{report.error !== null ? `: ${report.error}` : "."}
          </p>
          {allowRegenerate ? (
            <RegenerateButton claimId={claimId} stage="imaging" onRegenerated={onRegenerated} />
          ) : null}
        </div>
      ) : null}

      {payload !== null ? (
        <div className="mt-4 space-y-5">
          <div>
            <div className="flex items-center justify-between text-sm">
              <span className="text-slate-700">
                Modality:{" "}
                <span className="font-medium text-slate-900">
                  {report.modality !== null ? MODALITY_LABELS[report.modality] : "Unknown"}
                </span>
              </span>
              <span className="tabular-nums text-slate-500">
                Confidence {formatPercent(report.modality_confidence)}
              </span>
            </div>
            <div className="mt-1.5">
              <ScoreBar value={report.modality_confidence ?? 0} tone="blue" />
            </div>
            {!payload.modality_agrees_with_classifier ? (
              <p className="mt-1.5 text-xs text-amber-700">
                The drafted assessment ({payload.modality_assessment}) disagrees with the
                classifier.
              </p>
            ) : null}
          </div>

          <div className="rounded-md border border-slate-200 bg-slate-50 p-4">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="mr-auto text-xs font-semibold uppercase tracking-wider text-slate-600">
                Authenticity
              </h3>
              <VerdictBadge verdict={report.authenticity_verdict} />
              <span className="text-sm tabular-nums text-slate-700">
                Risk {formatPercent(payload.authenticity.risk_score)}
              </span>
            </div>
            <ul className="mt-3 space-y-3">
              {payload.authenticity.signals.map((signal) => (
                <li key={signal.name}>
                  <div className="flex items-center justify-between text-sm">
                    <span className="font-medium text-slate-800">{signal.name}</span>
                    <span className="tabular-nums text-slate-500">
                      {formatPercent(signal.score)}
                    </span>
                  </div>
                  <div className="mt-1">
                    <ScoreBar value={signal.score} tone={riskTone(signal.score)} />
                  </div>
                  <p className="mt-1 text-sm text-slate-600">{signal.finding}</p>
                </li>
              ))}
              {payload.authenticity.signals.length === 0 ? (
                <li className="text-sm text-slate-500">No forensic signals reported.</li>
              ) : null}
            </ul>
          </div>

          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-600">
              Findings
            </h3>
            {payload.findings.length === 0 ? (
              <p className="mt-2 text-sm text-slate-500">No findings recorded.</p>
            ) : (
              <ul className="mt-2 space-y-2">
                {payload.findings.map((finding, index) => (
                  <li
                    key={index}
                    className="rounded-md border border-slate-200 px-3 py-2 text-sm"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span className={`${BADGE_BASE} ${SEVERITY_STYLES[finding.severity]}`}>
                        {finding.severity}
                      </span>
                      {finding.location !== null ? (
                        <span className="text-slate-500">{finding.location}</span>
                      ) : null}
                      <span className="ml-auto tabular-nums text-slate-400">
                        {formatPercent(finding.confidence)}
                      </span>
                    </div>
                    <p className="mt-1 text-slate-800">{finding.description}</p>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-600">
              Impression
            </h3>
            <p className="mt-2 whitespace-pre-wrap text-sm text-slate-800">
              {payload.impression}
            </p>
            <p className="mt-1 text-xs text-slate-500">
              Region: {payload.anatomical_region}
              {payload.view !== null ? ` · View: ${payload.view}` : ""} · Draft confidence{" "}
              {formatPercent(payload.confidence)}
            </p>
          </div>

          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-600">
              Image quality
            </h3>
            <p className="mt-2 text-sm text-slate-800">
              {QUALITY_LABELS[payload.image_quality]}
            </p>
            {payload.quality_issues.length > 0 ? (
              <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm text-slate-600">
                {payload.quality_issues.map((issue) => (
                  <li key={issue}>{issue}</li>
                ))}
              </ul>
            ) : null}
            {payload.visual_inconsistencies.length > 0 ? (
              <div className="mt-2">
                <p className="text-sm font-medium text-amber-800">Visual inconsistencies</p>
                <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm text-amber-700">
                  {payload.visual_inconsistencies.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>

          <p className="border-t border-slate-200 pt-3 text-xs text-slate-500">
            {payload.disclaimer}
          </p>
        </div>
      ) : null}
    </section>
  );
}
