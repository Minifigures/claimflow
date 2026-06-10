/**
 * Types for the insurance-agent portal, mirroring the pydantic response models in
 * backend/app/routers/agent.py (snake_case, as serialized by FastAPI), plus typed
 * views over the stage-3 adjudication payload (backend/app/llm/schemas.py).
 *
 * Owned by the agent-portal workstream; shared primitives are imported read-only
 * from src/lib/types.ts.
 */

import type {
  ArtifactStatus,
  AuthenticityVerdict,
  ClaimState,
  DocumentKind,
  Modality,
  TimelineEntry,
} from "@/lib/types";

/** Mirrors `RecommendationNoteLLM.recommendation` in backend/app/llm/schemas.py. */
export type SpecialistRecommendation =
  | "SUPPORTS_CLAIM"
  | "INSUFFICIENT_EVIDENCE"
  | "REQUIRES_FURTHER_TESTING";

/** Mirrors `AdjudicationSummaryLLM.recommendation_lean` in backend/app/llm/schemas.py. */
export type RecommendationLean = "LEAN_APPROVE" | "LEAN_REJECT" | "NO_CLEAR_LEAN";

/** Decision value sent to POST /api/agent/cases/{id}/draft-email. */
export type DecisionValue = "APPROVED" | "REJECTED";

/** Mirrors `QueueItem` in backend/app/routers/agent.py. */
export interface AgentQueueItem {
  claim_id: number;
  claim_ref: string;
  claim_type: string;
  claimant: string;
  state: string;
  submitted_at: string;
  summary_status: ArtifactStatus | null;
  recommendation_lean: RecommendationLean | null;
  confidence: number | null;
  requires_mandatory_review: boolean | null;
  specialist_recommendation: SpecialistRecommendation | null;
}

/** Mirrors `ClaimCore` in backend/app/routers/agent.py. */
export interface ClaimCore {
  id: number;
  claim_ref: string;
  claim_type: string;
  description: string;
  procedure_code: string;
  diagnosis_code: string;
  incident_date: string | null;
  amount_claimed: number;
  state: ClaimState;
  created_at: string;
  updated_at: string;
}

/** Mirrors `ClaimantInfo` in backend/app/routers/agent.py. */
export interface ClaimantInfo {
  full_name: string;
  member_id: string | null;
  preferred_language: string;
  preferred_tone: string;
}

/** Mirrors `DossierDocument` in backend/app/routers/agent.py. */
export interface DossierDocument {
  id: number;
  filename: string;
  kind: DocumentKind;
  modality: Modality | null;
  size_bytes: number;
  has_preview: boolean;
}

/** Mirrors `ArtifactOut` in backend/app/routers/agent.py. */
export interface ArtifactOut {
  id: number;
  status: ArtifactStatus;
  payload: Record<string, unknown> | null;
  generated_by: string;
  fallback_reason: string | null;
  requires_mandatory_review: boolean;
  created_at: string;
  completed_at: string | null;
}

/** Mirrors `DiagnosticReportOut` in backend/app/routers/agent.py. */
export interface DiagnosticReportOut extends ArtifactOut {
  modality: string | null;
  modality_confidence: number | null;
  authenticity_verdict: AuthenticityVerdict | null;
  authenticity_risk: number | null;
}

/** Mirrors `RecommendationNoteOut` in backend/app/routers/agent.py. */
export interface RecommendationNoteOut extends ArtifactOut {
  recommendation: SpecialistRecommendation | null;
  confidence: number | null;
}

/** Mirrors `AdjudicationSummaryOut` in backend/app/routers/agent.py. */
export interface AdjudicationSummaryOut extends ArtifactOut {
  recommendation_lean: RecommendationLean | null;
  confidence: number | null;
}

/** Mirrors `HistoryRow` in backend/app/routers/agent.py. */
export interface HistoryRow {
  claim_type: string;
  procedure_code: string;
  diagnosis_code: string;
  modality: string | null;
  billed_amount: number;
  outcome: string;
  date_of_service: string | null;
  decided_at: string | null;
}

/** Mirrors `HistoryStats` in backend/app/routers/agent.py. */
export interface HistoryStats {
  total: number;
  approved: number;
  rejected: number;
}

/** Mirrors `NotificationOut` in backend/app/routers/agent.py. */
export interface NotificationOut {
  id: number;
  subject: string;
  body_text: string;
  provider: string;
  status: string;
  created_at: string;
  sent_at: string | null;
}

/** Mirrors `Dossier` in backend/app/routers/agent.py. */
export interface Dossier {
  claim: ClaimCore;
  claimant: ClaimantInfo;
  documents: DossierDocument[];
  diagnostic_report: DiagnosticReportOut | null;
  recommendation_note: RecommendationNoteOut | null;
  adjudication_summary: AdjudicationSummaryOut | null;
  claim_history: HistoryRow[];
  history_stats: HistoryStats;
  timeline: TimelineEntry[];
  notifications: NotificationOut[];
}

/** Mirrors `DraftEmailOut` in backend/app/routers/agent.py. */
export interface DraftEmailOut {
  subject: string;
  greeting: string;
  body_paragraphs: string[];
  closing: string;
  generated_by: string;
  fallback_reason: string | null;
}

/** Mirrors `DecisionOut` in backend/app/routers/agent.py. */
export interface DecisionOut {
  state: ClaimState;
  notification_id: number;
  notification_status: string;
  case_ref: string | null;
}

/** Mirrors `RegenerateOut` in backend/app/routers/specialist.py (agent may regenerate stage 3). */
export interface RegenerateOut {
  claim_id: number;
  stage: string;
  artifact_id: number;
  status: ArtifactStatus;
}

// ------------------------------------------------------------------ payload views
// Artifact payloads arrive as `Record<string, unknown> | null`; the parsers below
// narrow them defensively (LLM-keyed and deterministic-fallback payloads share the
// same schema, but the UI must never trust the shape blindly).

export type RiskSeverity = "low" | "medium" | "high";

export interface RiskFactorView {
  factor: string;
  severity: RiskSeverity;
  source: string;
}

export type HistoryAssessment =
  | "consistent"
  | "minor_discrepancies"
  | "major_discrepancies"
  | "no_history";

export interface ConsistencyView {
  assessment: HistoryAssessment;
  details: string;
}

export interface SimilarCaseView {
  case_ref: string;
  similarity: number;
  outcome: string;
  relevance_note: string;
}

export interface AdjudicationPayloadView {
  summary: string | null;
  risk_factors: RiskFactorView[];
  consistency_with_history: ConsistencyView | null;
  similar_case_outcomes: SimilarCaseView[];
  advisory_notice: string | null;
}

export interface RecommendationPayloadView {
  summary: string | null;
  identified_gaps: string[];
}

export interface DiagnosticPayloadView {
  impression: string | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readString(record: Record<string, unknown>, key: string): string | null {
  const value = record[key];
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

function readStringList(record: Record<string, unknown>, key: string): string[] {
  const value = record[key];
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((item): item is string => typeof item === "string");
}

function asSeverity(value: unknown): RiskSeverity {
  return value === "medium" || value === "high" ? value : "low";
}

function asAssessment(value: unknown): HistoryAssessment {
  return value === "consistent" ||
    value === "minor_discrepancies" ||
    value === "major_discrepancies"
    ? value
    : "no_history";
}

export function parseAdjudicationPayload(
  payload: Record<string, unknown> | null,
): AdjudicationPayloadView {
  if (payload === null) {
    return {
      summary: null,
      risk_factors: [],
      consistency_with_history: null,
      similar_case_outcomes: [],
      advisory_notice: null,
    };
  }

  const riskFactorsRaw = payload.risk_factors;
  const riskFactors: RiskFactorView[] = Array.isArray(riskFactorsRaw)
    ? riskFactorsRaw.filter(isRecord).map((item) => ({
        factor: readString(item, "factor") ?? "Unspecified risk factor",
        severity: asSeverity(item.severity),
        source: readString(item, "source") ?? "unknown",
      }))
    : [];

  const consistencyRaw = payload.consistency_with_history;
  const consistency: ConsistencyView | null = isRecord(consistencyRaw)
    ? {
        assessment: asAssessment(consistencyRaw.assessment),
        details: readString(consistencyRaw, "details") ?? "",
      }
    : null;

  const similarRaw = payload.similar_case_outcomes;
  const similarCases: SimilarCaseView[] = Array.isArray(similarRaw)
    ? similarRaw.filter(isRecord).map((item) => ({
        case_ref: readString(item, "case_ref") ?? "Unknown case",
        similarity: typeof item.similarity === "number" ? item.similarity : 0,
        outcome: readString(item, "outcome") ?? "unknown",
        relevance_note: readString(item, "relevance_note") ?? "",
      }))
    : [];

  return {
    summary: readString(payload, "summary"),
    risk_factors: riskFactors,
    consistency_with_history: consistency,
    similar_case_outcomes: similarCases,
    advisory_notice: readString(payload, "advisory_notice"),
  };
}

export function parseRecommendationPayload(
  payload: Record<string, unknown> | null,
): RecommendationPayloadView {
  if (payload === null) {
    return { summary: null, identified_gaps: [] };
  }
  return {
    summary: readString(payload, "summary"),
    identified_gaps: readStringList(payload, "identified_gaps"),
  };
}

export function parseDiagnosticPayload(
  payload: Record<string, unknown> | null,
): DiagnosticPayloadView {
  if (payload === null) {
    return { impression: null };
  }
  return { impression: readString(payload, "impression") };
}

/** "82%" for 0.82; a plain hyphen when the backend has no confidence yet. */
export function formatConfidence(value: number | null): string {
  if (value === null) {
    return "-";
  }
  return `${Math.round(value * 100)}%`;
}
