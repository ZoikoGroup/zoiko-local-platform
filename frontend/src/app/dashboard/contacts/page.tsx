"use client";

import { useEffect, useState, useCallback } from "react";
import {
  getCurrentUser,
  listContacts,
  createContact,
  updateContact,
  deleteContact,
  getContactHistory,
  ApiError,
  type User,
  type Contact,
  type ContactHistoryEntry,
} from "@/lib/api";
import { getToken } from "@/lib/auth";

type FormState = { name: string; phone_number: string; email: string; notes: string };
const EMPTY_FORM: FormState = { name: "", phone_number: "", email: "", notes: "" };

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
  const [me, setMe] = useState<User | null>(null);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [historyByContact, setHistoryByContact] = useState<Record<string, HistoryState>>({});
  const [search, setSearch] = useState("");

  const canWrite = me?.role !== "viewer";

  const loadAll = useCallback(() => {
    if (!token) return;
    return Promise.all([getCurrentUser(token), listContacts(token)])
      .then(([userData, contactsData]) => {
        setMe(userData);
        setContacts(contactsData);
        setLoadError(null);
      })
      .catch(() => setLoadError("Couldn't load contacts."))
      .finally(() => setLoading(false));
  }, [token]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  function startCreate() {
    setEditingId(null);
    setForm(EMPTY_FORM);
    setFormError(null);
    setShowForm(true);
  }

  function startEdit(contact: Contact) {
    setEditingId(contact.id);
    setForm({
      name: contact.name,
      phone_number: contact.phone_number,
      email: contact.email ?? "",
      notes: contact.notes ?? "",
    });
    setFormError(null);
    setShowForm(true);
  } 

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (!token) return;
    setSaving(true);
    setFormError(null);
    const input = {
      name: form.name.trim(),
      phone_number: form.phone_number.trim(),
      email: form.email.trim() || null,
      notes: form.notes.trim() || null,
    };
    try {
      if (editingId) {
        const updated = await updateContact(token, editingId, input);
        setContacts((prev) => prev.map((c) => (c.id === editingId ? updated : c)));
      } else {
        const created = await createContact(token, input);
        setContacts((prev) => [...prev, created].sort((a, b) => a.name.localeCompare(b.name)));
      }
      setShowForm(false);
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Couldn't save this contact.");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(contactId: string) {
    if (!token) return;
    try {
      await deleteContact(token, contactId);
      setContacts((prev) => prev.filter((c) => c.id !== contactId));
    } catch {
      setLoadError("Couldn't delete this contact.");
    }
  }

  async function handleToggleHistory(contactId: string) {
    if (expandedId === contactId) {
      setExpandedId(null);
      return;
    }
    setExpandedId(contactId);
    if (!token || historyByContact[contactId]?.status === "done") return;

    setHistoryByContact((prev) => ({ ...prev, [contactId]: { status: "loading" } }));
    try {
      const entries = await getContactHistory(token, contactId);
      setHistoryByContact((prev) => ({ ...prev, [contactId]: { status: "done", entries } }));
    } catch {
      setHistoryByContact((prev) => ({
        ...prev,
        [contactId]: { status: "error", message: "Couldn't load history for this contact." },
      }));
    }
  }

  const filteredContacts = contacts.filter(
    (c) =>
      c.name.toLowerCase().includes(search.toLowerCase()) ||
      c.phone_number.includes(search)
  );

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-slate-900">Contacts</h2>
          <p className="text-sm text-slate-500">
            Your saved contacts, and call/voicemail/receptionist-call history with each one.
          </p>
        </div>
        {canWrite && (
          <button
            onClick={startCreate}
            className="bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg px-4 py-2"
          >
            Add Contact
          </button>
        )}
      </div>

      {loadError && <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{loadError}</p>}

      {showForm && (
        <form
          onSubmit={handleSave}
          className="bg-white rounded-xl border border-slate-200 p-6 space-y-4"
        >
          <h3 className="font-semibold text-slate-900">{editingId ? "Edit Contact" : "Add Contact"}</h3>
          <div className="grid sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-500 mb-1">Name</label>
              <input
                required
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-500 mb-1">Phone number</label>
              <input
                required
                value={form.phone_number}
                onChange={(e) => setForm((f) => ({ ...f, phone_number: e.target.value }))}
                placeholder="+15551234567"
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-mono text-slate-900 placeholder:text-slate-400"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-500 mb-1">Email (optional)</label>
              <input
                type="email"
                value={form.email}
                onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))}
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-500 mb-1">Notes (optional)</label>
              <input
                value={form.notes}
                onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))}
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900"
              />
            </div>
          </div>

          {formError && <p className="text-sm text-red-600">{formError}</p>}

          <div className="flex gap-2">
            <button
              type="submit"
              disabled={saving}
              className="bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white text-sm font-medium rounded-lg px-4 py-2"
            >
              {saving ? "Saving..." : "Save"}
            </button>
            <button
              type="button"
              onClick={() => setShowForm(false)}
              className="text-sm font-medium rounded-lg px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700"
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search by name or number..."
          className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400"
        />

        {loading && <p className="text-sm text-slate-500">Loading...</p>}
        {!loading && filteredContacts.length === 0 && (
          <p className="text-sm text-slate-500">
            {contacts.length === 0 ? "No contacts yet." : "No contacts match your search."}
          </p>
        )}

        <ul className="divide-y divide-slate-100">
          {filteredContacts.map((contact) => {
            const isExpanded = expandedId === contact.id;
            return (
              <li key={contact.id} className="py-3">
                <div className="flex items-center justify-between gap-4">
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-slate-800 truncate">{contact.name}</div>
                    <div className="text-xs text-slate-500 font-mono">{contact.phone_number}</div>
                    {contact.email && <div className="text-xs text-slate-400">{contact.email}</div>}
                    {contact.notes && <div className="text-xs text-slate-400 italic">{contact.notes}</div>}
                  </div>
                  <div className="flex items-center gap-3 shrink-0 text-xs font-medium">
                    <button
                      onClick={() => handleToggleHistory(contact.id)}
                      className="text-indigo-600 hover:text-indigo-800"
                    >
                      {isExpanded ? "Hide history" : "History"}
                    </button>
                    {canWrite && (
                      <>
                        <button onClick={() => startEdit(contact)} className="text-slate-500 hover:text-slate-700">
                          Edit
                        </button>
                        <button onClick={() => handleDelete(contact.id)} className="text-red-600 hover:text-red-700">
                          Delete
                        </button>
                      </>
                    )}
                  </div>
                </div>

                {isExpanded && <ContactHistory state={historyByContact[contact.id]} />}
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

function ContactHistory({ state }: { state: HistoryState | undefined }) {
  if (!state || state.status === "loading") {
    return <p className="mt-3 text-xs text-slate-500 bg-slate-50 rounded-lg px-3 py-2">Loading history...</p>;
  }
  if (state.status === "error") {
    return <p className="mt-3 text-xs text-red-600 bg-red-50 rounded-lg px-3 py-2">{state.message}</p>;
  }
  if (state.entries.length === 0) {
    return (
      <p className="mt-3 text-xs text-slate-500 bg-slate-50 rounded-lg px-3 py-2">
        No calls, voicemails, or receptionist calls with this number yet.
      </p>
    );
  }

  return (
    <div className="mt-3 space-y-2">
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
