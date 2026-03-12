export const dynamic = "force-dynamic";

import { AccessToken } from "livekit-server-sdk";
import { NextResponse } from "next/server"; 

export async function GET() {
  const startedAt = Date.now();
  // Prefer server-only env vars (Netlify/production). Fallback to NEXT_PUBLIC_ for local dev.
  const apiKey =
    process.env.LIVEKIT_API_KEY ?? process.env.NEXT_PUBLIC_LIVEKIT_API_KEY;
  const apiSecret =
    process.env.LIVEKIT_API_SECRET ?? process.env.NEXT_PUBLIC_LIVEKIT_API_SECRET;
  const livekitUrl =
    process.env.NEXT_PUBLIC_LIVEKIT_URL ?? process.env.LIVEKIT_URL;

  if (!apiKey?.trim() || !apiSecret?.trim() || !livekitUrl?.trim()) {
    return NextResponse.json(
      {
        error: "Server misconfigured",
        detail:
          "Set NEXT_PUBLIC_LIVEKIT_API_KEY, NEXT_PUBLIC_LIVEKIT_API_SECRET, and NEXT_PUBLIC_LIVEKIT_URL in Netlify (or .env.local).",
      },
      { status: 500 }
    );
  }

  const roomName = `room-${crypto.randomUUID()}`;
  const participantName = `user-${Math.random().toString(36).substring(7)}`;

  try {
    const at = new AccessToken(apiKey.trim(), apiSecret.trim(), {
      identity: participantName,
      ttl: "15m",
    });
    at.addGrant({
      roomJoin: true,
      room: roomName,
      canPublish: true,
      canSubscribe: true,
    });

    const token = await at.toJwt();

    const response = NextResponse.json({
      token,
      url: livekitUrl.trim().replace(/^http:/, "ws:").replace(/^https:/, "wss:"),
      roomName,
    });

    // Prevent caching so every "Start Call" gets a fresh token (avoids 401 after a few minutes)
    response.headers.set("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0");
    response.headers.set("Pragma", "no-cache");
    response.headers.set("X-Token-Gen-Ms", String(Date.now() - startedAt));

    return response;
  } catch (e) {
    console.error("Token generation failed:", e);
    return NextResponse.json(
      { error: "Token generation failed", detail: String(e) },
      { status: 500 }
    );
  }
}
