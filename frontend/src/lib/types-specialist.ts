/**
 * Types mirroring the specialist-portal response models in
 * backend/app/routers/specialist.py plus the artifact payload shapes from
 * backend/app/llm/schemas.py and backend/app/ml/base.py (snake_case, as
 * serialized by FastAPI). Shared enums are imported read-only from types.ts.
 */

import type {
  ArtifactStatus,
  AuthenticityVerdict,
  ClaimState,
  DocumentKind,
  Modality,
} from "@/lib/types";

/** Mirrors `RecommendationNoteLLM.recommendation` in backend/app/llm/schemas.py. */
export type Recommendation =
  | "SUPPORTS_CLAIM"
  | "INSUFFICIENT_EVIDENCE"
  | "REQUIRES_FURTHER_TESTING";

/** Mirrors `QueueItem` in backend/app/routers/specialist.py (stage=imaging). */
export interface ImagingQueueItem {
  claim_id: number;
  claim_ref: string;
  claim_type: string;
  claimant: string;
  state: ClaimState;
  submitted_at: string;
  report_status: ArtifactStatus | null;
  modality: Modality | null;
  authenticity_verdict: AuthenticityVerdict | null;
  authenticity_risk: number | null;
  requires_mandatory_review: boolean | null;
}

/** Mirrors `RecommendationQueueItem` in backend/app/routers/specialist.py. */
export interface RecommendationQueueItem {
  claim_id: number;
  claim_ref: string;
  claim_type: string;
  claimant: string;
  state: ClaimState;
  submitted_at: string;
  note_status: ArtifactStatus | null;
  recommendation: Recommendation | null;
  confidence: number | null;
  requires_mandatory_review: boolean | null;
}

/** Mirrors `CaseDocument` in backend/app/routers/specialist.py. */
export interface CaseDocument {
  id: number;
  filename: string;
  kind: DocumentKind;
  modality: Modality | null;
  has_preview: boolean;
}

/** Mirrors `ForensicSignal` in backend/app/ml/base.py (system-injected). */
export interface ForensicSignal {
  name: string;
  score: number;
  finding: string;
}

/** System-injected `payload.authenticity` section (stage1_diagnostic.py). */
export interface AuthenticitySection {
  verdict: AuthenticityVerdict;
  risk_score: number;
  signals: ForensicSignal[];
}

/** System-injected `payload.classifier` section (stage1_diagnostic.py). */
export interface ClassifierSection {
  modality: string;
  confidence: number;
}

export type FindingSeverity = "normal" | "minor" | "moderate" | "significant";

/** Mirrors `Finding` in backend/app/llm/schemas.py. */
export interface ReportFinding {
  description: string;
  location: string | null;
  severity: FindingSeverity;
  confidence: number;
}

export type ImageQuality = "adequate" | "degraded" | "non_diagnostic";

/** `DiagnosticReportLLM` plus the system-injected sections (stage1_diagnostic.py). */
export interface DiagnosticReportPayload {
  modality_assessment: "xray" | "ct" | "mri" | "other";
  modality_agrees_with_classifier: boolean;
  anatomical_region: string;
  view: string | null;
  image_quality: ImageQuality;
  quality_issues: string[];
  findings: ReportFinding[];
  impression: string;
  visual_inconsistencies: string[];
  confidence: number;
  authenticity: AuthenticitySection;
  classifier: ClassifierSection;
  disclaimer: string;
}

/** Mirrors `SupportingFinding` in backend/app/llm/schemas.py. */
export interface SupportingFinding {
  source_document: string;
  finding: string;
  relevance: string;
}

export type ConsistencyCheckResult =
  | "consistent"
  | "inconsistent"
  | "indeterminate"
  | "not_applicable";

/** Mirrors `ConsistencyCheck` in backend/app/llm/schemas.py. */
export interface ConsistencyCheck {
  check: string;
  result: ConsistencyCheckResult;
  detail: string;
}

/** System-injected `payload.documents_reviewed` entry (stage2_recommendation.py). */
export interface ReviewedDocument {
  filename: string;
  sha256: string;
}

/** `RecommendationNoteLLM` plus the system-injected sections (stage2_recommendation.py). */
export interface RecommendationNotePayload {
  recommendation: Recommendation;
  confidence: number;
  summary: string;
  supporting_findings: SupportingFinding[];
  identified_gaps: string[];
  suggested_next_steps: string[];
  consistency_checks: ConsistencyCheck[];
  advisory_notice: string;
  documents_reviewed: ReviewedDocument[];
}

/** Mirrors `CaseReport` in backend/app/routers/specialist.py. */
export interface CaseReport {
  id: number;
  status: ArtifactStatus;
  modality: Modality | null;
  modality_confidence: number | null;
  authenticity_verdict: AuthenticityVerdict | null;
  authenticity_risk: number | null;
  requires_mandatory_review: boolean;
  payload: DiagnosticReportPayload | null;
  generated_by: string;
  fallback_reason: string | null;
  error: string | null;
}

/** Mirrors `CaseNote` in backend/app/routers/specialist.py. */
export interface CaseNote {
  id: number;
  status: ArtifactStatus;
  recommendation: Recommendation | null;
  confidence: number | null;
  requires_mandatory_review: boolean;
  payload: RecommendationNotePayload | null;
  generated_by: string;
  fallback_reason: string | null;
  error: string | null;
}

/** Mirrors `CaseDetail` in backend/app/routers/specialist.py (artifacts newest first). */
export interface CaseDetail {
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
  claimant: string;
  documents: CaseDocument[];
  diagnostic_reports: CaseReport[];
  recommendation_notes: CaseNote[];
}

/** Mirrors `RegenerateBody.stage` in backend/app/routers/specialist.py. */
export type RegenerateStage = "imaging" | "recommendation" | "adjudication";

/** Mirrors `ForwardOut` in backend/app/routers/specialist.py. */
export interface ForwardOut {
  claim_id: number;
  state: ClaimState;
  note_id: number;
  note_status: ArtifactStatus;
}

/** Mirrors `ReturnOut` (shared by return and request-further-testing). */
export interface ReturnOut {
  claim_id: number;
  state: ClaimState;
  notification_id: number;
}

/** Mirrors `SendToInsurerOut` in backend/app/routers/specialist.py. */
export interface SendToInsurerOut {
  claim_id: number;
  state: ClaimState;
  summary_id: number;
  summary_status: ArtifactStatus;
}

/** Mirrors `RegenerateOut` in backend/app/routers/specialist.py. */
export interface RegenerateOut {
  claim_id: number;
  stage: RegenerateStage;
  artifact_id: number;
  status: ArtifactStatus;
}
