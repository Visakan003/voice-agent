import { NextResponse } from "next/server";

/**
 * GET /api/token/check - Safe check that LiveKit env vars are set (no values exposed).
 * Use this to verify Netlify has the right env at runtime when things work "sometimes".
 */
export async function GET() {
  const apiKey =
    process.env.LIVEKIT_API_KEY ?? process.env.NEXT_PUBLIC_LIVEKIT_API_KEY;
  const apiSecret =
    process.env.LIVEKIT_API_SECRET ?? process.env.NEXT_PUBLIC_LIVEKIT_API_SECRET;
  const livekitUrl =
    process.env.NEXT_PUBLIC_LIVEKIT_URL ?? process.env.LIVEKIT_URL;

  const ok =
    Boolean(apiKey?.trim()) &&
    Boolean(apiSecret?.trim()) &&
    Boolean(livekitUrl?.trim());

  return NextResponse.json({
    configured: ok,
    hasKey: Boolean(apiKey?.trim()),
    hasSecret: Boolean(apiSecret?.trim()),
    hasUrl: Boolean(livekitUrl?.trim()),
    urlPrefix: ok ? livekitUrl!.trim().slice(0, 30) + "…" : null,
  });
}
