"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import type { FormEvent } from "react";

import { DicomBadge, KindChip, ModalityChip } from "@/components/document-chips";
import { DocumentUploader } from "@/components/document-uploader";
import { PortalShell } from "@/components/portal-shell";
import { ApiError, apiFetch } from "@/lib/api-client";
import { formatBytes, formatCurrency } from "@/lib/format";
import type { AnalyzeOut, ClaimOut, DocumentOut } from "@/lib/types";

const CLAIM_TYPES = ["imaging", "physio", "dental", "prescription"] as const;

const STEPS = ["Details", "Documents", "Review"] as const;

const INPUT_CLASS =
  "mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-blue-600 focus:outline-none focus:ring-1 focus:ring-blue-600 disabled:bg-slate-50 disabled:text-slate-500";

function StepHeader({ current }: { current: number }) {
  return (
    <ol className="mb-6 flex items-center gap-2">
      {STEPS.map((label, index) => (
        <li key={label} className="flex items-center gap-2">
          {index > 0 ? <span className="h-px w-8 bg-slate-300" aria-hidden="true" /> : null}
          <span
            className={`flex h-6 w-6 items-center justify-center rounded-full text-xs font-semibold ${
              index === current
                ? "bg-blue-700 text-white"
                : index < current
                  ? "bg-emerald-100 text-emerald-700"
                  : "bg-slate-200 text-slate-500"
            }`}
          >
            {index + 1}
          </span>
          <span
            className={`text-sm ${index === current ? "font-semibold text-slate-900" : "text-slate-500"}`}
          >
            {label}
          </span>
        </li>
      ))}
    </ol>
  );
}

export default function NewClaimPage() {
  const router = useRouter();
  const [step, setStep] = useState(0);

  // Step 1 fields.
  const [claimType, setClaimType] = useState<string>("imaging");
  const [procedureCode, setProcedureCode] = useState("");
  const [diagnosisCode, setDiagnosisCode] = useState("");
  const [incidentDate, setIncidentDate] = useState("");
  const [amountClaimed, setAmountClaimed] = useState("");
  const [description, setDescription] = useState("");

  // Created claim + uploads.
  const [claim, setClaim] = useState<ClaimOut | null>(null);
  const [uploaded, setUploaded] = useState<DocumentOut[]>([]);

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const hasImagingDoc = uploaded.some((doc) => doc.kind === "imaging");

  const handleDetailsSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);

    // The backend has no claim-update endpoint, so the claim is created once
    // and the details are locked afterwards; Continue simply advances.
    if (claim !== null) {
      setStep(1);
      return;
    }

    const amount = Number(amountClaimed);
    if (!Number.isFinite(amount) || amount < 0) {
      setError("Please enter a valid claim amount of 0 or more.");
      return;
    }
    if (description.trim().length === 0) {
      setError("Please describe the claim.");
      return;
    }

    setSubmitting(true);
    try {
      const created = await apiFetch<ClaimOut>("/api/claims", {
        method: "POST",
        body: {
          claim_type: claimType,
          description: description.trim(),
          procedure_code: procedureCode.trim(),
          diagnosis_code: diagnosisCode.trim(),
          incident_date: incidentDate === "" ? null : incidentDate,
          amount_claimed: amount,
        },
      });
      setClaim(created);
      setStep(1);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Unable to reach the API.");
    } finally {
      setSubmitting(false);
    }
  };

  const handleAnalyze = async () => {
    if (claim === null) {
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      await apiFetch<AnalyzeOut>(`/api/claims/${claim.id}/analyze`, { method: "POST" });
      router.push(`/claimant/claims/${claim.id}`);
    } catch (err) {
      // 409 (wrong state / analysis already running) and 422 (no imaging
      // document) come back as ApiError with a human-readable detail.
      setError(err instanceof ApiError ? err.detail : "Unable to reach the API.");
      setSubmitting(false);
    }
  };

  return (
    <PortalShell title="New claim" subtitle="Submit a medical claim in three steps">
      <div className="mx-auto max-w-2xl">
        <div className="mb-4">
          <Link href="/claimant" className="text-sm text-blue-700 hover:underline">
            Back to your claims
          </Link>
        </div>

        <StepHeader current={step} />

        {error ? (
          <p role="alert" className="mb-4 rounded-md border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700">
            {error}
          </p>
        ) : null}

        {step === 0 ? (
          <form
            onSubmit={(event) => void handleDetailsSubmit(event)}
            className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm"
          >
            {claim !== null ? (
              <p className="mb-4 rounded-md bg-slate-50 px-3 py-2 text-xs text-slate-500">
                Claim {claim.claim_ref} has been created; details are locked.
              </p>
            ) : null}

            <label htmlFor="claim-type" className="block text-sm font-medium text-slate-700">
              Claim type
            </label>
            <select
              id="claim-type"
              required
              value={claimType}
              disabled={submitting || claim !== null}
              onChange={(event) => setClaimType(event.target.value)}
              className={INPUT_CLASS}
            >
              {CLAIM_TYPES.map((type) => (
                <option key={type} value={type}>
                  {type.charAt(0).toUpperCase() + type.slice(1)}
                </option>
              ))}
            </select>

            <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <label
                  htmlFor="procedure-code"
                  className="block text-sm font-medium text-slate-700"
                >
                  Procedure code
                </label>
                <input
                  id="procedure-code"
                  type="text"
                  maxLength={16}
                  value={procedureCode}
                  disabled={submitting || claim !== null}
                  onChange={(event) => setProcedureCode(event.target.value)}
                  className={INPUT_CLASS}
                  placeholder="e.g. 71045"
                />
              </div>
              <div>
                <label
                  htmlFor="diagnosis-code"
                  className="block text-sm font-medium text-slate-700"
                >
                  Diagnosis code
                </label>
                <input
                  id="diagnosis-code"
                  type="text"
                  maxLength={16}
                  value={diagnosisCode}
                  disabled={submitting || claim !== null}
                  onChange={(event) => setDiagnosisCode(event.target.value)}
                  className={INPUT_CLASS}
                  placeholder="e.g. S42.001"
                />
              </div>
            </div>

            <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <label htmlFor="incident-date" className="block text-sm font-medium text-slate-700">
                  Incident date
                </label>
                <input
                  id="incident-date"
                  type="date"
                  value={incidentDate}
                  disabled={submitting || claim !== null}
                  onChange={(event) => setIncidentDate(event.target.value)}
                  className={INPUT_CLASS}
                />
              </div>
              <div>
                <label
                  htmlFor="amount-claimed"
                  className="block text-sm font-medium text-slate-700"
                >
                  Amount claimed (CAD)
                </label>
                <input
                  id="amount-claimed"
                  type="number"
                  min={0}
                  step="0.01"
                  required
                  value={amountClaimed}
                  disabled={submitting || claim !== null}
                  onChange={(event) => setAmountClaimed(event.target.value)}
                  className={INPUT_CLASS}
                  placeholder="0.00"
                />
              </div>
            </div>

            <label htmlFor="description" className="mt-4 block text-sm font-medium text-slate-700">
              Description
            </label>
            <textarea
              id="description"
              required
              rows={4}
              value={description}
              disabled={submitting || claim !== null}
              onChange={(event) => setDescription(event.target.value)}
              className={INPUT_CLASS}
              placeholder="Briefly describe the incident and the treatment received."
            />

            <div className="mt-6 flex justify-end">
              <button
                type="submit"
                disabled={submitting}
                className="rounded-md bg-blue-700 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-blue-800 disabled:opacity-50"
              >
                {submitting ? "Creating claim..." : "Continue"}
              </button>
            </div>
          </form>
        ) : null}

        {step === 1 && claim !== null ? (
          <div className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
            <h2 className="text-sm font-semibold text-slate-900">
              Upload documents for {claim.claim_ref}
            </h2>
            <p className="mt-1 text-sm text-slate-500">
              At least one imaging document (X-Ray, CT, or MRI) is required before the claim can
              be analyzed.
            </p>

            <div className="mt-4">
              <DocumentUploader
                claimId={claim.id}
                uploaded={uploaded}
                onUploaded={(doc) => setUploaded((prev) => [...prev, doc])}
                disabled={submitting}
              />
            </div>

            {!hasImagingDoc ? (
              <p className="mt-4 text-sm text-amber-700">
                Upload at least one imaging document to continue.
              </p>
            ) : null}

            <div className="mt-6 flex items-center justify-between">
              <button
                type="button"
                onClick={() => setStep(0)}
                className="rounded-md border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-100"
              >
                Back
              </button>
              <button
                type="button"
                disabled={!hasImagingDoc}
                onClick={() => setStep(2)}
                className="rounded-md bg-blue-700 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-blue-800 disabled:opacity-50"
              >
                Continue
              </button>
            </div>
          </div>
        ) : null}

        {step === 2 && claim !== null ? (
          <div className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
            <h2 className="text-sm font-semibold text-slate-900">Review and submit</h2>

            <dl className="mt-4 grid grid-cols-1 gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
              <div>
                <dt className="text-slate-500">Reference</dt>
                <dd className="font-mono text-slate-900">{claim.claim_ref}</dd>
              </div>
              <div>
                <dt className="text-slate-500">Type</dt>
                <dd className="capitalize text-slate-900">{claim.claim_type}</dd>
              </div>
              <div>
                <dt className="text-slate-500">Procedure code</dt>
                <dd className="text-slate-900">{claim.procedure_code || "Not provided"}</dd>
              </div>
              <div>
                <dt className="text-slate-500">Diagnosis code</dt>
                <dd className="text-slate-900">{claim.diagnosis_code || "Not provided"}</dd>
              </div>
              <div>
                <dt className="text-slate-500">Incident date</dt>
                <dd className="text-slate-900">{claim.incident_date ?? "Not provided"}</dd>
              </div>
              <div>
                <dt className="text-slate-500">Amount claimed</dt>
                <dd className="tabular-nums text-slate-900">
                  {formatCurrency(claim.amount_claimed)}
                </dd>
              </div>
              <div className="sm:col-span-2">
                <dt className="text-slate-500">Description</dt>
                <dd className="whitespace-pre-wrap text-slate-900">{claim.description}</dd>
              </div>
            </dl>

            <h3 className="mt-6 text-sm font-semibold text-slate-900">
              Documents ({uploaded.length})
            </h3>
            <ul className="mt-2 space-y-2">
              {uploaded.map((doc) => (
                <li
                  key={doc.id}
                  className="flex flex-wrap items-center gap-2 rounded-md border border-slate-200 bg-slate-50 px-4 py-2.5 text-sm"
                >
                  <span className="min-w-0 flex-1 truncate text-slate-900">{doc.filename}</span>
                  <span className="text-xs text-slate-500">{formatBytes(doc.size_bytes)}</span>
                  <KindChip kind={doc.kind} />
                  <ModalityChip modality={doc.modality} />
                  <DicomBadge hasPreview={doc.has_preview} />
                </li>
              ))}
            </ul>

            <div className="mt-6 flex items-center justify-between">
              <button
                type="button"
                disabled={submitting}
                onClick={() => setStep(1)}
                className="rounded-md border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-100 disabled:opacity-50"
              >
                Back
              </button>
              <button
                type="button"
                disabled={submitting}
                onClick={() => void handleAnalyze()}
                className="rounded-md bg-blue-700 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-blue-800 disabled:opacity-50"
              >
                {submitting ? "Submitting..." : "Submit for analysis"}
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </PortalShell>
  );
}
