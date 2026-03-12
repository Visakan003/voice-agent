# Voice Agent Backend

LiveKit voice agent (Zia) that collects booking details and checks Calendly availability.

## Environment variables

Copy `.env.example` to `.env` and set your keys. Required for the agent:

- `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`
- `OPENAI_API_KEY`

### Optional: Calendly (availability after booking)

You can use either:

1. **Scheduling link** (easiest): set your public Calendly link and the agent will resolve the event type.
   ```env
   CALENDLY_ACCESS_TOKEN=your_personal_access_token
   CALENDLY_SCHEDULING_LINK=https://calendly.com/your-username/your-event-slug
   ```

2. **Event type URI**: set the API event type URI directly.
   ```env
   CALENDLY_ACCESS_TOKEN=your_personal_access_token
   CALENDLY_EVENT_TYPE_URI=https://api.calendly.com/event_types/XXXXXXXX
   ```

Get a token at: Calendly → Integrations → Developer → Personal access tokens.

**Booking meetings:** After the user picks a time, the agent calls the Calendly API to create an invitee (book the meeting). Calendly then sends the confirmation email and calendar invite. Creating invitees via the API requires a **paid Calendly plan** (Standard, Teams, or Enterprise).

Availability (checking open slots) works without any extra config. **If meeting booking fails with "location kind not configured"** or similar, set `CALENDLY_LOCATION_KIND` to the location type of your event type (e.g. `custom_link` for "Custom conferencing link", `zoom_conference` for Zoom). Common values:

- `custom_link` — Custom conferencing link
- `zoom_conference` — Zoom
- `google_meet` — Google Meet
- `phone_call` — Phone call
- `in_person` — In-person

Example:

```env
CALENDLY_LOCATION_KIND=custom_link
```

## Run

```bash
python agent.py start
```

