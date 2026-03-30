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

type BookingFormValues = {
  email: string;
  countryCode: string;
  phone: string;
};

function BookingFormPopup({
  values,
  submitting,
  error,
  onChange,
  onSubmit,
}: {
  values: BookingFormValues;
  submitting: boolean;
  error: string | null;
  onChange: (field: keyof BookingFormValues, value: string) => void;
  onSubmit: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
      <div className="w-full max-w-md rounded-2xl border border-white/20 bg-white/10 backdrop-blur-xl shadow-2xl p-5">
        <h3 className="text-lg font-semibold text-white">Quick Booking Form</h3>
        <p className="text-sm text-gray-200 mt-1 mb-4">
          Fill this once and I will fetch available slots for you.
        </p>

        <label className="text-xs text-cyan-200">Email</label>
        <input
          value={values.email}
          onChange={(e) => onChange("email", e.target.value)}
          type="email"
          className="mt-1 mb-3 w-full rounded-lg border border-white/20 bg-black/25 text-white px-3 py-2 outline-none focus:ring-2 focus:ring-cyan-400/60"
          placeholder="you@example.com"
        />

        <label className="text-xs text-cyan-200">Country Code</label>
        <select
          value={values.countryCode}
          onChange={(e) => onChange("countryCode", e.target.value)}
          className="mt-1 mb-3 w-full rounded-lg border border-white/20 bg-black/25 text-black px-3 py-2 outline-none focus:ring-2 focus:ring-cyan-400/60"
        >
          <option value="+1" className="text-black">+1 (US/Canada)</option>
          <option value="+44" className="text-black">+44 (UK)</option>
          <option value="+91" className="text-black">+91 (India)</option>
          <option value="+61" className="text-black">+61 (Australia)</option>
          <option value="+971" className="text-black">+971 (UAE)</option>
        </select>

        <label className="text-xs text-cyan-200">Phone</label>
        <input
          value={values.phone}
          onChange={(e) => onChange("phone", e.target.value)}
          type="tel"
          className="mt-1 w-full rounded-lg border border-white/20 bg-black/25 text-white px-3 py-2 outline-none focus:ring-2 focus:ring-cyan-400/60"
          placeholder="9876543210"
        />

        {error && <p className="text-red-300 text-xs mt-3">{error}</p>}

        <button
          onClick={onSubmit}
          disabled={submitting}
          className="mt-4 w-full rounded-lg bg-gradient-to-r from-cyan-500 to-fuchsia-500 px-4 py-2.5 font-medium text-white disabled:opacity-60"
        >
          {submitting ? "Submitting..." : "Submit Details"}
        </button>
      </div>
    </div>
  );
}

function formatDuration(totalSeconds: number): string {
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

// ---------------------------------------------------------------------------
// Speaking animation
// ---------------------------------------------------------------------------

function SpeakingAnimation({ state }: { state: string }) {
  const isSpeaking = state === "speaking";
  const isListening = state === "listening";
  const isThinking = state === "thinking";
  const isConnecting = state === "connecting";

  // Pulse rings while connecting or speaking
  const showRings = isSpeaking || isConnecting || isThinking;
  const ringColor = isSpeaking ? "#3b82f6" : isListening ? "#22c55e" : "#6b7280";
  const coreColor = isSpeaking
    ? "#3b82f6"
    : isListening
      ? "#22c55e"
      : isThinking
        ? "#8b5cf6"
        : "#374151";

  const label = isSpeaking
    ? "Agent is speaking..."
    : isListening
      ? "Listening..."
      : isThinking
        ? "Thinking..."
        : isConnecting
          ? "Connecting..."
          : "Connected";

  return (
    <div className="flex flex-col items-center gap-6">
      <div className="relative flex items-center justify-center w-40 h-40">
        {showRings && (
          <>
            <div
              className="absolute w-40 h-40 rounded-full border-2 speaking-ring"
              style={{ borderColor: "#22d3ee", animationDelay: "0s" }}
            />
            <div
              className="absolute w-32 h-32 rounded-full border-2 speaking-ring"
              style={{ borderColor: "#8b5cf6", animationDelay: "0.3s" }}
            />
            <div
              className="absolute w-24 h-24 rounded-full border-2 speaking-ring"
              style={{ borderColor: "#ec4899", animationDelay: "0.6s" }}
            />
          </>
        )}

        <div
          className="relative z-10 w-20 h-20 rounded-full flex items-center justify-center transition-colors duration-300"
          style={{ backgroundColor: coreColor }}
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

      {(isSpeaking || isListening) && (
        <div className="flex items-center gap-1 h-10">
          {[...Array(5)].map((_, i) => (
            <div
              key={i}
              className="w-1.5 rounded-full speaking-bar"
              style={{
                backgroundColor: ringColor,
                height: "8px",
                animationDelay: `${i * 0.1}s`,
              }}
            />
          ))}
        </div>
      )}

      <p className="text-sm text-gray-400">{label}</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Inner component — has access to LiveKit hooks
// ---------------------------------------------------------------------------

function VoiceAssistantUI({ onDisconnect }: { onDisconnect: () => void }) {
  const { state } = useVoiceAssistant();
  const { localParticipant } = useLocalParticipant();
  const room = useRoomContext();

  const [isMuted, setIsMuted] = useState(false);
  const [roomConnected, setRoomConnected] = useState(false);
  const [agentSpokeOnce, setAgentSpokeOnce] = useState(false);
  const [callDurationSec, setCallDurationSec] = useState(0);
  const [latestAiText, setLatestAiText] = useState("Waiting for response...");
  const [showBookingForm, setShowBookingForm] = useState(false);
  const [formSubmitting, setFormSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [formValues, setFormValues] = useState<BookingFormValues>({
    email: "",
    countryCode: "+1",
    phone: "",
  });
  const micEnabledRef = useRef(false);

  // -------------------------------------------------------------------------
  // Track room connection state (used for display state, not agent speech)
  // -------------------------------------------------------------------------
  useEffect(() => {
    if (!room) return;

    if (room.state === "connected") setRoomConnected(true);

    const handleConnected = () => setRoomConnected(true);
    const handleDisconnected = () => setRoomConnected(false);

    room.on("connected", handleConnected);
    room.on("disconnected", handleDisconnected);

    return () => {
      room.off("connected", handleConnected);
      room.off("disconnected", handleDisconnected);
    };
  }, [room]);

  useEffect(() => {
    if (!roomConnected) {
      setCallDurationSec(0);
      return;
    }
    const timer = setInterval(() => {
      setCallDurationSec((prev) => prev + 1);
    }, 1000);
    return () => clearInterval(timer);
  }, [roomConnected]);

  // -------------------------------------------------------------------------
  // Resume AudioContext the moment the room connects (fixes Safari / mobile)
  // -------------------------------------------------------------------------
  useEffect(() => {
    if (!roomConnected) return;
    try {
      const AudioCtx =
        window.AudioContext || (window as any).webkitAudioContext;
      if (AudioCtx) {
        const ctx = new AudioCtx();
        if (ctx.state === "suspended") {
          ctx.resume().catch(() => { });
        }
      }
    } catch {
      // non-fatal
    }
  }, [roomConnected]);

  // -------------------------------------------------------------------------
  // Mark that the agent has spoken at least once
  // -------------------------------------------------------------------------
  useEffect(() => {
    if (state === "speaking" && !agentSpokeOnce) {
      setAgentSpokeOnce(true);
    }
  }, [state, agentSpokeOnce]);

  // Try to show latest spoken/transcribed text if transcription events are available.
  useEffect(() => {
    if (!room) return;

    const handleTranscription = (segments: any[] = []) => {
      if (!Array.isArray(segments)) return;
      const text = segments
        .map((seg) => String(seg?.text ?? "").trim())
        .filter(Boolean)
        .join(" ")
        .trim();
      if (!text) return;

      // participant identity is often present on each segment
      const firstSeg = segments[0] ?? {};
      const fromIdentity = String(firstSeg?.participantIdentity ?? "").toLowerCase();
      if (!fromIdentity.includes("user")) {
        setLatestAiText(text);
      }
    };

    room.on("transcriptionReceived", handleTranscription);
    return () => {
      room.off("transcriptionReceived", handleTranscription);
    };
  }, [room]);

  // -------------------------------------------------------------------------
  // Clear booking data when room disconnects
  // -------------------------------------------------------------------------
  useEffect(() => {
    if (!room) return;
    const clear = () => {
      try {
        localStorage.removeItem(BOOKING_STORAGE_KEY);
      } catch {
        // ignore
      }
    };
    room.on("disconnected", clear);
    return () => {
      room.off("disconnected", clear);
    };
  }, [room]);

  // -------------------------------------------------------------------------
  // Handle data messages from the agent
  // -------------------------------------------------------------------------
  useEffect(() => {
    if (!room) return;

    const handleData = (
      payload: Uint8Array,
      _participant?: unknown,
      _kind?: unknown,
      topic?: string
    ) => {
      try {
        const data = JSON.parse(new TextDecoder().decode(payload)) as Record<
          string,
          unknown
        >;

        if (topic === "booking_details" && data.type === "booking_details") {
          const booking = {
            email: data.email ?? "",
            phone: data.phone ?? "",
            country: data.country ?? "",
          };
          localStorage.setItem(BOOKING_STORAGE_KEY, JSON.stringify(booking));
          console.log("[Zia] Booking details saved:", booking);
          return;
        }

        if (topic === "show_booking_form") {
          setShowBookingForm(true);
          setFormError(null);
          return;
        }

        if (topic === "close_booking_form") {
          setShowBookingForm(false);
          setFormSubmitting(false);
          setFormError(null);
          return;
        }

        if (topic === "prefill_confirmed") {
          setShowBookingForm(false);
          setFormSubmitting(false);
          setFormError(null);
          setLatestAiText("Thanks, I got your details. Let me check available slots.");
          return;
        }

        if (topic === "meeting_booked") {
          console.log("[Zia] Meeting booking result:", {
            booked: data.booked,
            start_time: data.start_time,
            email: data.email,
            scheduled_event_uri: data.scheduled_event_uri ?? null,
            invitee_uri: data.invitee_uri ?? null,
            error: data.error ?? null,
            raw: data,
          });
          if (data.booked === true) {
            setLatestAiText("Great news, your meeting has been booked successfully.");
          }
        }
      } catch (e) {
        console.error("[Zia] Failed to parse data:", topic, e);
      }
    };

    room.on("dataReceived", handleData);
    return () => {
      room.off("dataReceived", handleData);
    };
  }, [room]);

  const handleFormChange = useCallback((field: keyof BookingFormValues, value: string) => {
    setFormValues((prev) => ({ ...prev, [field]: value }));
  }, []);

  const submitBookingForm = useCallback(async () => {
    const email = formValues.email.trim();
    const phoneDigits = formValues.phone.replace(/\D/g, "");
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
      setFormError("Please enter a valid email.");
      return;
    }
    if (phoneDigits.length < 6 || phoneDigits.length > 15) {
      setFormError("Phone must be 6 to 15 digits.");
      return;
    }
    if (!localParticipant) {
      setFormError("Not connected yet. Please try again.");
      return;
    }

    setFormSubmitting(true);
    setFormError(null);
    try {
      await localParticipant.publishData(
        new TextEncoder().encode(
          JSON.stringify({
            type: "prefill_contact",
            email,
            countryCode: formValues.countryCode,
            phone: `${formValues.countryCode}${phoneDigits}`,
            country: formValues.countryCode,
          })
        ),
        { topic: "prefill_contact" }
      );
      // Keep popup open until backend confirmation arrives.
    } catch (e) {
      console.error("[Zia] Failed to submit booking form:", e);
      setFormSubmitting(false);
      setFormError("Failed to submit. Please try again.");
    }
  }, [formValues, localParticipant]);

  // -------------------------------------------------------------------------
  // Enable microphone as soon as the room is connected
  // -------------------------------------------------------------------------
  useEffect(() => {
    if (!room || !localParticipant || micEnabledRef.current) return;

    const enable = async () => {
      try {
        await localParticipant.setMicrophoneEnabled(true);
        micEnabledRef.current = true;
        console.log("[Zia] Microphone enabled");
      } catch (err) {
        console.error("[Zia] Failed to enable microphone:", err);
      }
    };

    if (room.state === "connected") {
      enable();
    } else {
      room.once("connected", enable);
    }
  }, [room, localParticipant]);

  // -------------------------------------------------------------------------
  // Mute / unmute
  // -------------------------------------------------------------------------
  const toggleMute = useCallback(async () => {
    if (!localParticipant) return;
    const next = !isMuted;
    await localParticipant.setMicrophoneEnabled(!next);
    setIsMuted(next);
  }, [localParticipant, isMuted]);

  // -------------------------------------------------------------------------
  // Derive display state:
  //   "connecting" — room not yet connected
  //   "thinking"   — room connected, agent hasn't spoken yet
  //   state        — whatever the voice assistant says once active
  // -------------------------------------------------------------------------
  const displayState = !roomConnected
    ? "connecting"
    : !agentSpokeOnce
      ? "thinking"
      : state;

  return (
    <div className="relative flex flex-col items-center justify-center min-h-screen gap-8 overflow-hidden bg-gradient-to-br from-slate-950 via-indigo-950 to-fuchsia-950 text-white">
      <div className="pointer-events-none absolute -top-24 -left-24 h-72 w-72 rounded-full bg-cyan-500/20 blur-3xl animate-pulse" />
      <div className="pointer-events-none absolute -bottom-24 -right-16 h-80 w-80 rounded-full bg-fuchsia-500/20 blur-3xl animate-pulse" />

      <h1 className="text-2xl font-semibold tracking-wide">Talk to Zia</h1>
      <div className="px-4 py-2 rounded-full border border-white/20 bg-white/10 backdrop-blur text-sm font-medium">
        {formatDuration(callDurationSec)}
      </div>

      <SpeakingAnimation state={displayState} />

      <div className="w-full max-w-2xl px-4">
        <div className="rounded-2xl border border-white/15 bg-black/25 backdrop-blur-md p-4 shadow-xl">
          <p className="text-xs uppercase tracking-wider text-cyan-300 mb-1">Latest AI response</p>
          <p className="text-sm text-white/90 min-h-6">{latestAiText}</p>
        </div>
      </div>

      <div className="flex gap-4">
        <button
          onClick={toggleMute}
          className={`px-6 py-3 rounded-full font-medium transition-all duration-300 ${isMuted
              ? "bg-yellow-500/90 hover:bg-yellow-500"
              : "bg-white/20 hover:bg-white/30"
            }`}
        >
          {isMuted ? (
            <span className="flex items-center gap-2">
              <svg
                width="20"
                height="20"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
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
              <svg
                width="20"
                height="20"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
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
          className="px-6 py-3 rounded-full font-medium bg-red-600/90 hover:bg-red-500 transition-all duration-300 flex items-center gap-2 shadow-lg shadow-red-900/40"
        >
          <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M10.68 13.31a16 16 0 0 0 3.41 2.6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7 2 2 0 0 1 1.72 2v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91" />
            <line x1="23" y1="1" x2="1" y2="23" />
          </svg>
          End Call
        </button>
      </div>

      <RoomAudioRenderer />
      {showBookingForm && (
        <BookingFormPopup
          values={formValues}
          submitting={formSubmitting}
          error={formError}
          onChange={handleFormChange}
          onSubmit={submitBookingForm}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Root page component
// ---------------------------------------------------------------------------

export default function Home() {
  const [connectionDetails, setConnectionDetails] = useState<{
    token: string;
    url: string;
    roomName: string;
  } | null>(null);

  const [isConnecting, setIsConnecting] = useState(false);
  const [tokenError, setTokenError] = useState<string | null>(null);

  // Pre-fetched token — ready before the user clicks
  const [prefetchedToken, setPrefetchedToken] = useState<{
    token: string;
    url: string;
    roomName: string;
  } | null>(null);

  // -------------------------------------------------------------------------
  // Pre-fetch a token immediately on mount so clicking "Start Call" is instant
  // -------------------------------------------------------------------------
  const prefetch = useCallback(async () => {
    try {
      const controller = new AbortController();
      const tid = setTimeout(() => controller.abort(), 4000);
      const res = await fetch(`/api/token?t=${Date.now()}`, {
        cache: "no-store",
        signal: controller.signal,
      });
      clearTimeout(tid);
      if (res.ok) {
        const data = await res.json();
        if (data?.token && data?.url) {
          setPrefetchedToken({
            token: data.token,
            url: data.url,
            roomName: data.roomName ?? "",
          });
        }
      }
    } catch {
      // silent — we'll fetch fresh on click
    }
  }, []);

  useEffect(() => {
    prefetch();
  }, [prefetch]);

  // -------------------------------------------------------------------------
  // Initialize AudioContext on first user interaction (required by browsers)
  // -------------------------------------------------------------------------
  const initAudio = useCallback(() => {
    try {
      const AudioCtx =
        window.AudioContext || (window as any).webkitAudioContext;
      if (AudioCtx) {
        const ctx = new AudioCtx();
        if (ctx.state === "suspended") ctx.resume().catch(() => { });
      }
    } catch {
      // non-fatal
    }
  }, []);

  // -------------------------------------------------------------------------
  // Start call
  // -------------------------------------------------------------------------
  const startCall = useCallback(async () => {
    initAudio();
    setTokenError(null);
    setIsConnecting(true);

    try {
      // Use the pre-fetched token if available (zero extra latency)
      if (prefetchedToken) {
        setConnectionDetails(prefetchedToken);
        setPrefetchedToken(null);
        setIsConnecting(false);
        prefetch(); // start fetching the next one
        return;
      }

      // Fallback: fetch fresh
      const controller = new AbortController();
      const tid = setTimeout(() => controller.abort(), 6000);
      const res = await fetch(`/api/token?t=${Date.now()}`, {
        cache: "no-store",
        signal: controller.signal,
      });
      clearTimeout(tid);

      const data = await res.json();

      if (!res.ok) {
        setTokenError(data?.detail ?? data?.error ?? "Failed to get token");
        return;
      }
      if (!data?.token || !data?.url) {
        setTokenError("Invalid token response from server");
        return;
      }

      setConnectionDetails({
        token: data.token,
        url: data.url,
        roomName: data.roomName ?? "",
      });
    } catch (err) {
      const isAbort = err instanceof Error && err.name === "AbortError";
      setTokenError(
        isAbort
          ? "Request timed out. Please try again."
          : "Could not reach server. Check your network and try again."
      );
    } finally {
      setIsConnecting(false);
    }
  }, [prefetchedToken, initAudio, prefetch]);

  // -------------------------------------------------------------------------
  // End call
  // -------------------------------------------------------------------------
  const endCall = useCallback(() => {
    try {
      localStorage.removeItem(BOOKING_STORAGE_KEY);
    } catch {
      // ignore
    }
    setConnectionDetails(null);
    prefetch(); // pre-fetch next token for fast re-connect
  }, [prefetch]);

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

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
          maxRetries: 3,
          peerConnectionTimeout: 15000,
        }}
        onConnected={() => console.info("[Zia] LiveKit room connected")}
        onDisconnected={() => console.info("[Zia] LiveKit room disconnected")}
        onError={(err) => console.error("[Zia] LiveKit error:", err)}
      >
        <VoiceAssistantUI onDisconnect={endCall} />
      </LiveKitRoom>
    );
  }

  return (
    <div
      className="relative flex flex-col items-center justify-center min-h-screen gap-8 overflow-hidden bg-gradient-to-br from-slate-950 via-purple-950 to-indigo-950 text-white"
      onClick={initAudio}
    >
      <div className="pointer-events-none absolute -top-24 -left-20 h-80 w-80 rounded-full bg-cyan-500/20 blur-3xl animate-pulse" />
      <div className="pointer-events-none absolute -bottom-24 -right-20 h-96 w-96 rounded-full bg-fuchsia-500/20 blur-3xl animate-pulse" />
      <img src="/dialzia.png" alt="Zia" className="w-24 h-24" />
      <h1 className="text-3xl font-bold">Talk to Zia</h1>
      <p className="text-gray-300 text-center max-w-md">
        Click the button below to start a conversation with Zia,
        <br /> our AI voice assistant.
      </p>

      <div className="w-24 h-24 rounded-full bg-white/10 border border-white/20 flex items-center justify-center animate-pulse">
        <svg
          width="40"
          height="40"
          viewBox="0 0 24 24"
          fill="none"
          stroke="#d1d5db"
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
        <svg
          width="24"
          height="24"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7 2 2 0 0 1 1.72 2z" />
        </svg>
        {isConnecting ? "Connecting..." : "Start Call"}
      </button>
    </div>
  );
}