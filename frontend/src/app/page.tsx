"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import {
  LiveKitRoom,
  useVoiceAssistant,
  RoomAudioRenderer,
  useLocalParticipant,
  useRoomContext,
} from "@livekit/components-react";
import "@livekit/components-styles";

const BOOKING_STORAGE_KEY = "zia_booking_details";

function SpeakingAnimation({ state, timeElapsed }: { state: string; timeElapsed?: number }) {
  const isSpeaking = state === "speaking";
  const isListening = state === "listening";
  const isConnecting = state === "connecting";
  // Same animation for speaking and loading (connecting)
  const isActive = isSpeaking || isListening || isConnecting;
  const animationColor = isSpeaking || isConnecting ? "#3b82f6" : "#22c55e";

  return (
    <div className="flex flex-col items-center gap-6">
      <div className="relative flex items-center justify-center w-40 h-40">
        {isActive && (
          <>
            <div
              className="absolute w-40 h-40 rounded-full border-2 speaking-ring"
              style={{
                borderColor: animationColor,
                animationDelay: "0s",
              }}
            />
            <div
              className="absolute w-32 h-32 rounded-full border-2 speaking-ring"
              style={{
                borderColor: animationColor,
                animationDelay: "0.3s",
              }}
            />
            <div
              className="absolute w-24 h-24 rounded-full border-2 speaking-ring"
              style={{
                borderColor: animationColor,
                animationDelay: "0.6s",
              }}
            />
          </>
        )}

        <div
          className="relative z-10 w-20 h-20 rounded-full flex items-center justify-center transition-colors duration-300"
          style={{
            backgroundColor: isSpeaking || isConnecting
              ? "#3b82f6"
              : isListening
                ? "#22c55e"
                : "#374151",
          }}
        >
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

      {isActive && (
        <div className="flex items-center gap-1 h-10">
          {[...Array(5)].map((_, i) => (
            <div
              key={i}
              className="w-1.5 rounded-full speaking-bar"
              style={{
                backgroundColor: animationColor,
                height: "8px",
                animationDelay: `${i * 0.1}s`,
              }}
            />
          ))}
        </div>
      )}

      {!isConnecting && (
        <p className="text-sm text-gray-400 capitalize">
          {isSpeaking
            ? "Agent is speaking..."
            : isListening
              ? "Listening..."
              : state === "thinking"
                ? "Thinking..."
                : "Connected"}
        </p>
      )}
    </div>
  );
}

function VoiceAssistantUI({ onDisconnect }: { onDisconnect: () => void }) {
  const { state } = useVoiceAssistant();
  const { localParticipant } = useLocalParticipant();
  const room = useRoomContext();
  const [isMuted, setIsMuted] = useState(false);
  const [initialGreetingSent, setInitialGreetingSent] = useState(false);
  const [connectTime] = useState(Date.now());
  const [timeElapsed, setTimeElapsed] = useState(0);
  const micEnabledRef = useRef(false);

  useEffect(() => {
    if (room?.state === "connected") {
      console.log("[Timing] Room connected after:", Date.now() - connectTime, "ms");
    }
  }, [room?.state, connectTime]);

  useEffect(() => {
    if (state === "speaking" && !initialGreetingSent) {
      setInitialGreetingSent(true);
      console.log("[Timing] AI started speaking after:", Date.now() - connectTime, "ms");
    }
  }, [state, initialGreetingSent, connectTime]);

  useEffect(() => {
    let interval: NodeJS.Timeout;
    if (!initialGreetingSent) {
      interval = setInterval(() => {
        setTimeElapsed(prev => prev + 1);
      }, 1000);
    }
    return () => clearInterval(interval);
  }, [initialGreetingSent]);

  useEffect(() => {
    if (!room) return;
    
    const clearBookingStorage = () => {
      try {
        localStorage.removeItem(BOOKING_STORAGE_KEY);
        console.log("[Zia] Call ended: booking data cleared from localStorage.");
      } catch {
        // ignore
      }
    };
    
    room.on("disconnected", clearBookingStorage);
    return () => {
      room.off("disconnected", clearBookingStorage);
    };
  }, [room]);

  useEffect(() => {
    if (!room) return;
    
    const handleDataReceived = (
      payload: Uint8Array,
      _participant?: unknown,
      _kind?: unknown,
      topic?: string
    ) => {
      try {
        const text = new TextDecoder().decode(payload);
        const data = JSON.parse(text) as Record<string, unknown>;

        if (topic === "booking_details") {
          if (data.type === "booking_details" && data.email != null) {
            const booking = {
              email: data.email ?? "",
              phone: data.phone ?? "",
              country: data.country ?? "",
            };
            localStorage.setItem(BOOKING_STORAGE_KEY, JSON.stringify(booking));
            console.log("[Zia] Booking details received and saved:", booking);
          }
          return;
        }

        if (topic === "meeting_booked") {
          const booked = data.booked === true;
          const startTime = data.start_time as string | undefined;
          const email = data.email as string | undefined;
          const error = data.error as string | undefined;
          console.log("[Zia] Meeting booking result:", {
            booked,
            start_time: startTime,
            email,
            error: error ?? null,
          });
        }
      } catch (e) {
        console.error("[Zia] Failed to parse data:", topic, e);
      }
    };
    
    room.on("dataReceived", handleDataReceived);
    return () => {
      room.off("dataReceived", handleDataReceived);
    };
  }, [room]);

  // Enable mic immediately when room connects
  useEffect(() => {
    if (!room || !localParticipant || micEnabledRef.current) return;

    const enableMic = async () => {
      try {
        await localParticipant.setMicrophoneEnabled(true);
        micEnabledRef.current = true;
        console.log("[Zia] Microphone enabled");
      } catch (err) {
        console.error("Failed to enable microphone:", err);
      }
    };

    if (room.state === "connected") {
      enableMic();
    } else {
      room.once("connected", enableMic);
    }
  }, [room, localParticipant]);

  const toggleMute = useCallback(async () => {
    if (localParticipant) {
      const nextMuted = !isMuted;
      await localParticipant.setMicrophoneEnabled(!nextMuted);
      setIsMuted(nextMuted);
      console.log("[Zia] Microphone", nextMuted ? "muted" : "unmuted");
    }
  }, [localParticipant, isMuted]);

  const displayState = !initialGreetingSent ? "connecting" : state;

  return (
    <div className="flex flex-col items-center justify-center min-h-screen gap-8">
      <h1 className="text-2xl font-semibold">Talk to Zia</h1>

      <SpeakingAnimation state={displayState} timeElapsed={timeElapsed} />

      <div className="flex gap-4">
        <button
          onClick={toggleMute}
          className={`px-6 py-3 rounded-full font-medium transition-colors ${
            isMuted
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
  const [connectStartAt, setConnectStartAt] = useState<number | null>(null);
  const [prefetchedToken, setPrefetchedToken] = useState<{
    token: string;
    url: string;
    roomName: string;
  } | null>(null);
  const [audioInitialized, setAudioInitialized] = useState(false);

  // Pre-fetch token immediately
  useEffect(() => {
    const prefetchToken = async () => {
      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 3000);
        
        const response = await fetch(`/api/token?t=${Date.now()}`, { 
          cache: "no-store",
          signal: controller.signal,
        });
        
        clearTimeout(timeoutId);
        
        if (response.ok) {
          const data = await response.json();
          if (data?.token && data?.url) {
            setPrefetchedToken({ 
              token: data.token, 
              url: data.url, 
              roomName: data.roomName ?? "" 
            });
            console.info("[voice] Token pre-fetched successfully");
          }
        }
      } catch (error: any) {
        if (error.name !== 'AbortError') {
          console.error("[voice] Token pre-fetch failed:", error);
        }
      }
    };
    
    prefetchToken();
  }, []);

  // Initialize audio context on user interaction
  const initAudio = useCallback(() => {
    if (audioInitialized) return;
    
    try {
      const AudioContextClass = window.AudioContext || (window as any).webkitAudioContext;
      if (AudioContextClass) {
        const audioContext = new AudioContextClass();
        if (audioContext.state === 'suspended') {
          audioContext.resume().then(() => {
            console.log("[audio] Audio context resumed");
            setAudioInitialized(true);
          });
        } else {
          setAudioInitialized(true);
        }
      }
    } catch (e) {
      console.warn("[audio] Could not initialize audio context:", e);
    }
  }, [audioInitialized]);

  const startCall = useCallback(async () => {
    initAudio(); // Initialize audio before starting call
    
    setTokenError(null);
    setIsConnecting(true);
    const started = performance.now();
    setConnectStartAt(started);
    console.info("[voice] Start Call clicked");
    
    try {
      // Use prefetched token if available
      if (prefetchedToken) {
        console.info("[voice] Using prefetched token");
        setConnectionDetails(prefetchedToken);
        setPrefetchedToken(null);
        setIsConnecting(false);
        return;
      }
      
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 5000);
      
      const response = await fetch(`/api/token?t=${Date.now()}`, { 
        cache: "no-store",
        signal: controller.signal,
      });
      
      clearTimeout(timeoutId);
      
      const data = await response.json();
      const tokenMs = Math.round(performance.now() - started);
      console.info("[voice] Token received", { totalMs: tokenMs, ok: response.ok });
      
      if (!response.ok) {
        setTokenError(data?.detail ?? data?.error ?? "Failed to get token");
        setIsConnecting(false);
        return;
      }
      
      if (!data?.token || !data?.url) {
        setTokenError("Invalid token response");
        setIsConnecting(false);
        return;
      }
      
      setConnectionDetails({ token: data.token, url: data.url, roomName: data.roomName ?? "" });
    } catch (error: unknown) {
      const isAbort = error instanceof Error && error.name === 'AbortError';
      if (isAbort) {
        setTokenError("Request timed out. Please try again.");
      } else {
        console.error("Failed to get token:", error);
        setTokenError("Could not reach server. Check network and try again.");
      }
    } finally {
      setIsConnecting(false);
    }
  }, [prefetchedToken, initAudio]);

  const endCall = useCallback(() => {
    try {
      localStorage.removeItem(BOOKING_STORAGE_KEY);
    } catch {
      // ignore
    }
    setConnectionDetails(null);
    
    // Pre-fetch next token
    const prefetchNext = async () => {
      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 3000);
        
        const response = await fetch(`/api/token?t=${Date.now()}`, { 
          cache: "no-store",
          signal: controller.signal
        });
        
        clearTimeout(timeoutId);
        
        if (response.ok) {
          const data = await response.json();
          if (data?.token && data?.url) {
            setPrefetchedToken({ 
              token: data.token, 
              url: data.url, 
              roomName: data.roomName ?? "" 
            });
            console.info("[voice] Next token pre-fetched");
          }
        }
      } catch (error: any) {
        if (error.name !== 'AbortError') {
          console.error("[voice] Next token pre-fetch failed:", error);
        }
      }
    };
    prefetchNext();
  }, []);

  if (connectionDetails) {
    return (
      <LiveKitRoom
        key={connectionDetails.token}
        token={connectionDetails.token}
        serverUrl={connectionDetails.url}
        connect={true}
        audio={{
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        }}
        connectOptions={{
          autoSubscribe: true,
          maxRetries: 2,
          peerConnectionTimeout: 10000,
        }}
        onConnected={() => {
          const total = connectStartAt ? Math.round(performance.now() - connectStartAt) : null;
          console.info("[voice] LiveKit connected", { totalMsSinceClick: total });
        }}
        onError={(error) => {
          console.error("LiveKit room error:", error);
        }}
      >
        <VoiceAssistantUI onDisconnect={endCall} />
      </LiveKitRoom>
    );
  }

  return (
    <div className="flex flex-col items-center justify-center min-h-screen gap-8" onClick={initAudio}>
      <img src="/dialzia.png" alt="Zia" className="w-24 h-24" />
      <h1 className="text-3xl font-bold">Talk to Zia</h1>
      <p className="text-gray-400 text-center max-w-md">
        Click the button below to start a conversation with Zia,<br/> our AI voice assistant.
      </p>

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