"use client";

import { useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { Room, RoomEvent, Track } from "livekit-client";
import { joinVideoRoomAsGuest, ApiError } from "@/lib/api";

type CallState = "idle" | "connecting" | "in-call" | "ended" | "not-found";

// Public, unauthenticated page — the shareable-link path for someone with
// no Zoiko account. Deliberately narrower than the host's dashboard video
// page: no room creation, no starting a recording (that's a host-only,
// consent-gated action), just join/leave and basic mic/camera controls.
export default function GuestJoinPage() {
  const params = useParams<{ roomName: string }>();
  const roomName = params.roomName;

  const [displayName, setDisplayName] = useState("");
  const [callState, setCallState] = useState<CallState>("idle");
  const [callError, setCallError] = useState<string | null>(null);
  const [recording, setRecording] = useState(false);
  const [cameraOn, setCameraOn] = useState(true);
  const [micOn, setMicOn] = useState(true);
  const [participantCount, setParticipantCount] = useState(0);

  const roomRef = useRef<Room | null>(null);
  const localVideoRef = useRef<HTMLVideoElement>(null);
  const remoteContainerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    return () => {
      roomRef.current?.disconnect();
    };
  }, []);

  useEffect(() => {
    if (callState !== "in-call") return;
    const room = roomRef.current;
    const videoEl = localVideoRef.current;
    if (!room || !videoEl) return;
    const cameraPublication = room.localParticipant.getTrackPublication(Track.Source.Camera);
    cameraPublication?.videoTrack?.attach(videoEl);
  }, [callState]);

  async function handleJoin() {
    const name = displayName.trim();
    if (!name) return;
    setCallError(null);
    setCallState("connecting");
    try {
      const { token, url, recording: isRecording } = await joinVideoRoomAsGuest(roomName, name);
      setRecording(isRecording);

      const room = new Room();
      roomRef.current = room;

      room.on(RoomEvent.TrackSubscribed, (track) => {
        if (track.kind === Track.Kind.Video || track.kind === Track.Kind.Audio) {
          const el = track.attach();
          remoteContainerRef.current?.appendChild(el);
        }
      });
      room.on(RoomEvent.TrackUnsubscribed, (track) => {
        track.detach().forEach((el) => el.remove());
      });
      room.on(RoomEvent.ParticipantConnected, () => setParticipantCount(room.remoteParticipants.size));
      room.on(RoomEvent.ParticipantDisconnected, () => setParticipantCount(room.remoteParticipants.size));
      room.on(RoomEvent.Disconnected, () => {
        setCallState("ended");
        roomRef.current = null;
      });

      await room.connect(url, token);
      await room.localParticipant.enableCameraAndMicrophone();

      setParticipantCount(room.remoteParticipants.size);
      setCallState("in-call");
    } catch (err) {
      roomRef.current?.disconnect();
      roomRef.current = null;
      if (err instanceof ApiError && err.status === 404) {
        setCallState("not-found");
        return;
      }
      setCallState("idle");
      const message = err instanceof ApiError || err instanceof Error ? err.message : "Unknown error.";
      setCallError(`Couldn't join the call: ${message}`);
    }
  }

  function handleLeave() {
    roomRef.current?.disconnect();
    roomRef.current = null;
    setCallState("ended");
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

  return (
    <div className="min-h-screen bg-slate-950 flex items-center justify-center p-6">
      <div className="w-full max-w-lg space-y-4">
        {(callState === "idle" || callState === "connecting") && (
          <div className="bg-white rounded-xl p-6 space-y-4">
            <div>
              <h1 className="text-lg font-semibold text-slate-900">Join video call</h1>
              <p className="text-sm text-slate-500 font-mono mt-0.5">{roomName}</p>
            </div>

            <form
              onSubmit={(e) => {
                e.preventDefault();
                handleJoin();
              }}
              className="space-y-3"
            >
              <input
                autoFocus
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="Your name"
                maxLength={60}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
              <p className="text-xs text-slate-500 bg-slate-50 rounded-lg px-3 py-2">
                This call may be recorded by the host. By joining, you acknowledge that.
              </p>
              <button
                type="submit"
                disabled={!displayName.trim() || callState === "connecting"}
                className="w-full bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white text-sm font-medium rounded-lg px-4 py-2"
              >
                {callState === "connecting" ? "Joining..." : "Join call"}
              </button>
            </form>

            {callError && <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{callError}</p>}
          </div>
        )}

        {callState === "not-found" && (
          <div className="bg-white rounded-xl p-6 text-center space-y-1">
            <p className="text-sm font-medium text-slate-900">This call has ended or doesn&apos;t exist.</p>
            <p className="text-xs text-slate-500">Ask the host for a new link.</p>
          </div>
        )}

        {callState === "ended" && (
          <div className="bg-white rounded-xl p-6 text-center">
            <p className="text-sm font-medium text-slate-900">You left the call.</p>
          </div>
        )}

        {callState === "in-call" && (
          <div className="bg-slate-900 rounded-xl border border-slate-800 p-4 space-y-3">
            <div className="flex items-center justify-between text-xs text-slate-400 px-1">
              <span className="font-mono">{roomName}</span>
              <div className="flex items-center gap-3">
                {recording && (
                  <span className="flex items-center gap-1.5 text-red-400">
                    <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />
                    Recording
                  </span>
                )}
                <span>{participantCount} other participant{participantCount === 1 ? "" : "s"}</span>
              </div>
            </div>

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
                onClick={handleLeave}
                className="text-xs font-medium rounded-lg px-4 py-2 bg-red-700 hover:bg-red-600 text-white"
              >
                Leave Call
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
