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

**Amendment — 2026-10-23: recovery and deliverability as built.** No password-reset link flow was built; ADR-0024 records the deliberate cut. Code sign-in restores account access, but a customer who already has a password must still supply it to change it at `/account/password/`, even after signing in by code. Code sign-in therefore does not yet provide the forgotten-password replacement claimed in ADR-0024. Mailbox possession is equivalent to account access through code sign-in alone; removing it would remove the built recovery route for a customer who has forgotten their password. Auth shipped with Gmail delivery verified and delivery to Iranian mail hosts knowingly untested, as ADR-0024 records. That untested route remains a launch check, not a passed deliverability test.
