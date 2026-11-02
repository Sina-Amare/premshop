# ADR-0030 — Legal pages and protected drafts

**Status:** Accepted · 2026-10-29

**Context.** Legal copy must be read in the storefront before it becomes a public commitment. A staff flag alone does not authorize access to unpublished content.

**Decision.** `cms.Page` holds the five legal/information pages in restricted Markdown through the existing core renderer. Its five URLs are fixed; editing a saved page cannot change its address. The app imports core only, and product questions remain in catalog.

All initial pages are unpublished. Staff with `cms.view_page` may preview a draft at its eventual address; everyone else receives 404. Previews use noindex, private/no-store and Cookie variation. Only published pages enter footer navigation. Admin editing uses the existing Unfold interface; `cms.publish_page` is separate from draft editing. Changing published text requires publishing authority. Empty or owner-placeholder content cannot be published. The publication checkbox is an operator decision, not evidence that the described business process exists.

**Consequences.** Initial content is inserted only when absent, preserving later edits. Content approval and operational readiness precede public release. Mutable pages do not preserve a purchase contract: before real orders, snapshot the applicable seller/terms/refund text and versions alongside product/price snapshots, check policy changes at checkout, and provide the customer with a retainable confirmation. Neither the snapshot nor the paid operational processes are implemented by these page models.

**Alternatives.** Hardcoded templates prevent ordinary content edits. A page builder adds layout choices these five pages do not need. Sharing product questions with CMS would couple independent apps.

**Revisit when.** An approved guide needs a new route or a second author needs a review workflow. Extend the content model for that requirement rather than allowing arbitrary public pages now.

**Amended 2026-11-02 — purchase records.** New orders copy the published terms, refunds and contact titles and bodies into `Order.policy_snapshot`. Each copy includes a SHA-256 content hash of canonical JSON containing its title and body. Contact is the seller-details source; there is no separate seller model or CMS revision history. The existing quote terms/digest includes these copies, and replay returns the original record. Draft or missing pages are omitted and currently do not block order placement. Manual payment must refuse any order missing one of the three saved pages; publishing a page later does not repair an older record. Product snapshots also include the full description and ordered section text; product questions remain outside the record under their existing content rule. Customer and read-only operator order pages show the saved conditions. Older snapshots render without backfill. Retainable confirmation and the shared mail-send guard remain separate prerequisites before real orders.
