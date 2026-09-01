# ADR-0015 — Error tracking: sentry-sdk reporting to self-hosted GlitchTip

**Status:** Accepted · 2026-09-01

**Context:** Production errors must be visible without customer complaints, but sentry.io is unreachable for the operator (US SaaS, Iran access), and any foreign error service both risks account closure and receives whatever the app sends it.

**Decision:** The app uses the standard `sentry-sdk` with hard scrubbing (`send_default_pii=False`, no request bodies, a tested `before_send` scrubber for credential/token keys, delivery-link URL truncation). The backend is **self-hosted GlitchTip** — open source, Sentry-SDK-compatible — running as one more container on the VPS from the soft deploy. Before the server exists, the SDK no-ops with an empty DSN.

**Alternatives considered:** sentry.io (unreachable); hosted GlitchTip free tier (same reachability class of risk); self-hosted Sentry (operationally enormous); no tracking (invisible production failures).

**Consequences:** No foreign account, no blocks, dashboard reachable exactly where the site is. Known limitation: the tracker shares the VPS's fate — the *external* dead-man/uptime monitor is a separate concern whose provider must also be chosen for operator reachability.
