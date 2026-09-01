# ADR-0012 — Auth: email identity, inline checkout OTP, and the sign-in alert

**Status:** Accepted · 2026-09-01

**Context:** Email is the username and the recovery channel, which makes email possession equivalent to account access (OTP login and password reset both ride it) — irreducibly so, until a second factor exists. Meanwhile a hard "verify before you may buy" gate turns every undelivered email into a lost sale.

**Decision:**
- `email` is `USERNAME_FIELD`; login via password or emailed OTP; **checkout for new users runs OTP inline** — one flow doubles as account creation and email verification; no prior-verification hard block on ordering.
- Phone number is collected at checkout from day one (schema ready); SMS as a channel is deferred until decided.
- A completed Telegram link is an accepted alternate channel: linked users can receive login OTPs via Telegram, which also de-risks fragile email.
- **Guard:** any OTP-created session on an account that has a usable password — login page or checkout — emits an immediate new-sign-in alert on email *and* Telegram-if-linked. The Telegram half matters: an attacker holding the mailbox cannot suppress it.
- The email provider must pass a real deliverability test before the auth step ships.

**Alternatives considered:** order-scoped checkout OTP (incoherent while the OTP login page exists — the attacker just uses that; removing OTP login gains nothing while reset exists); mandatory pre-purchase verification (kills conversion on the channel the same brief calls fragile).

**Consequences:** Checkout is an auth surface and is rate-limited like login. Adding a true second factor for customers is future work; for the operator, 2FA is mandatory from the soft deploy.
