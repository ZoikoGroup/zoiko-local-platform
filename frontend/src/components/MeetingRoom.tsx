"use client";

import { useEffect, useRef, useState, type FormEvent, type RefObject } from "react";

export type ReactionEvent = { id: string; identity: string; emoji: string };

export type ChatMessage = {
  id: string;
  senderName: string;
  isLocal: boolean;
  text: string;
};

const REACTION_EMOJIS = ["👍", "❤️", "😂", "👏", "🎉", "✋"];

// Same avatar shown for a participant everywhere (tile placeholder, in-call
// participant chips) - derived from the name so it's stable across
// reconnects without needing a real profile picture anywhere in the system.
export function getInitials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

const AVATAR_COLORS = ["bg-indigo-600", "bg-emerald-600", "bg-rose-600", "bg-amber-600", "bg-sky-600", "bg-fuchsia-600"];
export function avatarColorFor(identity: string): string {
  let hash = 0;
  for (let i = 0; i < identity.length; i++) hash = (hash * 31 + identity.charCodeAt(i)) >>> 0;
  return AVATAR_COLORS[hash % AVATAR_COLORS.length];
}

// Shared tile markup for a remote participant - both the dashboard host page
// and the guest join page call this from their own room.on(...) handlers
// (each owns its Room instance and participantTiles map), so the visual
// result never drifts between the two. The avatar+name placeholder is
// always in the DOM; a <video>/<audio> element from track.attach() gets
// appended on top of it when a track is actually subscribed (it's absolutely
// positioned, so it visually covers the placeholder), and removing it on
// TrackUnsubscribed reveals the placeholder again - no separate "has video"
// state needs tracking anywhere.
export function createParticipantTile(identity: string, name: string): HTMLDivElement {
  const tile = document.createElement("div");
  tile.dataset.identity = identity;
  tile.className =
    "relative w-full h-full min-h-0 bg-slate-800 rounded-xl overflow-hidden flex items-center justify-center " +
    "[&>video]:absolute [&>video]:inset-0 [&>video]:w-full [&>video]:h-full [&>video]:object-cover [&>audio]:hidden";

  const placeholder = document.createElement("div");
  placeholder.className = "flex flex-col items-center gap-2 pointer-events-none";
  const avatar = document.createElement("div");
  avatar.className = `w-16 h-16 rounded-full ${avatarColorFor(identity)} text-white flex items-center justify-center text-xl font-semibold`;
  avatar.textContent = getInitials(name);
  placeholder.appendChild(avatar);
  tile.appendChild(placeholder);

  const label = document.createElement("span");
  label.className =
    "absolute bottom-2 left-2 z-10 text-xs text-white/90 bg-black/40 rounded px-2 py-0.5 pointer-events-none";
  label.textContent = name;
  tile.appendChild(label);

  return tile;
}

// Adaptive tile-count -> column-count, roughly matching how Meet grows/
// shrinks tiles as people join rather than a fixed minimum tile size. A
// solo call (just you, 0 others) gets exactly 1 column so the one tile
// fills the whole grid instead of being squeezed into half of a 2-column
// layout with the other half empty.
function gridColumns(tileCount: number): number {
  if (tileCount <= 1) return 1;
  if (tileCount <= 4) return 2;
  if (tileCount <= 9) return 3;
  return 4;
}

// Drops a short-lived emoji burst directly into a tile's DOM node - the
// tile already has `relative` positioning and a stable identity via
// data-identity, so this sidesteps trying to keep a React-rendered overlay
// in sync with an imperatively-managed video grid. "local" always targets
// localTileRef; any other identity is looked up in the remote container.
function burstReaction(
  identity: string,
  emoji: string,
  localTileRef: RefObject<HTMLDivElement | null>,
  remoteContainerRef: RefObject<HTMLDivElement | null>
) {
  const target =
    identity === "local"
      ? localTileRef.current
      : remoteContainerRef.current?.querySelector<HTMLDivElement>(`[data-identity="${CSS.escape(identity)}"]`);
  if (!target) return;

  const span = document.createElement("span");
  span.textContent = emoji;
  span.className = "absolute left-1/2 bottom-10 -translate-x-1/2 text-4xl transition-all duration-[1500ms] ease-out pointer-events-none z-20";
  target.appendChild(span);
  // Two rAFs so the initial position paints before the transition target
  // class is applied - otherwise the browser may coalesce both style
  // states into one frame and the emoji just appears at its end position
  // with no visible float/fade.
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      span.classList.add("-translate-y-16", "opacity-0");
    });
  });
  setTimeout(() => span.remove(), 1600);
}

function IconButton({
  onClick,
  active,
  danger,
  label,
  children,
  badge,
}: {
  onClick: () => void;
  active?: boolean;
  danger?: boolean;
  label: string;
  children: React.ReactNode;
  badge?: number;
}) {
  return (
    <button
      onClick={onClick}
      title={label}
      className={`relative w-12 h-12 rounded-full flex items-center justify-center transition ${
        danger
          ? "bg-red-600 hover:bg-red-500 text-white"
          : active
            ? "bg-white text-slate-900 hover:bg-slate-200"
            : "bg-slate-700/80 hover:bg-slate-600 text-white"
      }`}
    >
      {children}
      {!!badge && badge > 0 && (
        <span className="absolute -top-1 -right-1 flex items-center justify-center w-4.5 h-4.5 min-w-[18px] rounded-full bg-red-500 text-white text-[10px] font-bold px-1">
          {badge > 9 ? "9+" : badge}
        </span>
      )}
    </button>
  );
}

const iconProps = { xmlns: "http://www.w3.org/2000/svg", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, className: "w-5 h-5" };

const MicIcon = () => <svg {...iconProps}><path d="M12 15a3 3 0 003-3V6a3 3 0 00-6 0v6a3 3 0 003 3z" /><path d="M19 11a7 7 0 01-14 0M12 18v3" /></svg>;
const MicOffIcon = () => <svg {...iconProps}><path d="M3 3l18 18M9 9v3a3 3 0 004.5 2.6M15 6a3 3 0 00-6-.5" /><path d="M19 11a7 7 0 01-1.2 3.9M5 11a7 7 0 001 3.9M12 18v3" /></svg>;
const VideoIcon = () => <svg {...iconProps}><path d="M15 10l4.55-2.9A1 1 0 0121 8v8a1 1 0 01-1.45.9L15 14M5 6h7a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2z" /></svg>;
const VideoOffIcon = () => <svg {...iconProps}><path d="M3 3l18 18M15 10l4.55-2.9A1 1 0 0121 8v8a1 1 0 01-1.45.9L15 14M5 6h7a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2z" /></svg>;
const ScreenShareIcon = () => <svg {...iconProps}><rect x="3" y="4" width="18" height="12" rx="1.5" /><path d="M8 20h8M12 16v4M9 11l3-3 3 3M12 8v6" /></svg>;
const ChatIcon = () => <svg {...iconProps}><path d="M21 11.5a8.38 8.38 0 01-.9 3.8 8.5 8.5 0 01-7.6 4.7 8.38 8.38 0 01-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 01-.9-3.8 8.5 8.5 0 014.7-7.6 8.38 8.38 0 013.8-.9h.5a8.48 8.48 0 018 8v.5z" /></svg>;
const SmileIcon = () => <svg {...iconProps}><circle cx="12" cy="12" r="9" /><path d="M8 14s1.5 2 4 2 4-2 4-2M9 9h.01M15 9h.01" /></svg>;
const RecordIcon = () => <svg {...iconProps} fill="currentColor" stroke="none"><circle cx="12" cy="12" r="6" /></svg>;
const StopIcon = () => <svg {...iconProps} fill="currentColor" stroke="none"><rect x="7" y="7" width="10" height="10" rx="1.5" /></svg>;
const PhoneOffIcon = () => <svg {...iconProps}><path d="M3 3l18 18M10.68 13.31a16 16 0 003.41 2.6l1.27-1.27a2 2 0 012.11-.45c.87.28 1.79.48 2.73.59A2 2 0 0122 16.72V19a2 2 0 01-2.18 2 19.79 19.79 0 01-6.8-1.87 19.5 19.5 0 01-6-4.55M6.62 10.79a19.5 19.5 0 01-1.7-4.31A2 2 0 016.9 4h2.28a2 2 0 012 1.72c.11.94.31 1.86.59 2.73a2 2 0 01-.45 2.11L9.69 12" /></svg>;

export type MeetingRoomProps = {
  displayName: string;
  micOn: boolean;
  cameraOn: boolean;
  onToggleMic: () => void;
  onToggleCamera: () => void;
  localVideoRef: RefObject<HTMLVideoElement | null>;
  remoteContainerRef: RefObject<HTMLDivElement | null>;
  participants: { identity: string; name: string }[];
  connectionQuality?: "excellent" | "good" | "poor" | null;

  reactions: ReactionEvent[];
  onSendReaction: (emoji: string) => void;

  chatOpen: boolean;
  onToggleChat: () => void;
  unreadChatCount: number;
  chatMessages: ChatMessage[];
  chatInput: string;
  onChatInputChange: (v: string) => void;
  onSendChat: (e: FormEvent) => void;

  onLeave: () => void;
  leaveLabel: string;
  topLeft?: React.ReactNode;
  recordingBadge?: boolean;
  confidentialBadge?: boolean;

  // Host-only extras - all optional, omitted entirely by the guest page.
  screenSharing?: boolean;
  onToggleScreenShare?: () => void;
  localScreenVideoRef?: RefObject<HTMLVideoElement | null>;
  recordingState?: "idle" | "busy" | "consent_required" | "active";
  onStartRecording?: () => void;
  onStopRecording?: () => void;
  onGrantConsentAndRecord?: () => void;
  recordingError?: string | null;
  waitingGuests?: { id: string; display_name: string }[];
  onAdmitGuest?: (id: string) => void;
  onDenyGuest?: (id: string) => void;
  admittingGuestId?: string | null;
};

export default function MeetingRoom(props: MeetingRoomProps) {
  const {
    displayName, micOn, cameraOn, onToggleMic, onToggleCamera, localVideoRef, remoteContainerRef,
    participants, connectionQuality, reactions, onSendReaction, chatOpen, onToggleChat, unreadChatCount,
    chatMessages, chatInput, onChatInputChange, onSendChat, onLeave, leaveLabel, topLeft, recordingBadge,
    confidentialBadge, screenSharing, onToggleScreenShare, localScreenVideoRef, recordingState,
    onStartRecording, onStopRecording, onGrantConsentAndRecord, recordingError, waitingGuests,
    onAdmitGuest, onDenyGuest, admittingGuestId,
  } = props;

  const [reactionPickerOpen, setReactionPickerOpen] = useState(false);
  const localTileRef = useRef<HTMLDivElement>(null);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const burstedIds = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (!chatOpen) return;
    chatEndRef.current?.scrollIntoView({ block: "end" });
  }, [chatMessages, chatOpen]);

  useEffect(() => {
    for (const r of reactions) {
      if (burstedIds.current.has(r.id)) continue;
      burstedIds.current.add(r.id);
      burstReaction(r.identity, r.emoji, localTileRef, remoteContainerRef);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reactions]);

  const tileCount = participants.length + 1;
  const columns = gridColumns(tileCount);

  return (
    <div className="fixed inset-0 z-[60] bg-slate-950 flex flex-col">
      <div className="flex items-center justify-between px-4 sm:px-6 py-3 text-xs text-slate-400 shrink-0">
        <div className="flex items-center gap-3 min-w-0">{topLeft}</div>
        <div className="flex items-center gap-3 shrink-0">
          {recordingBadge && (
            <span className="flex items-center gap-1.5 text-red-400">
              <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />
              Recording
            </span>
          )}
          {confidentialBadge && (
            <span className="text-indigo-300 bg-indigo-950/60 border border-indigo-900 rounded-full px-2 py-0.5 text-[11px] font-medium">
              Confidential Mode
            </span>
          )}
          <span>{participants.length} other participant{participants.length === 1 ? "" : "s"}</span>
        </div>
      </div>

      {recordingState === "consent_required" && (
        <div className="mx-4 sm:mx-6 mb-2 text-xs bg-amber-950 text-amber-400 rounded-lg px-3 py-2 flex items-center justify-between gap-3 shrink-0">
          <span>Recording this call needs your consent first.</span>
          <button onClick={onGrantConsentAndRecord} className="font-medium underline shrink-0">
            Grant consent &amp; record
          </button>
        </div>
      )}
      {recordingError && (
        <p className="mx-4 sm:mx-6 mb-2 text-xs text-red-400 bg-red-950/50 rounded-lg px-3 py-2 shrink-0">{recordingError}</p>
      )}
      {!!waitingGuests?.length && (
        <div className="mx-4 sm:mx-6 mb-2 bg-indigo-950/50 border border-indigo-900 rounded-lg px-3 py-2 space-y-2 shrink-0">
          <p className="text-xs font-medium text-indigo-300">
            {waitingGuests.length} {waitingGuests.length === 1 ? "person" : "people"} waiting to join
          </p>
          <ul className="space-y-1.5">
            {waitingGuests.map((guest) => (
              <li key={guest.id} className="flex items-center justify-between gap-3 text-sm">
                <span className="text-slate-200 truncate">{guest.display_name}</span>
                <div className="flex items-center gap-2 shrink-0">
                  <button
                    onClick={() => onAdmitGuest?.(guest.id)}
                    disabled={admittingGuestId === guest.id}
                    className="text-xs font-medium rounded-lg px-2.5 py-1 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white"
                  >
                    Admit
                  </button>
                  <button
                    onClick={() => onDenyGuest?.(guest.id)}
                    disabled={admittingGuestId === guest.id}
                    className="text-xs font-medium rounded-lg px-2.5 py-1 bg-slate-800 hover:bg-slate-700 disabled:opacity-60 text-slate-300"
                  >
                    Deny
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex-1 min-h-0 flex gap-3 px-4 sm:px-6 pb-3">
        <div className="flex-1 min-w-0 flex flex-col gap-3">
          {screenSharing && (
            <div className="relative shrink-0 max-h-[45%] aspect-video bg-black rounded-xl overflow-hidden mx-auto w-full">
              <video ref={localScreenVideoRef} autoPlay muted playsInline className="w-full h-full object-contain" />
              <span className="absolute bottom-2 left-2 text-xs text-white/80 bg-black/40 rounded px-2 py-0.5">Your screen</span>
            </div>
          )}

          <div
            className="flex-1 min-h-0 grid gap-3 auto-rows-fr overflow-y-auto content-center"
            style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
          >
            <div ref={localTileRef} className="relative w-full h-full min-h-0 bg-slate-800 rounded-xl overflow-hidden flex items-center justify-center">
              {/* The <video> element stays permanently mounted (never
                  conditionally removed) so localVideoRef always points to a
                  real DOM node - LiveKit's LocalTrackPublished handler
                  attaches to it the moment the camera track republishes
                  after being toggled off/on, which can fire before React
                  would have re-rendered a conditionally-mounted element.
                  Confirmed live: conditionally rendering this caused video
                  to intermittently never reappear after a camera toggle,
                  since the attach could race ahead of the remount. The
                  avatar placeholder is layered on top instead of replacing
                  it, and simply hides once real video is visible again. */}
              <video ref={localVideoRef} autoPlay muted playsInline className="absolute inset-0 w-full h-full object-cover" />
              {!cameraOn && (
                <div className="absolute inset-0 bg-slate-800 flex items-center justify-center">
                  <div className={`w-16 h-16 rounded-full ${avatarColorFor(displayName || "you")} text-white flex items-center justify-center text-xl font-semibold`}>
                    {getInitials(displayName || "You")}
                  </div>
                </div>
              )}
              <span className="absolute bottom-2 left-2 z-10 flex items-center gap-1.5 text-xs text-white/90 bg-black/40 rounded px-2 py-0.5">
                <span
                  title={connectionQuality ? `Connection: ${connectionQuality}` : undefined}
                  className={`w-1.5 h-1.5 rounded-full ${
                    connectionQuality === "excellent" ? "bg-emerald-400" : connectionQuality === "good" ? "bg-amber-400" : connectionQuality === "poor" ? "bg-red-500" : "bg-slate-500"
                  }`}
                />
                You {!micOn && "(muted)"}
              </span>
            </div>
            <div ref={remoteContainerRef} className="contents" />
          </div>
        </div>

        {chatOpen && (
          <div className="w-72 shrink-0 flex flex-col bg-slate-900 border border-slate-800 rounded-xl">
            <div className="px-3 py-2.5 border-b border-slate-800 text-sm font-medium text-slate-200">In-call chat</div>
            <div className="flex-1 min-h-0 overflow-y-auto px-3 py-2 space-y-2">
              {chatMessages.length === 0 ? (
                <p className="text-xs text-slate-500">No messages yet.</p>
              ) : (
                chatMessages.map((m) => (
                  <div key={m.id} className="text-sm">
                    <span className={`text-xs font-medium ${m.isLocal ? "text-indigo-400" : "text-slate-400"}`}>{m.senderName}</span>
                    <p className="text-slate-200 break-words">{m.text}</p>
                  </div>
                ))
              )}
              <div ref={chatEndRef} />
            </div>
            <form onSubmit={onSendChat} className="flex items-center gap-1.5 p-2 border-t border-slate-800">
              <input
                value={chatInput}
                onChange={(e) => onChatInputChange(e.target.value)}
                placeholder="Message everyone"
                className="flex-1 min-w-0 text-sm rounded-lg bg-slate-800 border border-slate-700 text-white px-2.5 py-1.5 placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
              />
              <button type="submit" disabled={!chatInput.trim()} className="text-xs font-medium rounded-lg px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-40 text-white">
                Send
              </button>
            </form>
          </div>
        )}
      </div>

      <div className="relative flex items-center justify-center gap-3 pb-6 pt-1 shrink-0">
        <IconButton onClick={onToggleMic} active={!micOn} danger={!micOn} label={micOn ? "Mute" : "Unmute"}>
          {micOn ? <MicIcon /> : <MicOffIcon />}
        </IconButton>
        <IconButton onClick={onToggleCamera} active={!cameraOn} danger={!cameraOn} label={cameraOn ? "Stop video" : "Start video"}>
          {cameraOn ? <VideoIcon /> : <VideoOffIcon />}
        </IconButton>
        {onToggleScreenShare && (
          <IconButton onClick={onToggleScreenShare} active={screenSharing} label={screenSharing ? "Stop sharing" : "Share screen"}>
            <ScreenShareIcon />
          </IconButton>
        )}

        <div className="relative">
          <IconButton onClick={() => setReactionPickerOpen((v) => !v)} label="React">
            <SmileIcon />
          </IconButton>
          {reactionPickerOpen && (
            <div className="absolute bottom-14 left-1/2 -translate-x-1/2 bg-slate-800 border border-slate-700 rounded-full px-2 py-1.5 flex items-center gap-1 shadow-xl">
              {REACTION_EMOJIS.map((emoji) => (
                <button
                  key={emoji}
                  onClick={() => {
                    onSendReaction(emoji);
                    setReactionPickerOpen(false);
                  }}
                  className="text-xl w-8 h-8 flex items-center justify-center rounded-full hover:bg-slate-700 transition"
                >
                  {emoji}
                </button>
              ))}
            </div>
          )}
        </div>

        <IconButton onClick={onToggleChat} active={chatOpen} label="Chat" badge={unreadChatCount}>
          <ChatIcon />
        </IconButton>

        {onStartRecording && recordingState !== "active" && (
          <IconButton onClick={onStartRecording} label={recordingState === "busy" ? "Starting..." : "Record"}>
            <RecordIcon />
          </IconButton>
        )}
        {onStopRecording && recordingState === "active" && (
          <IconButton onClick={onStopRecording} danger label="Stop recording">
            <StopIcon />
          </IconButton>
        )}

        <IconButton onClick={onLeave} danger label={leaveLabel}>
          <PhoneOffIcon />
        </IconButton>
      </div>
    </div>
  );
}
