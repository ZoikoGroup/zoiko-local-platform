"use client";

import { useEffect, useState, useCallback } from "react";
import {
  getCurrentUser,
  listTeamMembers,
  listQueues,
  createQueue,
  updateQueue,
  addQueueMember,
  removeQueueMember,
  getQueueStatus,
  pullNextCaller,
  getMyPresence,
  setMyPresence,
  ApiError,
  type User,
  type TeamMember,
  type CallQueue,
  type QueueStatus,
  type AgentPresence,
} from "@/lib/api";
import { getToken } from "@/lib/auth";

export default function QueuesPage() {
  const [token] = useState<string | null>(() => getToken());
  const [me, setMe] = useState<User | null>(null);
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [queues, setQueues] = useState<CallQueue[]>([]);
  const [statuses, setStatuses] = useState<Record<string, QueueStatus>>({});
  const [presence, setPresence] = useState<AgentPresence | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [newName, setNewName] = useState("");
  const [newMaxWait, setNewMaxWait] = useState(120);
  const [newWrapUp, setNewWrapUp] = useState(30);
  const [creating, setCreating] = useState(false);
  const [selectedMemberByQueue, setSelectedMemberByQueue] = useState<Record<string, string>>({});
  const [pullResult, setPullResult] = useState<Record<string, string>>({});

  const canWrite = me?.role !== "viewer";

  const loadAll = useCallback(async () => {
    if (!token) return;
    try {
      const [userData, memberData, queueData, presenceData] = await Promise.all([
        getCurrentUser(token),
        listTeamMembers(token),
        listQueues(token),
        getMyPresence(token),
      ]);
      setMe(userData);
      setMembers(memberData);
      setQueues(queueData);
      setPresence(presenceData);
      setError(null);

      const statusEntries = await Promise.all(queueData.map((q) => getQueueStatus(token, q.id).then((s) => [q.id, s] as const)));
      setStatuses(Object.fromEntries(statusEntries));
    } catch {
      setError("Couldn't load queues.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !newName.trim()) return;
    setCreating(true);
    try {
      await createQueue(token, { name: newName.trim(), max_wait_seconds: newMaxWait, wrap_up_seconds: newWrapUp });
      setNewName("");
      await loadAll();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't create queue.");
    } finally {
      setCreating(false);
    }
  }

  async function handleUpdate(queueId: string, patch: { max_wait_seconds?: number; wrap_up_seconds?: number }) {
    if (!token) return;
    try {
      await updateQueue(token, queueId, patch);
      await loadAll();
    } catch {
      setError("Couldn't update this queue.");
    }
  }

  async function handleAddMember(queueId: string) {
    if (!token) return;
    const userId = selectedMemberByQueue[queueId];
    if (!userId) return;
    try {
      await addQueueMember(token, queueId, userId);
      await loadAll();
    } catch {
      setError("Couldn't add this member.");
    }
  }

  async function handleRemoveMember(queueId: string, userId: string) {
    if (!token) return;
    try {
      await removeQueueMember(token, queueId, userId);
      await loadAll();
    } catch {
      setError("Couldn't remove this member.");
    }
  }

  async function handlePullNext(queueId: string) {
    if (!token) return;
    setPullResult((prev) => ({ ...prev, [queueId]: "" }));
    try {
      const result = await pullNextCaller(token, queueId);
      setPullResult((prev) => ({ ...prev, [queueId]: `Calling you now to connect ${result.caller_number}...` }));
      await loadAll();
    } catch (err) {
      setPullResult((prev) => ({
        ...prev,
        [queueId]: err instanceof ApiError ? err.message : "Couldn't pull the next caller.",
      }));
    }
  }

  async function handlePresenceToggle() {
    if (!token || !presence) return;
    const next = presence.effectively_available ? "offline" : "available";
    try {
      setPresence(await setMyPresence(token, next));
    } catch {
      setError("Couldn't update your presence.");
    }
  }

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-slate-900">Call Queues</h2>
          <p className="text-sm text-slate-500">
            Contact-center-lite: real FIFO hold queues with agent presence, wrap-up time, and pull-to-answer -
            assign a queue node to one of your Call Flows to route callers here.
          </p>
        </div>
        {presence && (
          <button
            onClick={handlePresenceToggle}
            className={`text-sm font-medium rounded-lg px-4 py-2 ${
              presence.effectively_available
                ? "bg-emerald-100 text-emerald-700 hover:bg-emerald-200"
                : "bg-slate-100 text-slate-600 hover:bg-slate-200"
            }`}
          >
            {presence.effectively_available
              ? "Available - go offline"
              : presence.status === "wrap_up"
                ? "In wrap-up - go available"
                : "Offline - go available"}
          </button>
        )}
      </div>

      {error && <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{error}</p>}
      {loading && <p className="text-sm text-slate-500">Loading...</p>}

      {!loading && canWrite && (
        <form onSubmit={handleCreate} className="bg-white rounded-xl border border-slate-200 p-6 space-y-3">
          <h3 className="font-semibold text-slate-900">New queue</h3>
          <div className="grid sm:grid-cols-3 gap-3">
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="e.g. Support"
              className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400"
            />
            <div>
              <label className="block text-xs font-medium text-slate-500 mb-1">Max wait (seconds)</label>
              <input
                type="number"
                min={10}
                max={1800}
                value={newMaxWait}
                onChange={(e) => setNewMaxWait(Number(e.target.value))}
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-500 mb-1">Wrap-up (seconds)</label>
              <input
                type="number"
                min={0}
                max={600}
                value={newWrapUp}
                onChange={(e) => setNewWrapUp(Number(e.target.value))}
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900"
              />
            </div>
          </div>
          <button
            type="submit"
            disabled={creating || !newName.trim()}
            className="bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white text-sm font-medium rounded-lg px-4 py-2"
          >
            {creating ? "Creating..." : "Create Queue"}
          </button>
        </form>
      )}

      {!loading && queues.length === 0 && (
        <p className="text-sm text-slate-500">No queues yet. Create one, then add it as a queue node in a Call Flow.</p>
      )}

      {queues.map((queue) => {
        const queueStatus = statuses[queue.id];
        const memberIds = new Set(queue.members.map((m) => m.user_id));
        const nonMembers = members.filter((m) => !memberIds.has(m.id));
        return (
          <div key={queue.id} className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="font-semibold text-slate-900">{queue.name}</h3>
              {canWrite && (
                <button
                  onClick={() => handlePullNext(queue.id)}
                  className="bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg px-4 py-2"
                >
                  Pull Next Caller
                </button>
              )}
            </div>

            {pullResult[queue.id] && <p className="text-xs text-slate-600 bg-slate-50 rounded-lg px-3 py-2">{pullResult[queue.id]}</p>}

            {queueStatus && (
              <div className="grid grid-cols-3 gap-3 text-center">
                <div className="bg-slate-50 rounded-lg p-3">
                  <div className="text-lg font-semibold text-slate-900">{queueStatus.waiting_count}</div>
                  <div className="text-xs text-slate-500">Waiting</div>
                </div>
                <div className="bg-slate-50 rounded-lg p-3">
                  <div className="text-lg font-semibold text-slate-900">{queueStatus.in_progress_count}</div>
                  <div className="text-xs text-slate-500">In progress</div>
                </div>
                <div className={`rounded-lg p-3 ${queueStatus.sla_breached ? "bg-red-50" : "bg-slate-50"}`}>
                  <div className={`text-lg font-semibold ${queueStatus.sla_breached ? "text-red-700" : "text-slate-900"}`}>
                    {queueStatus.longest_wait_seconds}s
                  </div>
                  <div className={`text-xs ${queueStatus.sla_breached ? "text-red-600" : "text-slate-500"}`}>
                    Longest wait{queueStatus.sla_breached ? " - SLA breached" : ""}
                  </div>
                </div>
              </div>
            )}

            {canWrite && (
              <div className="grid sm:grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-slate-500 mb-1">Max wait (seconds)</label>
                  <input
                    type="number"
                    defaultValue={queue.max_wait_seconds}
                    onBlur={(e) => handleUpdate(queue.id, { max_wait_seconds: Number(e.target.value) })}
                    className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-500 mb-1">Wrap-up (seconds)</label>
                  <input
                    type="number"
                    defaultValue={queue.wrap_up_seconds}
                    onBlur={(e) => handleUpdate(queue.id, { wrap_up_seconds: Number(e.target.value) })}
                    className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900"
                  />
                </div>
              </div>
            )}

            <div>
              <h4 className="text-xs font-semibold text-slate-600 mb-2">Agents</h4>
              <ul className="space-y-1">
                {queue.members.map((member) => (
                  <li key={member.user_id} className="flex items-center justify-between text-xs">
                    <span className="text-slate-700">{member.email}</span>
                    {canWrite && (
                      <button
                        onClick={() => handleRemoveMember(queue.id, member.user_id)}
                        className="text-red-600 hover:text-red-700"
                      >
                        Remove
                      </button>
                    )}
                  </li>
                ))}
                {queue.members.length === 0 && <li className="text-xs text-slate-500">No agents assigned yet.</li>}
              </ul>
              {canWrite && nonMembers.length > 0 && (
                <div className="flex gap-2 mt-2">
                  <select
                    value={selectedMemberByQueue[queue.id] ?? ""}
                    onChange={(e) => setSelectedMemberByQueue((prev) => ({ ...prev, [queue.id]: e.target.value }))}
                    className="flex-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs text-slate-900"
                  >
                    <option value="">Add an agent...</option>
                    {nonMembers.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.email}
                      </option>
                    ))}
                  </select>
                  <button
                    onClick={() => handleAddMember(queue.id)}
                    disabled={!selectedMemberByQueue[queue.id]}
                    className="text-xs font-medium rounded-lg px-3 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 disabled:opacity-60"
                  >
                    Add
                  </button>
                </div>
              )}
            </div>

            <p className="text-xs text-slate-400 font-mono">queue_id: {queue.id}</p>
          </div>
        );
      })}
    </div>
  );
}
