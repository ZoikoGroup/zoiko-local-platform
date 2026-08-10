"use client";

import { useEffect, useRef, useState, useCallback, type FormEvent } from "react";
import { Room, RoomEvent, Track, ConnectionQuality } from "livekit-client";
import {
  getCurrentUser,
  listVideoRooms,
  createVideoRoom,
  joinVideoRoom,
  endVideoRoom,
  startVideoRecording,
  reportCallQuality,
  summarizeVideoSession,
  grantAiProcessingConsent,
  listWaitingGuests,
  admitWaitingGuest,
  denyWaitingGuest,
  ApiError,
  type VideoRoom,
  type ConversationSummary,
  type WaitingGuest,
} from "@/lib/api";
import { getToken } from "@/lib/auth";

type CallState = "idle" | "lobby" | "connecting" | "in-call";
type RecordingState = "idle" | "busy" | "consent_required" | "active";
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

export default function VideoPage() {
  const [token] = useState<string | null>(() => getToken());

  const [rooms, setRooms] = useState<VideoRoom[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [callState, setCallState] = useState<CallState>("idle");
  const [callError, setCallError] = useState<string | null>(null);
  const [roomName, setRoomName] = useState<string | null>(null);
  const [joinRoomInput, setJoinRoomInput] = useState("");
  const [cameraOn, setCameraOn] = useState(true);
  const [micOn, setMicOn] = useState(true);
  const [screenSharing, setScreenSharing] = useState(false);
  const [participantCount, setParticipantCount] = useState(0);
  const [participants, setParticipants] = useState<{ identity: string; name: string }[]>([]);
  const [inviteLinkCopied, setInviteLinkCopied] = useState(false);
  const [waitingGuests, setWaitingGuests] = useState<WaitingGuest[]>([]);
  const [admittingGuestId, setAdmittingGuestId] = useState<string | null>(null);
  const [recordingState, setRecordingState] = useState<RecordingState>("idle");
  const [recordingError, setRecordingError] = useState<string | null>(null);

  const [summaries, setSummaries] = useState<Record<string, ConversationSummary>>({});
  const [summarizingRoom, setSummarizingRoom] = useState<string | null>(null);
  const [summaryErrors, setSummaryErrors] = useState<Record<string, string>>({});
  const [copiedRoom, setCopiedRoom] = useState<string | null>(null);

  // Pre-join lobby - a device/camera check before actually connecting to the
  // LiveKit room, not just an instant auto-join. lobbyRoomName is null when
  // starting a brand new call (a room is created only once "Join meeting"
  // is clicked) vs joining an existing active one.
  const [lobbyRoomName, setLobbyRoomName] = useState<string | null>(null);
  const [lobbyDisplayName, setLobbyDisplayName] = useState("");
  const [lobbyCameraOn, setLobbyCameraOn] = useState(false);
  const [lobbyMicOn, setLobbyMicOn] = useState(false);
  const [lobbyCameraStatus, setLobbyCameraStatus] = useState<DeviceStatus>("idle");
  const [lobbyMicStatus, setLobbyMicStatus] = useState<DeviceStatus>("idle");
  const [lobbyVideoStream, setLobbyVideoStream] = useState<MediaStream | null>(null);
  const [videoDevices, setVideoDevices] = useState<MediaDeviceInfo[]>([]);
  const [audioDevices, setAudioDevices] = useState<MediaDeviceInfo[]>([]);
  const [selectedVideoDeviceId, setSelectedVideoDeviceId] = useState("");
  const [selectedAudioDeviceId, setSelectedAudioDeviceId] = useState("");
  const [lobbyConfidential, setLobbyConfidential] = useState(false);
  const [roomConfidential, setRoomConfidential] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [unreadChatCount, setUnreadChatCount] = useState(0);
  const [connectionQuality, setConnectionQuality] = useState<"excellent" | "good" | "poor" | null>(null);

  const roomRef = useRef<Room | null>(null);
  const localVideoRef = useRef<HTMLVideoElement>(null);
  const localScreenVideoRef = useRef<HTMLVideoElement>(null);
  const lobbyVideoRef = useRef<HTMLVideoElement>(null);
  const remoteContainerRef = useRef<HTMLDivElement>(null);
  const attachedElements = useRef<Map<string, HTMLMediaElement>>(new Map());
  const participantTiles = useRef<Map<string, HTMLDivElement>>(new Map());

  function getOrCreateTile(identity: string, name: string): HTMLDivElement {
    const existing = participantTiles.current.get(identity);
    if (existing) return existing;

    const tile = document.createElement("div");
    tile.className =
      "relative aspect-video bg-black rounded-lg overflow-hidden [&>video]:w-full [&>video]:h-full [&>video]:object-cover [&>audio]:hidden";
    const label = document.createElement("span");
    label.className =
      "absolute bottom-2 left-2 text-xs text-white/80 bg-black/40 rounded px-2 py-0.5 pointer-events-none";
    label.textContent = name;
    tile.appendChild(label);

    remoteContainerRef.current?.appendChild(tile);
    participantTiles.current.set(identity, tile);
    return tile;
  }

  function clearRemoteTiles() {
    participantTiles.current.forEach((tile) => tile.remove());
    participantTiles.current.clear();
    setParticipants([]);
  }

  const chatEndRef = useRef<HTMLDivElement>(null);
  const chatOpenRef = useRef(false);

  const loadRooms = useCallback(() => {
    if (!token) return;
    return listVideoRooms(token)
      .then((data) => {
        setRooms(data);
        setLoadError(null);
      })
      .catch(() => setLoadError("Couldn't load your video call history."))
      .finally(() => setLoading(false));
  }, [token]);

  useEffect(() => {
    loadRooms();
  }, [loadRooms]);

  useEffect(() => {
    return () => {
      roomRef.current?.disconnect();
      lobbyVideoStream?.getTracks().forEach((t) => t.stop());
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Attaches the lobby's own getUserMedia preview stream (separate from any
  // LiveKit room - we haven't connected to one yet at this point) to its
  // <video> element whenever the stream changes.
  useEffect(() => {
    const videoEl = lobbyVideoRef.current;
    if (!videoEl) return;
    videoEl.srcObject = lobbyVideoStream;
  }, [lobbyVideoStream, callState]);

  // Runs once callState flips to "in-call", which is when the <video> element
  // below actually mounts - attaching earlier (e.g. right after
  // enableCameraAndMicrophone) targets a ref that's still null, since that
  // branch of the JSX hasn't rendered yet.
  useEffect(() => {
    if (callState !== "in-call") return;
    const room = roomRef.current;
    const videoEl = localVideoRef.current;
    if (!room || !videoEl) return;
    const cameraPublication = room.localParticipant.getTrackPublication(Track.Source.Camera);
    cameraPublication?.videoTrack?.attach(videoEl);
  }, [callState]);

  // Polls for guests waiting to be let in, while actually in a call - not
  // before (no room exists yet to wait for) and not after (nothing to poll).
  useEffect(() => {
    if (callState !== "in-call" || !token || !roomName) return;
    let cancelled = false;
    const poll = () => {
      listWaitingGuests(token, roomName)
        .then((guests) => {
          if (!cancelled) setWaitingGuests(guests);
        })
        .catch(() => {
          // Transient failure - the next poll a few seconds later will just
          // pick up where this one left off, not worth surfacing an error.
        });
    };
    poll();
    const interval = setInterval(poll, 3000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [callState, token, roomName]);

  useEffect(() => {
    chatOpenRef.current = chatOpen;
  }, [chatOpen]);

  useEffect(() => {
    if (!chatOpen) return;
    chatEndRef.current?.scrollIntoView({ block: "end" });
  }, [chatMessages, chatOpen]);

  // Same timing issue as the camera effect above - the screen-share preview
  // element only renders once screenSharing is true, so attach after.
  useEffect(() => {
    if (!screenSharing) return;
    const room = roomRef.current;
    const videoEl = localScreenVideoRef.current;
    if (!room || !videoEl) return;
    const screenPublication = room.localParticipant.getTrackPublication(Track.Source.ScreenShare);
    screenPublication?.videoTrack?.attach(videoEl);
  }, [screenSharing]);

  async function refreshDeviceList() {
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      setVideoDevices(devices.filter((d) => d.kind === "videoinput"));
      setAudioDevices(devices.filter((d) => d.kind === "audioinput"));
    } catch {
      // Device labels are blank until permission has been granted at least
      // once - the dropdowns just show generically-named options until then.
    }
  }

  function stopLobbyVideoPreview() {
    lobbyVideoStream?.getTracks().forEach((t) => t.stop());
    setLobbyVideoStream(null);
  }

  async function openLobby(existingRoomName: string | null) {
    setCallError(null);
    setLobbyRoomName(existingRoomName);
    setLobbyCameraOn(false);
    setLobbyMicOn(false);
    setLobbyCameraStatus("idle");
    setLobbyMicStatus("idle");
    setLobbyConfidential(existingRoomName ? rooms.find((r) => r.room_name === existingRoomName)?.confidential ?? false : false);
    setCallState("lobby");

    if (token) {
      try {
        const me = await getCurrentUser(token);
        setLobbyDisplayName((prev) => prev || me.email);
      } catch {
        // Name field just stays editable/blank - not fatal to opening the lobby.
      }
    }
    await refreshDeviceList();
  }

  function handleCancelLobby() {
    stopLobbyVideoPreview();
    setCallState("idle");
    setLobbyRoomName(null);
  }

  async function handleLobbyToggleCamera() {
    if (lobbyCameraOn) {
      stopLobbyVideoPreview();
      setLobbyCameraOn(false);
      return;
    }
    setLobbyCameraStatus("requesting");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: selectedVideoDeviceId ? { deviceId: { exact: selectedVideoDeviceId } } : true,
      });
      setLobbyVideoStream(stream);
      setLobbyCameraOn(true);
      setLobbyCameraStatus("ready");
      await refreshDeviceList(); // device labels become available once permission is granted
    } catch {
      setLobbyCameraStatus("blocked");
      setLobbyCameraOn(false);
    }
  }

  async function handleLobbyToggleMic() {
    if (lobbyMicOn) {
      setLobbyMicOn(false);
      return;
    }
    setLobbyMicStatus("requesting");
    try {
      // Just a permission/availability check - the mic isn't actually kept
      // open in the lobby (nothing here needs a live audio level meter), so
      // the stream is released immediately. LiveKit acquires its own fresh
      // mic stream once the call actually connects.
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: selectedAudioDeviceId ? { deviceId: { exact: selectedAudioDeviceId } } : true,
      });
      stream.getTracks().forEach((t) => t.stop());
      setLobbyMicOn(true);
      setLobbyMicStatus("ready");
      await refreshDeviceList();
    } catch {
      setLobbyMicStatus("blocked");
      setLobbyMicOn(false);
    }
  }

  async function handleLobbyVideoDeviceChange(deviceId: string) {
    setSelectedVideoDeviceId(deviceId);
    if (!lobbyCameraOn) return;
    stopLobbyVideoPreview();
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { deviceId: { exact: deviceId } } });
      setLobbyVideoStream(stream);
    } catch {
      setLobbyCameraStatus("blocked");
      setLobbyCameraOn(false);
    }
  }

  async function connectToRoom(existingRoomName: string | null) {
    if (!token) return;
    setCallError(null);
    setCallState("connecting");
    setRecordingState("idle");
    setRecordingError(null);
    const useCamera = lobbyCameraOn;
    const useMic = lobbyMicOn;
    const displayName = lobbyDisplayName.trim();
    const videoDeviceId = selectedVideoDeviceId;
    const audioDeviceId = selectedAudioDeviceId;
    stopLobbyVideoPreview();

    try {
      const me = await getCurrentUser(token);
      let targetRoomName = existingRoomName;
      let isConfidential = lobbyConfidential;
      if (!targetRoomName) {
        const created = await createVideoRoom(token, lobbyConfidential);
        targetRoomName = created.room_name;
        isConfidential = created.confidential;
      } else {
        isConfidential = rooms.find((r) => r.room_name === targetRoomName)?.confidential ?? false;
      }
      const { token: liveKitToken, url } = await joinVideoRoom(token, targetRoomName, displayName || me.email);

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
        setParticipantCount(room.remoteParticipants.size);
        setParticipants((prev) => [...prev, { identity: participant.identity, name: participant.name || participant.identity }]);
      });
      room.on(RoomEvent.ParticipantDisconnected, (participant) => {
        setParticipantCount(room.remoteParticipants.size);
        setParticipants((prev) => prev.filter((p) => p.identity !== participant.identity));
        participantTiles.current.get(participant.identity)?.remove();
        participantTiles.current.delete(participant.identity);
      });
      room.on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
        const speakingIds = new Set(speakers.map((p) => p.identity));
        participantTiles.current.forEach((tile, identity) => {
          tile.classList.toggle("ring-2", speakingIds.has(identity));
          tile.classList.toggle("ring-emerald-400", speakingIds.has(identity));
        });
      });
      room.on(RoomEvent.DataReceived, (payload, participant) => {
        let text: string;
        try {
          const parsed = JSON.parse(CHAT_DECODER.decode(payload));
          if (parsed?.type !== "chat" || typeof parsed.text !== "string") return;
          text = parsed.text;
        } catch {
          return;
        }
        setChatMessages((prev) => [
          ...prev,
          { id: `${Date.now()}-${Math.random()}`, senderName: participant?.name || "Guest", isLocal: false, text, ts: Date.now() },
        ]);
        if (!chatOpenRef.current) setUnreadChatCount((c) => c + 1);
      });
      room.on(RoomEvent.ConnectionQualityChanged, (quality, participant) => {
        if (!participant.isLocal) return;
        if (quality !== ConnectionQuality.Excellent && quality !== ConnectionQuality.Good && quality !== ConnectionQuality.Poor) {
          return; // "lost"/"unknown" aren't a quality tier worth persisting - a real disconnect is covered separately
        }
        setConnectionQuality(quality);
        reportCallQuality(token, targetRoomName as string, quality).catch(() => {
          // Best-effort telemetry - a failed sample just means one gap in
          // the call's quality history, not worth interrupting the call.
        });
      });
      room.on(RoomEvent.Reconnected, () => {
        reportCallQuality(token, targetRoomName as string, connectionQuality ?? "good", true).catch(() => {});
      });
      room.on(RoomEvent.Disconnected, () => {
        setCallState("idle");
        setRoomName(null);
        roomRef.current = null;
        clearRemoteTiles();
      });

      await room.connect(url, liveKitToken);
      // Respects the lobby's camera/mic toggles and chosen devices, rather
      // than always turning both on - a caller who left the camera off in
      // the lobby expects it to still be off once they're actually in the call.
      if (useCamera) {
        await room.localParticipant.setCameraEnabled(true, videoDeviceId ? { deviceId: videoDeviceId } : undefined);
      }
      if (useMic) {
        await room.localParticipant.setMicrophoneEnabled(true, audioDeviceId ? { deviceId: audioDeviceId } : undefined);
      }
      setCameraOn(useCamera);
      setMicOn(useMic);

      setRoomName(targetRoomName);
      setRoomConfidential(isConfidential);
      setParticipantCount(room.remoteParticipants.size);
      setCallState("in-call");
    } catch (err) {
      roomRef.current?.disconnect();
      roomRef.current = null;
      setCallState("idle");
      const message = err instanceof ApiError || err instanceof Error ? err.message : "Unknown error.";
      setCallError(`Couldn't ${existingRoomName ? "join" : "start"} the call: ${message}`);
    }
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

  async function handleCopyInviteLink() {
    if (!roomName) return;
    const inviteUrl = `${window.location.origin}/join/${roomName}`;
    try {
      await navigator.clipboard.writeText(inviteUrl);
      setInviteLinkCopied(true);
      setTimeout(() => setInviteLinkCopied(false), 2000);
    } catch {
      // Clipboard access can be denied by the browser - not worth a hard error,
      // the room name/link is already visible on screen to copy by hand.
    }
  }

  async function handleAdmitGuest(waitingId: string) {
    if (!token || !roomName) return;
    setAdmittingGuestId(waitingId);
    try {
      await admitWaitingGuest(token, roomName, waitingId);
      setWaitingGuests((prev) => prev.filter((g) => g.id !== waitingId));
    } catch {
      // Next poll will re-sync the list either way - not worth a hard error banner.
    } finally {
      setAdmittingGuestId(null);
    }
  }

  async function handleDenyGuest(waitingId: string) {
    if (!token || !roomName) return;
    setAdmittingGuestId(waitingId);
    try {
      await denyWaitingGuest(token, roomName, waitingId);
      setWaitingGuests((prev) => prev.filter((g) => g.id !== waitingId));
    } catch {
      // Next poll will re-sync the list either way - not worth a hard error banner.
    } finally {
      setAdmittingGuestId(null);
    }
  }

  async function handleEndCall() {
    if (!token || !roomName) return;
    const endingRoomName = roomName;
    roomRef.current?.disconnect();
    roomRef.current = null;
    setCallState("idle");
    setRoomName(null);
    setScreenSharing(false);
    setRecordingState("idle");
    clearRemoteTiles();
    setWaitingGuests([]);
    setRoomConfidential(false);
    setChatMessages([]);
    setChatOpen(false);
    setUnreadChatCount(0);
    setConnectionQuality(null);
    try {
      await endVideoRoom(token, endingRoomName);
    } catch {
      // room is already disconnected locally either way - not worth surfacing
    }
    await loadRooms();
  }

  async function handleCopyRoomInviteLink(targetRoomName: string) {
    const link = `${window.location.origin}/join/${targetRoomName}`;
    try {
      await navigator.clipboard.writeText(link);
      setCopiedRoom(targetRoomName);
      setTimeout(() => setCopiedRoom((current) => (current === targetRoomName ? null : current)), 2000);
    } catch {
      // clipboard permission denied or unavailable - not worth surfacing an error for
    }
  }

  function handleToggleCamera() {
    const next = !cameraOn;
    setCameraOn(next);
    roomRef.current?.localParticipant.setCameraEnabled(next);
  }

  function handleToggleMic() {
    const next = !micOn;
    setMicOn(next);
    roomRef.current?.localParticipant.setMicrophoneEnabled(next);
  }

  async function handleStartRecording() {
    if (!token || !roomName) return;
    setRecordingState("busy");
    setRecordingError(null);
    try {
      await startVideoRecording(token, roomName);
      setRecordingState("active");
    } catch (err) {
      if (err instanceof ApiError && err.status === 403 && err.message.toLowerCase().includes("consent")) {
        setRecordingState("consent_required");
        return;
      }
      setRecordingState("idle");
      const message = err instanceof ApiError || err instanceof Error ? err.message : "Unknown error.";
      setRecordingError(`Couldn't start recording: ${message}`);
    }
  }

  async function handleGrantConsentAndRecord() {
    if (!token) return;
    try {
      await grantAiProcessingConsent(token);
      await handleStartRecording();
    } catch {
      setRecordingError("Couldn't grant recording consent.");
    }
  }

  async function handleSummarize(targetRoomName: string) {
    if (!token) return;
    setSummarizingRoom(targetRoomName);
    setSummaryErrors((prev) => ({ ...prev, [targetRoomName]: "" }));
    try {
      const summary = await summarizeVideoSession(token, targetRoomName);
      setSummaries((prev) => ({ ...prev, [targetRoomName]: summary }));
    } catch (err) {
      if (err instanceof ApiError && err.status === 403 && err.message.toLowerCase().includes("consent")) {
        try {
          await grantAiProcessingConsent(token);
          const summary = await summarizeVideoSession(token, targetRoomName);
          setSummaries((prev) => ({ ...prev, [targetRoomName]: summary }));
        } catch (retryErr) {
          const message = retryErr instanceof ApiError || retryErr instanceof Error ? retryErr.message : "Couldn't summarize this call.";
          setSummaryErrors((prev) => ({ ...prev, [targetRoomName]: message }));
        }
      } else {
        const message = err instanceof ApiError || err instanceof Error ? err.message : "Couldn't summarize this call.";
        setSummaryErrors((prev) => ({ ...prev, [targetRoomName]: message }));
      }
    } finally {
      setSummarizingRoom(null);
    }
  }

  async function handleToggleScreenShare() {
    const room = roomRef.current;
    if (!room) return;

    if (screenSharing) {
      await room.localParticipant.setScreenShareEnabled(false);
      setScreenSharing(false);
      return;
    }

    try {
      const publication = await room.localParticipant.setScreenShareEnabled(true);
      // Detects the browser's own native "Stop sharing" bar/button, which
      // bypasses our button entirely - without this, our UI would keep
      // showing "Stop Sharing" after the share has actually already ended.
      publication?.videoTrack?.mediaStreamTrack.addEventListener("ended", () => {
        room.localParticipant.setScreenShareEnabled(false);
        setScreenSharing(false);
      });
      setScreenSharing(true);
    } catch {
      // user canceled the browser's screen-picker dialog - not an error worth surfacing
    }
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-slate-900">Video</h2>
        <p className="text-sm text-slate-500">1:1 and larger group video calling — up to 50 participants.</p>
      </div>

      {callState === "idle" && (
        <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
          <button
            onClick={() => openLobby(null)}
            className="bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg px-4 py-2"
          >
            Start a Video Call
          </button>

          <div className="flex items-center gap-3 pt-1">
            <div className="h-px bg-slate-200 flex-1" />
            <span className="text-xs text-slate-600">or join a teammate&apos;s call</span>
            <div className="h-px bg-slate-200 flex-1" />
          </div>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (joinRoomInput.trim()) openLobby(joinRoomInput.trim());
            }}
            className="flex gap-2"
          >
            <input
              value={joinRoomInput}
              onChange={(e) => setJoinRoomInput(e.target.value)}
              placeholder="Room name (e.g. zl-...)"
              className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm font-mono placeholder:text-slate-400"
            />
            <button
              type="submit"
              disabled={!joinRoomInput.trim()}
              className="bg-slate-800 hover:bg-slate-900 disabled:opacity-60 text-white text-sm font-medium rounded-lg px-4 py-2"
            >
              Join
            </button>
          </form>

          {callError && (
            <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{callError}</p>
          )}
        </div>
      )}

      {callState === "lobby" && (
        <div className="bg-slate-900 rounded-xl border border-slate-800 p-4 grid gap-4 sm:grid-cols-[1.3fr_1fr]">
          {/* Camera preview */}
          <div className="space-y-3">
            <div className="relative aspect-video bg-black rounded-lg overflow-hidden flex items-center justify-center">
              {lobbyCameraOn ? (
                <video ref={lobbyVideoRef} autoPlay muted playsInline className="w-full h-full object-cover" />
              ) : (
                <div className="flex flex-col items-center gap-2 text-slate-400">
                  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="w-10 h-10">
                    <path d="M3 3l18 18M15 10l4.55-2.9A1 1 0 0121 8v8a1 1 0 01-1.45.9L15 14M5 6h7a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2z" />
                  </svg>
                  <span className="text-sm">Camera off</span>
                  {lobbyCameraStatus === "blocked" && (
                    <span className="text-xs text-red-400">Camera access was blocked by the browser.</span>
                  )}
                </div>
              )}
            </div>

            <div className="flex items-center justify-center gap-3">
              <button
                onClick={handleLobbyToggleMic}
                disabled={lobbyMicStatus === "requesting"}
                className={`text-xs font-medium rounded-lg px-3 py-2 disabled:opacity-60 ${
                  lobbyMicOn ? "bg-slate-800 text-white" : "bg-red-700 text-white"
                }`}
              >
                Mic {lobbyMicOn ? "On" : "Off"}
              </button>
              <button
                onClick={handleLobbyToggleCamera}
                disabled={lobbyCameraStatus === "requesting"}
                className={`text-xs font-medium rounded-lg px-3 py-2 disabled:opacity-60 ${
                  lobbyCameraOn ? "bg-slate-800 text-white" : "bg-red-700 text-white"
                }`}
              >
                {lobbyCameraStatus === "requesting" ? "Turning on…" : `Camera ${lobbyCameraOn ? "On" : "Off"}`}
              </button>
            </div>

            {(videoDevices.length > 0 || audioDevices.length > 0) && (
              <div className="grid grid-cols-2 gap-2">
                <select
                  value={selectedVideoDeviceId}
                  onChange={(e) => handleLobbyVideoDeviceChange(e.target.value)}
                  className="w-full text-xs rounded-lg bg-slate-800 text-slate-200 border border-slate-700 px-2 py-1.5"
                >
                  <option value="">Default camera</option>
                  {videoDevices.map((d) => (
                    <option key={d.deviceId} value={d.deviceId}>
                      {d.label || "Camera"}
                    </option>
                  ))}
                </select>
                <select
                  value={selectedAudioDeviceId}
                  onChange={(e) => setSelectedAudioDeviceId(e.target.value)}
                  className="w-full text-xs rounded-lg bg-slate-800 text-slate-200 border border-slate-700 px-2 py-1.5"
                >
                  <option value="">Default microphone</option>
                  {audioDevices.map((d) => (
                    <option key={d.deviceId} value={d.deviceId}>
                      {d.label || "Microphone"}
                    </option>
                  ))}
                </select>
              </div>
            )}
          </div>

          {/* Meeting details + join */}
          <div className="space-y-4">
            <div>
              <h3 className="text-white font-semibold">{lobbyRoomName ? "Join call" : "Start a call"}</h3>
              <p className="text-xs text-slate-400 font-mono mt-0.5">{lobbyRoomName ?? "A new room will be created"}</p>
            </div>

            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1 tracking-wide uppercase">Your name</label>
              <input
                value={lobbyDisplayName}
                onChange={(e) => setLobbyDisplayName(e.target.value)}
                placeholder="Enter your name"
                className="w-full rounded-lg bg-slate-800 border border-slate-700 text-white text-sm px-3 py-2 placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
              />
            </div>

            {lobbyRoomName ? (
              lobbyConfidential && (
                <p className="text-xs text-indigo-300 bg-indigo-950/40 border border-indigo-900 rounded-lg px-3 py-2">
                  Confidential Mode is active for this call. Recording and AI notes are off.
                </p>
              )
            ) : (
              <label className="flex items-start gap-2 text-xs text-slate-300 bg-slate-800/60 rounded-lg px-3 py-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={lobbyConfidential}
                  onChange={(e) => setLobbyConfidential(e.target.checked)}
                  className="mt-0.5"
                />
                <span>
                  <span className="font-medium text-slate-200">Confidential Mode</span>
                  <br />
                  Disables recording and AI notes for this call. Can&apos;t be changed once the call starts.
                </span>
              </label>
            )}

            {callError && <p className="text-xs text-red-400 bg-red-950/50 rounded-lg px-3 py-2">{callError}</p>}

            <div className="flex gap-2 pt-1">
              <button
                onClick={() => connectToRoom(lobbyRoomName)}
                className="flex-1 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg px-4 py-2"
              >
                Join meeting
              </button>
              <button
                onClick={handleCancelLobby}
                className="text-sm font-medium rounded-lg px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {callState === "connecting" && (
        <div className="bg-white rounded-xl border border-slate-200 p-6">
          <p className="text-sm text-slate-500">Connecting...</p>
        </div>
      )}

      {callState === "in-call" && (
        <div className="bg-slate-900 rounded-xl border border-slate-800 p-4 space-y-3">
          <div className="flex items-center justify-between text-xs text-slate-400 px-1">
            <div className="flex items-center gap-2">
              <span className="font-mono">{roomName}</span>
              <button
                onClick={handleCopyInviteLink}
                className="text-indigo-400 hover:text-indigo-300 font-medium"
                title="Copy a link anyone can use to join this call without a Zoiko account"
              >
                {inviteLinkCopied ? "Link copied!" : "Copy invite link"}
              </button>
              {roomConfidential && (
                <span className="flex items-center gap-1 text-indigo-300 bg-indigo-950/60 border border-indigo-900 rounded-full px-2 py-0.5 text-[11px] font-medium">
                  Confidential Mode: Active
                </span>
              )}
            </div>
            <div className="flex items-center gap-3">
              {recordingState === "active" && (
                <span className="flex items-center gap-1.5 text-red-400">
                  <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />
                  Recording
                </span>
              )}
              <span>{participantCount} other participant{participantCount === 1 ? "" : "s"}</span>
            </div>
          </div>

          {recordingState === "consent_required" && (
            <div className="text-xs bg-amber-950 text-amber-400 rounded-lg px-3 py-2 flex items-center justify-between gap-3">
              <span>Recording this call needs your consent first.</span>
              <button onClick={handleGrantConsentAndRecord} className="font-medium underline shrink-0">
                Grant consent &amp; record
              </button>
            </div>
          )}
          {recordingError && (
            <p className="text-xs text-red-400 bg-red-950/50 rounded-lg px-3 py-2">{recordingError}</p>
          )}

          {waitingGuests.length > 0 && (
            <div className="bg-indigo-950/50 border border-indigo-900 rounded-lg px-3 py-2 space-y-2">
              <p className="text-xs font-medium text-indigo-300">
                {waitingGuests.length} {waitingGuests.length === 1 ? "person" : "people"} waiting to join
              </p>
              <ul className="space-y-1.5">
                {waitingGuests.map((guest) => (
                  <li key={guest.id} className="flex items-center justify-between gap-3 text-sm">
                    <span className="text-slate-200 truncate">{guest.display_name}</span>
                    <div className="flex items-center gap-2 shrink-0">
                      <button
                        onClick={() => handleAdmitGuest(guest.id)}
                        disabled={admittingGuestId === guest.id}
                        className="text-xs font-medium rounded-lg px-2.5 py-1 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white"
                      >
                        Admit
                      </button>
                      <button
                        onClick={() => handleDenyGuest(guest.id)}
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

          <div className="flex gap-3 items-stretch">
            <div className="flex-1 min-w-0 space-y-3">
              {screenSharing && (
                <div className="relative aspect-video bg-black rounded-lg overflow-hidden">
                  <video ref={localScreenVideoRef} autoPlay muted playsInline className="w-full h-full object-contain" />
                  <span className="absolute bottom-2 left-2 text-xs text-white/80 bg-black/40 rounded px-2 py-0.5">
                    Your screen
                  </span>
                </div>
              )}

              {/* auto-fit grid, not a fixed 2-column split - a fixed split works
                  for 1:1 but squeezes every remote tile into a single narrow
                  column once a 3rd+ participant joins (up to MAX_PARTICIPANTS=50
                  per app.integrations.video.livekit). display:contents on the
                  remote container lets each participant tile appended into it
                  imperatively (see getOrCreateTile) land directly in this grid
                  as its own sibling cell, sharing one unified auto-fit layout
                  instead of a fixed side column. max-h + overflow caps how tall
                  the grid gets once a call has many participants. */}
              <div
                className="grid gap-3 max-h-[60vh] overflow-y-auto pr-1"
                style={{ gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))" }}
              >
                <div className="relative aspect-video bg-black rounded-lg overflow-hidden">
                  <video ref={localVideoRef} autoPlay muted playsInline className="w-full h-full object-cover" />
                  <span className="absolute bottom-2 left-2 flex items-center gap-1.5 text-xs text-white/80 bg-black/40 rounded px-2 py-0.5">
                    <span
                      title={connectionQuality ? `Connection: ${connectionQuality}` : undefined}
                      className={`w-1.5 h-1.5 rounded-full ${
                        connectionQuality === "excellent"
                          ? "bg-emerald-400"
                          : connectionQuality === "good"
                            ? "bg-amber-400"
                            : connectionQuality === "poor"
                              ? "bg-red-500"
                              : "bg-slate-500"
                      }`}
                    />
                    You
                  </span>
                </div>
                <div ref={remoteContainerRef} className="contents" />
              </div>
            </div>

            {chatOpen && (
              <div className="w-64 shrink-0 flex flex-col bg-slate-950 border border-slate-800 rounded-lg">
                <div className="px-3 py-2 border-b border-slate-800 text-xs font-medium text-slate-300">In-call chat</div>
                <div className="flex-1 min-h-[200px] max-h-[320px] overflow-y-auto px-3 py-2 space-y-2">
                  {chatMessages.length === 0 ? (
                    <p className="text-xs text-slate-500">No messages yet.</p>
                  ) : (
                    chatMessages.map((m) => (
                      <div key={m.id} className="text-sm">
                        <span className={`text-xs font-medium ${m.isLocal ? "text-indigo-400" : "text-slate-400"}`}>
                          {m.senderName}
                        </span>
                        <p className="text-slate-200 break-words">{m.text}</p>
                      </div>
                    ))
                  )}
                  <div ref={chatEndRef} />
                </div>
                <form onSubmit={handleSendChatMessage} className="flex items-center gap-1.5 p-2 border-t border-slate-800">
                  <input
                    value={chatInput}
                    onChange={(e) => setChatInput(e.target.value)}
                    placeholder="Message everyone"
                    className="flex-1 min-w-0 text-sm rounded-lg bg-slate-800 border border-slate-700 text-white px-2.5 py-1.5 placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
                  />
                  <button
                    type="submit"
                    disabled={!chatInput.trim()}
                    className="text-xs font-medium rounded-lg px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-40 text-white"
                  >
                    Send
                  </button>
                </form>
              </div>
            )}
          </div>

          {participants.length > 0 && (
            <div className="flex flex-wrap gap-2 px-1">
              {participants.map((p) => (
                <span
                  key={p.identity}
                  className="text-xs text-slate-300 bg-slate-800 rounded-full px-2.5 py-1"
                >
                  {p.name}
                </span>
              ))}
            </div>
          )}

          <div className="flex items-center justify-center gap-3 pt-2">
            <button
              onClick={handleToggleMic}
              className={`text-xs font-medium rounded-lg px-3 py-2 ${
                micOn ? "bg-slate-800 text-white" : "bg-red-700 text-white"
              }`}
            >
              {micOn ? "Mute" : "Unmute"}
            </button>
            <button
              onClick={handleToggleCamera}
              className={`text-xs font-medium rounded-lg px-3 py-2 ${
                cameraOn ? "bg-slate-800 text-white" : "bg-red-700 text-white"
              }`}
            >
              {cameraOn ? "Stop Video" : "Start Video"}
            </button>
            <button
              onClick={handleToggleScreenShare}
              className={`text-xs font-medium rounded-lg px-3 py-2 ${
                screenSharing ? "bg-emerald-700 text-white" : "bg-slate-800 text-white"
              }`}
            >
              {screenSharing ? "Stop Sharing" : "Share Screen"}
            </button>
            <button
              onClick={() => {
                setChatOpen((v) => !v);
                setUnreadChatCount(0);
              }}
              className={`relative text-xs font-medium rounded-lg px-3 py-2 ${
                chatOpen ? "bg-emerald-700 text-white" : "bg-slate-800 text-white"
              }`}
            >
              Chat
              {unreadChatCount > 0 && (
                <span className="absolute -top-1.5 -right-1.5 flex items-center justify-center w-4 h-4 rounded-full bg-red-500 text-white text-[10px] font-bold">
                  {unreadChatCount > 9 ? "9+" : unreadChatCount}
                </span>
              )}
            </button>
            {recordingState !== "active" && !roomConfidential && (
              <button
                onClick={handleStartRecording}
                disabled={recordingState === "busy"}
                className="text-xs font-medium rounded-lg px-3 py-2 bg-slate-800 text-white disabled:opacity-60"
              >
                {recordingState === "busy" ? "Starting..." : "Record"}
              </button>
            )}
            <button
              onClick={handleEndCall}
              className="text-xs font-medium rounded-lg px-4 py-2 bg-red-700 hover:bg-red-600 text-white"
            >
              Leave &amp; End Call
            </button>
          </div>
        </div>
      )}

      <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <h3 className="font-semibold text-slate-900">Call History</h3>

        {loading && <p className="text-sm text-slate-500">Loading...</p>}
        {loadError && <p className="text-sm text-red-600">{loadError}</p>}
        {!loading && rooms.length === 0 && (
          <p className="text-sm text-slate-500">No video calls yet.</p>
        )}

        <div className="space-y-2">
          {rooms.map((r) => {
            const summary = summaries[r.room_name];
            const summaryError = summaryErrors[r.room_name];
            return (
              <div key={r.room_name} className="rounded-lg border border-slate-200 px-4 py-3 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="flex items-center gap-2">
                    <span className="font-mono text-sm text-slate-800">{r.room_name}</span>
                    {r.confidential && (
                      <span className="text-[11px] font-medium text-indigo-700 bg-indigo-50 border border-indigo-200 rounded-full px-2 py-0.5">
                        Confidential
                      </span>
                    )}
                    {r.worst_connection_quality === "poor" && (
                      <span
                        className="text-[11px] font-medium text-red-700 bg-red-50 border border-red-200 rounded-full px-2 py-0.5"
                        title={r.reconnect_count > 0 ? `${r.reconnect_count} reconnect(s) during this call` : undefined}
                      >
                        Poor connection
                      </span>
                    )}
                  </span>
                  <div className="flex items-center gap-3">
                    {r.participant_minutes > 0 && (
                      <span className="text-xs text-slate-400">{r.participant_minutes} participant-min</span>
                    )}
                    {r.recording_url && (
                      <a
                        href={r.recording_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-xs font-medium text-indigo-600 hover:text-indigo-800"
                      >
                        Play recording
                      </a>
                    )}
                    {r.recording_url && !summary && (
                      <button
                        onClick={() => handleSummarize(r.room_name)}
                        disabled={summarizingRoom === r.room_name}
                        className="text-xs font-medium text-indigo-600 hover:text-indigo-800 disabled:opacity-60"
                      >
                        {summarizingRoom === r.room_name ? "Summarizing…" : "Summarize with AI"}
                      </button>
                    )}
                    {r.recording_in_progress && (
                      <span className="text-xs font-medium text-red-600">Recording processing…</span>
                    )}
                    <span
                      className={`text-xs font-medium rounded-full px-2 py-0.5 capitalize ${
                        r.status === "active"
                          ? "bg-emerald-50 text-emerald-700"
                          : r.status === "ended"
                            ? "bg-slate-100 text-slate-600"
                            : "bg-amber-50 text-amber-700"
                      }`}
                    >
                      {r.status}
                    </span>
                    {r.status === "active" && (
                      <button
                        onClick={() => handleCopyRoomInviteLink(r.room_name)}
                        className="text-xs font-medium text-indigo-600 hover:text-indigo-800"
                      >
                        {copiedRoom === r.room_name ? "Copied!" : "Copy invite link"}
                      </button>
                    )}
                    {r.status === "active" && callState === "idle" && (
                      <button
                        onClick={() => openLobby(r.room_name)}
                        className="text-xs font-medium text-indigo-600 hover:text-indigo-800"
                      >
                        Join
                      </button>
                    )}
                  </div>
                </div>

                {summaryError && <p className="text-xs text-red-600">{summaryError}</p>}

                {summary && (
                  <div className="bg-slate-50 rounded-lg border border-slate-200 p-3 space-y-1.5">
                    <div className="flex items-center gap-2">
                      {summary.urgency && (
                        <span
                          className={`text-[10px] font-semibold uppercase tracking-wide rounded-full px-2 py-0.5 ${
                            summary.urgency === "high"
                              ? "bg-red-100 text-red-700"
                              : summary.urgency === "medium"
                                ? "bg-amber-100 text-amber-700"
                                : "bg-slate-200 text-slate-600"
                          }`}
                        >
                          {summary.urgency} urgency
                        </span>
                      )}
                      {summary.language && (
                        <span className="text-[10px] text-slate-400 uppercase">{summary.language}</span>
                      )}
                    </div>
                    <p className="text-sm text-slate-700">{summary.summary}</p>
                    {summary.action_items.length > 0 && (
                      <ul className="text-xs text-slate-500 list-disc list-inside">
                        {summary.action_items.map((item, i) => (
                          <li key={i}>{item}</li>
                        ))}
                      </ul>
                    )}
                    {summary.suggested_follow_up && (
                      <p className="text-xs text-slate-500">Follow-up: {summary.suggested_follow_up}</p>
                    )}
                    <p className="text-[10px] text-slate-400">{summary.disclaimer}</p>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
