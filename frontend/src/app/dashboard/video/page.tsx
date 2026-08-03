"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { Room, RoomEvent, Track } from "livekit-client";
import {
  getCurrentUser,
  listVideoRooms,
  createVideoRoom,
  joinVideoRoom,
  endVideoRoom,
  startVideoRecording,
  grantAiProcessingConsent,
  ApiError,
  type VideoRoom,
} from "@/lib/api";
import { getToken } from "@/lib/auth";

type CallState = "idle" | "connecting" | "in-call";
type RecordingState = "idle" | "busy" | "consent_required" | "active";

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
  const [recordingState, setRecordingState] = useState<RecordingState>("idle");
  const [recordingError, setRecordingError] = useState<string | null>(null);

  const roomRef = useRef<Room | null>(null);
  const localVideoRef = useRef<HTMLVideoElement>(null);
  const localScreenVideoRef = useRef<HTMLVideoElement>(null);
  const remoteContainerRef = useRef<HTMLDivElement>(null);
  const attachedElements = useRef<Map<string, HTMLMediaElement>>(new Map());

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
    };
  }, []);

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

  async function connectToRoom(existingRoomName: string | null) {
    if (!token) return;
    setCallError(null);
    setCallState("connecting");
    setRecordingState("idle");
    setRecordingError(null);
    try {
      const me = await getCurrentUser(token);
      const targetRoomName = existingRoomName ?? (await createVideoRoom(token)).room_name;
      const { token: liveKitToken, url } = await joinVideoRoom(token, targetRoomName, me.email);

      const room = new Room();
      roomRef.current = room;

      room.on(RoomEvent.TrackSubscribed, (track) => {
        if (track.kind === Track.Kind.Video || track.kind === Track.Kind.Audio) {
          const el = track.attach();
          attachedElements.current.set(track.sid ?? el.id, el);
          remoteContainerRef.current?.appendChild(el);
        }
      });
      room.on(RoomEvent.TrackUnsubscribed, (track) => {
        track.detach().forEach((el) => el.remove());
      });
      room.on(RoomEvent.ParticipantConnected, () => setParticipantCount(room.remoteParticipants.size));
      room.on(RoomEvent.ParticipantDisconnected, () => setParticipantCount(room.remoteParticipants.size));
      room.on(RoomEvent.Disconnected, () => {
        setCallState("idle");
        setRoomName(null);
        roomRef.current = null;
      });

      await room.connect(url, liveKitToken);
      await room.localParticipant.enableCameraAndMicrophone();

      setRoomName(targetRoomName);
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

  async function handleEndCall() {
    if (!token || !roomName) return;
    const endingRoomName = roomName;
    roomRef.current?.disconnect();
    roomRef.current = null;
    setCallState("idle");
    setRoomName(null);
    setScreenSharing(false);
    setRecordingState("idle");
    try {
      await endVideoRoom(token, endingRoomName);
    } catch {
      // room is already disconnected locally either way - not worth surfacing
    }
    await loadRooms();
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
        <p className="text-sm text-slate-500">1:1 and small-group video calling.</p>
      </div>

      {callState === "idle" && (
        <div className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
          <button
            onClick={() => connectToRoom(null)}
            className="bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg px-4 py-2"
          >
            Start a Video Call
          </button>

          <div className="flex items-center gap-3 pt-1">
            <div className="h-px bg-slate-200 flex-1" />
            <span className="text-xs text-slate-400">or join a teammate&apos;s call</span>
            <div className="h-px bg-slate-200 flex-1" />
          </div>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (joinRoomInput.trim()) connectToRoom(joinRoomInput.trim());
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

      {callState === "connecting" && (
        <div className="bg-white rounded-xl border border-slate-200 p-6">
          <p className="text-sm text-slate-500">Connecting...</p>
        </div>
      )}

      {callState === "in-call" && (
        <div className="bg-slate-900 rounded-xl border border-slate-800 p-4 space-y-3">
          <div className="flex items-center justify-between text-xs text-slate-400 px-1">
            <span className="font-mono">{roomName}</span>
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

          {screenSharing && (
            <div className="relative aspect-video bg-black rounded-lg overflow-hidden">
              <video ref={localScreenVideoRef} autoPlay muted playsInline className="w-full h-full object-contain" />
              <span className="absolute bottom-2 left-2 text-xs text-white/80 bg-black/40 rounded px-2 py-0.5">
                Your screen
              </span>
            </div>
          )}

          <div className="grid grid-cols-2 gap-3">
            <div className="relative aspect-video bg-black rounded-lg overflow-hidden">
              <video ref={localVideoRef} autoPlay muted playsInline className="w-full h-full object-cover" />
              <span className="absolute bottom-2 left-2 text-xs text-white/80 bg-black/40 rounded px-2 py-0.5">
                You
              </span>
            </div>
            <div
              ref={remoteContainerRef}
              className="grid grid-cols-1 gap-2 [&>video]:w-full [&>video]:h-full [&>video]:object-cover [&>audio]:hidden"
            />
          </div>

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
            {recordingState !== "active" && (
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
          {rooms.map((r) => (
            <div
              key={r.room_name}
              className="flex items-center justify-between rounded-lg border border-slate-200 px-4 py-3"
            >
              <span className="font-mono text-sm text-slate-800">{r.room_name}</span>
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
                {r.status === "active" && callState === "idle" && (
                  <button
                    onClick={() => connectToRoom(r.room_name)}
                    className="text-xs font-medium text-indigo-600 hover:text-indigo-800"
                  >
                    Join
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
