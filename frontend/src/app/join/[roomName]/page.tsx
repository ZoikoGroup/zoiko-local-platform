"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { useParams } from "next/navigation";
import { Room, RoomEvent, Track } from "livekit-client";
import { guestJoinVideoRoom, checkGuestWaitingStatus, ApiError } from "@/lib/api";
import MeetingRoom, { createParticipantTile, type ReactionEvent } from "@/components/MeetingRoom";

type CallState = "lobby" | "requesting" | "waiting" | "in-call" | "ended" | "denied" | "expired" | "not-found";
type DeviceStatus = "idle" | "requesting" | "ready" | "blocked";

type ChatMessage = {
  id: string;
  senderName: string;
  isLocal: boolean;
  text: string;
  ts: number;
};

const CHAT_ENCODER = new TextEncoder();
const CHAT_DECODER = new TextDecoder();

const POLL_INTERVAL_MS = 2000;

// Public, unauthenticated page - anyone with the link can land here, no
// Zoiko account or login involved. Deliberately a much smaller feature set
// than the dashboard's video page (no create/end room, no screen share) -
// a guest can request to join, wait for the host to admit them, then
// see/hear everyone and leave. Recording is host-only and consent-gated on
// the dashboard side; this page only ever displays whether one is active.
export default function GuestJoinPage() {
  const params = useParams<{ roomName: string }>();
  const roomName = params.roomName;

  const [callState, setCallState] = useState<CallState>("lobby");
  const [callError, setCallError] = useState<string | null>(null);
  const [displayName, setDisplayName] = useState("");
  const [cameraOn, setCameraOn] = useState(false);
  const [micOn, setMicOn] = useState(false);
  const [cameraStatus, setCameraStatus] = useState<DeviceStatus>("idle");
  const [micStatus, setMicStatus] = useState<DeviceStatus>("idle");
  const [lobbyVideoStream, setLobbyVideoStream] = useState<MediaStream | null>(null);
  const [waitingId, setWaitingId] = useState<string | null>(null);
  const [recording, setRecording] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [unreadChatCount, setUnreadChatCount] = useState(0);
  const [reactions, setReactions] = useState<ReactionEvent[]>([]);
  const [participants, setParticipants] = useState<{ identity: string; name: string }[]>([]);

  const roomRef = useRef<Room | null>(null);
  const lobbyVideoRef = useRef<HTMLVideoElement>(null);
  const localVideoRef = useRef<HTMLVideoElement>(null);
  const remoteContainerRef = useRef<HTMLDivElement>(null);
  const attachedElements = useRef<Map<string, HTMLMediaElement>>(new Map());
  const participantTiles = useRef<Map<string, HTMLDivElement>>(new Map());
  const pendingJoinRef = useRef<{ camera: boolean; mic: boolean } | null>(null);
  const chatOpenRef = useRef(false);
  // Mirror of lobbyVideoStream for the unmount cleanup below, which is
  // registered once at mount time - reading the state variable directly
  // there would close over its initial `null` value forever (same reason
  // chatOpenRef exists).
  const lobbyVideoStreamRef = useRef<MediaStream | null>(null);

  useEffect(() => {
    lobbyVideoStreamRef.current = lobbyVideoStream;
  }, [lobbyVideoStream]);

  useEffect(() => {
    return () => {
      roomRef.current?.disconnect();
      lobbyVideoStreamRef.current?.getTracks().forEach((t) => t.stop());
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const videoEl = lobbyVideoRef.current;
    if (!videoEl) return;
    videoEl.srcObject = lobbyVideoStream;
  }, [lobbyVideoStream, callState]);

  useEffect(() => {
    if (callState !== "in-call") return;
    const room = roomRef.current;
    const videoEl = localVideoRef.current;
    if (!room || !videoEl) return;
    const cameraPublication = room.localParticipant.getTrackPublication(Track.Source.Camera);
    cameraPublication?.videoTrack?.attach(videoEl);
  }, [callState]);

  useEffect(() => {
    chatOpenRef.current = chatOpen;
  }, [chatOpen]);

  function stopLobbyVideoPreview() {
    lobbyVideoStream?.getTracks().forEach((t) => t.stop());
    setLobbyVideoStream(null);
  }

  async function handleToggleCamera() {
    if (cameraOn) {
      stopLobbyVideoPreview();
      setCameraOn(false);
      return;
    }
    setCameraStatus("requesting");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: true });
      setLobbyVideoStream(stream);
      setCameraOn(true);
      setCameraStatus("ready");
    } catch {
      setCameraStatus("blocked");
      setCameraOn(false);
    }
  }

  async function handleToggleMic() {
    if (micOn) {
      setMicOn(false);
      return;
    }
    setMicStatus("requesting");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream.getTracks().forEach((t) => t.stop());
      setMicOn(true);
      setMicStatus("ready");
    } catch {
      setMicStatus("blocked");
      setMicOn(false);
    }
  }

  function getOrCreateTile(identity: string, name: string): HTMLDivElement {
    const existing = participantTiles.current.get(identity);
    if (existing) return existing;
    const tile = createParticipantTile(identity, name);
    remoteContainerRef.current?.appendChild(tile);
    participantTiles.current.set(identity, tile);
    return tile;
  }

  function clearRemoteTiles() {
    participantTiles.current.forEach((tile) => tile.remove());
    participantTiles.current.clear();
    setParticipants([]);
  }

  async function connectToRoom(liveKitToken: string, url: string) {
    const { camera: useCamera, mic: useMic } = pendingJoinRef.current ?? { camera: false, mic: false };

    const room = new Room();
    roomRef.current = room;

    room.on(RoomEvent.TrackSubscribed, (track, _publication, participant) => {
      if (track.kind === Track.Kind.Video || track.kind === Track.Kind.Audio) {
        const tile = getOrCreateTile(participant.identity, participant.name || participant.identity);
        const el = track.attach();
        attachedElements.current.set(track.sid ?? el.id, el);
        tile.appendChild(el);
      }
    });
    room.on(RoomEvent.TrackUnsubscribed, (track) => {
      track.detach().forEach((el) => el.remove());
    });
    room.on(RoomEvent.ParticipantConnected, (participant) => {
      getOrCreateTile(participant.identity, participant.name || participant.identity);
      setParticipants((prev) => [...prev, { identity: participant.identity, name: participant.name || participant.identity }]);
    });
    room.on(RoomEvent.ParticipantDisconnected, (participant) => {
      setParticipants((prev) => prev.filter((p) => p.identity !== participant.identity));
      participantTiles.current.get(participant.identity)?.remove();
      participantTiles.current.delete(participant.identity);
    });
    room.on(RoomEvent.DataReceived, (payload, participant) => {
      let parsed: { type?: string; text?: string; emoji?: string };
      try {
        parsed = JSON.parse(CHAT_DECODER.decode(payload));
      } catch {
        return;
      }
      if (parsed.type === "chat" && typeof parsed.text === "string") {
        setChatMessages((prev) => [
          ...prev,
          { id: `${Date.now()}-${Math.random()}`, senderName: participant?.name || "Guest", isLocal: false, text: parsed.text as string, ts: Date.now() },
        ]);
        if (!chatOpenRef.current) setUnreadChatCount((c) => c + 1);
      } else if (parsed.type === "reaction" && typeof parsed.emoji === "string") {
        const id = `${Date.now()}-${Math.random()}`;
        setReactions((prev) => [...prev, { id, identity: participant?.identity ?? "unknown", emoji: parsed.emoji as string }]);
        setTimeout(() => setReactions((prev) => prev.filter((r) => r.id !== id)), 2500);
      }
    });
    room.on(RoomEvent.Disconnected, () => {
      setCallState("ended");
      roomRef.current = null;
      clearRemoteTiles();
    });

    await room.connect(url, liveKitToken);
    if (useCamera) await room.localParticipant.setCameraEnabled(true);
    if (useMic) await room.localParticipant.setMicrophoneEnabled(true);
    setCameraOn(useCamera);
    setMicOn(useMic);

    // Participants already in the room when we connect don't fire
    // ParticipantConnected (that only fires for joins after us) - without
    // this, someone already on the call with both camera and mic off would
    // never get a tile at all.
    const already = Array.from(room.remoteParticipants.values());
    already.forEach((p) => getOrCreateTile(p.identity, p.name || p.identity));
    setParticipants(already.map((p) => ({ identity: p.identity, name: p.name || p.identity })));
    setCallState("in-call");
  }

  // Polls the waiting-room status while a request is pending - stops as
  // soon as the host responds (or the guest navigates away, via cleanup).
  useEffect(() => {
    if (callState !== "waiting" || !roomName || !waitingId) return;

    let cancelled = false;
    const interval = setInterval(async () => {
      try {
        const result = await checkGuestWaitingStatus(roomName, waitingId);
        if (cancelled) return;
        if (result.status === "admitted" && result.token && result.url) {
          clearInterval(interval);
          setRecording(result.recording);
          await connectToRoom(result.token, result.url);
        } else if (result.status === "denied") {
          clearInterval(interval);
          setCallState("denied");
        } else if (result.status === "expired") {
          clearInterval(interval);
          setCallState("expired");
        }
      } catch (err) {
        if (cancelled) return;
        clearInterval(interval);
        if (err instanceof ApiError && err.status === 404) {
          setCallState("not-found");
          return;
        }
        setCallState("lobby");
        setCallError("Lost connection while waiting - please try joining again.");
      }
    }, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [callState, roomName, waitingId]);

  async function handleJoin() {
    if (!roomName || !displayName.trim()) return;
    setCallError(null);
    setCallState("requesting");
    pendingJoinRef.current = { camera: cameraOn, mic: micOn };
    stopLobbyVideoPreview();

    try {
      const { waiting_id } = await guestJoinVideoRoom(roomName, displayName.trim());
      setWaitingId(waiting_id);
      setCallState("waiting");
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setCallState("not-found");
        return;
      }
      setCallState("lobby");
      const message = err instanceof ApiError || err instanceof Error ? err.message : "Unknown error.";
      setCallError(`Couldn't request to join: ${message}`);
    }
  }

  function handleLeave() {
    roomRef.current?.disconnect();
    roomRef.current = null;
    setCallState("ended");
    setChatMessages([]);
    setChatOpen(false);
    setUnreadChatCount(0);
    clearRemoteTiles();
    setReactions([]);
  }

  function handleSendChatMessage(e: FormEvent) {
    e.preventDefault();
    const text = chatInput.trim();
    const room = roomRef.current;
    if (!text || !room) return;
    room.localParticipant.publishData(CHAT_ENCODER.encode(JSON.stringify({ type: "chat", text })), { reliable: true });
    setChatMessages((prev) => [
      ...prev,
      { id: `${Date.now()}-${Math.random()}`, senderName: "You", isLocal: true, text, ts: Date.now() },
    ]);
    setChatInput("");
  }

  function handleSendReaction(emoji: string) {
    const room = roomRef.current;
    if (!room) return;
    room.localParticipant.publishData(CHAT_ENCODER.encode(JSON.stringify({ type: "reaction", emoji })), { reliable: true });
    const id = `${Date.now()}-${Math.random()}`;
    setReactions((prev) => [...prev, { id, identity: "local", emoji }]);
    setTimeout(() => setReactions((prev) => prev.filter((r) => r.id !== id)), 2500);
  }

  function handleToggleMicInCall() {
    const next = !micOn;
    setMicOn(next);
    roomRef.current?.localParticipant.setMicrophoneEnabled(next);
  }

  function handleToggleCameraInCall() {
    const next = !cameraOn;
    setCameraOn(next);
    roomRef.current?.localParticipant.setCameraEnabled(next);
  }

  return (
    <main className="min-h-screen bg-slate-950 flex items-center justify-center p-6">
      <div className="w-full max-w-2xl">
        <div className="flex items-center gap-2 justify-center mb-6">
          <div className="w-8 h-8 rounded-lg bg-indigo-600 text-white flex items-center justify-center font-bold text-sm">
            Z
          </div>
          <h1 className="font-semibold text-white">Zoiko Local</h1>
        </div>

        {callState === "not-found" && (
          <div className="bg-slate-900 border border-slate-800 rounded-2xl p-8 text-center space-y-2">
            <h2 className="text-lg font-semibold text-white">This call isn&apos;t available</h2>
            <p className="text-sm text-slate-400">
              The link may have expired, or the call has already ended.
            </p>
          </div>
        )}

        {callState === "denied" && (
          <div className="bg-slate-900 border border-slate-800 rounded-2xl p-8 text-center space-y-2">
            <h2 className="text-lg font-semibold text-white">You weren&apos;t let in</h2>
            <p className="text-sm text-slate-400">The host didn&apos;t admit you to this call.</p>
          </div>
        )}

        {callState === "expired" && (
          <div className="bg-slate-900 border border-slate-800 rounded-2xl p-8 text-center space-y-3">
            <h2 className="text-lg font-semibold text-white">Nobody responded in time</h2>
            <p className="text-sm text-slate-400">
              The host didn&apos;t respond to your request to join. They may not be on the call right now.
            </p>
            <button
              onClick={() => setCallState("lobby")}
              className="text-sm font-medium text-indigo-400 hover:text-indigo-300"
            >
              Try again
            </button>
          </div>
        )}

        {callState === "waiting" && (
          <div className="bg-slate-900 border border-slate-800 rounded-2xl p-8 text-center space-y-3">
            <div className="w-8 h-8 mx-auto rounded-full border-2 border-slate-700 border-t-indigo-500 animate-spin" />
            <h2 className="text-lg font-semibold text-white">Waiting for the host to let you in…</h2>
            <p className="text-sm text-slate-400">You&apos;ll join automatically once they admit you.</p>
          </div>
        )}

        {callState === "ended" && (
          <div className="bg-slate-900 border border-slate-800 rounded-2xl p-8 text-center space-y-2">
            <h2 className="text-lg font-semibold text-white">You&apos;ve left the call</h2>
            <p className="text-sm text-slate-400">You can close this tab now.</p>
          </div>
        )}

        {(callState === "lobby" || callState === "requesting") && (
          <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-4">
            <div className="relative aspect-video bg-black rounded-lg overflow-hidden flex items-center justify-center">
              {cameraOn ? (
                <video ref={lobbyVideoRef} autoPlay muted playsInline className="w-full h-full object-cover" />
              ) : (
                <div className="flex flex-col items-center gap-2 text-slate-400">
                  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="w-10 h-10">
                    <path d="M3 3l18 18M15 10l4.55-2.9A1 1 0 0121 8v8a1 1 0 01-1.45.9L15 14M5 6h7a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2z" />
                  </svg>
                  <span className="text-sm">Camera off</span>
                  {cameraStatus === "blocked" && (
                    <span className="text-xs text-red-400">Camera access was blocked by the browser.</span>
                  )}
                </div>
              )}
            </div>

            <div className="flex items-center justify-center gap-3">
              <button
                onClick={handleToggleMic}
                disabled={micStatus === "requesting"}
                className={`text-xs font-medium rounded-lg px-3 py-2 disabled:opacity-60 ${
                  micOn ? "bg-slate-800 text-white" : "bg-red-700 text-white"
                }`}
              >
                Mic {micOn ? "On" : "Off"}
              </button>
              <button
                onClick={handleToggleCamera}
                disabled={cameraStatus === "requesting"}
                className={`text-xs font-medium rounded-lg px-3 py-2 disabled:opacity-60 ${
                  cameraOn ? "bg-slate-800 text-white" : "bg-red-700 text-white"
                }`}
              >
                {cameraStatus === "requesting" ? "Turning on…" : `Camera ${cameraOn ? "On" : "Off"}`}
              </button>
            </div>

            <div>
              <label htmlFor="guest-name" className="block text-xs font-medium text-slate-400 mb-1 tracking-wide uppercase">
                Your name
              </label>
              <input
                id="guest-name"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="Enter your name"
                className="w-full rounded-lg bg-slate-800 border border-slate-700 text-white text-sm px-3 py-2 placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
              />
            </div>

            <p className="text-xs text-slate-500 bg-slate-800/60 rounded-lg px-3 py-2">
              This call may be recorded by the host. By joining, you acknowledge that.
            </p>

            {callError && <p className="text-xs text-red-400 bg-red-950/50 rounded-lg px-3 py-2">{callError}</p>}

            <button
              onClick={handleJoin}
              disabled={!displayName.trim() || callState === "requesting"}
              className="w-full bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white text-sm font-medium rounded-lg px-4 py-2.5"
            >
              {callState === "requesting" ? "Requesting…" : "Join meeting"}
            </button>
          </div>
        )}

        {callState === "in-call" && (
          <MeetingRoom
            displayName={displayName}
            micOn={micOn}
            cameraOn={cameraOn}
            onToggleMic={handleToggleMicInCall}
            onToggleCamera={handleToggleCameraInCall}
            localVideoRef={localVideoRef}
            remoteContainerRef={remoteContainerRef}
            participants={participants}
            reactions={reactions}
            onSendReaction={handleSendReaction}
            chatOpen={chatOpen}
            onToggleChat={() => {
              setChatOpen((v) => !v);
              setUnreadChatCount(0);
            }}
            unreadChatCount={unreadChatCount}
            chatMessages={chatMessages}
            chatInput={chatInput}
            onChatInputChange={setChatInput}
            onSendChat={handleSendChatMessage}
            onLeave={handleLeave}
            leaveLabel="Leave call"
            topLeft={<span className="truncate">{displayName}</span>}
            recordingBadge={recording}
          />
        )}
      </div>
    </main>
  );
}
