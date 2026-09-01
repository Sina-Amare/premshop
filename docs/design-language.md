# Design Language

The chosen visual system (decision: [ADR-0016](decisions/0016-brand-and-type.md)). The site's biggest conversion obstacle is customer doubt; the typography and palette exist to read as an established Iranian business — composed, specific, unhurried. Not a template, not a poster.

## Type

| Role | Face | Weights shipped | Notes |
|---|---|---|---|
| Body | **Vazirmatn** (OFL) | 400 · 500 · 700 | the Persian-web workhorse; fallback stack `Vazirmatn, "Segoe UI", Tahoma, sans-serif` |
| Headings / display | **Estedad** (OFL-1.1) | 600 · 800 | contrast through skeleton + weight, same modern idiom as the body |

Self-hosted woff2 only — never a font CDN (Iranian clients can't reach them). `font-display: swap`. Vendor each family's `OFL.txt` beside the files.

## Scale (Persian-adjusted per working agreement §9)

| Token | Size / line-height | Face & weight |
|---|---|---|
| body | 17px / 1.9 | Vazirmatn 400 |
| body-sm | 15px / 1.8 | Vazirmatn 400 |
| h4 / h3 | 21px · 26px / 1.5 | Estedad 600 |
| h2 / h1 | 33px · 41px / 1.4 | Estedad 800 |
| panel body | 14–15px / 1.6 | Vazirmatn 400/500 — the panel is a tool; denser |

## Palette

One neutral ramp, one accent, semantic status colors — nothing else. All pairs pass WCAG AA for their use.

| Role | Value | Use |
|---|---|---|
| Ground | stone-50 `#FAFAF9` | page background (warm neutral, deliberately not template blue-gray) |
| Ink | stone-900 `#1C1917` | text |
| Muted | stone-500 `#78716C` | secondary text; borders at stone-200/300 |
| **Accent** | teal-700 `#0F766E` | links, primary buttons, active states; hover teal-800 `#115E59` |
| Success | green-600 `#16A34A` | DELIVERED, payment confirmed — always with text, never color alone |
| **Warning** | amber-600 `#D97706` | **time meaning only**: SLA <6h, AWAITING_INPUT, payment window. The one loud thing in the panel |
| Danger | red-600 `#DC2626` | overdue, failed payment, destructive actions |

If a new color "is needed," the design is drifting. If amber ever means anything but time, the operator's trained eye is lost.

## Rules (encoded as base CSS from S1)

- No `letter-spacing` on Persian, ever. No italics. No uppercase transforms.
- Persian digits in prices/dates (via core template filters); Latin digits in inputs and technical IDs. Thousands separators everywhere. Jalali display only; Gregorian storage.
- Thin borders over big soft shadows; small, consistent radius; motion = state transitions and interaction feedback only — no entrance animations.
- RTL from the root (`dir="rtl"`, logical CSS properties); directional icons mirrored, non-directional ones not; Latin-in-Persian isolated with `unicode-bidi: isolate`.
- The wordmark is پرم‌شاپ in Estedad 800, accent-colored, until a real logotype exists.
- Two registers: public site = trust-building, prices and terms unmissable; panel = dense working tool, no decoration, the only loud element is time-remaining.

## Implementation checklist (S1 gate items)

- [ ] Exact woff2 files from each family's release assets; total payload target ≤ ~350KB for all five files
- [ ] `OFL.txt` vendored per family
- [ ] Rendering check: Windows Chrome, Android Chrome, one iOS Safari (vertical metrics, نیم‌فاصله joins)
- [ ] Amber-on-stone contrast verified in the panel badge component
- [ ] Palette exposed as CSS custom properties; Tailwind theme maps to them; no raw hex in templates
