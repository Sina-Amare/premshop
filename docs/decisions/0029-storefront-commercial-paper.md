# ADR-0029 — The storefront as commercial paper: a rate sheet and a پیش‌فاکتور on warm paper, tile art beside them

**Status:** Accepted · 2026-10-20 · Supersedes [ADR-0026](0026-storefront-composition.md)

**Context.** The storefront of ADR-0026, as amended on 2026-10-14b, opened on a light field washed in four brand hues, with the marks floating over it as a constellation of tiles. Read again on 2026-10-19, the floating marks and the colour washes were what made the site look generated, and the home page had no price in its first view. The owner had asked on 2026-10-14 for «پلت و انیمیشن و بک گراند پیکچر», and on 2026-10-19 for a more ambitious design. Both requests are about colour, motion and picture, not about missing information.

Three directions were built as mock-ups, each the home page and the ChatGPT Plus page at 390 and 1440px with the real fonts, marks, names and prices, and scored by a business and brand review and by a technical, accessibility and right-to-left review:

| Direction | Idea | Business (of 30) | Technical (of 30) | Distinct from templates (of 5) | Fits the owner's feedback (of 5) |
|---|---|---|---|---|---|
| A, «دفتر» | a dated price list of every plan as the home page, ruled like a ledger, on the tokens of the day | 23 | 27, its pick | 4 | 2 |
| B, «راهنمای خرید» | products arranged by the job the buyer has; the product page a file with a summary, answers and a comparison | 26 | 18 | 3 | 3 |
| C, «نرخ‌نامه» | Iranian commercial paper as the identity: a rate sheet, a پیش‌فاکتور with the amount in words, a dated seal, crimson in place of teal | 25, its pick as the base | 18 | 5 | 4 |

The last two columns are the business review's. Each review wrote down when its own pick would be wrong. The technical review: if «خیلی خامه» meant missing colour and effect rather than an empty page, the Ledger is the wrong choice, because it is the direction nearest to the build that was rejected. The business review: if the owner meant missing motion and picture, the rate sheet answers that no better than the other two. Both conditions hold.

The combination below was then built as one prototype on the real catalogue and reviewed at 320, 390, 768, 1024 and 1440px, with hover and focus, reduced motion and 200% zoom, for brand, Persian type and right-to-left, accessibility, conversion and trust, and buildability, including layout shift and the largest paint on a throttled phone. It passed that review before any template changed.

**Decision.** «نرخ‌نامه‌ی فیروزه‌ای»: C's identity, B's decision content, A's technical discipline, and a palette, an illustration system and one motion that none of the three had. The rules in full are `docs/design-language.md`, "Amendment — 2026-10-20".

1. **The shop is a set of commercial papers.** A dated rate sheet (نرخ‌نامه), a پیش‌فاکتور with the total in words, tables with «ردیف» and «شرح». Large Persian numerals in Estedad 800 are the darkest marks on a page. A sheet is dated with the oldest price confirmation among the plans it shows, so one fresh row cannot make a stale list look current. Where money or a decision is, there is only paper and figures.
2. **Turquoise and lapis on warm paper**, from Iranian tilework, deepening the approved teal rather than replacing it ([ADR-0016](0016-brand-and-type.md), amended 2026-10-20): turquoise for what a person does and what they chose, lapis for the record, glaze only in the art, amber only for time.
3. **Tile art, drawn in house.** An eight-pointed star and a cross on a square lattice, inline SVG coloured from the tokens, in the tables' 1 and 2px line weights. It stands beside text and never under it, and it never appears inside a rate sheet, an invoice, a receipt, a form, search or the panel, or in place of a brand mark.
4. **One motion that starts by itself**, on the home page: a slow glint across three glaze stars, opacity only, from 900px, only while the hero is on screen, with a «توقف حرکت» control that works without script, and none at all under reduced motion. Everything else answers a person. Nothing ticks: the server writes a deadline's time left in days and hours, or hours and minutes, never seconds, and prints the fixed end time beside it.
5. **The rules of the paper replace ADR-0026's radii and shadow.** 2px ink opens and closes a document, hairlines run between rows, and a double lapis rule sits only under the amount paid. There is no shadow: `--shadow-lift` is gone. Paper corners are 2px (`--radius-lg`, 14px, became `--radius-sheet`), controls keep 6px, and anything that meets the viewport edge has none.
6. **Ink frames the page.** The top strip and the footer stay `--color-deep`. The strip says only what is true of every product, the support hours and the Telegram handle, from the one source the footer reads as well.
7. **The same five faces, each preloaded on every page, with `font-display: optional`** over fallbacks fitted per platform (Tahoma on Windows, Noto Naskh Arabic UI and Noto Sans Arabic UI on Android), so text never moves once it has painted.

**Alternatives considered.**

- **The Ledger (A) as the base**, the technical review's pick and the cheapest path. It is the nearest direction to the white ruled build the owner had called «خیلی خامه», nearly monochrome, and it scored 2 of 5 on fit with the owner's feedback, the lowest of the three. Its discipline carries over: the token names, `<bdi>` on every Latin run, the phone line-break rules, the name a screen reader gives each link, `@supports` fallbacks, the S4b form contract, and every plan on the home page's rate sheet.
- **The rate sheet (C) unchanged.** Its crimson sits beside the error red and could read as an imitation of Digikala, and its dated seal could be taken for an official trust seal in a shop that has none; a seal that imitates one is never built. Also left out of C: the 128px «۱۰ تا ۲۲», which is a landing page's big-number pattern, and fixed-width price columns, which an eight-digit price overflowed by 21px from 768 to 1440px. Column widths come from their content.
- **The buying guide (B) as the base.** Its strength is its content, which sits on any base and is kept. Its shell, a rounded, shadowed four-column panel with pastel column heads and accordion arrows, reads first as a SaaS pricing table, and an arrow promises an accordion where each row is only a jump link.
- **«فیروزه‌ای و مرکب»: lapis's jobs handed to ink**, with links in ink and underlined as in the Ledger, built beside the proposal on the home and ChatGPT Plus pages at 390 and 1440px. Without lapis, the band over a document and the double rule under the total become more black rules among the ink ones, and the paper is back to the near-monochrome the owner rejected. The risk it guarded against, lapis reading as a bank's colour, is met instead by keeping lapis off broad surfaces and buttons.
- **The top of the home page without art or motion**, built once at the same widths to see whether they earn their place. It is the same page without the two things the owner asked for, and the glint is cheap to remove if it is later found to add nothing.
- **A seconds countdown under a day**, as the previous clock had. Text that rewrites itself every second is moving content with no way to stop it (WCAG 2.2.2). Writing the text from a script at load moved the deadline line by up to 0.058 CLS at 360px, so the server writes it and no script touches it until the tab is shown again.
- **Tabular figures where prices stack.** Both faces' tabular digits space the dot-like Persian zero so wide that `۱,۳۹۰,۰۰۰` reads letter-spaced. Digits are proportional and a money column aligns on its units digit, which is the start side of a right-to-left table; the sign-in code's cells are the one tabular run.
- **`font-display: swap`**, as under ADR-0026. Counted in full on an emulated phone (CPU x4, 1.6 Mbps, 150 ms round trip, cache off), it moved the page in 12 and 14 of 90 first visits in two passes, the invoice column by up to 44px, and in 41 of 45 at CPU x2. With `optional`, no load scored over 0.004 in 225 at either setting. Hiding the page until the faces arrive, and shipping them inside the stylesheet as data, were measured as well: at CPU x2 neither brought the web faces into a single first visit, and both painted later.

**Why.** The buyer's fear is paying into a void, and this answers it with visible bookkeeping, as the system's first stance did: a dated price list, a pre-invoice with the total in words, the support hours on every page. Commercial paper is familiar to an Iranian buyer and appears in no Western template, which is why C scored 5 of 5 on distinctiveness. The owner's request for colour, motion and picture is answered from one motif, the tile, and the motif is kept off everything that carries money or a decision, so the pages that sell stay as plain as a receipt.

**Assumptions.**

- The brand marks may be used nominatively by a reseller, as ADR-0026 assumed.
- Lapis does not read as a bank's colour while it stays on lines, links and art. The tile does not read as a souvenir: no arabesque, calligraphy, domes, carpets or gold, and the pattern is seen large and cut, like the wall of a building, never small and repeated like wrapping paper. The paper does not read as bureaucratic: it is commercial paper only, with no seal and no «شماره/تاریخ/پیوست» block.
- The catalogue stays under about 20 products, the size at which every plan of every product still fits the home page's rate sheet.
- The owner accepts the cost of `optional`: on a slow first visit a page keeps the fitted fallback, and the next page has the web faces in cache.
- The fitted Android faces resolve on a real phone. This has not been checked on a device, and iOS's SF Arabic is not fitted.
- Until the owner reads the built pages, no Persian-speaking reader has checked them, and no real phone or screen reader has either.

**Revisit when.**

- A reader takes the tile for a souvenir: the art goes and the system stands, because the identity is in the paper.
- Lapis reads as a bank's colour: «فیروزه‌ای و مرکب» is a change to the token block alone, since the illustrations take their colours from the tokens and no template or image is redrawn.
- The catalogue passes about 20 products: the home page would show each product's cheapest plan and send the rest to its category page, a rule not yet designed or tested.
- Too many first visits read in the fallback, or a device check fails: `swap` over a fallback fitted so closely that nothing moves, proven on a real Android phone, with SF Arabic fitted on an Apple machine.
- An e-namad seal is granted: it needs a white card and will change the footer.
- The glint is found to add nothing: removing it is one rule.

**Carried over from ADR-0026: compression and secrets.** `GZipMiddleware` makes BREACH possible wherever a secret and attacker-controlled text share a response. That shape exists on `/search/?q=…` for a signed-in visitor; Django's per-request CSRF token masking defuses it, and nothing else secret is rendered, because ADR-0008 keeps codes and credentials out of every page. **This stops being true at S5**, when an order page wants to show a delivered credential: that view must opt out of compression or keep the credential out of the HTML.

**Consequences.** `docs/design-language.md` carries the clause-by-clause amendment and says which older clauses it replaces. Two tokens were renamed (`--radius-lg` to `--radius-sheet`, `--hdr-h` to `--header-h`) and one deleted (`--shadow-lift`). What ADR-0026 decided about the goods stands: brand marks are sourced per brand and normalised at seed time, and the fonts are the same subset files.

**Amendment — 2026-10-20 (as built).** The templates follow the decision above, with these differences found once they were built:

- **No «شرح» column.** A product's sections are the operator's Markdown (ADR-0028), so the prototype's «ردیف / شرح» table of what a plan includes was not built. «ردیف» heads the questions ledger on the home and questions pages, and the پیش‌فاکتور labels its rows by what they hold: سرویس, مدت اشتراک or مبلغ کارت, تعداد, تحویل, از شما می‌پرسیم, جمع.
- **Until S4b the پیش‌فاکتور does not take the order.** Its button opens Telegram, and the line on payment shows the owner's pending decision; S4b puts its submit button in the link's place, with the form's contract unchanged.
- **A category of one product** has no comparison beside its rate sheet, so the sheet keeps the narrow 760px measure instead of leaving the comparison's column empty.
- **Link previews are per product.** Every page's `og:image` is `og-default.png`, and each public product has its own `og-<slug>.png` with its name and mark, drawn by `scripts/render_og.py` with the tile and no price (`docs/development.md`).
- **Every Latin run is `<bdi lang="en">`**, so a screen reader reads a name in its own voice; a whole name or plan title stays one isolate (`|isolate`).
- **Red's tint is set by hand**, a cool pink apart from the time tint, as amended in [ADR-0016](0016-brand-and-type.md).
