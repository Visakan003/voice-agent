export const dynamic = "force-dynamic";

import { AccessToken } from "livekit-server-sdk";
import { NextResponse } from "next/server";

function jsonError(error: string, detail: string, status: number = 500) {
  return NextResponse.json({ error, detail }, { status });
}

export async function GET() {
  const startedAt = Date.now();

  try {
    // Prefer server-only env vars (Netlify/production). Fallback to NEXT_PUBLIC_ for local dev.
    const apiKey =
      process.env.LIVEKIT_API_KEY ?? process.env.NEXT_PUBLIC_LIVEKIT_API_KEY;
    const apiSecret =
      process.env.LIVEKIT_API_SECRET ?? process.env.NEXT_PUBLIC_LIVEKIT_API_SECRET;
    const livekitUrl =
      process.env.NEXT_PUBLIC_LIVEKIT_URL ?? process.env.LIVEKIT_URL;

    if (!apiKey?.trim() || !apiSecret?.trim() || !livekitUrl?.trim()) {
      return jsonError(
        "Server misconfigured",
        "Set LIVEKIT_API_KEY, LIVEKIT_API_SECRET, and NEXT_PUBLIC_LIVEKIT_URL (or LIVEKIT_URL) in .env.local."
      );
    }

    const roomName = `room-${crypto.randomUUID()}`;
    const participantName = `user-${Math.random().toString(36).substring(7)}`;

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
    const url = livekitUrl.trim().replace(/^http:/, "ws:").replace(/^https:/, "wss:");

    const response = NextResponse.json({ token, url, roomName });
    response.headers.set("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0");
    response.headers.set("Pragma", "no-cache");
    response.headers.set("X-Token-Gen-Ms", String(Date.now() - startedAt));

    return response;
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    console.error("[api/token] Error:", e);
    return jsonError("Token generation failed", message);
  }
}
