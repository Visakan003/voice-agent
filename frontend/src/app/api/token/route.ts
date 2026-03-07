import { AccessToken } from "livekit-server-sdk";
import { NextResponse } from "next/server";

export async function GET() {
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

  const roomName = `room-${Math.random().toString(36).substring(7)}`;
  const participantName = `user-${Math.random().toString(36).substring(7)}`;

  try {
    const at = new AccessToken(apiKey.trim(), apiSecret.trim(), {
      identity: participantName,
      ttl: "5m",
    });
    at.addGrant({
      roomJoin: true,
      room: roomName,
      canPublish: true,
      canSubscribe: true,
    });

    const token = await at.toJwt();

    return NextResponse.json({
      token,
      url: livekitUrl.trim().replace(/^http:/, "ws:").replace(/^https:/, "wss:"),
      roomName,
    });
  } catch (e) {
    console.error("Token generation failed:", e);
    return NextResponse.json(
      { error: "Token generation failed", detail: String(e) },
      { status: 500 }
    );
  }
}
