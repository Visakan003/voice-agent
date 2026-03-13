export const dynamic = "force-dynamic";
export const revalidate = 0;

import { AccessToken } from "livekit-server-sdk";
import { NextResponse } from "next/server";

// Simple in-memory cache
const tokenCache = new Map();
const CACHE_TTL = 5000;

function jsonError(error: string, detail: string, status: number = 500) {
  return NextResponse.json({ error, detail }, { status });
}

export async function GET() {
  const startedAt = Date.now();

  try {
    // Check cache
    const cacheKey = "livekit_token";
    const cached = tokenCache.get(cacheKey);
    if (cached && Date.now() - cached.timestamp < CACHE_TTL) {
      const response = NextResponse.json(cached.data);
      response.headers.set("Cache-Control", "no-store, no-cache, must-revalidate");
      response.headers.set("X-Token-Gen-Ms", "0 (cached)");
      return response;
    }

    const apiKey = process.env.LIVEKIT_API_KEY ?? process.env.NEXT_PUBLIC_LIVEKIT_API_KEY;
    const apiSecret = process.env.LIVEKIT_API_SECRET ?? process.env.NEXT_PUBLIC_LIVEKIT_API_SECRET;
    const livekitUrl = process.env.NEXT_PUBLIC_LIVEKIT_URL ?? process.env.LIVEKIT_URL;

    if (!apiKey?.trim() || !apiSecret?.trim() || !livekitUrl?.trim()) {
      return jsonError(
        "Server misconfigured",
        "Missing LiveKit credentials"
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

    const responseData = { token, url, roomName };
    
    // Cache the result
    tokenCache.set(cacheKey, {
      data: responseData,
      timestamp: Date.now()
    });

    const response = NextResponse.json(responseData);
    response.headers.set("Cache-Control", "no-store, no-cache, must-revalidate");
    response.headers.set("X-Token-Gen-Ms", String(Date.now() - startedAt));

    return response;
  } catch (e) {
    console.error("[api/token] Error:", e);
    return jsonError("Token generation failed", e instanceof Error ? e.message : String(e));
  }
}