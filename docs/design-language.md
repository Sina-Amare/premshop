# Design Language

The visual system in force (decision: [ADR-0016](decisions/0016-brand-and-type.md)). Grounded in research into Iranian e-commerce convention, Persian typographic metrics, and the CSS-level differences between authored and generated interfaces.

## The stance

This shop should read like **a small business that keeps records** — not like a brand, and not like a landing page. The buyer's fear is paying into a void, so the antidote is visible bookkeeping: hairlines, aligned figures, stated hours, exact amounts, and a payable typeset with more care than any headline on the site. Warm stone paper, one teal accent spent sparingly, amber only when a clock is running.

## Type

| Role | Face | Weight | Size | Line-height |
|---|---|---|---|---|
| h1 | Estedad | 800 | 34px | 1.25 |
| h2 | Estedad | 800 | 26px | 1.35 |
| h3 | Estedad | 600 | 20px | 1.45 |
| h4 | Estedad | 600 | 17px | 1.5 |
| Body prose | Vazirmatn | 400 | 17px | **1.85** |
| UI / buttons | Vazirmatn | 400–500 | 15px | 1.5–1.6 |
| Small, meta | Vazirmatn | 400 | 13px | 1.7 |
| Micro, footnote | Vazirmatn | 400 | 12px | 1.9 |
| Label (the only use of 500) | Vazirmatn | 500 | 13px | 1.5 |
| Price numeral | Estedad | 800 | 24 / 32 / 40px | 1.2 |
| Currency word | Vazirmatn | 400 | 0.72em of numeral | 1 |

**Why these numbers.** Persian carries finer detail than Latin at the same size — the dot clusters distinguishing ب/پ/ت/ث — so the base is 17px, not 16, and nothing goes below 12px. Descenders (ج, ی) drop well below the baseline while marks sit high; at 1.5 they nearly touch the next line, which is the loudest "translated from a Latin design" signal there is. Hence 1.85 on prose. Headings run tight because at 26px+ the collision is proportionally smaller and tightness reads as confidence. Weight jumps are ≥200 because Persian's uniform baseline mass makes a 500/600 difference invisible.

**`size-adjust: 82%` on Estedad** normalises it against Vazirmatn: it renders larger at the same nominal size, so without this every heading reads louder than the scale intends.

**Absolute rules.** `letter-spacing: 0` everywhere — Arabic script is cursive and any tracking breaks the joins. No italic (neither family ships one; a synthesised oblique shears the joins), no `text-transform` (it would uppercase the Latin half of «اشتراک Claude Pro»), no `justify` (no browser implements kashida; you get rivers). `font-synthesis: none`. Latin runs inside Persian are isolated so bidi cannot scramble them.

## Spacing

Nine values, and nothing outside them appears in the stylesheet:

`4 · 8 · 12 · 16 · 24 · 32 · 48 · 64 · 96`

| Values | Job |
|---|---|
| 4, 8 | inside a component — icon↔label, badge padding |
| 12, 16 | tightly related — label↔input, card inner gaps |
| 24, 32 | component↔component |
| 48, 64 | block↔block within a page region |
| 96 | page region↔region |

**The rhythm rule:** the gap *between* groups is at least twice the gap *within* them. If two gaps are equal but the relationships aren't, one is wrong.

**Asymmetry is deliberate.** Headings own the space that follows them, never split evenly. Cards get more padding at the block-end because Persian descenders eat optical space there. Buttons are `11px 20px 9px` — Persian ink sits low in the line box, so equal padding reads as floating high.

## Colour

| Role | Value | Use |
|---|---|---|
| Ground / Surface | `#FAFAF9` / `#FFFFFF` | warm stone paper, white slabs on it |
| Fill subtle | `#F2F1F0` | hover fills, quiet panels |
| Line / Line strong | `#E7E5E4` / `#D6D3D1` | hairlines; borders that must be seen |
| Muted / Secondary / Ink | `#78716C` / `#57534E` / `#1C1917` | text ramp |
| **Accent** | teal-700 `#0F766E` | see budget below |
| Success | `#16A34A` (text `#166534`) | delivered, confirmed |
| **Time** | amber `#D97706` (text `#92400E`) | deadlines and countdowns, nothing else |
| Danger | `#DC2626` (text `#991B1B`) | overdue, failed, destructive |

**The accent budget: teal appears at most three times in a viewport**, and only as (1) the single primary action, (2) the current-state indicator, (3) inline links. Never on icons, headings, rules, or "to feel branded" — scarcity is what makes the primary button read as *the* button.

**Neutrals carry all structure.** A border means "this has an edge". A background step means "this is a distinct surface". A shadow means "this floats and will disappear" — menus and toasts only. **Deleting every shadow from the stylesheet would change nothing about the hierarchy**; that is the test.

**Badges are tint + border + dark text**, never a saturated fill. Colour is never the only carrier of meaning — every status has words too.

## Numbers and RTL

- Persian digits in all output, **ASCII comma** as the thousands separator. Not U+066C: it renders as a faint high comma in both our faces and reads as a rendering fault, while the comma is what Iranian shops use and is bidi-safe in mixed text.
- `tabular-nums` on every price, count and code — Vazirmatn's ۱ and ۳ differ in width by over 2×, which makes any stacked column ragged without it.
- Zero renders **رایگان**, never «۰ تومان». رﯾﺎل never appears in the UI.
- The currency word is never the numeral's weight — that pairing is a reliable amateur tell.
- A promotional price shows the original beside it, struck through and muted (`.price__was`: `Muted #78716C`, `line-through`, one step down the size scale, never bold). It stays a neutral — **never amber**, which means time and only time. The promotional figure keeps the price numeral treatment; the two never share a weight.
- Inputs keep Latin digits and `direction: ltr`: in RTL the caret, Home/End and backspace all run backwards while someone types a card number, which is the bug that ends a payment.
- Logical properties only (`margin-inline-start`, never `margin-left`). Directional icons mirror; clocks, locks, phones and cards do not.

## The signature: the ledger line

Every fact-value pair is a ledger row — label at the start, value at the end, joined by a dotted leader.

```html
<div class="fact">
  <span class="fact__k">شماره سفارش</span>
  <span class="fact__lead"></span>
  <span class="fact__v latin">PS-1405-0217</span>
</div>
```

The dotted leader is the visual grammar of an invoice, a receipt, a statement — documents that exist because someone keeps records. Against a buyer's fear of paying into a void, that says "this is a real operation" in a way a trust badge cannot, because a badge can be copied and a habit of typesetting cannot. The leader is lifted `-0.28em` so it clears the ج descender.

Used **once per page region** — a second instance reads as sloppiness rather than authorship.

## Email

Email is a different medium and gets its own rules ([ADR-0023](decisions/0023-email-templates.md)). What carries over unchanged: the palette, hairlines instead of shadows, the `11px 20px 9px` optical button padding, the ledger line, `letter-spacing: 0`, 1.85 on prose, and **amber for time and only time** — the code expiry and the delivery-link expiry, nothing else.

What cannot carry over: Estedad and Vazirmatn, because Gmail and Outlook strip `@font-face`. The stack falls back to `Tahoma, "Segoe UI", "Noto Naskh Arabic", "Geeza Pro", Arial` — the metric rules survive, the faces do not. Layout is `<table>` because Outlook renders with Word's engine, and the wordmark is set in type because blocked images would show a broken box on the most trust-sensitive message the shop sends.

**The one tracking exception.** `letter-spacing: 0` holds everywhere in this system because Arabic is cursive and tracking breaks the joins. Persian *digits* ۰–۹ are isolated forms that never join, so the reason for the rule does not reach them — and a run of six bold digits in a Naskh-derived fallback face is precisely where tightness reads squat. The OTP code carries `letter-spacing: 3px`. Nothing else does, and the exception is scoped to that one element.

Templates live in `templates/email/`; `/dev/emails/` renders them in a browser while `DEBUG` is on.

## Anti-generic checklist

Run this against any new page:

- [ ] No spacing value outside the nine tokens
- [ ] Every between-group gap ≥ 2× its within-group gap; heading margins asymmetric
- [ ] Only the three radii in use; no element has border + shadow + radius together
- [ ] No `box-shadow` outside focus rings and true overlays; no gradients
- [ ] Teal ≤3 times per viewport, never on icons or headings; amber only on time
- [ ] Zero literal colour values outside the token block
- [ ] Body 17px, prose line-height 1.85, every text block capped at 32rem
- [ ] Estedad only in headings and price numerals; never both faces in one line
- [ ] `tabular-nums` on all figures; Persian digits out, Latin digits in inputs
- [ ] No `left`/`right` properties — logical only
- [ ] `:focus-visible` on everything interactive; no `transition: all`; 120–180ms; no hover `transform`
- [ ] `prefers-reduced-motion` honoured; `::selection`, `caret-color`, `accent-color` set
- [ ] One ledger group per region — no more
- [ ] No centred hero, no three feature cards under it, no rounded-up customer counts, no emoji

## Implementation notes

Fonts are self-hosted woff2 (~316KB for five files) with `font-display: swap` and a metric-matched Tahoma fallback so text does not jump when the real faces load. Tailwind's automatic source scanning is disabled (`source(none)`) and pointed only at `templates/` — left on, it read this very document and generated the utilities the document bans.
