# ADR-0031 — Sign-in by mobile number and SMS code

**Status:** Accepted · 2026-11-12 · supersedes [ADR-0012](0012-auth-email-otp.md)

**Context.** Customers come from Telegram sales and know their mobile number better than an email address, and an emailed code that lands in spam is a lost sale. Domestic SMS providers deliver a code in seconds. No real customer account existed when this was decided, so the identity could change without moving anyone.

**Decision.**

- **The mobile number is the account's identity:** `USERNAME_FIELD`, Iranian mobiles only, unique, in one normal form (`09` and nine digits) that a database check constraint enforces. An account made earlier with no number keeps NULL and cannot sign in.
- **A customer signs in, and registers, with the number and a six-digit code sent by SMS through SMS.ir.** With `SMS_IR_TEMPLATE_ID` set, the code goes through `verify` and the shop's approved template; without it, as a plain message from `SMS_IR_LINE_NUMBER`. Codes keep the handling of [ADR-0024](0024-auth-implementation.md): hashed in the cache for ten minutes, five guesses, single use, stored only after the send is accepted.
- **One way in per account.** Customers have no password. Staff sign in only at the panel, with phone and password, and the panel admits only sessions its own sign-in opened.
- **Email is an optional contact address,** neither confirmed nor unique, asked at checkout and edited on the account page. An order without one sends its notices through Telegram when the customer has linked it, and the order page holds the rest.
- **Limits, because every SMS is paid for:**
  - per number, one code every two minutes, five an hour and ten accepted a day
  - per network, ten an hour and twenty accepted a day
  - a shop-wide day of 300 accepted sends, with an email to the operator at the 100th
  
  A refused or failed request sends nothing and spends no daily unit. A known and an unknown number get the same answer.
- **No CAPTCHA,** as ADR-0024's 2026-11-03 amendment decided.

**Alternatives considered.**

- **Email as identity with a phone code as a second way in:** two ways in, and the weaker one decides.
- **Passwords as a fallback during SMS outages:** a password is another way in, not a lock. Telegram codes are the planned second channel.
- **SMS notices for customers with no email:** one approved template and one charge per message type.

**Consequences.**

- **A SIM swap or a recycled number holds the account.** Today the site shows a customer no delivered credential. Any change that adds such a page must first confirm a channel the number's next holder cannot set from a code session.
- **An SMS outage stops new sign-ins until Telegram codes exist.** Domestic SMS was cut for about nine days in January 2026. A signed-in device keeps its two-week session.
- **The shop-wide ceiling trades availability for money.** Spending the shop's day of SMS stops codes for every customer until the window turns, but cannot drain the balance.
- **A customer who gives no email receives no mail.** Their order notices reach them through Telegram or the order page.
- **An email is never proof of the account.** Anyone can type any address, so a delivery link never goes to an unconfirmed one ([ADR-0008](0008-content-free-messages-and-magic-link.md), amended 2026-11-12).

**Revisit when** SMS proves unreliable or dearer than Telegram's own code service, when normal trade reaches the daily budget, or when a customer without an Iranian number needs an account.

**Amendment — 2026-11-14: selectable Telegram codes.** Sign-in and guest checkout offer SMS, selected by default, or Telegram. Telegram sends only to an active customer's previously linked account; staff still cannot sign in by code. Every valid Telegram request receives the same conditional acknowledgement and queues the same task shape, including unknown, unlinked, inactive and staff numbers. No account is created for an unknown Telegram request, and no website response reports link eligibility or delivery failure. SMS remains the registration route.

SMS retains the limits above, including spending its short request windows on a failed send. Telegram has separate request limits: one per number every two minutes, five per number/hour and ten per network/hour, with no daily allowance. Telegram consumes no SMS budget or cooldown. The explicit SMS switch is available immediately and is checked against SMS's own limits; no paid fallback happens automatically. Both channels share the existing verification-attempt budgets and one live code per number.

Telegram delivery runs in Huey, outside the web request. Its ten-minute deadline starts at request acceptance and never moves while queued. Jobs contain recipient/request metadata, never a plaintext code. The worker generates the code and activates its hash only after Telegram accepts the message. The code is set in monospace so one tap copies it, and the message is not protected, because protection blocks that copy; the owner chose copying over the forwarding block (2026-11-14), since the message is deleted once used. Queue or transport failure preserves the previous live code and expiry; uncertain sends are not retried automatically. Expired, duplicate, superseded or changed-link jobs activate nothing. The account's existing `telegram_id` and `telegram_linked_at` identify the captured link generation; unlinking, moving or relinking invalidates its code. Account locks serialize activation, verification and link changes, with provider calls outside those locks.

Successful sign-in cancels older pending work and queues best-effort deletion of the code message it consumed. Deletion runs only in the worker, never as a Telegram call in the sign-in request; failure cannot undo sign-in. Replaced or stale messages need no deletion because their codes cannot sign in. Sign-in alerts remain removed. Production reaches Telegram through its relay; receipt and use on a real phone through that route are deployment acceptance checks.
