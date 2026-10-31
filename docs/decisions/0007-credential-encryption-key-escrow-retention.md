# ADR-0007 — Credential encryption, key escrow, and retention

**Status:** Accepted · 2026-09-01

**Context:** The system stores other people's account credentials on a VPS the operator does not even own. Encryption without a key-management story fails exactly when the backup is needed: lose the key with the VPS and every backup restores to unreadable ciphertext.

**Decision:**
- Field-level encryption (Fernet via `cryptography`, key from env, MultiFernet key-version prefix for future rotation, a ~30-line custom `EncryptedTextField`) for `DeliveryField.value` **and** `OrderItem.customer_input` — the latter can contain the customer's own password for on-their-account delivery.
- Stated threat model: theft of a DB dump/backup — not live-host compromise (key and DB share the server; accepted at this scale).
- **Key escrow before the first real delivery**: offline copies of the field key and the backup passphrase in the operator's password manager plus one offline copy. The periodic restore drill must **decrypt a row** to count as passed.
- Every reveal (customer, operator, or magic-link) writes a `CredentialAccessLog` row.
- Retention (owner-accepted): delivered credentials and `customer_input` hard-deleted at `expires_at + warranty + 30 days`; access logs 1 year; enforced by scheduled jobs and stated on the privacy page.

**Alternatives considered:** external KMS (overkill for one VPS); per-item keys / crypto-shredding (complexity without a driving requirement); keeping credentials forever (silently grows the blast radius of every other failure).

**Consequences:** A restore test that cannot decrypt is a **failed** restore. Any new sensitive field gets the same treatment by default.

**Amendment — 2026-10-26.** On-site activation input is limited by product policy to non-secret account identifiers such as email or username. Products that require a customer's password or direct login are disclosed before purchase and handled manually in Telegram after verified payment; this is an owner-approved exception for incoming activation details, outside the site's retention and access controls. The software still never sends credential values in messages. Delivered credentials remain encrypted on-site when that feature arrives.

`core.fields.EncryptedTextField` now encrypts non-empty values with a distinct environment key, prefixes ciphertext with an eight-hex-character key id and permits only `isnull` lookups. `FIELD_ENCRYPTION_KEY` encrypts; `FIELD_ENCRYPTION_KEY_FALLBACKS` only decrypt. Unknown keys and damaged values fail without including the value in the error. `reencrypt_fields --batch-size 200` locks and rewrites old-key rows in batches, without printing values; it can be rerun. Keep retired keys until all dependent rows are rotated and all older backups expire. Error reports disable stack-frame local capture. Offline escrow and a decrypting restore remain prerequisites for real delivery.

**Amendment — 2026-10-29.** For a cancelled order that was never activated, delete website activation input within 30 calendar days after cancellation. This does not delete invoice, payment or audit history. Completed subscriptions retain the original `expires_at + warranty duration + 30 days` calculation; credential-access logs retain their one-year limit when that feature exists.

An interim operator process may enforce these deadlines before scheduled deletion exists. Before affected paid orders are accepted, demonstrate how due fields are identified, cleared and the completion recorded. This process is not implemented by the content pages. Public wording must not claim automatic deletion or unbuilt credential/access-log features. Backup retention is disclosed separately from active-database deletion using the actual backup schedule. Planned automation remains unchanged.

**Amendment — 2026-10-31.** Retention deadlines remain internal operating rules, rather than statements on the public privacy page. This supersedes the original requirement to state those deadlines there and the 2026-10-29 requirement to disclose backup retention separately. The completed-subscription, cancelled-input and access-log deadlines, backup controls and requirement to demonstrate interim deletion before affected paid orders remain unchanged. The short privacy page describes the data collected, its use for orders, support and site emails, encrypted activation identifiers, cookies and security use of IP addresses, and a support route. It makes no claim that only the shop can see the data. Confirming backup retention or the error-reporting configuration is no longer a condition for approving this page; their operational requirements remain with deployment.
