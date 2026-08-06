type Endpoint = {
  method: "GET" | "POST";
  path: string;
  summary: string;
  requestBody?: string;
  responseShape: string;
  curl: string;
};

const BASE_URL = "https://api.zoikolocal.com"; // replace with your actual API host

const ENDPOINTS: Endpoint[] = [
  {
    method: "GET",
    path: "/public/v1/numbers",
    summary: "List your phone numbers.",
    responseShape:
      "[{ id, e164, country, status, forwarding_number, ai_receptionist_enabled, created_at }]",
    curl: `curl ${BASE_URL}/public/v1/numbers \\\n  -H "Authorization: Bearer $ZOIKO_API_KEY"`,
  },
  {
    method: "GET",
    path: "/public/v1/calls",
    summary: "List call log entries.",
    responseShape: "[{ id, direction, from_number, to_number, status, duration, created_at }]",
    curl: `curl ${BASE_URL}/public/v1/calls \\\n  -H "Authorization: Bearer $ZOIKO_API_KEY"`,
  },
  {
    method: "POST",
    path: "/public/v1/calls",
    summary: "Place an outbound call from one of your active numbers.",
    requestBody: '{ "to": "+15551234567", "from_number": "+15559998888", "message": "..." }',
    responseShape: "{ sid, status, to }",
    curl: `curl -X POST ${BASE_URL}/public/v1/calls \\\n  -H "Authorization: Bearer $ZOIKO_API_KEY" \\\n  -H "Content-Type: application/json" \\\n  -d '{"to": "+15551234567", "from_number": "+15559998888"}'`,
  },
  {
    method: "GET",
    path: "/public/v1/voicemails",
    summary: "List voicemails across your numbers.",
    responseShape: "[{ id, from_number, duration, created_at }]",
    curl: `curl ${BASE_URL}/public/v1/voicemails \\\n  -H "Authorization: Bearer $ZOIKO_API_KEY"`,
  },
  {
    method: "GET",
    path: "/public/v1/summaries",
    summary: "List AI-generated call and voicemail summaries.",
    responseShape: "[{ id, source_type, source_id, summary, urgency, created_at }]",
    curl: `curl ${BASE_URL}/public/v1/summaries \\\n  -H "Authorization: Bearer $ZOIKO_API_KEY"`,
  },
  {
    method: "GET",
    path: "/public/v1/contacts",
    summary: "List your account's saved contacts.",
    responseShape: "[{ id, name, phone_number, email, notes, created_at }]",
    curl: `curl ${BASE_URL}/public/v1/contacts \\\n  -H "Authorization: Bearer $ZOIKO_API_KEY"`,
  },
  {
    method: "POST",
    path: "/public/v1/contacts",
    summary: "Create a contact - e.g. to sync leads in from an external system.",
    requestBody: '{ "name": "Jane Doe", "phone_number": "+15551234567", "email": "jane@example.com" }',
    responseShape: "{ id, name, phone_number, email, notes, created_at }",
    curl: `curl -X POST ${BASE_URL}/public/v1/contacts \\\n  -H "Authorization: Bearer $ZOIKO_API_KEY" \\\n  -H "Content-Type: application/json" \\\n  -d '{"name": "Jane Doe", "phone_number": "+15551234567"}'`,
  },
];

function MethodBadge({ method }: { method: "GET" | "POST" }) {
  return (
    <span
      className={`text-xs font-mono font-semibold rounded px-1.5 py-0.5 ${
        method === "GET" ? "bg-emerald-50 text-emerald-700" : "bg-indigo-50 text-indigo-700"
      }`}
    >
      {method}
    </span>
  );
}

export default function ApiDocsPage() {
  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-slate-900">API Reference</h2>
        <p className="text-sm text-slate-500">
          A small, curated public surface — read access to your numbers, calls, voicemails, contacts, and AI
          summaries, plus two write actions (placing a call, creating a contact). Everything else about your
          account (billing, team, number purchasing) is managed through the dashboard only, not this API.
        </p>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-3">
        <h3 className="font-semibold text-slate-900">Authentication</h3>
        <p className="text-sm text-slate-600">
          Every request needs an API key, created from{" "}
          <a href="/dashboard/business" className="text-indigo-600 hover:text-indigo-800 font-medium">
            Business → Public API Keys
          </a>
          . Pass it as a bearer token:
        </p>
        <pre className="bg-slate-900 text-slate-100 text-xs rounded-lg p-3 overflow-x-auto">
          <code>Authorization: Bearer zlk_live_...</code>
        </pre>
        <p className="text-xs text-slate-500">
          Keys are shown once at creation and can be revoked at any time. There is currently no rate limit
          enforced on API key requests — keep keys private and revoke any that may have leaked.
        </p>
      </div>

      <div className="space-y-4">
        {ENDPOINTS.map((ep) => (
          <div key={`${ep.method}-${ep.path}`} className="bg-white rounded-xl border border-slate-200 p-6 space-y-3">
            <div className="flex items-center gap-2">
              <MethodBadge method={ep.method} />
              <code className="text-sm font-mono text-slate-800">{ep.path}</code>
            </div>
            <p className="text-sm text-slate-600">{ep.summary}</p>

            {ep.requestBody && (
              <div>
                <div className="text-xs font-medium text-slate-500 mb-1">Request body</div>
                <pre className="bg-slate-50 border border-slate-200 text-xs rounded-lg p-2.5 overflow-x-auto">
                  <code>{ep.requestBody}</code>
                </pre>
              </div>
            )}

            <div>
              <div className="text-xs font-medium text-slate-500 mb-1">Response</div>
              <pre className="bg-slate-50 border border-slate-200 text-xs rounded-lg p-2.5 overflow-x-auto">
                <code>{ep.responseShape}</code>
              </pre>
            </div>

            <div>
              <div className="text-xs font-medium text-slate-500 mb-1">Example</div>
              <pre className="bg-slate-900 text-slate-100 text-xs rounded-lg p-3 overflow-x-auto whitespace-pre-wrap">
                <code>{ep.curl}</code>
              </pre>
            </div>
          </div>
        ))}
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-2">
        <h3 className="font-semibold text-slate-900">Errors</h3>
        <p className="text-sm text-slate-600">
          Standard HTTP status codes: <code className="text-xs bg-slate-100 rounded px-1 py-0.5">401</code> for a
          missing/invalid/revoked key, <code className="text-xs bg-slate-100 rounded px-1 py-0.5">402</code> if
          billing is suspended, <code className="text-xs bg-slate-100 rounded px-1 py-0.5">403</code> for an
          authorization failure (e.g. calling from a number your account doesn&apos;t own, or a blocked
          destination), <code className="text-xs bg-slate-100 rounded px-1 py-0.5">429</code> for rate limiting,
          and <code className="text-xs bg-slate-100 rounded px-1 py-0.5">422</code> for a validation error. Error
          responses have a <code className="text-xs bg-slate-100 rounded px-1 py-0.5">detail</code> field
          describing what went wrong.
        </p>
      </div>
    </div>
  );
}
