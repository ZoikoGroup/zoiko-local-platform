"use client";

import { useCallback, useEffect, useState } from "react";
import {
  listContacts,
  createContact,
  updateContact,
  deleteContact,
  getContactHistory,
  ApiError,
  type Contact,
  type ContactHistoryEntry,
} from "@/lib/api";
import { getToken } from "@/lib/auth";

const EMPTY_FORM = { name: "", phone_number: "", email: "", notes: "" };

const HISTORY_TYPE_LABEL: Record<ContactHistoryEntry["type"], string> = {
  call: "Call",
  voicemail: "Voicemail",
  receptionist_call: "Receptionist call",
};

function formatDuration(seconds: number | null): string {
  if (seconds === null) return "—";
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

type HistoryState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "done"; entries: ContactHistoryEntry[] };

export default function ContactsPage() {
  const [token] = useState<string | null>(() => getToken());

  const [contacts, setContacts] = useState<Contact[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [formBusy, setFormBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const [actionBusyId, setActionBusyId] = useState<string | null>(null);
  const [historyByContact, setHistoryByContact] = useState<Record<string, HistoryState>>({});
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const loadContacts = useCallback(() => {
    if (!token) return;
    return listContacts(token)
      .then((data) => {
        setContacts(data);
        setLoadError(null);
      })
      .catch(() => setLoadError("Couldn't load contacts."))
      .finally(() => setLoading(false));
  }, [token]);

  useEffect(() => {
    loadContacts();
  }, [loadContacts]);

  function openCreateForm() {
    setEditingId(null);
    setForm(EMPTY_FORM);
    setFormError(null);
    setFormOpen(true);
  }

  function openEditForm(contact: Contact) {
    setEditingId(contact.id);
    setForm({
      name: contact.name,
      phone_number: contact.phone_number,
      email: contact.email ?? "",
      notes: contact.notes ?? "",
    });
    setFormError(null);
    setFormOpen(true);
  }

  function closeForm() {
    setFormOpen(false);
    setEditingId(null);
    setForm(EMPTY_FORM);
    setFormError(null);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!token) return;
    setFormBusy(true);
    setFormError(null);
    const input = {
      name: form.name,
      phone_number: form.phone_number,
      email: form.email || undefined,
      notes: form.notes || undefined,
    };
    try {
      if (editingId) {
        await updateContact(token, editingId, input);
      } else {
        await createContact(token, input);
      }
      closeForm();
      await loadContacts();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Couldn't save this contact.");
    } finally {
      setFormBusy(false);
    }
  }

  async function handleDelete(contact: Contact) {
    if (!token) return;
    setActionBusyId(contact.id);
    try {
      await deleteContact(token, contact.id);
      await loadContacts();
    } catch {
      setLoadError("Couldn't delete this contact.");
    } finally {
      setActionBusyId(null);
    }
  }

  async function handleToggleHistory(contact: Contact) {
    if (expandedId === contact.id) {
      setExpandedId(null);
      return;
    }
    setExpandedId(contact.id);
    if (!token || historyByContact[contact.id]?.status === "done") return;

    setHistoryByContact((prev) => ({ ...prev, [contact.id]: { status: "loading" } }));
    try {
      const entries = await getContactHistory(token, contact.id);
      setHistoryByContact((prev) => ({ ...prev, [contact.id]: { status: "done", entries } }));
    } catch {
      setHistoryByContact((prev) => ({
        ...prev,
        [contact.id]: { status: "error", message: "Couldn't load history for this contact." },
      }));
    }
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-slate-900">Contacts</h2>
          <p className="text-sm text-slate-500">
            Your saved contacts, and call/voicemail history with each one.
          </p>
        </div>
        {!formOpen && (
          <button
            onClick={openCreateForm}
            className="bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg px-4 py-2"
          >
            + Add contact
          </button>
        )}
      </div>

      {loadError && <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{loadError}</p>}

      {formOpen && (
        <form onSubmit={handleSubmit} className="bg-white rounded-xl border border-slate-200 p-6 space-y-3">
          <h3 className="font-semibold text-slate-900">{editingId ? "Edit contact" : "New contact"}</h3>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-500 mb-1">Name</label>
              <input
                required
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                className="w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-500 mb-1">Phone number</label>
              <input
                required
                value={form.phone_number}
                onChange={(e) => setForm({ ...form, phone_number: e.target.value })}
                placeholder="+15551234567"
                className="w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-mono placeholder:text-slate-400"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-500 mb-1">Email (optional)</label>
              <input
                type="email"
                value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
                className="w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-500 mb-1">Notes (optional)</label>
              <input
                value={form.notes}
                onChange={(e) => setForm({ ...form, notes: e.target.value })}
                className="w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
              />
            </div>
          </div>

          {formError && <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{formError}</p>}

          <div className="flex gap-2">
            <button
              type="submit"
              disabled={formBusy}
              className="bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white text-sm font-medium rounded-lg px-4 py-2"
            >
              {formBusy ? "Saving..." : editingId ? "Save changes" : "Add contact"}
            </button>
            <button type="button" onClick={closeForm} className="text-sm text-slate-500 hover:text-slate-700 px-2">
              Cancel
            </button>
          </div>
        </form>
      )}

      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-3">
        {loading && <p className="text-sm text-slate-500">Loading...</p>}
        {!loading && contacts.length === 0 && (
          <p className="text-sm text-slate-500">No contacts yet — add the people you call or text most.</p>
        )}

        {contacts.map((contact) => (
          <div key={contact.id} className="rounded-lg border border-slate-200 px-4 py-3 space-y-3">
            <div className="flex items-center justify-between gap-4">
              <div>
                <div className="text-sm font-medium text-slate-800">{contact.name}</div>
                <div className="text-xs text-slate-500 font-mono">{contact.phone_number}</div>
                {contact.email && <div className="text-xs text-slate-400">{contact.email}</div>}
                {contact.notes && <div className="text-xs text-slate-400 italic mt-0.5">{contact.notes}</div>}
              </div>
              <div className="flex items-center gap-3 shrink-0">
                <button
                  onClick={() => handleToggleHistory(contact)}
                  className="text-xs font-medium text-indigo-600 hover:text-indigo-800"
                >
                  {expandedId === contact.id ? "Hide history" : "View history"}
                </button>
                <button
                  onClick={() => openEditForm(contact)}
                  className="text-xs font-medium text-slate-500 hover:text-slate-800"
                >
                  Edit
                </button>
                <button
                  onClick={() => handleDelete(contact)}
                  disabled={actionBusyId === contact.id}
                  className="text-xs font-medium text-red-600 hover:text-red-800 disabled:opacity-60"
                >
                  Delete
                </button>
              </div>
            </div>

            {expandedId === contact.id && (
              <ContactHistory state={historyByContact[contact.id]} />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function ContactHistory({ state }: { state: HistoryState | undefined }) {
  if (!state || state.status === "loading") {
    return <p className="text-xs text-slate-500 bg-slate-50 rounded-lg px-3 py-2">Loading history...</p>;
  }
  if (state.status === "error") {
    return <p className="text-xs text-red-600 bg-red-50 rounded-lg px-3 py-2">{state.message}</p>;
  }
  if (state.entries.length === 0) {
    return <p className="text-xs text-slate-500 bg-slate-50 rounded-lg px-3 py-2">No calls, voicemails, or receptionist calls with this number yet.</p>;
  }

  return (
    <div className="space-y-2 pt-1 border-t border-slate-100">
      {state.entries.map((entry) => (
        <div key={`${entry.type}:${entry.id}`} className="rounded-lg border border-slate-200 px-3 py-2">
          <div className="flex items-center justify-between gap-4">
            <span className="text-xs font-medium text-slate-700">{HISTORY_TYPE_LABEL[entry.type]}</span>
            <span className="text-xs text-slate-400">{new Date(entry.created_at).toLocaleString()}</span>
          </div>
          <div className="text-xs text-slate-500 mt-0.5">
            {[entry.status, entry.duration !== null && formatDuration(entry.duration)]
              .filter(Boolean)
              .join(" · ")}
          </div>
          {entry.summary && <p className="text-xs text-slate-600 mt-1">{entry.summary}</p>}
          {entry.recording_url && (
            <a
              href={entry.recording_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs font-medium text-indigo-600 hover:text-indigo-800 mt-1 inline-block"
            >
              Play recording
            </a>
          )}
        </div>
      ))}
    </div>
  );
}
