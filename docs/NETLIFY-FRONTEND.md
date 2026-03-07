# Deploy Frontend to Netlify & Fix "Room not connected" / 401 Invalid Token

## Why the room doesn't connect (401 / invalid token)

LiveKit rejects the access token when:

1. **Env vars are missing or wrong on Netlify** – The `/api/token` route needs the same LiveKit API Key and Secret as your LiveKit project. If they’re not set or don’t match `dialzia-gcdrg8cg.livekit.cloud`, you get **401 Unauthorized** and **invalid token**.

2. **Wrong project** – Key/secret must be from the [LiveKit Cloud](https://cloud.livekit.io) project that matches `NEXT_PUBLIC_LIVEKIT_URL` (e.g. `wss://dialzia-gcdrg8cg.livekit.cloud`).

---

## Set environment variables in Netlify

1. In Netlify: **Site configuration** → **Environment variables** → **Add a variable** (or **Edit**).
2. Add these for **Production** (and optionally Branch deploys):

| Variable | Value | Scopes |
|----------|--------|--------|
| `LIVEKIT_API_KEY` | Your LiveKit API Key (e.g. `APIVC64oyok2zG5`) | Production, Deploy |
| `LIVEKIT_API_SECRET` | Your LiveKit API Secret | Production, Deploy |
| `NEXT_PUBLIC_LIVEKIT_URL` | `wss://dialzia-gcdrg8cg.livekit.cloud` | Production, Deploy |

Use the **exact** API Key and API Secret from the LiveKit project that owns `dialzia-gcdrg8cg.livekit.cloud` (same as in your backend `.env`).

3. **Save** and trigger a **new deploy** (e.g. **Trigger deploy** → **Deploy site**) so the new env vars are used at runtime.

---

## Checklist

- [ ] `LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET` are set in Netlify (no typos, no extra spaces).
- [ ] They match the project at https://cloud.livekit.io for the host in `NEXT_PUBLIC_LIVEKIT_URL`.
- [ ] `NEXT_PUBLIC_LIVEKIT_URL` is `wss://...` (not `https://`).
- [ ] A new deploy was run after changing env vars.

---

## Optional: use NEXT_PUBLIC_ for local dev only

For security, the token API prefers **server-only** vars:

- `LIVEKIT_API_KEY`
- `LIVEKIT_API_SECRET`

If these are set on Netlify, the secret is never sent to the browser. Your local `frontend/.env.local` can still use `NEXT_PUBLIC_LIVEKIT_*` for development; the API route falls back to those if the server-only vars are not set.
