# ADR-0007 — Credential encryption, key escrow, and retention

**Status:** Accepted · 2026-09-01

**Context:** The system stores other people's account credentials on a VPS the operator does not even own. Encryption without a key-management story fails exactly when the backup is needed: lose the key with the VPS and every backup restores to unreadable ciphertext.

**Decision:**
- Field-level encryption (Fernet via `cryptography`, key from env, MultiFernet key-version prefix for future rotation, a ~30-line custom `EncryptedTextField`) for `DeliveryField.value` **and** `OrderItem.customer_input` — the latter can contain the customer's own password for on-their-account delivery.
- Stated threat model: theft of a DB dump/backup — not live-host compromise (key and DB share the server; accepted at this scale).
- **Key escrow before the first real delivery**: offline copies of the field key and the backup passphrase in the operator's password manager plus one offline copy. The periodic restore drill must **decrypt a row** to count as passed.
- Every reveal (customer, operator, or magic-link) writes a `CredentialAccessLog` row.
- Retention (owner-accepted): receipt images 90 days after confirmation; delivered credentials and `customer_input` hard-deleted at `expires_at + warranty + 30 days`; access logs 1 year; enforced by scheduled jobs and stated on the privacy page. Receipts live outside public media, served only via an authenticated view.

**Alternatives considered:** external KMS (overkill for one VPS); per-item keys / crypto-shredding (complexity without a driving requirement); keeping credentials forever (silently grows the blast radius of every other failure).

**Consequences:** A restore test that cannot decrypt is a **failed** restore. Any new sensitive field gets the same treatment by default.
