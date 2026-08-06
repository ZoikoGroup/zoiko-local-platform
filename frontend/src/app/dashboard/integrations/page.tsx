"use client";

import { useEffect, useState, useCallback, type FormEvent } from "react";
import {
  getPublicStatus,
  listMyNumbers,
  getCurrentUser,
  listWebhookEndpoints,
  createWebhookEndpoint,
  deleteWebhookEndpoint,
  listWebhookDeliveries,
  listApiKeys,
  createApiKey,
  revokeApiKey,
  getCrmConnection,
  connectCrm,
  disconnectCrm,
  listCrmSyncEvents,
  ApiError,
  type PublicStatus,
  type MyPhoneNumber,
  type WebhookEndpoint,
  type WebhookDelivery,
  type ApiKey,
  type CrmConnection,
  type CrmSyncEvent,
  type CrmProvider,
} from "@/lib/api";
import { getToken } from "@/lib/auth";

function Dot({ status }: { status: "operational" | "degraded" }) {
  return (
    <span
      className={`inline-block w-2.5 h-2.5 rounded-full ${
        status === "operational" ? "bg-emerald-500" : "bg-amber-500"
      }`}
    />
  );
}

function statusLabel(status: "operational" | "degraded") {
  return status === "operational" ? "Operational" : "Degraded performance";
}

export default function IntegrationsPage() {
  const [token] = useState<string | null>(() => getToken());
  const [status, setStatus] = useState<PublicStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [numbers, setNumbers] = useState<MyPhoneNumber[]>([]);
  const [loading, setLoading] = useState(true);

  const [isAdmin, setIsAdmin] = useState(false);
  const [endpoints, setEndpoints] = useState<WebhookEndpoint[]>([]);
  const [deliveries, setDeliveries] = useState<WebhookDelivery[]>([]);
  const [webhooksLoading, setWebhooksLoading] = useState(true);
  const [webhooksError, setWebhooksError] = useState<string | null>(null);
  const [newUrl, setNewUrl] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [creating, setCreating] = useState(false);
  const [revealedSecret, setRevealedSecret] = useState<string | null>(null);

  const loadWebhooks = useCallback(() => {
    if (!token) return;
    return Promise.all([listWebhookEndpoints(token), listWebhookDeliveries(token)])
      .then(([endpointList, deliveryList]) => {
        setEndpoints(endpointList);
        setDeliveries(deliveryList);
        setWebhooksError(null);
      })
      .catch(() => setWebhooksError("Couldn't load webhook endpoints."))
      .finally(() => setWebhooksLoading(false));
  }, [token]);

  useEffect(() => {
    if (!token) return;
    getCurrentUser(token).then((me) => setIsAdmin(me.role === "owner" || me.role === "admin"));
    loadWebhooks();
  }, [token, loadWebhooks]);

  async function handleCreateEndpoint(e: FormEvent) {
    e.preventDefault();
    if (!token || !newUrl) return;
    setCreating(true);
    setWebhooksError(null);
    try {
      const created = await createWebhookEndpoint(token, {
        url: newUrl,
        description: newDescription || undefined,
      });
      setRevealedSecret(created.secret);
      setNewUrl("");
      setNewDescription("");
      await loadWebhooks();
    } catch (err) {
      setWebhooksError(err instanceof ApiError ? err.message : "Couldn't create the webhook endpoint.");
    } finally {
      setCreating(false);
    }
  }

  async function handleDeleteEndpoint(endpointId: string) {
    if (!token) return;
    try {
      await deleteWebhookEndpoint(token, endpointId);
      await loadWebhooks();
    } catch (err) {
      setWebhooksError(err instanceof ApiError ? err.message : "Couldn't remove the webhook endpoint.");
    }
  }

  const [apiKeys, setApiKeys] = useState<ApiKey[]>([]);
  const [apiKeysLoading, setApiKeysLoading] = useState(true);
  const [apiKeysError, setApiKeysError] = useState<string | null>(null);
  const [newKeyLabel, setNewKeyLabel] = useState("");
  const [creatingKey, setCreatingKey] = useState(false);
  const [revealedKey, setRevealedKey] = useState<string | null>(null);

  const loadApiKeys = useCallback(() => {
    if (!token) return;
    return listApiKeys(token)
      .then((keys) => {
        setApiKeys(keys);
        setApiKeysError(null);
      })
      .catch(() => setApiKeysError("Couldn't load API keys."))
      .finally(() => setApiKeysLoading(false));
  }, [token]);

  useEffect(() => {
    loadApiKeys();
  }, [loadApiKeys]);

  async function handleCreateApiKey(e: FormEvent) {
    e.preventDefault();
    if (!token || !newKeyLabel) return;
    setCreatingKey(true);
    setApiKeysError(null);
    try {
      const created = await createApiKey(token, newKeyLabel);
      setRevealedKey(created.raw_key);
      setNewKeyLabel("");
      await loadApiKeys();
    } catch (err) {
      setApiKeysError(err instanceof ApiError ? err.message : "Couldn't create the API key.");
    } finally {
      setCreatingKey(false);
    }
  }

  async function handleRevokeApiKey(keyId: string) {
    if (!token) return;
    try {
      await revokeApiKey(token, keyId);
      await loadApiKeys();
    } catch (err) {
      setApiKeysError(err instanceof ApiError ? err.message : "Couldn't revoke the API key.");
    }
  }

  const [crmConnection, setCrmConnection] = useState<CrmConnection | null>(null);
  const [crmSyncEvents, setCrmSyncEvents] = useState<CrmSyncEvent[]>([]);
  const [crmLoading, setCrmLoading] = useState(true);
  const [crmError, setCrmError] = useState<string | null>(null);
  const [crmProviderChoice, setCrmProviderChoice] = useState<CrmProvider>("hubspot");
  const [crmBusy, setCrmBusy] = useState(false);

  const loadCrm = useCallback(() => {
    if (!token) return;
    return Promise.all([getCrmConnection(token), listCrmSyncEvents(token)])
      .then(([connection, events]) => {
        setCrmConnection(connection);
        setCrmSyncEvents(events);
        setCrmError(null);
      })
      .catch(() => setCrmError("Couldn't load the CRM connection."))
      .finally(() => setCrmLoading(false));
  }, [token]);

  useEffect(() => {
    loadCrm();
  }, [loadCrm]);

  async function handleConnectCrm() {
    if (!token) return;
    setCrmBusy(true);
    setCrmError(null);
    try {
      await connectCrm(token, crmProviderChoice);
      await loadCrm();
    } catch (err) {
      setCrmError(err instanceof ApiError ? err.message : "Couldn't connect the CRM.");
    } finally {
      setCrmBusy(false);
    }
  }

  async function handleDisconnectCrm() {
    if (!token) return;
    setCrmBusy(true);
    setCrmError(null);
    try {
      await disconnectCrm(token);
      await loadCrm();
    } catch (err) {
      setCrmError(err instanceof ApiError ? err.message : "Couldn't disconnect the CRM.");
    } finally {
      setCrmBusy(false);
    }
  }

  const loadStatus = useCallback(() => {
    return getPublicStatus()
      .then((data) => {
        setStatus(data);
        setStatusError(null);
      })
      .catch((err) => setStatusError(err instanceof ApiError ? err.message : "Couldn't load provider status."));
  }, []);

  useEffect(() => {
    loadStatus();
    const interval = setInterval(loadStatus, 30_000);
    return () => clearInterval(interval);
  }, [loadStatus]);

  useEffect(() => {
    if (!token) return;
    listMyNumbers(token)
      .then(setNumbers)
      .finally(() => setLoading(false));
  }, [token]);

  const activeNumbers = numbers.filter((n) => n.status === "active");
  const forwardingCount = activeNumbers.filter((n) => n.forwarding_number).length;
  const receptionistCount = activeNumbers.filter((n) => n.ai_receptionist_enabled).length;

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-slate-900">Integrations</h2>
        <p className="text-sm text-slate-500">
          Zoiko Local runs your calling, video, and AI on our managed provider connections — there&apos;s nothing
          for you to configure there. Below, you can register your own webhook endpoints to receive events.
        </p>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-5 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="font-semibold text-slate-900">Platform Services</h3>
          <span className="text-xs text-slate-500">Updates every 30s</span>
        </div>

        {statusError && <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{statusError}</p>}
        {!status && !statusError && <p className="text-sm text-slate-500">Loading...</p>}

        {status && (
          <>
            <div
              className={`flex items-center gap-2 rounded-lg px-4 py-3 text-sm font-medium ${
                status.overall === "operational" ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"
              }`}
            >
              <Dot status={status.overall} />
              {status.overall === "operational"
                ? "All connected services operational"
                : "Some connected services are experiencing degraded performance"}
            </div>

            <div className="divide-y divide-slate-100 rounded-lg border border-slate-200">
              {status.components.map((c) => (
                <div key={c.name} className="flex items-center justify-between px-4 py-3 text-sm">
                  <span className="text-slate-700">{c.name}</span>
                  <span className="flex items-center gap-2 text-slate-500">
                    <Dot status={c.status} />
                    {statusLabel(c.status)}
                  </span>
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-5 space-y-4">
        <h3 className="font-semibold text-slate-900">Your Connected Numbers</h3>
        {loading ? (
          <p className="text-sm text-slate-500">Loading...</p>
        ) : activeNumbers.length === 0 ? (
          <p className="text-sm text-slate-500">No active numbers yet — add one from the Numbers page.</p>
        ) : (
          <div className="grid grid-cols-3 gap-4">
            <div>
              <div className="text-2xl font-semibold text-slate-900">{activeNumbers.length}</div>
              <div className="text-xs text-slate-500">Active numbers</div>
            </div>
            <div>
              <div className="text-2xl font-semibold text-slate-900">{forwardingCount}</div>
              <div className="text-xs text-slate-500">With call forwarding</div>
            </div>
            <div>
              <div className="text-2xl font-semibold text-slate-900">{receptionistCount}</div>
              <div className="text-xs text-slate-500">With AI Receptionist</div>
            </div>
          </div>
        )}
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-5 space-y-4">
        <div>
          <h3 className="font-semibold text-slate-900">Developer Webhooks</h3>
          <p className="text-sm text-slate-500 mt-1">
            Register a URL to receive a real-time POST for account events — number activated, call summary
            available, porting completed, and more. Each request is signed with your endpoint&apos;s secret via an
            <code className="mx-1 text-xs bg-slate-100 rounded px-1 py-0.5">X-Zoiko-Signature</code>
            header (HMAC-SHA256) so you can verify it came from Zoiko Local.
          </p>
        </div>

        {webhooksError && <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{webhooksError}</p>}

        {revealedSecret && (
          <div className="bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 text-sm">
            <p className="font-medium text-amber-800">Save this secret now — it won&apos;t be shown again.</p>
            <code className="block mt-1 text-xs bg-white border border-amber-200 rounded px-2 py-1 break-all">
              {revealedSecret}
            </code>
            <button
              onClick={() => setRevealedSecret(null)}
              className="mt-2 text-xs font-medium text-amber-700 hover:text-amber-900"
            >
              Dismiss
            </button>
          </div>
        )}

        {webhooksLoading ? (
          <p className="text-sm text-slate-500">Loading...</p>
        ) : (
          <>
            {endpoints.length === 0 ? (
              <p className="text-sm text-slate-500">No webhook endpoints registered yet.</p>
            ) : (
              <div className="divide-y divide-slate-100 rounded-lg border border-slate-200">
                {endpoints.map((ep) => (
                  <div key={ep.id} className="flex items-center justify-between px-4 py-3 text-sm">
                    <div>
                      <div className="text-slate-800 font-mono text-xs">{ep.url}</div>
                      {ep.description && <div className="text-xs text-slate-400 mt-0.5">{ep.description}</div>}
                    </div>
                    {isAdmin && (
                      <button
                        onClick={() => handleDeleteEndpoint(ep.id)}
                        className="text-xs font-medium text-red-600 hover:text-red-800"
                      >
                        Remove
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )}

            {isAdmin ? (
              <form onSubmit={handleCreateEndpoint} className="flex flex-wrap items-center gap-2 pt-1">
                <input
                  type="url"
                  required
                  placeholder="https://your-server.com/webhook"
                  value={newUrl}
                  onChange={(e) => setNewUrl(e.target.value)}
                  className="flex-1 min-w-[220px] text-sm rounded-lg border border-slate-200 px-3 py-1.5"
                />
                <input
                  type="text"
                  placeholder="Description (optional)"
                  value={newDescription}
                  onChange={(e) => setNewDescription(e.target.value)}
                  className="text-sm rounded-lg border border-slate-200 px-3 py-1.5 w-48"
                />
                <button
                  type="submit"
                  disabled={creating}
                  className="text-xs font-medium rounded-lg px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white"
                >
                  {creating ? "Adding..." : "Add endpoint"}
                </button>
              </form>
            ) : (
              <p className="text-xs text-slate-400">Only an account Owner or Admin can manage webhook endpoints.</p>
            )}

            {deliveries.length > 0 && (
              <div className="pt-2">
                <h4 className="text-xs font-medium text-slate-500 mb-2">Recent deliveries</h4>
                <ul className="space-y-1.5">
                  {deliveries.slice(0, 10).map((d) => (
                    <li
                      key={d.id}
                      className="flex items-center justify-between text-xs bg-slate-50 rounded-lg px-3 py-2"
                    >
                      <span className="text-slate-700">{d.event_type}</span>
                      <span className="flex items-center gap-2 text-slate-400">
                        <span
                          className={
                            d.status === "delivered" ? "text-emerald-600 font-medium" : "text-red-600 font-medium"
                          }
                        >
                          {d.status}
                        </span>
                        {new Date(d.created_at).toLocaleString()}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-5 space-y-4">
        <div>
          <h3 className="font-semibold text-slate-900">Public API Keys</h3>
          <p className="text-sm text-slate-500 mt-1">
            Read-only access to your numbers, calls, voicemails, and AI summaries at{" "}
            <code className="text-xs bg-slate-100 rounded px-1 py-0.5">/public/v1/*</code>. Pass your key as{" "}
            <code className="text-xs bg-slate-100 rounded px-1 py-0.5">Authorization: Bearer &lt;key&gt;</code>.
          </p>
        </div>

        {apiKeysError && <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{apiKeysError}</p>}

        {revealedKey && (
          <div className="bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 text-sm">
            <p className="font-medium text-amber-800">Save this key now — it won&apos;t be shown again.</p>
            <code className="block mt-1 text-xs bg-white border border-amber-200 rounded px-2 py-1 break-all">
              {revealedKey}
            </code>
            <button
              onClick={() => setRevealedKey(null)}
              className="mt-2 text-xs font-medium text-amber-700 hover:text-amber-900"
            >
              Dismiss
            </button>
          </div>
        )}

        {apiKeysLoading ? (
          <p className="text-sm text-slate-500">Loading...</p>
        ) : (
          <>
            {apiKeys.length === 0 ? (
              <p className="text-sm text-slate-500">No API keys created yet.</p>
            ) : (
              <div className="divide-y divide-slate-100 rounded-lg border border-slate-200">
                {apiKeys.map((k) => (
                  <div key={k.id} className="flex items-center justify-between px-4 py-3 text-sm">
                    <div>
                      <div className="text-slate-800">{k.label}</div>
                      <div className="text-xs text-slate-400 font-mono mt-0.5">
                        {k.key_prefix}… {k.revoked_at && <span className="text-red-500">(revoked)</span>}
                      </div>
                    </div>
                    {isAdmin && !k.revoked_at && (
                      <button
                        onClick={() => handleRevokeApiKey(k.id)}
                        className="text-xs font-medium text-red-600 hover:text-red-800"
                      >
                        Revoke
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )}

            {isAdmin ? (
              <form onSubmit={handleCreateApiKey} className="flex flex-wrap items-center gap-2 pt-1">
                <input
                  type="text"
                  required
                  placeholder="Key label (e.g. Reporting script)"
                  value={newKeyLabel}
                  onChange={(e) => setNewKeyLabel(e.target.value)}
                  className="flex-1 min-w-[220px] text-sm rounded-lg border border-slate-200 px-3 py-1.5"
                />
                <button
                  type="submit"
                  disabled={creatingKey}
                  className="text-xs font-medium rounded-lg px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white"
                >
                  {creatingKey ? "Creating..." : "Create key"}
                </button>
              </form>
            ) : (
              <p className="text-xs text-slate-400">Only an account Owner or Admin can manage API keys.</p>
            )}
          </>
        )}
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-5 space-y-4">
        <div>
          <h3 className="font-semibold text-slate-900">CRM Connection</h3>
          <p className="text-sm text-slate-500 mt-1">
            Mock integration — no real HubSpot, Salesforce, or Pipedrive connection exists yet. This lets contacts
            and call activity sync into a simulated CRM so the integration shape is ready once a real one is built.
          </p>
        </div>

        {crmError && <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{crmError}</p>}

        {crmLoading ? (
          <p className="text-sm text-slate-500">Loading...</p>
        ) : crmConnection ? (
          <>
            <div className="flex items-center justify-between rounded-lg border border-slate-200 px-4 py-3 text-sm">
              <div>
                <div className="text-slate-800 font-medium capitalize">{crmConnection.provider}</div>
                <div className="text-xs text-slate-400 mt-0.5">
                  {crmConnection.external_account_label} · connected{" "}
                  {new Date(crmConnection.connected_at).toLocaleDateString()}
                </div>
              </div>
              {isAdmin && (
                <button
                  onClick={handleDisconnectCrm}
                  disabled={crmBusy}
                  className="text-xs font-medium text-red-600 hover:text-red-800 disabled:opacity-50"
                >
                  {crmBusy ? "Disconnecting..." : "Disconnect"}
                </button>
              )}
            </div>

            {crmSyncEvents.length > 0 && (
              <div className="pt-1">
                <h4 className="text-xs font-medium text-slate-500 mb-2">Recent sync events</h4>
                <ul className="space-y-1.5">
                  {crmSyncEvents.slice(0, 10).map((e) => (
                    <li
                      key={e.id}
                      className="flex items-center justify-between text-xs bg-slate-50 rounded-lg px-3 py-2"
                    >
                      <span className="text-slate-700 capitalize">{e.event_type.replaceAll("_", " ")}</span>
                      <span className="text-slate-400">{new Date(e.created_at).toLocaleString()}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        ) : isAdmin ? (
          <div className="flex items-center gap-2">
            <select
              value={crmProviderChoice}
              onChange={(e) => setCrmProviderChoice(e.target.value as CrmProvider)}
              className="text-sm rounded-lg border border-slate-200 px-3 py-1.5"
            >
              <option value="hubspot">HubSpot</option>
              <option value="salesforce">Salesforce</option>
              <option value="pipedrive">Pipedrive</option>
            </select>
            <button
              onClick={handleConnectCrm}
              disabled={crmBusy}
              className="text-xs font-medium rounded-lg px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white"
            >
              {crmBusy ? "Connecting..." : "Connect"}
            </button>
          </div>
        ) : (
          <p className="text-xs text-slate-400">Only an account Owner or Admin can connect a CRM.</p>
        )}
      </div>

      <div className="bg-white rounded-xl border border-slate-200 p-5 space-y-2">
        <h3 className="font-semibold text-slate-900">Billing</h3>
        <p className="text-sm text-slate-500">
          Billing and payment integration isn&apos;t connected yet — this account isn&apos;t being charged.
        </p>
      </div>
    </div>
  );
}
