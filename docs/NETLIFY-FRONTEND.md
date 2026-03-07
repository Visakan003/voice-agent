# Deploy Frontend to Netlify & Fix "Room not connected" / 401 Invalid Token

## Why it works locally but not on Netlify

**Locally:** Next.js reads `frontend/.env.local`. Your `/api/token` route gets `NEXT_PUBLIC_LIVEKIT_*` from there.

**On Netlify:** `.env.local` is **not** deployed (it’s in `.gitignore`). Netlify only has the env vars you add in the Netlify UI. If you don’t add them there, the token API has no key/secret and LiveKit returns 401 Invalid token.

**Fix:** Add the same three variables from your `.env.local` in Netlify → Site configuration → Environment variables (see below).

---

## Why the room doesn't connect (401 / invalid token)

LiveKit rejects the access token when:

1. **Env vars are missing or wrong on Netlify** – The `/api/token` route needs the same LiveKit API Key and Secret as your LiveKit project. If they’re not set or don’t match `dialzia-gcdrg8cg.livekit.cloud`, you get **401 Unauthorized** and **invalid token**.

2. **Wrong project** – Key/secret must be from the [LiveKit Cloud](https://cloud.livekit.io) project that matches `NEXT_PUBLIC_LIVEKIT_URL` (e.g. `wss://dialzia-gcdrg8cg.livekit.cloud`).

---

## Set environment variables in Netlify

For this Next.js project you can use **NEXT_PUBLIC_** variables on Netlify. The token API route reads them on the server when generating tokens.

1. In Netlify: **Site configuration** → **Environment variables** → **Add a variable** (or **Edit**).
2. Add these for **Production** (and optionally Branch deploys):

| Variable | Value |
|----------|--------|
| `NEXT_PUBLIC_LIVEKIT_API_KEY` | Your LiveKit API Key (e.g. `APIVC64oyok2zG5`) |
| `NEXT_PUBLIC_LIVEKIT_API_SECRET` | Your LiveKit API Secret |
| `NEXT_PUBLIC_LIVEKIT_URL` | `wss://dialzia-gcdrg8cg.livekit.cloud` |

Use the **exact** API Key and API Secret from the LiveKit project that owns that URL (same as in your backend `.env`).

3. **Save** and trigger a **new deploy** (e.g. **Trigger deploy** → **Deploy site**) so the new env vars are used at runtime.

---

## Checklist

- [ ] `NEXT_PUBLIC_LIVEKIT_API_KEY` and `NEXT_PUBLIC_LIVEKIT_API_SECRET` are set in Netlify (no typos, no extra spaces).
- [ ] They match the project at https://cloud.livekit.io for the host in `NEXT_PUBLIC_LIVEKIT_URL`.
- [ ] `NEXT_PUBLIC_LIVEKIT_URL` is `wss://...` (not `https://`).
- [ ] A new deploy was run after changing env vars.

---

## Note on variable names

The token API route accepts **either** naming style (so both work on Netlify):

- `NEXT_PUBLIC_LIVEKIT_API_KEY` / `NEXT_PUBLIC_LIVEKIT_API_SECRET` (typical for Next.js)
- `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` (server-only; used if set)

---

## "It worked then stopped" / intermittent 401 Invalid token

If it worked a few minutes ago and then you get **401 Unauthorized** or **Invalid token** again:

1. **Netlify env scope** – In **Environment variables**, for each variable set **Scopes** to include **Production** and **Deploy** (not only "Build"). Otherwise the serverless function may not see them at request time on every request.
2. **Redeploy** – After changing env or scopes: **Trigger deploy** → **Clear cache and deploy site**.
3. **LiveKit dashboard** – At [cloud.livekit.io](https://cloud.livekit.io), check that the API key was **not** regenerated. If you rotated the key/secret, update the same values in Netlify and redeploy.
4. **Verify at runtime** – Open `https://your-site.netlify.app/api/token/check` in a new tab. You should see `{"configured":true,"hasKey":true,"hasSecret":true,"hasUrl":true,...}`. If `configured` is `false`, the server doesn’t have the env for that request (wrong scope or old deploy).
5. **Hard refresh** – On the app page use **Ctrl+Shift+R** (or Cmd+Shift+R) so the browser doesn’t reuse an old token.
