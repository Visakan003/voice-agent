"use client";

import { useState, useCallback } from "react";
import {
  LiveKitRoom,
  useVoiceAssistant,
  RoomAudioRenderer,
  useLocalParticipant,
} from "@livekit/components-react";
import "@livekit/components-styles";

function SpeakingAnimation({ state }: { state: string }) {
  const isSpeaking = state === "speaking";
  const isListening = state === "listening";
  const isActive = isSpeaking || isListening;

  return (
    <div className="flex flex-col items-center gap-6">
      {/* Animated circle */}
      <div className="relative flex items-center justify-center w-40 h-40">
        {/* Pulse rings */}
        {isActive && (
          <>
            <div
              className="absolute w-40 h-40 rounded-full border-2 speaking-ring"
              style={{
                borderColor: isSpeaking ? "#3b82f6" : "#22c55e",
                animationDelay: "0s",
              }}
            />
            <div
              className="absolute w-32 h-32 rounded-full border-2 speaking-ring"
              style={{
                borderColor: isSpeaking ? "#3b82f6" : "#22c55e",
                animationDelay: "0.3s",
              }}
            />
            <div
              className="absolute w-24 h-24 rounded-full border-2 speaking-ring"
              style={{
                borderColor: isSpeaking ? "#3b82f6" : "#22c55e",
                animationDelay: "0.6s",
              }}
            />
          </>
        )}

        {/* Center circle */}
        <div
          className="relative z-10 w-20 h-20 rounded-full flex items-center justify-center transition-colors duration-300"
          style={{
            backgroundColor: isSpeaking
              ? "#3b82f6"
              : isListening
                ? "#22c55e"
                : "#374151",
          }}
        >
          {/* Mic icon */}
          <svg
            width="32"
            height="32"
            viewBox="0 0 24 24"
            fill="none"
            stroke="white"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
            <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
            <line x1="12" y1="19" x2="12" y2="23" />
            <line x1="8" y1="23" x2="16" y2="23" />
          </svg>
        </div>
      </div>

      {/* Sound bars */}
      {isActive && (
        <div className="flex items-center gap-1 h-10">
          {[...Array(5)].map((_, i) => (
            <div
              key={i}
              className="w-1.5 rounded-full speaking-bar"
              style={{
                backgroundColor: isSpeaking ? "#3b82f6" : "#22c55e",
                height: "8px",
              }}
            />
          ))}
        </div>
      )}

      {/* Status text */}
      <p className="text-sm text-gray-400 capitalize">
        {state === "speaking"
          ? "Agent is speaking..."
          : state === "listening"
            ? "Listening..."
            : state === "thinking"
              ? "Thinking..."
              : "Connected"}
      </p>
    </div>
  );
}

function VoiceAssistantUI({ onDisconnect }: { onDisconnect: () => void }) {
  const { state } = useVoiceAssistant();
  const { localParticipant } = useLocalParticipant();
  const [isMuted, setIsMuted] = useState(false);

  const toggleMute = useCallback(async () => {
    if (localParticipant) {
      await localParticipant.setMicrophoneEnabled(isMuted);
      setIsMuted(!isMuted);
    }
  }, [localParticipant, isMuted]);

  return (
    <div className="flex flex-col items-center justify-center min-h-screen gap-8">
      <h1 className="text-2xl font-semibold">Talk to Zia</h1>

      <SpeakingAnimation state={state} />

      <div className="flex gap-4">
        {/* Mute / Unmute */}
        <button
          onClick={toggleMute}
          className={`px-6 py-3 rounded-full font-medium transition-colors ${isMuted
              ? "bg-yellow-600 hover:bg-yellow-700"
              : "bg-gray-700 hover:bg-gray-600"
            }`}
        >
          {isMuted ? (
            <span className="flex items-center gap-2">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="1" y1="1" x2="23" y2="23" />
                <path d="M9 9v3a3 3 0 0 0 5.12 2.12M15 9.34V4a3 3 0 0 0-5.94-.6" />
                <path d="M17 16.95A7 7 0 0 1 5 12v-2m14 0v2c0 .76-.13 1.49-.36 2.18" />
                <line x1="12" y1="19" x2="12" y2="23" />
                <line x1="8" y1="23" x2="16" y2="23" />
              </svg>
              Unmute
            </span>
          ) : (
            <span className="flex items-center gap-2">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
                <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                <line x1="12" y1="19" x2="12" y2="23" />
                <line x1="8" y1="23" x2="16" y2="23" />
              </svg>
              Mute
            </span>
          )}
        </button>

        {/* End Call */}
        <button
          onClick={onDisconnect}
          className="px-6 py-3 rounded-full font-medium bg-red-600 hover:bg-red-700 transition-colors flex items-center gap-2"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M10.68 13.31a16 16 0 0 0 3.41 2.6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7 2 2 0 0 1 1.72 2v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91" />
            <line x1="23" y1="1" x2="1" y2="23" />
          </svg>
          End Call
        </button>
      </div>

      <RoomAudioRenderer />
    </div>
  );
}

export default function Home() {
  const [connectionDetails, setConnectionDetails] = useState<{
    token: string;
    url: string;
    roomName: string;
  } | null>(null);
  const [isConnecting, setIsConnecting] = useState(false);
  const [tokenError, setTokenError] = useState<string | null>(null);

  const startCall = useCallback(async () => {
    setTokenError(null);
    setIsConnecting(true);
    try {
      const response = await fetch(`/api/token?t=${Date.now()}`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) {
        setTokenError(data.detail ?? data.error ?? "Failed to get token");
        return;
      }
      if (!data.token || !data.url) {
        setTokenError("Invalid token response");
        return;
      }
      setConnectionDetails({ token: data.token, url: data.url, roomName: data.roomName });
    } catch (error) {
      console.error("Failed to get token:", error);
      setTokenError("Could not reach server. Check network and try again.");
    } finally {
      setIsConnecting(false);
    }
  }, []);

  const endCall = useCallback(() => {
    setConnectionDetails(null);
  }, []);

  if (connectionDetails) {
    return (
      <LiveKitRoom
        key={connectionDetails.token}
        token={connectionDetails.token}
        serverUrl={connectionDetails.url}
        connect={true}
        audio={true}
        onDisconnected={endCall}
      >
        <VoiceAssistantUI onDisconnect={endCall} />
      </LiveKitRoom>
    );
  }

  return (
    <div className="flex flex-col items-center justify-center min-h-screen gap-8">
      <img src="/dialzia.png" alt="Zia" className="w-24 h-24" />
      <h1 className="text-3xl font-bold">Talk to Zia</h1>
      <p className="text-gray-400 text-center max-w-md">
      Click the button below to start a conversation with Zia,<br/> our AI voice assistant.
      </p>

      {/* Mic icon */}
      <div className="w-24 h-24 rounded-full bg-gray-800 flex items-center justify-center">
        <svg
          width="40"
          height="40"
          viewBox="0 0 24 24"
          fill="none"
          stroke="#9ca3af"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
          <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
          <line x1="12" y1="19" x2="12" y2="23" />
          <line x1="8" y1="23" x2="16" y2="23" />
        </svg>
      </div>

      {tokenError && (
        <p className="text-red-400 text-center max-w-md text-sm" role="alert">
          {tokenError}
        </p>
      )}

      <button
        onClick={startCall}
        disabled={isConnecting}
        className="px-10 py-4 rounded-full font-semibold text-lg text-white bg-gradient-to-r from-purple-500 via-fuchsia-500 to-indigo-500 shadow-[0_0_25px_rgba(168,85,247,0.6)] hover:shadow-[0_0_45px_rgba(168,85,247,0.9)] hover:from-purple-400 hover:via-fuchsia-400 hover:to-indigo-400 transition-all duration-300 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-3"
      >
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7 2 2 0 0 1 1.72 2z" />
        </svg>
        {isConnecting ? "Connecting..." : "Start Call"}
      </button>
    </div>
  );
}
