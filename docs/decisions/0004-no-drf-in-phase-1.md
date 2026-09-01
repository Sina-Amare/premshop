# ADR-0004 — No DRF / no HTTP API in phase 1

**Status:** Accepted · 2026-09-01

**Context:** The founding principle is one database, one service layer, with site and bot as two displays. The phase-1 Telegram bot is one webhook view plus outbound sends — it runs **in the same process** and calls `services.py` directly.

**Decision:** No DRF, no `/api/v1`, no `serializers.py` anywhere in phase 1. The bot's only HTTP surface is the webhook endpoint. The API arrives with the phase-3 mini-app, when a real out-of-process consumer exists.

**Alternatives considered:** DRF from day one (a whole parallel interface — auth surface, BOLA risk class, tests — maintained for nobody).

**Consequences:** The fat service layer is what makes adding the API later cheap: endpoints become serializer + one existing service call. Nobody should "helpfully" add DRF before phase 3.
