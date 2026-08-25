"use client";

import { useEffect, useState, useCallback } from "react";
import {
  listMyNumbers,
  listConversations,
  listConversationMessages,
  sendWhatsAppMessage,
  sendSms,
  ApiError,
  type MyPhoneNumber,
  type MessagingConversation,
  type MessagingMessage,
  type MessagingChannel,
} from "@/lib/api";
import { getToken } from "@/lib/auth";

const CHANNEL_LABEL: Record<MessagingChannel, string> = { whatsapp: "WhatsApp", sms: "SMS" };

function sendOnChannel(
  token: string,
  channel: MessagingChannel,
  input: { phone_number_id: string; to: string; body: string }
) {
  return channel === "whatsapp" ? sendWhatsAppMessage(token, input) : sendSms(token, input);
}

export default function MessagingPage() {
  const [token] = useState<string | null>(() => getToken());
  const [numbers, setNumbers] = useState<MyPhoneNumber[]>([]);
  const [conversations, setConversations] = useState<MessagingConversation[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [messages, setMessages] = useState<MessagingMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [newChannel, setNewChannel] = useState<MessagingChannel>("whatsapp");
  const [newNumberId, setNewNumberId] = useState("");
  const [newTo, setNewTo] = useState("");
  const [newBody, setNewBody] = useState("");
  const [sending, setSending] = useState(false);

  const channelNumbers = numbers.filter((n) => (newChannel === "whatsapp" ? n.whatsapp_enabled : n.sms_enabled));
  const anyChannelEnabled = numbers.some((n) => n.whatsapp_enabled || n.sms_enabled);

  const loadConversations = useCallback(() => {
    if (!token) return;
    return Promise.all([listMyNumbers(token), listConversations(token)])
      .then(([numberData, conversationData]) => {
        setNumbers(numberData);
        setConversations(conversationData);
        setError(null);
      })
      .catch(() => setError("Couldn't load conversations."))
      .finally(() => setLoading(false));
  }, [token]);

  useEffect(() => {
    loadConversations();
  }, [loadConversations]);

  // Resets the selected number whenever the channel tab changes, rather than
  // in a separate effect - see https://react.dev/learn/you-might-not-need-an-effect#adjusting-some-state-when-a-prop-changes
  const [prevChannel, setPrevChannel] = useState(newChannel);
  if (newChannel !== prevChannel) {
    setPrevChannel(newChannel);
    setNewNumberId("");
  }

  const loadMessages = useCallback(
    (conversationId: string) => {
      if (!token) return;
      return listConversationMessages(token, conversationId)
        .then(setMessages)
        .catch(() => setError("Couldn't load this conversation's messages."));
    },
    [token]
  );

  useEffect(() => {
    if (selectedId) loadMessages(selectedId);
  }, [selectedId, loadMessages]);

  async function handleSend(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !newNumberId || !newTo.trim() || !newBody.trim()) return;
    setSending(true);
    setError(null);
    try {
      await sendOnChannel(token, newChannel, { phone_number_id: newNumberId, to: newTo.trim(), body: newBody.trim() });
      setNewBody("");
      await loadConversations();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't send that message.");
    } finally {
      setSending(false);
    }
  }

  async function handleReply(e: React.FormEvent) {
    e.preventDefault();
    const conversation = conversations.find((c) => c.id === selectedId);
    if (!token || !conversation || !newBody.trim()) return;
    setSending(true);
    setError(null);
    try {
      await sendOnChannel(token, conversation.channel, {
        phone_number_id: conversation.phone_number_id,
        to: conversation.customer_number,
        body: newBody.trim(),
      });
      setNewBody("");
      await Promise.all([loadConversations(), loadMessages(conversation.id)]);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't send that message.");
    } finally {
      setSending(false);
    }
  }

  const selected = conversations.find((c) => c.id === selectedId) ?? null;

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-slate-900">Business Messaging</h2>
        <p className="text-sm text-slate-500">
          WhatsApp and SMS conversations - only numbers with the matching channel approval recorded can send.
        </p>
      </div>

      {error && <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{error}</p>}
      {loading && <p className="text-sm text-slate-500">Loading...</p>}

      {!loading && !anyChannelEnabled && (
        <p className="text-sm text-slate-500 bg-slate-50 rounded-lg px-3 py-2">
          No number is WhatsApp- or SMS-enabled yet. Enable one from a number&apos;s routing settings once the
          matching approval is confirmed.
        </p>
      )}

      {!loading && anyChannelEnabled && (
        <form onSubmit={handleSend} className="bg-white rounded-xl border border-slate-200 p-6 space-y-3">
          <h3 className="font-semibold text-slate-900">Start a conversation</h3>
          <div className="flex gap-2">
            {(["whatsapp", "sms"] as const).map((c) => (
              <button
                key={c}
                type="button"
                onClick={() => setNewChannel(c)}
                className={`text-xs font-medium rounded-full px-3 py-1 ${
                  newChannel === c ? "bg-indigo-600 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                }`}
              >
                {CHANNEL_LABEL[c]}
              </button>
            ))}
          </div>
          <div className="grid sm:grid-cols-3 gap-3">
            <select
              value={newNumberId}
              onChange={(e) => setNewNumberId(e.target.value)}
              className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900"
            >
              <option value="">From number...</option>
              {channelNumbers.map((n) => (
                <option key={n.id} value={n.id}>
                  {n.e164}
                </option>
              ))}
            </select>
            <input
              value={newTo}
              onChange={(e) => setNewTo(e.target.value)}
              placeholder="Customer number (+1...)"
              className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400"
            />
            <input
              value={newBody}
              onChange={(e) => setNewBody(e.target.value)}
              placeholder="Message"
              className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400"
            />
          </div>
          {channelNumbers.length === 0 && (
            <p className="text-xs text-amber-600">No {CHANNEL_LABEL[newChannel]}-enabled number yet.</p>
          )}
          <button
            type="submit"
            disabled={sending || !newNumberId || !newTo.trim() || !newBody.trim()}
            className="bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white text-sm font-medium rounded-lg px-4 py-2"
          >
            {sending ? "Sending..." : "Send"}
          </button>
        </form>
      )}

      {!loading && (
        <div className="grid md:grid-cols-3 gap-4">
          <div className="bg-white rounded-xl border border-slate-200 p-4 space-y-2 md:col-span-1">
            <h3 className="text-xs font-semibold text-slate-600 mb-1">Conversations</h3>
            {conversations.length === 0 && <p className="text-xs text-slate-500">No conversations yet.</p>}
            <ul className="space-y-1">
              {conversations.map((c) => (
                <li key={c.id}>
                  <button
                    onClick={() => setSelectedId(c.id)}
                    className={`w-full text-left rounded-lg px-3 py-2 text-sm ${
                      selectedId === c.id ? "bg-indigo-50 text-indigo-700" : "hover:bg-slate-50 text-slate-700"
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-medium">{c.customer_number}</span>
                      <span className="text-[10px] font-medium text-slate-400 bg-slate-100 rounded-full px-2 py-0.5">
                        {CHANNEL_LABEL[c.channel]}
                      </span>
                    </div>
                    <div className="text-xs text-slate-400">
                      {c.opted_out ? "Opted out" : new Date(c.last_message_at).toLocaleString()}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          </div>

          <div className="bg-white rounded-xl border border-slate-200 p-4 md:col-span-2 space-y-3">
            {!selected && <p className="text-sm text-slate-500">Select a conversation to view its messages.</p>}
            {selected && (
              <>
                <div className="flex items-center justify-between">
                  <h3 className="font-semibold text-slate-900">
                    {selected.customer_number}
                    <span className="ml-2 text-xs font-medium text-slate-400">{CHANNEL_LABEL[selected.channel]}</span>
                  </h3>
                  {selected.opted_out && (
                    <span className="text-xs font-medium text-red-700 bg-red-50 rounded-full px-2 py-1">Opted out</span>
                  )}
                </div>
                <div className="space-y-2 max-h-96 overflow-y-auto">
                  {messages.map((m) => (
                    <div
                      key={m.id}
                      className={`max-w-[75%] rounded-lg px-3 py-2 text-sm ${
                        m.direction === "outbound" ? "ml-auto bg-indigo-50 text-indigo-900" : "bg-slate-50 text-slate-800"
                      }`}
                    >
                      <div>{m.body}</div>
                      <div className="text-[10px] text-slate-400 mt-1">
                        {m.status} - {new Date(m.created_at).toLocaleString()}
                      </div>
                    </div>
                  ))}
                  {messages.length === 0 && <p className="text-xs text-slate-500">No messages yet.</p>}
                </div>
                {!selected.opted_out && (
                  <form onSubmit={handleReply} className="flex gap-2 pt-2 border-t border-slate-100">
                    <input
                      value={newBody}
                      onChange={(e) => setNewBody(e.target.value)}
                      placeholder="Reply..."
                      className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm placeholder:text-slate-400"
                    />
                    <button
                      type="submit"
                      disabled={sending || !newBody.trim()}
                      className="bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white text-sm font-medium rounded-lg px-4 py-2"
                    >
                      Send
                    </button>
                  </form>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
