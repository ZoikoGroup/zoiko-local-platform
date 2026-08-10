"use client";

import { useEffect, useState } from "react";
import {
  listMyComplianceCases,
  startKycVerification,
  submitComplianceDocument,
  getComplianceDocumentDownloadUrl,
  listConsentStatus,
  ApiError,
  type MyComplianceCase,
  type ConsentRecordStatus,
} from "@/lib/api";
import { getToken } from "@/lib/auth";

const STATUS_STYLES: Record<string, string> = {
  pending: "bg-amber-50 text-amber-700",
  approved: "bg-emerald-50 text-emerald-700",
  rejected: "bg-red-50 text-red-700",
  expired: "bg-slate-100 text-slate-600",
};

export default function CompliancePage() {
  const [cases, setCases] = useState<MyComplianceCase[]>([]);
  const [casesLoading, setCasesLoading] = useState(true);
  const [verifyingCaseId, setVerifyingCaseId] = useState<string | null>(null);
  const [verifyError, setVerifyError] = useState<string | null>(null);
  const [uploadOpenCaseId, setUploadOpenCaseId] = useState<string | null>(null);
  const [uploadDocumentType, setUploadDocumentType] = useState("government_id");
  const [uploadingCaseId, setUploadingCaseId] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [downloadingDoc, setDownloadingDoc] = useState<string | null>(null);

  const [consentRecords, setConsentRecords] = useState<ConsentRecordStatus[]>([]);
  const [consentLoading, setConsentLoading] = useState(true);
  const [consentError, setConsentError] = useState<string | null>(null);

  useEffect(() => {
    const token = getToken();
    if (!token) return;
    listMyComplianceCases(token)
      .then(setCases)
      .finally(() => setCasesLoading(false));
    listConsentStatus(token)
      .then((records) => {
        setConsentRecords(records);
        setConsentError(null);
      })
      .catch(() => setConsentError("Couldn't load consent records."))
      .finally(() => setConsentLoading(false));
  }, []);

  async function handleStartVerification(caseId: string) {
    const token = getToken();
    if (!token) return;
    setVerifyingCaseId(caseId);
    setVerifyError(null);
    try {
      const { inquiry_id, verification_url } = await startKycVerification(token, caseId);
      setCases((prev) =>
        prev.map((c) => (c.id === caseId ? { ...c, status: "pending", kyc_inquiry_id: inquiry_id } : c))
      );
      window.open(verification_url, "_blank", "noopener,noreferrer");
    } catch (err) {
      setVerifyError(err instanceof ApiError ? err.message : "Couldn't start identity verification.");
    } finally {
      setVerifyingCaseId(null);
    }
  }

  async function handleUploadDocument(caseId: string, file: File) {
    const token = getToken();
    if (!token) return;
    setUploadingCaseId(caseId);
    setUploadError(null);
    try {
      const updated = await submitComplianceDocument(token, caseId, uploadDocumentType, file);
      setCases((prev) => prev.map((c) => (c.id === caseId ? updated : c)));
      setUploadOpenCaseId(null);
    } catch (err) {
      setUploadError(err instanceof ApiError ? err.message : "Couldn't upload document.");
    } finally {
      setUploadingCaseId(null);
    }
  }

  async function handleViewDocument(caseId: string, documentIndex: number) {
    const token = getToken();
    if (!token) return;
    const key = `${caseId}:${documentIndex}`;
    setDownloadingDoc(key);
    setUploadError(null);
    try {
      const { url } = await getComplianceDocumentDownloadUrl(token, caseId, documentIndex);
      window.open(url, "_blank", "noopener,noreferrer");
    } catch (err) {
      setUploadError(err instanceof ApiError ? err.message : "Couldn't open document.");
    } finally {
      setDownloadingDoc(null);
    }
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-slate-900">Compliance</h2>
        <p className="text-sm text-slate-500">
          Identity verification status and every consent or disclosure on record for this account.
        </p>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <div>
          <h3 className="font-semibold text-slate-900">Identity verification</h3>
          <p className="text-xs text-slate-500 mt-0.5">
            Status of any ID verification requests on your account.
          </p>
        </div>

        {casesLoading && <p className="text-sm text-slate-500">Loading...</p>}

        {(verifyError || uploadError) && (
          <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{verifyError || uploadError}</p>
        )}

        {!casesLoading && cases.length === 0 && (
          <p className="text-sm text-slate-500">
            No verification requests yet — these appear here when a number purchase requires ID
            checks for that country.
          </p>
        )}

        {!casesLoading && cases.length > 0 && (
          <ul className="divide-y divide-slate-100">
            {cases.map((c) => (
              <li key={c.id} className="py-3 space-y-2">
                <div className="flex items-center justify-between gap-4">
                  <div>
                    <div className="text-sm font-medium text-slate-800">
                      {c.jurisdiction} — {c.requirement_type.replaceAll("_", " ")}
                    </div>
                    <div className="text-xs text-slate-500 mt-0.5">
                      {c.documents.length} document{c.documents.length === 1 ? "" : "s"} submitted ·{" "}
                      {new Date(c.created_at).toLocaleDateString()}
                    </div>
                  </div>
                  <div className="flex items-center gap-3 shrink-0">
                    {(c.status === "pending" || c.status === "rejected") && (
                      <button
                        onClick={() => handleStartVerification(c.id)}
                        disabled={verifyingCaseId === c.id}
                        className="text-xs font-medium text-indigo-600 hover:text-indigo-800 disabled:opacity-50"
                      >
                        {verifyingCaseId === c.id
                          ? "Starting…"
                          : c.status === "rejected"
                            ? "Try again"
                            : c.kyc_inquiry_id
                              ? "Continue verification"
                              : "Verify identity"}
                      </button>
                    )}
                    <span
                      className={`text-xs font-medium rounded-full px-2.5 py-1 capitalize ${
                        STATUS_STYLES[c.status] ?? "bg-slate-100 text-slate-600"
                      }`}
                    >
                      {c.status}
                    </span>
                  </div>
                </div>

                {c.documents.length > 0 && (
                  <ul className="space-y-1 pl-0.5">
                    {c.documents.map((d, i) => {
                      const docKey = `${c.id}:${i}`;
                      return (
                        <li key={docKey} className="flex items-center gap-2 text-xs text-slate-600">
                          <span>{d.document_type.replaceAll("_", " ")} — {d.filename}</span>
                          <button
                            onClick={() => handleViewDocument(c.id, i)}
                            disabled={downloadingDoc === docKey}
                            className="text-indigo-600 hover:text-indigo-800 disabled:opacity-50 font-medium"
                          >
                            {downloadingDoc === docKey ? "Opening…" : "View"}
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                )}

                {c.status !== "approved" && (
                  <div>
                    {uploadOpenCaseId === c.id ? (
                      <div className="flex items-center gap-2 flex-wrap">
                        <select
                          value={uploadDocumentType}
                          onChange={(e) => setUploadDocumentType(e.target.value)}
                          className="text-xs rounded-lg border border-slate-200 px-2 py-1.5"
                        >
                          <option value="government_id">Government ID</option>
                          <option value="business_registration">Business registration</option>
                          <option value="proof_of_address">Proof of address</option>
                          <option value="other">Other</option>
                        </select>
                        <input
                          type="file"
                          accept="application/pdf,image/jpeg,image/png"
                          disabled={uploadingCaseId === c.id}
                          onChange={(e) => {
                            const file = e.target.files?.[0];
                            if (file) handleUploadDocument(c.id, file);
                          }}
                          className="text-xs"
                        />
                        {uploadingCaseId === c.id && <span className="text-xs text-slate-500">Uploading…</span>}
                        <button
                          onClick={() => setUploadOpenCaseId(null)}
                          className="text-xs text-slate-500 hover:text-slate-700"
                        >
                          Cancel
                        </button>
                      </div>
                    ) : (
                      <button
                        onClick={() => {
                          setUploadOpenCaseId(c.id);
                          setUploadError(null);
                        }}
                        className="text-xs font-medium text-indigo-600 hover:text-indigo-800"
                      >
                        Upload document
                      </button>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <div>
          <h3 className="font-semibold text-slate-900">Consent &amp; Disclosures</h3>
          <p className="text-xs text-slate-500 mt-0.5">
            Every consent or disclosure acknowledgment on record for this account - AI processing (used for call
            summaries and the AI receptionist) and the emergency-calling limitation notice.
          </p>
        </div>

        {consentLoading && <p className="text-sm text-slate-500">Loading...</p>}
        {consentError && <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{consentError}</p>}

        {!consentLoading && consentRecords.length === 0 && (
          <p className="text-sm text-slate-500">No consent has been granted yet.</p>
        )}

        {!consentLoading && consentRecords.length > 0 && (
          <ul className="divide-y divide-slate-100">
            {consentRecords.map((r) => {
              const active = r.granted_at && !r.revoked_at;
              return (
                <li key={`${r.consent_type}:${r.jurisdiction}`} className="py-3 flex items-center justify-between gap-4">
                  <div>
                    <div className="text-sm font-medium text-slate-800 capitalize">
                      {r.consent_type.replaceAll("_", " ")}
                    </div>
                    <div className="text-xs text-slate-500 mt-0.5">
                      {r.jurisdiction === "GLOBAL" ? "Applies everywhere" : r.jurisdiction}
                      {r.granted_at && ` · granted ${new Date(r.granted_at).toLocaleDateString()}`}
                      {r.revoked_at && ` · revoked ${new Date(r.revoked_at).toLocaleDateString()}`}
                    </div>
                  </div>
                  <span
                    className={`text-xs font-medium rounded-full px-2.5 py-1 shrink-0 ${
                      active ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-600"
                    }`}
                  >
                    {active ? "Active" : "Revoked"}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
