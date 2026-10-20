# Design Language

The visual system in force is the 2026-10-20 amendment directly below (decisions: [ADR-0016](decisions/0016-brand-and-type.md), [ADR-0029](decisions/0029-storefront-commercial-paper.md)). The sections after it are kept as they were written and stand wherever it does not replace them. Grounded in research into Iranian e-commerce convention, Persian typographic metrics, and the CSS-level differences between authored and generated interfaces.

## Amendment — 2026-10-20: «نرخ‌نامه‌ی فیروزه‌ای»

The storefront becomes a set of the commercial papers an Iranian buyer already trusts: a dated rate sheet, a پیش‌فاکتور with the total in words, the questions as a ledger under «ردیف». Beside the papers, never under them, stands a tile of turquoise and lapis, and the site's colour, its pictures and its one self-starting motion all come from that tile. Wherever money or a decision is, there is only paper and figures. Decision and alternatives: [ADR-0029](decisions/0029-storefront-commercial-paper.md); the palette: [ADR-0016](decisions/0016-brand-and-type.md), amended the same day. These clauses supersede the amendments of 2026-10-14b, 2026-10-14 and 2026-10-13 and the matching parts of the sections after them; what they do not mention stands. The two Voice sections are untouched.

### Colour

| Token | Value | Role | Measured contrast |
|---|---|---|---|
| `--color-ground` (paper) | `#F6F4EF` | the page | ink 15.80:1 |
| `--color-surface` (sheet) | `#FFFFFF` | the rate sheet, the پیش‌فاکتور, forms | ink 17.36:1 |
| `--color-fill-subtle` | `#EFEBE4` | a row under the pointer, a disabled control | ink 14.61:1, muted 5.32:1 |
| `--color-line`, `--color-line-strong` | `#E2DDD3`, `#CEC7BA` | hairlines and dotted leaders; decoration, never the only edge of a control | 1.23:1 and 1.53:1 on paper |
| `--color-control` | `#857D70` | the edge of anything a person operates: search, a field, a plan, the stepper | 3.70:1 on paper, 4.07:1 on a sheet, 3.42:1 on a hovered row |
| `--color-muted` | `#655F56` | labels, the struck price, the total in words | 5.75:1 on paper, 6.32:1 on a sheet, 5.40:1 on a chosen plan |
| `--color-secondary` | `#46423B` | second-level text | 9.09:1 on paper, 9.99:1 on a sheet |
| `--color-ink` | `#1C1A17` | text and the heavy rules | as above |
| `--color-deep` | `#171614` | the top strip and the footer | `--color-on-deep` 16.01:1, `--color-on-deep-muted` 8.33:1 |
| `--color-accent` (turquoise) | `#0A6A6E` | the primary action, the chosen plan, the current category | white text 6.36:1; as text on paper 5.79:1; as an edge on its tint 5.44:1 |
| `--color-accent-hover`, `--color-accent-active` | `#085A5E`, `#064A4E` | pointer and press on the primary action | white text 7.97:1 and 10.01:1 |
| `--color-accent-tint` | `#E3F0EE` | behind a chosen plan; the tint cells of the art | ink 14.85:1 |
| `--color-lapis` | `#233D86` | a document's band, the double rule under the amount paid, text links, the focus ring, the lines of the art | 9.17:1 on paper, 10.08:1 on a sheet, 8.62:1 on a chosen plan |
| `--color-lapis-underline` | `#7786B0` | a link's underline at rest; on hover it takes the link's own lapis | 3.28:1 on paper, 3.61:1 on a sheet |
| `--color-glaze` | `#34A7A9` | the illustrations, and nothing else | 2.64:1 on paper: never text, never an edge |
| `--color-time`, `--color-time-text` | `#D97706`, `#92400E` | time left, and nothing else | text 5.76:1 on its tint, 6.45:1 on paper; the bright amber is 2.90:1 and never carries text |
| status green and red | `#16A34A` / `#166534`, `#DC2626` / `#991B1B` | delivered or confirmed; overdue, failed, destructive | red text 7.56:1 on paper and 6.68:1 on its tint; green text 5.88:1 on its tint |

Measured with the WCAG 2.2 formula on the values in `static/css/input.css`: text needs 4.5:1, a control's edge and a focus ring 3:1, and every pair above that carries text or an edge passes. The tints (`--color-time-tint`, `--color-time-edge`, `--color-ok-tint`, `--color-selection`) are `color-mix(in oklab, X n%, paper)` worked out to hex so older browsers get them too; a changed base colour means recomputing them. `--color-danger-tint` is the exception, `#F9E1E1` set by hand: mixed into the warm paper, the red came out 3/255 from the time tint in every channel, so a refused code or an unavailable plan read as a deadline.

- **Turquoise is what you do and what you chose.** The budget stands: at most three times in a view, never on a heading, an icon or a rule.
- **Lapis is the record.** It is never a broad background, a button or a fill. Links in running text carry the underline at rest. The focus ring is lapis, 2px at a 2px offset, so focus is never mistaken for a turquoise choice; on the ink strip, the footer and the skip link it is white.
- **Glaze lives only in the illustrations**, and the illustrations hold no yellow or ochre, so nothing on a page comes near amber.
- **Colour is no longer only the goods.** Brand marks keep their own colours beside a product's name; the interface around them now has the tile's two colours as well, each with one job.

### Paper and frame

- **Rules of the paper.** 2px ink opens and closes a document, 1px ink sits under column heads, hairlines run between rows, and dotted leaders join a label to its value. A 4px lapis band tops a document the shop issues, and a double lapis rule sits only under the amount paid.
- **Corners:** 2px on paper (`--radius-sheet`), 6px on a control (`--radius-sm`), 0 on anything that meets the viewport edge. This replaces 2026-10-14's 14px surface radius.
- **No shadow.** `--shadow-lift` is gone with the shelf it served. `box-shadow` appears only inset: to double the edge of a chosen plan or a field in error, and to draw a chosen plan's dot.
- **A list of goods is a rate sheet**, one row per plan under its product's name, never a grid of cards; the related products at the end of a product page are rows too. This replaces the product card under "The catalog".
- **The buy box is a پیش‌فاکتور**: a sheet under the lapis band, its total in Estedad 800 over the double rule with the amount in words beneath, every figure built on the server. This replaces the buy box under "The catalog".
- **Ink frames the page.** The top strip and the footer are `--color-deep`, full-bleed. The strip says only what is true of every product, the support hours and the Telegram handle, from the one source the footer reads too. The header and the category row stick together from 761px, and every in-page jump and sticky column clears them (`--frame-h`).
- **Spacing** keeps its nine values. The column is 1120px, with 24px gutters, 16px on a phone.

### Type

| Role | Face | Size, desktop / phone (760px and under) | Token |
|---|---|---|---|
| Home headline | Estedad 800 | 34 to 60px with the viewport | `--text-poster` |
| Total of a پیش‌فاکتور | Estedad 800 | 44 / 36px | `--text-total` |
| Price on a rate sheet or a plan | Estedad 800 | 24px from 1024px, 20px below | `--text-price` |
| Step number | Estedad 800 | 28px | `--text-step` |
| Page title | Estedad 600 | 34 / 30px | `--text-h1` |
| Section, subsection | Estedad 600 | 25px, 22px | `--text-h2`, `--text-h3` |
| One-line summary, the line under a title | Vazirmatn 400 | 19 / 17px | `--text-lead` |
| Text, line-height 1.85 | Vazirmatn 400 | 17px | `--text-body` |
| Buttons and interface | Vazirmatn 500 | 15px | `--text-ui` |
| Labels, the total in words, footnotes | Vazirmatn 400 and 500 | 13px, 12px | `--text-small`, `--text-micro` |

- **Estedad 800 is for figures.** Prices, totals and step numbers are the darkest marks on a page, and the home headline is the one heading that shares their weight. Other headings stay 600 so they do not compete with the figures; Vazirmatn 700 is emphasis.
- **Heading sizes are set for what Estedad draws.** At `size-adjust: 82%` a 25px h2 draws like 21px of Vazirmatn and a 22px h3 like 18px, so a heading never reads smaller than the 17px text under it, and a phone's 30px page title draws 1.2 times its h2s. This replaces the type table below.
- **The home headline is one line** in the web face at every width: its size is capped at 9.6% of its column, which «اشتراک دلاری، پرداخت تومانی» fills to at most 95%. In a wider fallback it breaks into two balanced lines rather than pushing the page sideways.
- **Digits are proportional everywhere**, prices, totals and deadline badges included: tabular digits space the dot-like Persian zero so wide that `۱,۳۹۰,۰۰۰` reads letter-spaced. Money in a column aligns on its units digit, which in a right-to-left table is the start side (`.num`). The sign-in code's cells are the one tabular run. This replaces "`tabular-nums` on every price, count and code".
- **The struck price** is muted and one step down, inside `<del>` with a hidden «قیمت قبل» for a screen reader, and its line runs above the dot-like Persian zero, not through it.

### Faces and their fallbacks

- **The same five files**, subset and self-hosted: Vazirmatn 400, 500 and 700, Estedad 600 and 800. `base.html` preloads all five on every page, because each paints in the first screen.
- **`font-display: optional`.** A face that has not arrived by the first paint stays out for that page view, so text never moves once it shows. The cost: a slow first visit reads in the fallback, and the next page has the faces in cache. The measurements behind it are in ADR-0029.
- **Fifteen fitted fallbacks**, one for each web face and weight on each platform: Tahoma on Windows («Vazirmatn Fallback», «Estedad Fallback»), Noto Naskh Arabic UI («Vazirmatn Naskh», «Estedad Naskh») and Noto Sans Arabic UI («Vazirmatn Noto», «Estedad Noto») on Android. Each `size-adjust` is the width of the shop's own Persian text in the web face over its width in the fallback, weight by weight, and the ascent and descent overrides give every fallback the web face's line, so a page in the fallback wraps as it would in the web face. `local()` matches a font's full or PostScript name only («Noto Naskh Arabic UI Regular», `NotoNaskhArabicUI-Regular`); a name that matches nothing skips the face without a word. iOS's SF Arabic is not fitted, and the Android names have not been checked on a real phone.
- This replaces 2026-10-14's metric fallback and the `swap` in the implementation notes.

### Tile illustrations

- **Geometry.** An eight-pointed star and a cross from tilework, on a square lattice. Lines in the tables' weights: a 2px lapis key line and 1px inner lines. Fills are paper, the turquoise tint and glaze; lapis is never a fill. Stars carry the glaze, and a cross is tinted only where it sets a glaze star, so no cross stands alone as a plus sign.
- **Walls**, on the home page's top and a category's head: one lattice, hung from the top edge and from the edge beside the text through star centres, so every cut ends on star tips, standing on a lapis line (on the home page, the rate sheet's band carried out to the viewport edge). Below 900px on the home page and 761px on a category, the wall becomes a close-up of itself, half a period tall and two to two and a half stars across: never a frieze of small repeats.
- **Spot art** for an empty state that has no list to show, and for the error pages; on the 404 a star has fallen out of the pattern. The top band of an email is drawn in table cells, never an image, and the 1200×630 Telegram preview carries a cut of the pattern and never a price.
- **Never** under any text; never inside a rate sheet, a پیش‌فاکتور, a receipt or anywhere else money is; never in a form, in search or in the panel; never under a control; never in place of a brand mark. An empty search and an empty category have no picture: the full rate sheet under their one line is the answer.
- **Made in house**, by hand, as SVG partials in `templates/svg/`, included inline. No `<style>`, filter, gradient, bitmap or text inside; `aria-hidden="true"`; each under 8 KB gzipped. Colour comes from the `.tile-*` rules in `input.css`, which read the tokens, so a palette change redraws nothing. A change to one is reviewed like code and shot at every width.
- **Neither a souvenir nor generated.** No arabesque, calligraphy, dome, arch or minaret, carpet or miniature, gold or aged texture: the pattern is seen large and cut, like the wall of a building, never small and repeated like wrapping paper. No generated image, stock photo or trace, no blurred blob, glow, glass, 3D or noise. Only the tile's two colours and paper.

### Motion

- **Feedback**, with three durations: `--dur-xs` 90ms for a press, `--dur` 140ms for colour and state, `--dur-lg` 220ms with `--ease-out` for what moves or reveals. A changed total travels 4px (`--shift`) and is announced politely, after the buyer's own action or when a discount ends on an open page. The total is the پیش‌فاکتور's one live region, so a change is announced once.
- **The one motion that starts by itself** is the home page's glint. Over three whole glaze stars of the row that carries the headline, a tint copy rises to half opacity and falls back, one star at a time toward the headline, one every 6s (`--dur-ambient`, 18s for the round). Opacity only; nothing travels. It runs from 900px, only when the visitor has not asked for reduced motion, and only while the hero is on screen (`frame.js` pauses it off screen).
- **The pause control**, «توقف حرکت», sits on paper at the top of the text column, against the wall's edge, never on the art. Its checkbox covers the whole label and stops the glint through `:has()`, without script (WCAG 2.2.2). A browser without `:has()` gets no glint at all, so the motion can always be stopped, and the control exists only where the motion runs.
- **Reduced motion:** no glint and no control; every animation and transition cut to 0.01ms, with an infinite one capped at a single run; a changed total swaps without travelling; an in-page jump is not glided.
- **Never:** an entrance or scroll animation, parallax, a lift or scale on hover, anything floating, a ticking number, `scroll-behavior: smooth` (the browser's own scroll to a focused control would race `frame.js` and leave the control under the sticky header). This replaces 2026-10-14's drafting grid and 2026-10-14b's drifting constellation.

### Amber means time pressure

- **Amber marks time left and nothing else:** a promotion's deadline, a sign-in code's validity, and in the panel the time remaining. A warning that is not about time, a stale price and a struck price are never amber. Nor is an error's ground: the danger tint is a cool pink, apart from the time tint, so the two never share a fill.
- **A deadline never ticks.** The server writes its text, days and hours, or under a day hours and minutes, never seconds, each number joined to its unit by a no-break space, and prints the fixed end time beside it; the page writes it again only when the tab is shown again. At the end the badge goes, its line says «تخفیف تمام شد.», and every price with that end becomes the figure the server built for after it. Discounts that share one end show one deadline, not one per row. This replaces the clock described under "Numbers and RTL".

### The panel

The panel is a tool for daily work. It takes the tokens, not the paper or the tile.

- **Colour:** Unfold's primary is built from turquoise; saving is turquoise and deleting is red. Unfold's own neutral ground stays, and no lapis band, illustration or poster size enters the panel.
- **Amber** is time remaining and the only loud thing on a panel page; a promotion's countdown is amber. A stale price or claim is never amber, only heavier.
- **Figures:** price columns in Estedad 800 with proportional digits, aligned on the units digit, as in the shop.
- **Density:** compact rows and no decorative space.

### Checklist, as of this date

It replaces the anti-generic checklist at the end of this document:

- [ ] No colour, size, space, radius, border or duration outside the token block; logical properties only
- [ ] Turquoise at most three times in a view, never on a heading, icon or rule; lapis never a background, button or fill; glaze only in the art; amber only on time
- [ ] 2px corners on paper, 6px on a control, none at the viewport edge; no shadow
- [ ] 2px ink opens and closes a document; the double lapis rule only under the amount paid
- [ ] Estedad 800 only for figures and the home headline; other headings 600
- [ ] Proportional digits, money columns aligned on the units digit
- [ ] Every Latin run in `<bdi lang="en">` (`|bdi`, `|isolate`), and text that mixes Persian and a `<bdi>` in one box, never loose in a flex or grid container
- [ ] A name never wraps inside itself in running text: an address under a heading moves to the next line whole
- [ ] No tile under text, near money, in a form, search or the panel, or in place of a brand mark
- [ ] Nothing moves by itself but the home glint, nothing ticks, and reduced motion stops both
- [ ] Focus visible everywhere and never under the sticky header
- [ ] No three feature cards, no emoji; every promise, badge and section head a noun phrase

## Voice — 2026-10-20: plain written Persian

The 2026-10-14c register below, polite-colloquial everywhere except money, errors, labels and commitments, did not hold at its own exceptions: the sentences a buyer could hold the shop to were exactly the ones that had drifted colloquial («پول برمی‌گرده», «شماره کارت شخصی برای واریز نمی‌دیم»). From this date the default is **plain written Persian**. These clauses supersede the matching ones in that section, which is kept as it was written; what they do not mention stands.

- **Written, not formal.** «است، را، می‌شود», never «ـه، رو، می‌شه». The shop is «ما», in the active voice: «برمی‌گردانیم», «تحویل می‌دهیم». The register is still built by deletion: no «می‌باشد», «می‌گردد», «صورت می‌گیرد», «جهت», «لذا», «نمایید», «مذکور», and no passive with «توسط».
- **Two exceptions.** The buyer's own voice keeps its colloquial form: the questions in the FAQ («اکانت اشتراکیه یا مال خودمه؟») and, when they exist, Telegram messages. Legal pages use standard formal Persian, still without the bureaucratic markers.
- **Address** is «شما» with «ـید». Never «تو», «ـین», «واسه» or «می‌باشه».
- **Spelling does not relax.** A half-space in «می‌», «ها» and the ezafe «ـه‌ی» after a silent ه («مشاهده‌ی همه»); a clitic joins with no space («ایمیلتان») except after ه («شماره‌تان»); «تأیید» has one spelling.
- **A commitment has three parts:** a number, the point it counts from («پس از تأیید پرداخت», «پس از تحویل»), and the shop as the one who acts. A half conditional («جایگزین نداشتیم، پول برمی‌گرده») is never how money is promised.
- **A site-wide promise is true of every public product, or it is not printed.** `apps/catalog/promises.py` holds each one beside the condition it must meet, and `apps/catalog/tests/test_promises.py` checks every public product against it. Today that is one line: delivery within the 72-hour cap after payment is confirmed (ADR-0009). It runs past the four words a label is allowed below, because the number needs its starting point. The payment method, refunds and warranty terms appear when the owner has decided them, not before.
- **Operator text is a field of its own.** A template adds nothing before or after it. A field that sits inside a fixed sentence, such as what the customer is asked for, takes a noun phrase, and its hint in the panel shows the sentence it lands in.
- **A Latin name inside Persian is isolated** everywhere: `<bdi>` in a template, U+2068 and U+2069 in a Python string. No em dash joins a Latin run to Persian; a title uses «|», anything else «،».
- **Dates are Jalali in Persian digits** for the customer and the operator alike. A security message and the event log carry the Tehran date and time.
- **«تومان» everywhere.** «تومن» only in a Telegram message.
- **A question only where it means something.** A gift card has no mid-period question, and a ready account has no password question.
- **Length is a target, not a cap.** Two sentences and about 25 words is still what a yes/no answer aims at, and `test_every_faq_answer_is_short` still holds every FAQ answer to two sentences and 30 words. Product content has no word cap. A sentence that commits money is never cut to make an answer shorter.
- **Punctuation.** «،» «؟» «» always; at most one «؛» on a page; no em dash in Persian text or in a page title.
- **No superlative a number could replace:** «بهترین», «سریع‌ترین».

The half of this a pattern can see is a test: `apps/catalog/tests/test_storefront_copy.py` reads every storefront page, the three emails in both halves and the strings the pages' scripts write; `apps/core/tests/test_persian_register.py` reads every string the panel shows, from the model labels and help to the Persian catalogue and the panel's own code; `apps/accounts/tests/test_copy.py` reads the sign-in and account pages. Those tests ban the glossary's «Not» forms that a pattern can tell apart from good Persian; the rest, such as «حساب» used for a service account, need a reader.

### Glossary

| Concept | Write | Not |
|---|---|---|
| The customer's account at PremShop | «حساب کاربری» («ورود به حساب», «خروج از حساب») | «حساب» for this and a service account alike |
| An account with a service (ChatGPT, Netflix) | «اکانت» | «حساب», «ایمیل حساب OpenAI» |
| The login details a customer receives | «اطلاعات ورود اکانت» | «اطلاعات حساب», which is also the account page's name |
| Delivery onto the customer's own account | «فعال‌سازی روی اکانت شخصی»; in a sentence, «روی اکانت خودتان فعال می‌شود» | «روی اکانت شخصی شما», «روی حساب خودتون», «روی ایمیل شما» |
| A ready account for one person | «اکانت آماده‌ی اختصاصی» | «اختصاصی» for an upgrade of the customer's own account |
| A ready account several people share | «اکانت آماده‌ی اشتراکی»; «پروفایل اختصاصی» only when the profile itself is what is sold | «یک پروفایل اختصاصی» beside «نوع اکانت: اختصاصی» |
| License and gift card codes | «کد لایسنس», «کد گیفت کارت» | «کد / لایسنس»; «گیفت کارت» as a delivery type |
| Gift card | «گیفت کارت», two words | «کارت‌های هدیه», «گیفت‌کارت» |
| The option a buyer picks | on the shop, «مدت اشتراک» or «مبلغ کارت»; in the panel, «پلن» | «پلن» to a customer |
| How long delivery takes | to the customer, «زمان تحویل»; to the operator, «مهلت تحویل» | «مهلت هر محصول» to a customer |
| Warranty | «گارانتی», always with its length and starting point | «پشتیبانی: تا پایان دوره» as a spec |
| Refund | «بازگشت وجه», always with its condition | «ضمانت بازگشت وجه», «پول برمی‌گرده» |
| What is left | «مانده», «باقی‌مانده» | «مونده», «باقی‌مونده» |
| A discount that has ended | «تخفیف تمام شد» | «تخفیف تموم شد» |
| The discounts section | «تخفیف‌ها» | «تخفیف‌دارها» |
| Phone number | «شماره‌ی موبایل» | «شماره تماس», «شماره موبایل» |
| Member since | «تاریخ عضویت» | «عضو از» |
| Sign-in code | «کد ورود» | «کد تأیید» |
| A product taken off sale | «ناموجود»: its page stays, with the buy box disabled, and lists leave it out. Until the storefront shows such a page, an unavailable product is a 404. | «ناموجود» meaning hidden |
| Nothing found | «پیدا نکردیم» | «پیدا نشد», «رو نداریم» |
| Search | «جستجو» | |
| Support hours | «هر روز، ساعت ۱۰ تا ۲۲» | «هر روز ۱۰ تا ۲۲», «بین ساعت ۱۰ تا ۲۲» |
| The panel's sections | «فروشگاه», «مشتریان» | «کاتالوگ», «حساب‌ها» |
| An item in the panel | «مورد» | «رکورد» |
| New (password, code, item) | «جدید» | «تازه» |
| Add | «افزودن» | «اضافه کردن», «ایجاد», «ساختن» |
| Ezafe after a silent ه | «ـه‌ی» («مشاهده‌ی همه») | «مشاهده همه», «افزودن یک مشخصه دیگر» |

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
- **A promise strip under the hero, and the same four promises in the footer — never both on one page.** The home page suppresses the footer copy through a block, because saying the same four things twice on one screen reads as a shop with four facts padding them out. *(2026-10-20: three of the four were not true of every product and are gone. The promises now come from `apps/catalog/promises.py`, one today; the rule against stating them twice on one screen stands.)*
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
- **Label, do not explain.** The owner's correction, and the one most easily lost: a Persian shop writes «ضمانت بازگشت وجه» on a promise row, not «اگر سفارش تحویل نشود، وجه برمی‌گردد». Promises, badges and section heads are **noun phrases of two to four words**; buttons are verbs that name what happens; a step's body carries the fact its title does not, never a restatement of it. Explanation is not banned — it is *located*: the FAQ page and the product page's own questions are where a sentence belongs, and nowhere else is. When a line on a shop surface is longer than about eight words, it is almost always a sentence that wants to be a label. *(2026-10-20: the example is withdrawn with the promise it labelled, since the shop has no refund policy yet. The rule stands.)*
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
| Field | `#8C8C8C` | the edge of a control a person types into (field, code cell, the header search), and nothing else: 3.4:1 on white, where WCAG 1.4.11 asks 3:1 and a hairline gave 1.48:1. Added 2026-10-20; the header search stopped being a grey well for the same reason |
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
- **A discount with an end date carries a clock**, in the amber that this document reserves for time (`.deadline`, `badge--time`): on the product card beside the plan name, and on the plan's own row in the buy box. Under a day it counts down to the second, padded to `۰۰:۰۸:۴۰` — `۰۸:۴۰` alone reads as eight hours. Above a day it says days and hours, because `۴۱۲:۰۶:۵۵` is a number, not a deadline. A discount with **no** end date shows no clock: invented urgency is the thing this shop is trying not to look like. The server renders the text and sends the seconds beside it, never the deadline, so a visitor whose own clock is wrong still sees the right number.
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
