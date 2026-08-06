"use client";

import { useEffect, useState, useCallback } from "react";
import {
  getCurrentUser,
  listCallFlows,
  createCallFlow,
  getCallFlow,
  saveCallFlowDraft,
  validateCallFlowDraft,
  publishCallFlow,
  rollbackCallFlow,
  listMyNumbers,
  assignCallFlow,
  unassignCallFlow,
  listQueues,
  ApiError,
  type User,
  type CallFlowSummary,
  type CallFlowDetail,
  type CallFlowNode,
  type MyPhoneNumber,
  type CallQueue,
} from "@/lib/api";
import { getToken } from "@/lib/auth";

const NODE_TYPES: { value: CallFlowNode["type"]; label: string }[] = [
  { value: "menu", label: "Menu (press a digit)" },
  { value: "business_hours", label: "Business hours check" },
  { value: "forward", label: "Forward to number(s)" },
  { value: "queue", label: "Queue (hold + agent pull)" },
  { value: "voicemail", label: "Voicemail" },
  { value: "ai_receptionist", label: "AI Receptionist" },
  { value: "hangup", label: "Hang up" },
];

function emptyNode(id: string): CallFlowNode {
  return { id, type: "menu", prompt: "", options: { "1": "" } };
}

export default function CallFlowsPage() {
  const [token] = useState<string | null>(() => getToken());
  const [me, setMe] = useState<User | null>(null);
  const [flows, setFlows] = useState<CallFlowSummary[]>([]);
  const [numbers, setNumbers] = useState<MyPhoneNumber[]>([]);
  const [queues, setQueues] = useState<CallQueue[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [newFlowName, setNewFlowName] = useState("");
  const [creating, setCreating] = useState(false);

  const [openId, setOpenId] = useState<string | null>(null);
  const [detail, setDetail] = useState<CallFlowDetail | null>(null);
  const [entryNodeId, setEntryNodeId] = useState("");
  const [nodes, setNodes] = useState<CallFlowNode[]>([]);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [publishErrors, setPublishErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  const canWrite = me?.role !== "viewer";

  const loadAll = useCallback(() => {
    if (!token) return;
    return Promise.all([getCurrentUser(token), listCallFlows(token), listMyNumbers(token), listQueues(token)])
      .then(([userData, flowData, numberData, queueData]) => {
        setMe(userData);
        setFlows(flowData);
        setNumbers(numberData);
        setQueues(queueData);
        setLoadError(null);
      })
      .catch(() => setLoadError("Couldn't load call flows."))
      .finally(() => setLoading(false));
  }, [token]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !newFlowName.trim()) return;
    setCreating(true);
    try {
      const created = await createCallFlow(token, newFlowName.trim());
      setFlows((prev) => [created, ...prev]);
      setNewFlowName("");
      await openFlow(created.id);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : "Couldn't create call flow.");
    } finally {
      setCreating(false);
    }
  }

  async function openFlow(id: string) {
    if (!token) return;
    setOpenId(id);
    setSaveMessage(null);
    setPublishErrors([]);
    try {
      const d = await getCallFlow(token, id);
      setDetail(d);
      setEntryNodeId(d.draft?.entry_node_id ?? "");
      setNodes(d.draft?.nodes ?? []);
    } catch {
      setLoadError("Couldn't load this call flow.");
    }
  }

  function addNode() {
    const id = `node_${nodes.length + 1}_${Math.random().toString(36).slice(2, 6)}`;
    setNodes((prev) => [...prev, emptyNode(id)]);
  }

  function updateNode(index: number, patch: Partial<CallFlowNode>) {
    setNodes((prev) => prev.map((n, i) => (i === index ? { ...n, ...patch } : n)));
  }

  function removeNode(index: number) {
    setNodes((prev) => prev.filter((_, i) => i !== index));
  }

  function updateMenuOption(index: number, digit: string, target: string) {
    setNodes((prev) =>
      prev.map((n, i) => (i === index ? { ...n, options: { ...(n.options ?? {}), [digit]: target } } : n))
    );
  }

  function addMenuOption(index: number) {
    setNodes((prev) =>
      prev.map((n, i) => {
        if (i !== index) return n;
        const options = { ...(n.options ?? {}) };
        let digit = "0";
        for (let d = 0; d <= 9; d++) {
          if (!(String(d) in options)) {
            digit = String(d);
            break;
          }
        }
        return { ...n, options: { ...options, [digit]: "" } };
      })
    );
  }

  function removeMenuOption(index: number, digit: string) {
    setNodes((prev) =>
      prev.map((n, i) => {
        if (i !== index) return n;
        const options = { ...(n.options ?? {}) };
        delete options[digit];
        return { ...n, options };
      })
    );
  }

  async function handleSaveDraft() {
    if (!token || !openId) return;
    setBusy(true);
    setSaveMessage(null);
    setPublishErrors([]);
    try {
      await saveCallFlowDraft(token, openId, { entry_node_id: entryNodeId, nodes });
      setSaveMessage("Draft saved.");
    } catch (err) {
      setSaveMessage(err instanceof ApiError ? err.message : "Couldn't save draft.");
    } finally {
      setBusy(false);
    }
  }

  async function handleValidate() {
    if (!token || !openId) return;
    setBusy(true);
    try {
      await handleSaveDraft();
      const result = await validateCallFlowDraft(token, openId);
      setPublishErrors(result.errors);
      if (result.errors.length === 0) setSaveMessage("Draft is valid and ready to publish.");
    } catch (err) {
      setSaveMessage(err instanceof ApiError ? err.message : "Couldn't validate draft.");
    } finally {
      setBusy(false);
    }
  }

  async function handlePublish() {
    if (!token || !openId) return;
    setBusy(true);
    setPublishErrors([]);
    try {
      await handleSaveDraft();
      const result = await publishCallFlow(token, openId);
      if (result.published) {
        setSaveMessage(`Published as version ${result.version?.version}.`);
        await openFlow(openId);
        await loadAll();
      } else {
        setPublishErrors(result.errors);
        setSaveMessage(null);
      }
    } catch (err) {
      setSaveMessage(err instanceof ApiError ? err.message : "Couldn't publish this call flow.");
    } finally {
      setBusy(false);
    }
  }

  async function handleRollback(version: number) {
    if (!token || !openId) return;
    setBusy(true);
    try {
      await rollbackCallFlow(token, openId, version);
      setSaveMessage(`Rolled back to version ${version}.`);
      await openFlow(openId);
      await loadAll();
    } catch (err) {
      setSaveMessage(err instanceof ApiError ? err.message : "Couldn't roll back this call flow.");
    } finally {
      setBusy(false);
    }
  }

  async function handleAssign(phoneNumberId: string, flowId: string | null) {
    if (!token) return;
    try {
      if (flowId) {
        await assignCallFlow(token, flowId, phoneNumberId);
      } else {
        await unassignCallFlow(token, phoneNumberId);
      }
      await loadAll();
      if (openId) await openFlow(openId);
    } catch {
      setLoadError("Couldn't update this number's call flow assignment.");
    }
  }

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-slate-900">Call Flows</h2>
        <p className="text-sm text-slate-500">
          Build touch-tone menus, business-hours routing, and failover for your numbers - draft, validate,
          publish, and roll back without disrupting live calls.
        </p>
      </div>

      {loadError && <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{loadError}</p>}
      {loading && <p className="text-sm text-slate-500">Loading...</p>}

      {!loading && (
        <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="font-semibold text-slate-900">Your call flows</h3>
          </div>

          {canWrite && (
            <form onSubmit={handleCreate} className="flex gap-2">
              <input
                value={newFlowName}
                onChange={(e) => setNewFlowName(e.target.value)}
                placeholder="e.g. Main Support Line"
                className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm placeholder:text-slate-400"
              />
              <button
                type="submit"
                disabled={creating || !newFlowName.trim()}
                className="bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white text-sm font-medium rounded-lg px-4 py-2"
              >
                {creating ? "Creating..." : "New Call Flow"}
              </button>
            </form>
          )}

          {flows.length === 0 && <p className="text-sm text-slate-500">No call flows yet.</p>}

          <ul className="divide-y divide-slate-100">
            {flows.map((flow) => (
              <li key={flow.id} className="py-3">
                <button
                  onClick={() => openFlow(flow.id)}
                  className="w-full flex items-center justify-between gap-4 text-left"
                >
                  <div>
                    <div className="text-sm font-medium text-slate-800">{flow.name}</div>
                    <div className="text-xs text-slate-500">
                      {flow.live_version ? `Live: version ${flow.live_version}` : "Not published yet"}
                      {flow.assigned_numbers.length > 0 && ` · Assigned to ${flow.assigned_numbers.join(", ")}`}
                    </div>
                  </div>
                  <span className="text-xs font-medium text-indigo-600">
                    {openId === flow.id ? "Editing" : "Edit"}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {openId && detail && (
        <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-5">
          <div className="flex items-center justify-between">
            <h3 className="font-semibold text-slate-900">{detail.name}</h3>
            <button onClick={() => setOpenId(null)} className="text-xs text-slate-500 hover:text-slate-700">
              Close
            </button>
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-500 mb-1">Entry node id</label>
            <input
              value={entryNodeId}
              onChange={(e) => setEntryNodeId(e.target.value)}
              disabled={!canWrite}
              placeholder="id of the first node a caller hits"
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm font-mono placeholder:text-slate-400 disabled:bg-slate-50"
            />
          </div>

          <div className="space-y-3">
            {nodes.map((node, index) => (
              <div key={index} className="rounded-lg border border-slate-200 p-4 space-y-3">
                <div className="grid sm:grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs font-medium text-slate-500 mb-1">Node id</label>
                    <input
                      value={node.id}
                      onChange={(e) => updateNode(index, { id: e.target.value })}
                      disabled={!canWrite}
                      className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm font-mono disabled:bg-slate-50"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-slate-500 mb-1">Type</label>
                    <select
                      value={node.type}
                      onChange={(e) => updateNode(index, { type: e.target.value as CallFlowNode["type"] })}
                      disabled={!canWrite}
                      className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm disabled:bg-slate-50"
                    >
                      {NODE_TYPES.map((t) => (
                        <option key={t.value} value={t.value}>
                          {t.label}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>

                {node.type === "menu" && (
                  <div className="space-y-2">
                    <div>
                      <label className="block text-xs font-medium text-slate-500 mb-1">Prompt</label>
                      <input
                        value={node.prompt ?? ""}
                        onChange={(e) => updateNode(index, { prompt: e.target.value })}
                        disabled={!canWrite}
                        placeholder="Press 1 for sales, 2 for support."
                        className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm disabled:bg-slate-50"
                      />
                    </div>
                    <div className="space-y-1">
                      <label className="block text-xs font-medium text-slate-500">Digit → next node</label>
                      {Object.entries(node.options ?? {}).map(([digit, target]) => (
                        <div key={digit} className="flex gap-2 items-center">
                          <span className="w-8 text-center text-sm font-mono bg-slate-100 rounded px-2 py-1">
                            {digit}
                          </span>
                          <input
                            value={target}
                            onChange={(e) => updateMenuOption(index, digit, e.target.value)}
                            disabled={!canWrite}
                            placeholder="target node id"
                            className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm font-mono disabled:bg-slate-50"
                          />
                          {canWrite && (
                            <button
                              onClick={() => removeMenuOption(index, digit)}
                              className="text-xs text-red-600 hover:text-red-700"
                            >
                              Remove
                            </button>
                          )}
                        </div>
                      ))}
                      {canWrite && (
                        <button onClick={() => addMenuOption(index)} className="text-xs text-indigo-600 hover:text-indigo-800">
                          + Add option
                        </button>
                      )}
                    </div>
                    <div className="grid sm:grid-cols-2 gap-3">
                      <div>
                        <label className="block text-xs font-medium text-slate-500 mb-1">On invalid input</label>
                        <input
                          value={node.invalid_node_id ?? ""}
                          onChange={(e) => updateNode(index, { invalid_node_id: e.target.value || null })}
                          disabled={!canWrite}
                          placeholder="default: repeat this menu"
                          className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm font-mono placeholder:text-slate-400 disabled:bg-slate-50"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-slate-500 mb-1">On no input (timeout)</label>
                        <input
                          value={node.timeout_node_id ?? ""}
                          onChange={(e) => updateNode(index, { timeout_node_id: e.target.value || null })}
                          disabled={!canWrite}
                          placeholder="default: repeat this menu"
                          className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm font-mono placeholder:text-slate-400 disabled:bg-slate-50"
                        />
                      </div>
                    </div>
                  </div>
                )}

                {node.type === "business_hours" && (
                  <div className="grid sm:grid-cols-2 gap-3">
                    <div>
                      <label className="block text-xs font-medium text-slate-500 mb-1">Start (HH:MM:SS)</label>
                      <input
                        value={node.start ?? ""}
                        onChange={(e) => updateNode(index, { start: e.target.value })}
                        disabled={!canWrite}
                        placeholder="09:00:00"
                        className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm font-mono disabled:bg-slate-50"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-slate-500 mb-1">End (HH:MM:SS)</label>
                      <input
                        value={node.end ?? ""}
                        onChange={(e) => updateNode(index, { end: e.target.value })}
                        disabled={!canWrite}
                        placeholder="17:00:00"
                        className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm font-mono disabled:bg-slate-50"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-slate-500 mb-1">Timezone</label>
                      <input
                        value={node.timezone ?? ""}
                        onChange={(e) => updateNode(index, { timezone: e.target.value })}
                        disabled={!canWrite}
                        placeholder="America/New_York"
                        className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm disabled:bg-slate-50"
                      />
                    </div>
                    <div />
                    <div>
                      <label className="block text-xs font-medium text-slate-500 mb-1">Within hours → node</label>
                      <input
                        value={node.within_node_id ?? ""}
                        onChange={(e) => updateNode(index, { within_node_id: e.target.value })}
                        disabled={!canWrite}
                        className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm font-mono disabled:bg-slate-50"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-slate-500 mb-1">Outside hours → node</label>
                      <input
                        value={node.outside_node_id ?? ""}
                        onChange={(e) => updateNode(index, { outside_node_id: e.target.value })}
                        disabled={!canWrite}
                        className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm font-mono disabled:bg-slate-50"
                      />
                    </div>
                  </div>
                )}

                {node.type === "forward" && (
                  <div className="space-y-2">
                    <div>
                      <label className="block text-xs font-medium text-slate-500 mb-1">
                        Destination numbers (comma-separated - more than one rings all simultaneously)
                      </label>
                      <input
                        value={(node.destinations ?? []).join(", ")}
                        onChange={(e) =>
                          updateNode(index, {
                            destinations: e.target.value.split(",").map((s) => s.trim()).filter(Boolean),
                          })
                        }
                        disabled={!canWrite}
                        placeholder="+15551234567, +15557654321"
                        className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm font-mono placeholder:text-slate-400 disabled:bg-slate-50"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-slate-500 mb-1">
                        On no answer/busy/failed → node (failover; default: voicemail)
                      </label>
                      <input
                        value={node.on_no_answer_node_id ?? ""}
                        onChange={(e) => updateNode(index, { on_no_answer_node_id: e.target.value || null })}
                        disabled={!canWrite}
                        placeholder="default: voicemail"
                        className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm font-mono placeholder:text-slate-400 disabled:bg-slate-50"
                      />
                    </div>
                  </div>
                )}

                {node.type === "queue" && (
                  <div className="space-y-2">
                    <div>
                      <label className="block text-xs font-medium text-slate-500 mb-1">Queue</label>
                      <select
                        value={node.queue_id ?? ""}
                        onChange={(e) => updateNode(index, { queue_id: e.target.value || null })}
                        disabled={!canWrite}
                        className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm disabled:bg-slate-50"
                      >
                        <option value="">Select a queue...</option>
                        {queues.map((q) => (
                          <option key={q.id} value={q.id}>
                            {q.name}
                          </option>
                        ))}
                      </select>
                      {queues.length === 0 && (
                        <p className="text-xs text-slate-400 mt-1">
                          No queues yet - create one on the Queues page first.
                        </p>
                      )}
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-slate-500 mb-1">
                        On max wait exceeded → node (default: voicemail)
                      </label>
                      <input
                        value={node.overflow_node_id ?? ""}
                        onChange={(e) => updateNode(index, { overflow_node_id: e.target.value || null })}
                        disabled={!canWrite}
                        placeholder="default: voicemail"
                        className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm font-mono placeholder:text-slate-400 disabled:bg-slate-50"
                      />
                    </div>
                  </div>
                )}

                {node.type === "hangup" && (
                  <div>
                    <label className="block text-xs font-medium text-slate-500 mb-1">Message (optional)</label>
                    <input
                      value={node.message ?? ""}
                      onChange={(e) => updateNode(index, { message: e.target.value })}
                      disabled={!canWrite}
                      placeholder="Thanks for calling. Goodbye."
                      className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm disabled:bg-slate-50"
                    />
                  </div>
                )}

                {(node.type === "voicemail" || node.type === "ai_receptionist") && (
                  <p className="text-xs text-slate-500">
                    Terminal node - no further configuration needed.
                  </p>
                )}

                {canWrite && (
                  <button onClick={() => removeNode(index)} className="text-xs text-red-600 hover:text-red-700">
                    Remove node
                  </button>
                )}
              </div>
            ))}

            {canWrite && (
              <button
                onClick={addNode}
                className="text-sm font-medium text-indigo-600 hover:text-indigo-800 border border-dashed border-indigo-300 rounded-lg px-4 py-2 w-full"
              >
                + Add node
              </button>
            )}
          </div>

          {publishErrors.length > 0 && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-3 space-y-1">
              <p className="text-xs font-medium text-red-700">This draft can&apos;t be published yet:</p>
              {publishErrors.map((e, i) => (
                <p key={i} className="text-xs text-red-600">
                  · {e}
                </p>
              ))}
            </div>
          )}
          {saveMessage && <p className="text-xs text-emerald-700 bg-emerald-50 rounded-lg px-3 py-2">{saveMessage}</p>}

          {canWrite && (
            <div className="flex gap-2">
              <button
                onClick={handleSaveDraft}
                disabled={busy}
                className="text-sm font-medium rounded-lg px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 disabled:opacity-60"
              >
                Save Draft
              </button>
              <button
                onClick={handleValidate}
                disabled={busy}
                className="text-sm font-medium rounded-lg px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 disabled:opacity-60"
              >
                Validate
              </button>
              <button
                onClick={handlePublish}
                disabled={busy}
                className="bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white text-sm font-medium rounded-lg px-4 py-2"
              >
                {busy ? "Working..." : "Publish"}
              </button>
            </div>
          )}

          <div className="border-t border-slate-200 pt-4 space-y-2">
            <h4 className="text-sm font-semibold text-slate-800">Version history</h4>
            {detail.version_history.length === 0 && <p className="text-xs text-slate-500">No versions yet.</p>}
            <ul className="space-y-1">
              {detail.version_history.map((v) => (
                <li key={v.id} className="flex items-center justify-between text-xs">
                  <span className="text-slate-600">
                    v{v.version} · {v.status}
                    {v.rolled_back_from_version && ` (rolled back from v${v.rolled_back_from_version})`}
                  </span>
                  {canWrite && v.status !== "draft" && (
                    <button
                      onClick={() => handleRollback(v.version)}
                      disabled={busy}
                      className="text-indigo-600 hover:text-indigo-800 disabled:opacity-60"
                    >
                      Roll back to this version
                    </button>
                  )}
                </li>
              ))}
            </ul>
          </div>

          <div className="border-t border-slate-200 pt-4 space-y-2">
            <h4 className="text-sm font-semibold text-slate-800">Assign to a number</h4>
            {numbers.length === 0 && <p className="text-xs text-slate-500">No numbers on this account yet.</p>}
            <ul className="space-y-1">
              {numbers.map((n) => (
                <li key={n.id} className="flex items-center justify-between text-xs">
                  <span className="font-mono text-slate-700">{n.e164}</span>
                  {n.call_flow_id === openId ? (
                    canWrite && (
                      <button onClick={() => handleAssign(n.id, null)} className="text-red-600 hover:text-red-700">
                        Unassign
                      </button>
                    )
                  ) : (
                    canWrite && (
                      <button onClick={() => handleAssign(n.id, openId)} className="text-indigo-600 hover:text-indigo-800">
                        Assign this flow
                      </button>
                    )
                  )}
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}
