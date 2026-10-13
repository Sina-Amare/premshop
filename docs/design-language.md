# Design Language

The visual system in force (decision: [ADR-0016](decisions/0016-brand-and-type.md)). Grounded in research into Iranian e-commerce convention, Persian typographic metrics, and the CSS-level differences between authored and generated interfaces.

## Voice — 2026-10-14c

Grounded in the FAQ and rules pages of Iranian shops already selling these products, and in Persian style guidance on محاوره‌نویسی. The owner's test: *«نه چیزی ک طرف بخونه مطمعن باشه با ai نوشتم»*.

**The register is polite-colloquial, and it is built by deleting formality markers, not by adding colloquial ones.** Cut `می‌باشد`, `می‌گردد`, `صورت می‌گیرد`, `جهت`, `لذا`, `نمایید`, `مذکور`, and every passive with `توسط`. What is left already sounds human. Only then contract: `است`→`ـه`, `را`→`رو`, `می‌شود`→`می‌شه`, `می‌دهیم`→`می‌دیم`, `اگر`→`اگه`, `می‌توانید`→`می‌تونید`.

- **Second person is always شما with ـید.** `می‌تونید`, `بزنید`, `حسابتون`. Never `ـین` (`می‌تونین`) — correct Persian, normal on Instagram, and nobody handling a customer's money writes it. Never `تو`.
- **Never `واسه`** (street register) and never `می‌باشه` (the single most illiterate form in Persian web copy — `می‌باشد` is already bureaucratic and breaking it makes it worse).
- **Orthography does not relax.** `می‌شه` not `میشه`; `رو` always separate, never `اینو`/`کدو`; clitics attach without ZWNJ (`بهتون`, `حسابتون`) except after a final ه (`شماره‌تون`); `آماده‌ست` not `آمادست`; `سفارش‌ها` not `سفارشا`.
- **One possessive axis per sentence.** `حسابتون` + `می‌تونید` ✓ · `حساب شما` + `می‌تونید` ✓ · `حسابتان` + `می‌تونید` ✗. The classic collision is `ایمیلت رو وارد کنید` — singular clitic, plural verb.
- **These stay plain whatever the rest does:** amounts and money sentences, error messages, field labels, buttons, and anything the shop could be held to (delivery windows, warranty, refunds). A failed payment is not the place for `یه مشکلی پیش اومد`.
- **Numbers, not adjectives.** `تا ۱۲ ساعت`, `۳۰ روز` — never `سریع`, `در کمترین زمان ممکن`, `پشتیبانی ۲۴/۷`. `تومان` in a price field, `تومن` in a sentence.
- **Label, do not explain** (the 2026-10-14 rule, restated because it is the one most easily lost). Verdict first; mechanism only when it is an instruction; reason never. `بله، چون …` is a machine justifying itself.
- **Admit a limitation somewhere.** The strongest non-generated signal there is: a model hedges, it never confesses. «Netflix را با آی‌پی ایران نمی‌شه باز کرد» and «سفارش نصف‌شب صبح در نوبت می‌افته» do more for trust than any badge.
- **Say no to something.** «رمز اکانتتون رو نمی‌خوایم.» «شماره کارت شخصی برای واریز نمی‌دیم.» Refusing card-to-card is the strongest substitute for a نماد اعتماد the shop does not have yet.
- **Punctuation.** No em dash `—` in body copy: it is English typography and renders as an ambiguous bar beside a Latin run in RTL. At most one `؛` per page and never in a short answer. `،` `؟` `«»` always, never the Latin `,` `?` `"`.
- **Banned as generated:** `همچنین`, `علاوه بر این`, `لازم به ذکر است`, `با خیال راحت`, `نگران نباشید`, `به راحتی`, `تنها با چند کلیک`, `تجربه‌ای بی‌نظیر`, `ما در پرم‌شاپ`; synonym pairs (`امن و مطمئن`, `سریع و آسان` — both words mean one thing); and the same-modifier triplet (`قیمت مشخص، تحویل مشخص، گارانتی مشخص`). Three facts are fine; three facts in the same grammatical shape are a tell.
- **Brand names in Latin, common nouns in Persian.** `Netflix` beside the Netflix mark is one spelling of one thing. The transliterations live in `Product.aliases` (so search finds «چت جیپت») and in SEO descriptions (so Google does). `گیفت کارت`, `اکانت`, `اشتراک` stay Persian.

**Length is measured, not felt.** Across 126 real answers on Iranian shop FAQ pages (iranicard, parspack, dicardo, irpremium), 61% are a single sentence, 87% are two or fewer, and the median is 25 words. A yes/no answer is capped here at **two sentences and 25 words**; a third puts this shop in the worst-written 12% of its market. 66% of the real ones open with a bare بله or نه.

**The test for a sentence after the first.** Name the subject of its condition and who has to act. Condition about *us* → delete it; our own behaviour was settled by sentence one. Condition about the customer, and it costs them money → that is a separate question, not a tail. The one sentence never cut is where *we* commit to money. This rule exists because an answer here ended «اگه یک‌جا رمز اکانتتون رو خواستیم، سفارش رو ادامه ندید و همون‌جا بهمون بگید» — circular, and **absent from all 126 answers surveyed**. The anti-phishing report-back loop is not a Persian convention; it was invented in this repository.

**Empty states take the blame and offer one thing.** digikala «پیدا نکردیم», basalam «چیزی پیدا نکردیم», modiseh «پیدا نکردیم» — first person plural, every time; «پیدا نشد» is the passive that puts the miss on the visitor. Then exactly one concrete next action. **Never «املا»** — that is what a teacher marks on a child's dictation, it accuses, and it is advice rather than an answer. Divar's «نگارش کلمه‌ها را بررسی کنید» survives only as a second line under a heading claiming the *exact* match is missing, with an action above it. At this shop's size the right action is the catalogue itself: a search miss prints every product, so someone who typed «چت جپت» sees ChatGPT without touching the keyboard again.

**The market's own words for what an account is.** Seats are counted in **کاربره** («۲ کاربره», «۶ کاربره») in a buy box; «نفر» is for prose. **Never «ظرفیت» for seats** — that is the PlayStation idiom, and on an AI account it means quota. Streams are «پخش هم‌زمان: ۲ دستگاه»; device *types* («Mobile / PC / TV») are the honest dodge when a number would over-promise. «نوع اکانت: اختصاصی / اشتراکی», «ریجن اکانت». A shared account is stated flatly as a definition plus one restriction, with no reassurance attached.

An FAQ answers what buyers worry about, not what the shop finds easy to state. «باید حساب بسازم؟» and «پرداخت چطور انجام می‌شود؟» are not questions anyone has; the real ones are whether the account is shared, whether they must hand over their own password, what happens when it is cut off mid-period, whether it works from an Iranian IP, and how they know the money is safe. Answers run one to three sentences.

## Amendment — 2026-10-14b: the references, applied

The owner reviewed the ink composition and asked the question the first attempt deserved: *«چرا اصلا دیزاین های نمونه ای ک گفتم رو هیچیشونو اعمال نکردی؟ پلت و انیمیشن و بک گراند پیکچر»*. It was a fair hit. All three reference systems are **light**, all three carry colour, and the answer shipped was a dark band — the one thing none of them do. These clauses supersede the matching ones in the amendment below.

- **The home page opens on light, coloured out of the goods.** Four very soft radial washes in real brand hues — Spotify green, Netflix red, Google blue, Claude clay — at a fraction of their saturation. Not an invented gradient and not a glow behind the type: it is the shelf, out of focus. Ink keeps the top strip and the footer, where it still frames the page.
- **The marks are the picture.** A loose constellation of tiles at three sizes over the field, each a link, each carrying the one shadow. A product with no mark gets a monogram tile rather than being skipped — the hero claims to list every service, so it has to.
- **Ambient motion is the constellation**, drifting a few pixels on a nine-second cycle, off below 900px and under reduced motion. Nothing else on the site moves unasked.
- **Departments are tinted surfaces, not bordered boxes.** Four pale tints with a 24px line glyph each, assigned by position so a fifth department restarts the cycle. monday.com's move, and the one place a little colour earns its keep outside the brand marks.
- **One pill, for the one opening action.** `.btn--pill` on the hero CTA and nowhere else; everything else keeps the two radii.
- **A promise strip under the hero, and the same four promises in the footer — never both on one page.** The home page suppresses the footer copy through a block, because saying the same four things twice on one screen reads as a shop with four facts padding them out.
- **A section is only worth a section if it adds something.** «مراحل خرید» explained in three cards what the promise strip, the product page and the FAQ each already said, and it is gone.

## Amendment — 2026-10-14: somewhere dark to stand

The owner reviewed the editorial turn below and rejected it: *«هیچ استایل و داینامیک بودنی نداریم … سایت خیلی خامه»*. The diagnosis was not that the system was wrong but that four things it forbade were the four things the page needed. Decision and alternatives: [ADR-0026](decisions/0026-storefront-composition.md). These clauses override the matching ones further down, including in the 2026-10-13 amendment.

- **Ink is a surface, not only a text colour.** `--color-deep #131313` carries the promise strip, the home hero and the footer, full-bleed and flat — no gradient, no glow, no blue cast, because twelve brand colours sit on top of it and must read true. Text on ink uses `--color-on-deep` / `--color-on-deep-muted` / `--color-on-deep-line`; the body ramp's `#525252` on `#131313` is about 2:1 and fails WCAG outright.
- **The primary action on ink is paper.** `.btn--invert`. Brightening teal until it passed contrast on near-black would have produced exactly the acid-accent-on-black that ADR-0016 exists to avoid.
- **Two radii, and the step between them is information.** `--radius-sm 6px` for a control you act on (button, input, chip, badge, plan, code cell); `--radius-lg 14px` for a surface that holds goods (card, tile, panel, list); `0` for anything touching a viewport edge, because a rounded corner on a full-bleed band is a lie about where the page ends. ~~One radius: 4px~~ is superseded; a single value across every element read as utilitarian rather than restrained.
- **One shadow token, one consumer.** `--shadow-lift` on `.shelf__panel` and nothing else. ~~Deleting every shadow from the stylesheet would change nothing about the hierarchy~~ is superseded: deleting it now changes exactly one element, which is the stronger claim this system can honestly make. Everywhere else, structure is still a hairline.
- **The home page opens on a band, not a sentence.** ~~No centred hero~~ stands — the hero is start-aligned like everything else — but the ban on having one does not. The composition is: ink band with one statement and one action, then a white shelf panel laid across its lower edge carrying every service as a full-colour mark, every mark a link. Nothing drifts: a horizontal strip of brand logos has meant "trusted by" for five years, the shape is read before the intent, and a moving target cannot be tapped.
- **One piece of ambient motion exists, and this is it.** The hero band's drafting grid, 4.5% white at a 34px pitch, drifting one pitch a minute. CSS only, one composited layer, zero bytes; off below 900px and under `prefers-reduced-motion`. Everything else moves only in answer to a person: `--dur-xs 90ms` for a press, `--dur 140ms` for colour and state, `--dur-lg 220ms` for anything that reveals, `--ease-out` for what enters.
- **Colour is only ever the goods.** Unchanged in intent, newly true in fact: the marks now download in their real brand colours from a source named per brand, normalised at seed time so a shelf of twelve looks like one family. Teal keeps the primary action and the current-state indicator; amber still means time and nothing else.
- **Every Latin run that comes from data is wrapped in `<bdi>`.** Product names, plan titles, spec values, emails. Without it «اشتراک Netflix.» puts the full stop on the wrong side and «(Spotify)» renders «)Spotify(» — the most common real bidi failure in a shop whose entire inventory is Latin names inside Persian sentences. The regression string lives on `/styleguide/` so the next change can be *seen* to break it.
- **Label, do not explain.** The owner's correction, and the one most easily lost: a Persian shop writes «ضمانت بازگشت وجه» on a promise row, not «اگر سفارش تحویل نشود، وجه برمی‌گردد». Promises, badges and section heads are **noun phrases of two to four words**; buttons are verbs that name what happens; a step's body carries the fact its title does not, never a restatement of it. Explanation is not banned — it is *located*: the FAQ page and the product page's own questions are where a sentence belongs, and nowhere else is. When a line on a shop surface is longer than about eight words, it is almost always a sentence that wants to be a label.
- **Fonts are subset, and all four faces that paint above the fold are preloaded.** `scripts/subset_fonts.py` cut 313 KB to 129 KB with no change to shaping. Estedad gained a measured metric fallback (`size-adjust: 76.5%`, Tahoma), which took the home page's CLS from 0.027 to 0.000 — the hero heading used to measure 109px in the fallback against 73px in Estedad, wrap to a third line, and drop the page 45px when the real face arrived.

## Amendment — 2026-10-13: the editorial turn

The owner reviewed the built shop and asked for a quieter, more professional surface, supplying an editorial reference (white canvas, hairline rules, 4px corners, headlines set light). The direction taken is hybrid, and these clauses override the matching ones above:

- **The canvas is white** (`--color-ground: #ffffff`) and the neutral ramp is neutral, not warm stone. The page should read as a printed sheet.
- **A hairline rule, not a box, marks a region.** Section heads carry a 1px rule the width of the column. Boxing a region to announce it is a region is the habit this replaces.
- **One radius: 4px.** `--radius-md` and the pill radius are gone; chips, cards, tiles, inputs and buttons all take the same slight corner.
- **Headings are set at 600, never 800**, and the scale came down: h1 30px, h2 21px, h3 18px. Authority comes from space and restraint, not from weight. **The price numeral keeps 800** — it is the one figure the page exists to state.
- **Colour belongs to the goods.** Product brand marks render in their own brand colours; the interface around them stays achromatic. Teal survives as the single primary action and inline links; amber still means time and nothing else.
- **Two motion tokens:** `--dur` 140ms for colour and state, `--dur-lg` 220ms for anything that moves or reveals. The easing is `cubic-bezier(0.2, 0, 0, 1)` — a soft stop, no overshoot, no bounce.

Not taken from the reference: its typefaces. Almarai is an Arabic face whose ک and ی are wrong for Persian, and Google-hosted fonts do not load from Iran at all. Estedad and Vazirmatn stay, self-hosted.

## The stance

This shop should read like **a small business that keeps records** — not like a brand, and not like a landing page. The buyer's fear is paying into a void, so the antidote is visible bookkeeping: hairlines, aligned figures, stated hours, exact amounts, and a payable typeset with more care than any headline on the site. Warm stone paper, one teal accent spent sparingly, amber only when a clock is running.

## Type

| Role | Face | Weight | Size | Line-height |
|---|---|---|---|---|
| Hero h1 (home) | Estedad | 600 | 40px (26 on a phone) | 1.28 |
| h1 | Estedad | 600 | 30px | 1.3 |
| h2 | Estedad | 600 | 21px | 1.4 |
| h3 | Estedad | 600 | 18px | 1.45 |
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
| Ground / Surface | `#FFFFFF` | the page, and every card on it |
| Deep | `#131313` | the bands the page opens and closes on; text on it uses the on-deep ramp |
| Fill subtle | `#F5F5F4` | hover fills, quiet panels |
| Line / Line strong | `#E7E5E4` / `#D4D4D4` | hairlines; borders that must be seen |
| Muted / Secondary / Ink | `#737373` / `#525252` / `#171717` | text ramp |
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

## The catalog

**The product card** is the tile, the words, and the price on its own line at the block-end, so a grid of them reads as one column of aligned figures. The tile is an icon on tinted stone, or a monogram when there is no image — the same shape either way, so a grid with and without pictures stays even. The whole card is the link. Region sits on the card as a neutral badge; it is a fact, not an alert.

**The buy box** structures by hairline. The selected plan is the accent's one appearance in it; the promotional original sits struck through and muted beside the new figure, never amber. On a phone the page reads title → buy box → details, so the price is never below the FAQ.

**Chips** for a filter: the active one takes the accent as the current-state indicator, which is one of its three permitted uses. Step numbers, category counts and everything else that merely wants to look branded stay in ink.

## Email

Email is a different medium and gets its own rules ([ADR-0023](decisions/0023-email-templates.md)). What carries over unchanged: the palette, hairlines instead of shadows, the `11px 20px 9px` optical button padding, the ledger line, `letter-spacing: 0`, 1.85 on prose, and **amber for time and only time** — the code expiry and the delivery-link expiry, nothing else.

What cannot carry over: Estedad and Vazirmatn, because Gmail and Outlook strip `@font-face`. The stack falls back to `Tahoma, "Segoe UI", "Noto Naskh Arabic", "Geeza Pro", Arial` — the metric rules survive, the faces do not. Layout is `<table>` because Outlook renders with Word's engine, and the wordmark is set in type because blocked images would show a broken box on the most trust-sensitive message the shop sends.

**The wordmark is `PremShop`, in Latin, everywhere** (owner's ruling at the S2 review): site header, footer, email sender name, subjects, masthead. One name, one spelling; the Latin form is the anchor in an inbox full of Persian subjects. Persian prose refers to the shop the same way.

**The code cells show Latin digits.** Six isolated cells in an LTR row; what is typed is what is shown, and Latin in inputs is this system's rule. Persian digits are accepted on the way in and normalised.

**The one tracking exception.** `letter-spacing: 0` holds everywhere in this system because Arabic is cursive and tracking breaks the joins. Persian *digits* ۰–۹ are isolated forms that never join, so the reason for the rule does not reach them — and a run of six bold digits in a Naskh-derived fallback face is precisely where tightness reads squat. The OTP code carries `letter-spacing: 3px`. Nothing else does, and the exception is scoped to that one element.

Templates live in `templates/email/`; `/dev/emails/` renders them in a browser while `DEBUG` is on.

## Anti-generic checklist

Run this against any new page:

- [ ] No spacing value outside the nine tokens
- [ ] Every between-group gap ≥ 2× its within-group gap; heading margins asymmetric
- [ ] Only `--radius-sm` and `--radius-lg` in use, and 0 on anything full-bleed
- [ ] No `box-shadow` outside focus rings and `--shadow-lift` on the shelf panel; no gradients
- [ ] Teal ≤3 times per viewport, never on icons or headings; amber only on time
- [ ] Zero literal colour values outside the token block
- [ ] Body 17px, prose line-height 1.85, every text block capped at 32rem
- [ ] Estedad only in headings and price numerals; never both faces in one line
- [ ] `tabular-nums` on all figures; Persian digits out, Latin digits in inputs
- [ ] No `left`/`right` properties — logical only
- [ ] `:focus-visible` on everything interactive; no `transition: all`; 120–180ms; no hover `transform`
- [ ] `prefers-reduced-motion` honoured; `::selection`, `caret-color`, `accent-color` set
- [ ] One ledger group per region — no more
- [ ] Nothing centred, no three feature cards, no rounded-up customer counts, no emoji
- [ ] Every promise, badge and section head is a noun phrase, not a sentence; explanation lives in the FAQ
- [ ] Every `{{ product.name }}`, `{{ plan.title }}` and user value wrapped in `<bdi>`
- [ ] Nothing drifts, slides or fades except the hero's grid; motion answers an action

## Implementation notes

Fonts are self-hosted woff2, subset by `scripts/subset_fonts.py` (129KB for five files, from 313KB) with `font-display: swap` and a metric-matched Tahoma fallback so text does not jump when the real faces load. Tailwind's automatic source scanning is disabled (`source(none)`) and pointed only at `templates/` — left on, it read this very document and generated the utilities the document bans.
