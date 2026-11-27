# Design language

The visual and verbal system of the storefront, the operator panel and the emails. The decisions behind it are [ADR-0016](decisions/0016-brand-and-type.md) (type and colour), [ADR-0026](decisions/0026-storefront-composition.md) (storefront composition) and [ADR-0023](decisions/0023-email-templates.md) (email). The tokens live in `static/css/input.css`, the base, and `static/css/storefront.css`, the storefront layer that sets the current palette.

- [Principles](#principles)
- [Colour](#colour)
- [Typography](#typography)
- [Space, shape and layout](#space-shape-and-layout)
- [Motion](#motion)
- [Numbers, currency and right-to-left text](#numbers-currency-and-right-to-left-text)
- [Components](#components)
- [Email](#email)
- [Voice](#voice)
- [Decided by the owner](#decided-by-the-owner)
- [Checklist](#checklist)

## Principles

- **Visible bookkeeping.** A buyer's biggest fear is paying and receiving nothing. The shop answers it the way a small business that keeps records would: hairlines, aligned figures, exact amounts, stated hours and plainly stated terms. It should not read like a brand campaign or a landing page.
- **Colour belongs to the goods.** Product brand colours appear in the products' artwork; the interface around them stays neutral, with one accent for what can be pressed. Departments are the exception: their banners, tiles, page heads and the hero's range pills carry each department's own colour, and a product page's head and section strips carry the product's.
- **Amber means attention:** the customer's move (an unpaid order, details awaited, a card number for a refund) or time running out (deadlines and countdowns), and nothing else (owner, 2026-11-11).
- **Nothing moves unless a person acts**, with two exceptions: the home page's service scene, and the small drawings in the banners that open the home sections, the department pages, the account, the cart, the checkout and sign-in.
- **Every choice is argued from this business, never defaulted.** Purple-blue gradients, glassmorphism, glowing dark heroes, large radii on everything, emoji as icons and entrance animations are generated-template habits, and they cost a shop like this its customers' trust.

## Colour

The storefront palette (`storefront.css`):

| Role | Value | Use |
|---|---|---|
| Ground | `#FFFFFF` | The page and its cards |
| Soft fill | `#F5F6F8` | Quiet panels, the search field, hover fills |
| Line | `#DFE3E9` | Hairlines and borders |
| Ink | `#20252E` | Text |
| Secondary | `#566170` | Supporting text |
| Accent, cobalt | `#2459D3`; hover `#1946B0`, pressed `#153D98`, tint `#EDF2FF` | What can be pressed |
| Graphite | `#181D26`; text `#C9D0DB`, lines `#38414F` | The footer band |

Semantic colours (`input.css`):

| Role | Value | Use |
|---|---|---|
| Attention | `#D97706`, text `#92400E` | The customer's move, and deadlines and countdowns |
| With us | `#0891B2`, text `#155E75` | Work the shop is doing; cyan, kept apart from cobalt |
| Success | `#16A34A`, text `#166534` | Delivered, confirmed |
| Danger | `#DC2626`, text `#991B1B` | Overdue, failed, destructive |
| Field edge | `#8C8C8C` | The border of a control a person types into: 3.4:1 on white, where WCAG 1.4.11 asks 3:1 |

- **Cobalt marks what can be pressed:** buttons, links, the discount chip and interactive icons. Bold text that cannot be pressed stays ink or grey, so the primary button is the one blue mass in its area.
- **Neutrals carry the structure.** A border means an edge, a background step means a separate surface, and a shadow means something sits above the page.
- **Badges** are a tint, a border and dark text, never a saturated fill. Colour is never the only carrier of meaning: every status also has words.

### Status tones

Every status, on the site, in the panel, in email and in the bot, takes one of five tones; the tones and their tokens exist, and each page adopts them as it is rebuilt. The tone belongs to the state, not to the reader, so the customer and the operator see the same colour for the same order. Each pill carries words and an icon from the shared set, and none is pressable.

| Tone | Means | Site badge | Panel tokens | Icon |
|---|---|---|---|---|
| Attention | The customer's move, or time running out | `.badge--time` | `--panel-attention-*` | `clock` |
| Problem | Something went wrong: a delay to chase, or a cancelled order | `.badge--danger` | `--panel-problem-*` | `alert`; `close` for a cancellation |
| With us | The shop is working on it | `.badge--progress` | `--panel-progress-*` | `progress` |
| Done | Finished as it should be | `.badge--ok` | `--panel-done-*` | `check` |
| Closed | Ended, nothing pending: a lapsed payment window, a refund sent | `.badge--neutral` | `--panel-closed-*` | `close` |

The rows are in ranking order: an order leads with its highest-ranked state (`apps/orders/status.py`). A cancellation is the exception: red, yet last, since any other item says more about the order. A pill is a noun phrase of at most four words («در انتظار پرداخت»); a finished state may be one word («تحویل‌شده», «لغوشده»), as the bot already says it. The heading beside a pill states the fact or the action and never repeats it. `apps/core/tests/test_status_tones.py` holds every tone's words at 4.5:1 or more on its tint, in both panel themes, and the cyan apart from cobalt.
- **The panel** uses the same cobalt on light and dark surfaces (`admin-experience.css`), and its tokens hold the five status tones in both themes. Its loud things are time remaining and what needs the operator.

## Typography

Estedad sets headings and price figures; Vazirmatn sets everything else. Both are self-hosted and subset ([development](development.md#front-end-assets)).

| Role | Face | Weight | Size | Line height |
|---|---|---|---|---|
| Home hero heading | Estedad | 600 | `clamp(32px, 3.6vw, 48px)`; 30px at ≤760px | 1.45; 1.4 at ≤760px |
| h1 | Estedad | 600 | 30px | 1.3 |
| h2 | Estedad | 600 | 21px | 1.4 |
| h3 | Estedad | 600 | 18px | 1.45 |
| h4 | Estedad | 600 | 17px | 1.5 |
| Body prose | Vazirmatn | 400 | 17px | 1.85 |
| Interface, buttons | Vazirmatn | 400–500 | 15px | 1.5–1.6 |
| Small, meta | Vazirmatn | 400 | 13px | 1.7 |
| Footnote | Vazirmatn | 400 | 12px | 1.9 |
| Label | Vazirmatn | 500 | 13px | 1.5 |
| Price figure | Estedad | 800 | by context | 1.2 |
| Currency word | Vazirmatn | 400 | 0.72em of the figure | 1 |

**Why these numbers.** Persian carries finer detail than Latin at the same size, in the dot clusters that tell ب پ ت ث apart, so the base is 17px and nothing goes below 12px. Descenders such as ج and ی drop well below the baseline, and at a line height of 1.5 they nearly touch the next line, the clearest sign of a layout translated from Latin; prose therefore runs at 1.85. Headings run tighter because at larger sizes the collision matters less. Weight steps are at least 200, because Persian's even baseline mass hides a 500 to 600 difference. Estedad carries `size-adjust: 82%`, since at the same nominal size it renders larger than Vazirmatn.

**Absolute rules.**

- `letter-spacing: 0` everywhere: Persian is cursive and tracking breaks the joins. The one exception is the sign-in code's field, whose six digits stand apart and never join.
- No italic: neither family ships one, and a synthesised slant shears the joins. `font-synthesis: none`.
- No `text-transform`, which would uppercase the Latin half of «اشتراک Claude Pro», and no `justify`, since no browser implements kashida.
- Never both faces in one line.

## Space, shape and layout

**Spacing.** Components are separated by nine values: `4 · 8 · 12 · 16 · 24 · 32 · 48 · 64 · 96`.

| Values | Between |
|---|---|
| 4, 8 | Parts of one component, such as an icon and its label |
| 12, 16 | Closely related items, such as a label and its field |
| 24, 32 | Components |
| 48, 64 | Blocks within a page region |
| 96 | Page regions |

- **The rhythm rule:** the gap between groups is at least twice the gap within them.
- **Asymmetry is deliberate.** Headings own the space after them. Cards get more padding at the block end, because Persian descenders take optical space there. Inside a component, padding is optical and may fall between the tokens: a button is padded `11px 20px 9px`, because Persian ink sits low in the line box and equal padding looks as if the text floats.
- **Measure.** A block of text is at most 32rem wide.
- **Corners.** `--radius-sm` (6px) for a control you act on, `--radius-lg` (14px) for a surface that holds goods, and square for anything that touches the viewport's edge. A few storefront components set their own small radii in `storefront.css`.
- **Targets.** Every primary action is at least 44px tall.
- **Direction.** Logical properties only (`margin-inline-start`, never `margin-left`). Directional icons mirror; clocks, locks, phones and cards do not.

## Motion

- **Durations:** `--dur-xs` 90ms for a press, `--dur` 140ms for colour and state, `--dur-lg` 220ms for anything that reveals. The easing is a soft stop with no overshoot; what enters uses `--ease-out`.
- **The one automatic motion** is the home page's service orbit. It keeps moving and has no pause button; it stops while a logo is pointed at or focused, while it is off screen or the tab is hidden, and never moves under `prefers-reduced-motion`. That is the owner's accepted trade against WCAG 2.2.2. On a wide screen the logos glow in their brand colours around the shop's mark, and the services' sketches circle that mark on a ring of their own, turning the other way; it is the one band no logo or name ever crosses (`orbit.js`).
- **The banners' loops.** A banner is a flat colour with one soft light, an icon tile, a title and a small looping drawing of what the section holds: for the departments a robot answering in a speech bubble (AI), code typing in a terminal (development), a video player beside a music player (entertainment), a gift box and a card (gift cards); for the offers a seal with the deepest discount among the department emoji; an ID card and an order's journey for the account; the cart's own goods dropping into a basket and piling up; a receipt with a check for the checkout; a code arriving in a bubble for sign-in. They are inline SVG moved by CSS, with no file to load, and stand still under `prefers-reduced-motion`. A banner that names a place opens it.
- **Adding to the cart** tosses the product's mark from the button into the header's cart, which bounces as it lands; the toast follows.
- **Everything else answers a person.** Shelves move only when scrolled, offer tags stay still, nothing animates on entry, and a hover moves an element a few pixels at most. No `transition: all`.

## Numbers, currency and right-to-left text

- **Digits.** Persian digits in all output, with the ASCII comma as the thousands separator: the Arabic separator U+066C renders as a faint high comma in both faces and reads as a fault. Inputs keep Latin digits and `direction: ltr`, because in right-to-left fields the caret, Home, End and Backspace run backwards while someone types a number; Persian digits typed in are accepted and normalised.
- **Figures.** Counts, codes and clocks use tabular figures, so a column of them aligns. Price figures in Estedad are proportional, because tabular widths make the dot-zero read as letter spacing.
- **Money.** «تومان» everywhere, set lighter than the figure and never at its weight. Zero renders as «رایگان», never «۰ تومان». Rial never appears.
- **Promotions.** A promotional price shows the original beside it, struck through in secondary grey, never amber and never bold. A discount with an end date carries a clock in amber that counts hours, minutes and seconds, padded as `۰۰:۰۸:۴۰`, and keeps counting hours past a day (`۱۴۸:۴۴:۰۶`), because a clock that visibly ticks reads as a live offer. A discount without an end date shows no clock, because invented urgency is what this shop avoids. The server renders the text and sends the seconds remaining, never the deadline, so a visitor whose clock is wrong still sees the right number.
- **Mixed text.** Every Latin value that comes from data, such as a product name, plan title, specification value or email address, is wrapped in `<bdi>`. Without it «اشتراک Netflix.» puts the full stop on the wrong side and «(Spotify)» renders as «)Spotify(». `/styleguide/` keeps the regression strings.
- **Dates** are Jalali, in Persian digits, for customers and the operator alike. Security messages and history carry Tehran date and time.

## Components

- **The ledger line** (`.fact`). Every fact-and-value pair is a row: label at the start, value at the end, joined by a dotted leader lifted `-0.28em` to clear the ج descender. The leader is the visual grammar of an invoice or a receipt, and says "this is a real operation" in a way a trust badge cannot. One ledger group per page region; summaries show subtotal, discount and total as three lines, never one figure.
- **Product card.** One linked card. The artwork is the service's mark on a white tile, like an app icon, over a panel shaded in the product's brand colour, 2:1 on wide screens and 16:9 on phones, with the shop's name, «PremShop», small in its corner (the top one on phones, where the tile reaches the bottom); the small tiles in the cart, the checkout and the search suggestions carry none. Title, delivery time, offer and starting price are text outside the artwork. The offer names its own plan and the starting price names the cheapest available plan, so the two may differ. An offer badge is neutral; amber appears only for its remaining time. A pointer lifts the card and tilts its tile. «گزینه‌های خرید» is a button that opens a read-only dialog of the plans' prices, rendered by the same server code as the product page, and the card's link still works without JavaScript.
- **Home page.** The hero introduces the shop as a whole and never headlines one product: the range as department pills, the headline «اشتراک دلاری، پرداخت تومانی» with its second half on a tilted cobalt sticker, the deepest current discount when there is one (a number from the catalogue, never «ارزان‌تر»), and only site-wide facts, namely the promise in `apps/catalog/promises.py` and the support hours. Department tiles in their colours lead to each department's page, and the current discounts sit in one rail under an amber banner. Every product then shows, department by department, in a grid: four across, three on a narrow window, two on a phone, never a rail. Each department is a band that opens with its banner, which always links to the department's page. After the last department a dashed card asks for a service the shop does not stock; the questions follow under a cobalt banner, as cards.
- **Pages' heads and sections.** A department page opens with its banner grown into the page head; the account, cart, checkout and sign-in open with a cobalt one. Content below sits in panels whose header strip carries the title; the product page's strips and fact tiles take the product's colour, the account's the accent. The FAQ's questions are cards that fill the accent when open.
- **Links and controls.** A link that stands alone is a pill with a forward chevron; a link inside a sentence keeps its chip. On a wide screen the header's departments are quiet tabs, colour only in their icons and in a line under the current one, so the hero's pills stay the one coloured list. On a phone the header is two rows, the brand with the cart and account, then search beside two round buttons for the department menu and support; reading back up it returns as one short row. Buttons and summaries never select the text around them on a quick click.
- **Product page.** On a phone the page reads title, buy box, then details, so the price is never below the questions. Key specifications sit as chips beside the title. The benefits come before the full specification table: one sentence on what the free plan withholds, then one gain per item, the limit last. The selected plan is the accent's one appearance in the buy box. Under the buy button there is only the link to the buying conditions: delivery is already on the page, and payment in toman is stated on the home page. «پیش از خرید» appears only when it says more than the warranty beside the title. «محصولات دیگر» is a fresh shuffle of the whole shop on each visit, under a banner whose drawing is the suggested products' own marks.
- **Order groups.** The customer's order list splits into the bot's four groups, «در انتظار پرداخت», «در جریان», «تحویل‌شده» and «لغوشده», as links with their counts, two by two on a phone. An order sits under the state it leads with (`apps/orders/status.py`), so a delivered product beside a refund still owed keeps it «در جریان», and the list opens on the first group that holds an order.
- **Order rows.** The cart, checkout, order pages and the order preview share one product row: a 48px tile (56px in the cart), the name at 17px, the plan at 14px and the figure at 18px, with hairlines between rows and never a box inside a box. From three products up, on phones, the total and the action sit in a bottom bar that steps aside while a field has focus.
- **Cart.** No stock hold and no countdown. An unavailable selection stays visible with a neutral status, a restock-support link and a remove action; it blocks checkout, and the subtotal of the remaining rows says so. A quantity control appears only where the plan allows several units, with its limits visibly disabled and explained. Changes happen in place, keeping errors visible and focus where it was.
- **Footer.** A graphite band. Its sales band appears only where people are still choosing, never on the cart, checkout, account or order pages. A site-wide promise is stated once per screen.
- **Empty states** take the blame and offer one action: «پیدا نکردیم», never «پیدا نشد». A search that finds nothing lists the catalogue.
- **Category pages** have no filters; if one is ever needed, sorting comes first.
- **Focus and selection.** Everything interactive has a `:focus-visible` ring, one continuous edge; links keep visible keyboard focus without a hover underline. `::selection`, `caret-color` and `accent-color` are set.
- **Signing out** asks once, in a dialog; without a script the button signs out directly.
- **Icons** come from the shared SVG set and carry text or an accessible name. Emoji never stand in for icons on the site; the Telegram bot and the offers banner's drawing are the owner's exceptions, under «Decided by the owner».

## Email

Email is a different medium with its own rules ([ADR-0023](decisions/0023-email-templates.md)):

- **Layout** is tables with inline styles, because Outlook renders with Word's engine.
- **Fonts.** No web font is loaded. The stack is `'Vazirmatn','Vazir','IRANSans','IRANYekan','Sahel','Shabnam',-apple-system,'SF Arabic','Segoe UI','Noto Sans Arabic','Noto Naskh Arabic',Tahoma,Arial,sans-serif`, so each reader sees the first one installed.
- **Colour.** A warm-stone ramp (`#1C1917`, `#57534E`, `#78716C` on `#FAFAF9`, lines `#E7E5E4`) with cobalt `#2459D3` buttons and links. Amber marks attention only: a delivery link's expiry and the customer's next move. Prose runs at a line height of 1.95, cards and buttons have a 10px radius, and a button is padded `13px 26px 11px`.
- **The wordmark is set in type**, never as an image, because blocked images would show a broken box on the messages where trust matters most. It is `PremShop`, in Latin, everywhere: the site header and footer, the sender name, subjects and the masthead.

Templates live in `templates/email/`; `/dev/emails/` renders them while `DEBUG` is on.

## Voice

Persian copy must be short, clear and natural. State the next action directly, without repeating the status or adding sentences that say nothing new. Read each message as a whole before shipping it; joining correct phrases can still produce an awkward sentence. The owner reaffirmed this on 2026-11-09 after the order-page sketch.

Give the reader enough to understand what happened and decide what to do. That can include reassurance, a reason to buy or a detail that affects price, delivery or access. Choose the length and tone that fit the situation. Professional is not formal, natural is not colloquial, and longer is not better (owner, 2026-11-24); the FAQ is the one surface written conversationally (Register).

### How to write a line

The examples in this section carry the judgment; the rules only name it. Before writing, read the examples for the surface at hand, then take every new or changed line through these steps in order:

1. **The situation, in Persian.** Who reads the line, what just happened and what they do next. The line is said the way a Persian speaker would say it to that reader (Written in Persian).
2. **Whether it belongs.** Read it beside the heading, status, labels and buttons already on screen. Keeping, rewriting, shortening and deleting are equally good outcomes (Decide whether the text belongs, Editorial review guide).
3. **The facts behind it.** A line is a claim about one code branch. Check the recipient, amount, limit and deadline, and what has and has not happened in that branch (Editorial review guide, its paragraphs on money, errors, sign-in and delivery).
4. **The words.** Write for the surface and in the owner's taste (Register, Rules for every register, Building a line, Glossary, Lines the owner rewrote).
5. **The rendered result.** Read the line on its page at 390 and 1440 in every state that shows it, as the buyer or operator would: plain-text email beside HTML, bot and SMS messages on their own (Read it the way a buyer or operator would).
6. **A second reader.** Someone who did not write the line reads it, and the owner sees changed lines beside the old ones in their rendered context (Editorial review guide).

An example is a judgment to reuse, never a wording to stamp. Copying one example's shape onto every line produces copy that reads as generated, however correct each line is.

### Written in Persian

The owner's strict rule, 2026-11-14: a line is thought and written in Persian, starting from its situation (who reads it, what just happened, what they do next) and said the way a Persian speaker would say it to that reader. It is never an English sentence carried over word by word, however correct: «درخواست کد ثبت شد» is "request submitted", «بررسی کنید» is "check", «در اختیار کسی نگذارید» is "do not share", and each is rewritten from the situation, not polished.


### Decide whether the text belongs

Start with the page or message a reader actually gets. Read the heading, status, field label, buttons and surrounding text together. For a notification, read the subject, body and action in that order. Ask what the reader still needs to know or do. Keep a good existing line. Remove a line that repeats an answer already visible beside it. Use a label or a short phrase for a field, value or relationship. Write a sentence when the reader needs an explanation, a condition or a next step.

- In the delivery-delay email, the heading says the time changed and the next line gives the new deadline. A sentence saying delivery was deferred makes the buyer wait another line for the date.
- On a paid order awaiting details, a visible form can make «اطلاعات فعال‌سازی را در فرم زیر وارد کنید.» unnecessary. A buyer with no form still needs the Telegram instructions. Check mixed orders too, because the route can differ by product.
- A refund form needs to say the bank transfer happens first and the panel only records it. The button «ثبت واریز بازپرداخت» is too short to carry that distinction and the fact that records cannot be corrected there after saving.

### Read it the way a buyer or operator would

Use familiar verbs and put the fact or action where the reader will find it on the first pass. Official-looking grammar can hide the subject or make something simple sound like a system operation. These examples from the shop show the decision in its setting.

| Context | Earlier copy | Better copy | Why |
|---|---|---|---|
| Empty category | «هنوز محصولی در این دسته برای خرید قرار نگرفته است.» | «فعلاً محصولی در این دسته نداریم.» | The category already has a name. Say what the buyer came to learn. «فعلاً» leaves room for the stock to change. |
| Ending a holiday after someone else ended it | «تعطیلی فعالی برای پایان دادن وجود ندارد.» | «فروشگاه در حالت تعطیلی نیست.» | Name the state on the screen. There is no need to describe a missing record. |
| Checking the number on the code screen | «اگر کد نرسیده است، ببینید شماره‌ی بالای صفحه درست نوشته شده باشد.» | «کد نرسید؟ شماره موبایلتان را بررسی کنید.» | Start with the problem and name the thing to check. Put it next to the number and its change link. |
| Sorting help in the panel | «موارد با عدد کوچک‌تر بالاتر نمایش داده می‌شوند.» | «عدد کمتر، اولویت بیشتر» | The field already says «ترتیب نمایش». The operator only needs the direction. |
| Reason for cancelling a purchase | «علت: عدم دریافت اطلاعات موردنیاز برای فعال‌سازی در مهلت تعیین‌شده.» | «اطلاعات فعال‌سازی را در مهلت تعیین‌شده دریافت نکردیم.» | A direct verb makes the cause clear. In the standalone notice, name the ۲۱ days and what starts the clock. Name the affected product if the rest of the order can still go ahead. |
| SMS resend on the website | «کد جدید با پیامک فرستاده شد.» | «کد جدید را برایتان پیامک کردیم.» | Use the verb a buyer expects and say what the shop has actually done. A message about an asynchronous Telegram request must also fit what the shop knows at that point. |

The operator usually needs a short label or a consequence. A product page should make the choice easier with a concrete benefit and the terms that affect buying. An error should name the problem and a way forward. In security and money messages, check the actual recipient, amount, limit, deadline and trigger against what the shop does. Read the rendered result at phone and desktop widths. Check plain-text email alongside HTML, and read bot and SMS copy as standalone messages. Spelling and formatting checks help after the wording works in context.

### Lines the owner rewrote

The owner's rewrites are the model to imitate. Every line the owner rewrites joins this table beside the line it replaced. `apps/core/tests/test_owner_lines.py` fails when the code no longer holds a line of this table or holds the line it replaced again, so changing one is the owner's decision and updates its row.

| Rejected | The owner's line | What changed |
|---|---|---|
| «برای دریافت کد در تلگرام، باید قبلاً آن را از صفحه‌ی حساب کاربری وصل کرده باشید.» | «برای دریافت کد در تلگرام باید از قبل آن را از صفحه‌ی حساب کاربری به حساب تلگرامتان متصل کرده باشید.» | One sentence names what connects to what; «وصل کرده باشید» alone left it open. |
| «درخواست کد ثبت شد. ربات پریمشاپ را در تلگرام بررسی کنید.» | «کد به ربات پرم‌شاپ در حساب تلگرام شما فرستاده شد.», with «ربات پرم‌شاپ» linking straight to the bot | It says what happened and where, and the link is the next step; «ثبت شد» and «بررسی کنید» were translated, and the brand was misspelled. The shipped line is conditional, «اگر حساب تلگرامتان به حساب کاربری متصل باشد، کد ورود را در ربات پرم‌شاپ می‌بینید.», because the request is queued and the site cannot yet know the code reached the bot. |
| «این کد را در اختیار کسی نگذارید.» | «این کد را به کسی ندهید.» | The warning reads as Persian rather than an English sentence carried over. |
| «اطلاعات ورود اکانت یا کد تحویل‌شده کار نمی‌کند.» | «اطلاعات ورود اکانت یا کد تحویل‌داده‌شده درست نیست.» | Use «تحویل‌داده‌شده», not «تحویل‌شده»; use «درست نیست», not «کار نمی‌کند». The reason names incorrect delivery details. |
| «نظر می‌تواند تا ۱۰۰۰ نویسه باشد.» | «تا ۱۰۰۰ کاراکتر می‌توانید بنویسید.» | The character limit uses the site's ordinary word and addresses the buyer. |
| «۴ از ۵، بر اساس ۱ نظر» | «۴ از ۵ · ۱ نظر» | The rating and count are enough. |
| «خریدار تأییدشده» | «خریدار» | The ability to submit already comes from a purchase. |
| «نظرتان در انتظار بررسی است.» | «اگر نظرتان تأیید شود، در صفحه‌ی محصول دیده می‌شود.» | The buyer knows what can happen next. Publication depends on approval. |
| «با این حساب تلگرام در هر ۲۴ ساعت بیشتر از ۵ بار نمی‌توانید اتصال را تأیید کنید. پس از پایان این بازه دوباره امتحان کنید.» | «با این حساب تلگرام در هر ۲۴ ساعت بیشتر از ۵ بار نمی‌توانید اتصال را تأیید کنید.» | The limit gives the reason and the time window. |
| «برای ادامه‌ی اتصال، از صفحه‌ی حساب کاربری لینک جدیدی بگیرید.» | «از صفحه‌ی حساب کاربری لینک جدیدی بگیرید.» | The heading already says the link expired. The sentence gives the next step. |
| «اگر درباره‌ی خرید یا سفارشتان سؤالی دارید، از دکمه‌ی «پشتیبانی» پیام بدهید تا پیام به دست ما برسد.» | «به پشتیبانی پیام بدهید.» | The word «پشتیبانی» is the link. |
| «می‌توانید جزئیات و اقدامات موردنیاز برای تحویل سفارش را در صفحه‌ی سفارش ببینید.» | «مراحل تحویل را در صفحه‌ی سفارش ببینید.» | The owner's second rewrite of their own line. A pointer names what the reader will find there and nothing more; the noun stack read heavy. |
| «رسید و خبرهای سفارش‌های بعدی به این نشانی می‌رسد. ایمیل سفارش‌هایی که قبلاً ثبت کرده‌اید، عوض نمی‌شود.» | «رسید و اطلاعات سفارش‌های بعدی به ایمیل شما ارسال می‌شود.» | Say what this email address is for. |
| «ایمیل Gmail خود را وارد کنید. برای دیدن اطلاعات تحویل، کد را به همین ایمیل می‌فرستیم.» | «ایمیل Gmail خود را وارد کنید.» | The hint above the field already says the code goes to this email. |
| «اگر از لینک یک محصول به این صفحه رسیده‌اید، نامش را جستجو کنید یا از فهرست محصولات به خرید ادامه دهید.» | «شاید آدرس اشتباه است، یا این محصول را دیگر نمی‌فروشیم.» | Keep the reason on the 404. A second line can point to search and the shop. |
| «اگر می‌خواهید خرید بقیه‌ی محصولات را ادامه دهید، این مورد را از سبد خرید حذف کنید.» | «برای ادامه‌ی خرید، لطفاً این مورد را از سبد خرید خود حذف کنید.» | The unavailable item is already identified. Give the action directly. |
| «برای خرید، محصولتان را از فهرست پیدا کنید و به سبد اضافه کنید.» | «برای شروع، یک محصول را انتخاب کنید.» | One step is enough for an empty cart. |
| «با ساخت شناسه‌ی جدید، شناسه‌ی قبلی همان لحظه از اعتبار می‌افتد.» | «با ساخت شناسه‌ی جدید، شناسه‌ی قبلی باطل می‌شود.» | State what happens to the previous identifier. |
| «برای تحویل فعلاً کاری لازم نیست.» | «فعلاً نیازی نیست کاری انجام دهید.» | The heading above already gives the delivery time. The reassurance stands on its own, without a «برای …» in front. |
| «این سفارش ایمیل ندارد.» | «برای این سفارش ایمیلی ثبت نشده است.» | An order does not "have" an email. Say what was or was not recorded. |
| «اطلاعات فعال‌سازی لازم است» | «برای فعال‌سازی به اطلاعات شما نیاز داریم» | The shop says what it needs from the buyer, instead of stating an impersonal requirement. |
| «روزی که توضیحات محصول را با منابع بررسی کردید.» | «تاریخ آخرین بررسی صحت اطلاعات محصول» | A field hint names what the date vouches for. The relative clause dangled under its label. |
| «وضعیت تعطیلی تغییر کرد. صفحه را دوباره باز کنید.» | «تعطیلات تمام شد و برگشتیم.» | Shown when the holiday ends or the operator submits an old form after reopening. When a different holiday is open, the panel names that state instead. |

### Building a line

The owner's rule, 2026-11-10, with their own example: «تأیید پرداخت، پشتیبانی، تحویل و گزارش هر سفارش را هم در تلگرام پیگیری کنید.»

- **Content first, then one verb.** Name the concrete things, then the one verb that acts on them.
- **A second verb only for a second real action:** «رمز ایمیلتان را عوض کنید و به پشتیبانی خبر بدهید.» Never a chain that narrates what happens next: «این تلگرام وصل می‌شود و خبر سفارش‌ها هم می‌رسد. اگر حساب خودتان است، دکمه را بزنید.»
- **Write a complete thought.** A standalone notice needs a sentence the reader can understand in one pass. A label or short field hint can carry a complete thought in context.
- **A label and its value** where a line only names something: «پشتیبانی: @premshop_support».
- **One fluent sentence for one message,** even when it holds a condition, a place and an action, as the owner's rewrites above show; a message is not chopped into short sentences (owner, 2026-11-14). This never makes a line longer than its message: a label stays a label, a second message or a chain of «که» clauses becomes a second sentence, and a line that adds nothing is deleted (owner, 2026-11-24).

### Register

The owner set the split on 2026-11-10: conversational Persian belongs to the FAQ, where a buyer's question reads best in their own words; every other surface is written.

| Surface | Register |
|---|---|
| The FAQ: questions as buyers ask them, and the answers | Polite conversational Persian: «می‌تونید», «رو», «می‌شه», «تومن» |
| Pages, product content, cart and checkout, account and order pages, emails, bot messages, error pages | Standard written Persian, natural and polite: «می‌توانید», «را», «می‌شود» |
| Buttons, navigation, field labels | Short and neutral; a button is a verbal noun that names what happens: «دریافت کد ورود», «ثبت سفارش», never «ـید» |
| The operator panel | Standard written Persian; labels are noun phrases |
| SEO text, articles, legal pages | Standard written Persian, without bureaucratic verbs |

A conversational FAQ answer still states money, time and warranty exactly: its commitment keeps all three parts below.

### Rules for every register

- **Address** is «شما» with «ـید»: «می‌توانید» (in the FAQ, «می‌تونید»), never «می‌تونین», «تو», «واسه» or «می‌باشه». The verb carries the address; «شما» is not repeated in every sentence.
- **Use the point of view that makes responsibility clear.** Address the reader through the verb. Use the shop’s voice for what it did or will do, and state a condition directly when that is what matters.
- **One register per sentence:** «حسابتان» with «می‌توانید» ✓ · «حسابتون» with «می‌تونید» in the FAQ ✓ · «حسابتان» with «می‌تونید» ✗.
- **No bureaucratic markers:** «می‌باشد», «می‌گردد», «صورت می‌گیرد», «جهت», «لذا», «نمایید», «مذکور», or a passive with «توسط».
- **Spelling never relaxes.** A half-space in «می‌» and «ها», and the ezafe «ـه‌ی» after a silent ه («مشاهده‌ی همه»), never «ـهٔ». A clitic joins with no space, except after ه («شماره‌تان»). «تأیید» has one spelling. Persian «ی» and «ک», never Arabic «ي» and «ك».
- **A Persian line never opens with a Latin word:** Telegram, plain-text email and many editors set a line's direction from its first letter. «سرویس Netflix …», not «Netflix …».
- **A commitment has three parts:** a number, the point it counts from («پس از تأیید پرداخت», «پس از تحویل»), and the shop as the one who acts. A half conditional is never how money is promised.
- **A site-wide promise is true of every public product, or it is not printed.** `apps/catalog/promises.py` holds each promise beside its condition, and `apps/catalog/tests/test_promises.py` checks every public product. A warranty, refund or replacement belongs to the product whose verified terms support it.
- **Let a label do its job.** A badge can name the state and a button can name the action. Put an explanation where the reader needs it to choose or act.
- **Say a limit the way a person would:** «از این گزینه تا ۳ اشتراک می‌توانید بخرید.», not «حداکثر تعداد قابل افزودن: ۳ عدد». No «قابل» with an infinitive, and no «عدد» as a unit in a sentence.
- **Numbers, not adjectives:** «تا ۱۲ ساعت», never «سریع» or «در کمترین زمان ممکن», and no superlative a number could replace («بهترین», «سریع‌ترین»).
- **Product and tool names stay in English** wherever a customer reads them. Persian spellings appear only in search aliases and page titles, because shoppers search with them.
- **«تومان» everywhere,** except a conversational FAQ answer, which may say «تومن».
- **Operator text is a field of its own:** a template adds nothing before or after it. A field that sits inside a fixed sentence takes a noun phrase, and its hint in the panel shows the sentence it lands in.
- **Punctuation:** «،», «؟» and «» always; at most one «؛» on a page; no em dash in Persian text or in a page title.
- **Length is a target, not a cap.** A yes-or-no answer aims at two sentences and about 25 words, and every FAQ answer is held to two sentences and 30 words. Product content has no cap, and a sentence that commits money is never cut to make an answer shorter.
- **Questions buyers actually have,** asked in the buyer's own words: whether the account is shared, whether they must hand over a password, what happens if access is cut mid-period, whether it works from an Iranian IP address, and how they know the money is safe; not what the shop finds easy to say. A question appears only where it means something: a gift card has no mid-period question.
- **The market's own words:** seats are counted in «کاربره» («۲ کاربره»), never «ظرفیت»; streams are «پخش هم‌زمان: ۲ دستگاه»; account kinds are «نوع اکانت: اختصاصی / اشتراکی».
- **Marketing leads with what the product does,** such as file analysis in ChatGPT Plus or background playback in YouTube Premium, explaining paid capacity without promising unlimited use. No founder story, lifestyle slogan, slang or invented evidence.
- **Admit a limitation where there is one.** «سرویس Netflix با آی‌پی ایران باز نمی‌شود.» does more for trust than any badge.
- **Phrases that read as generated:** «همچنین», «علاوه بر این», «لازم به ذکر است», «با خیال راحت», «نگران نباشید», «به راحتی», «تنها با چند کلیک», «تجربه‌ای بی‌نظیر», «ما در پرم‌شاپ», «در دنیای امروز», «نه تنها … بلکه»; synonym pairs such as «امن و مطمئن»; and three adjectives or claims in the same grammatical shape («سریع، آسان و مطمئن»). A list of concrete things, as in the owner's example above, is not this.

### Glossary

| Concept | Write | Not |
|---|---|---|
| The customer's account at PremShop | «حساب کاربری» («ورود به حساب», «خروج از حساب») | «حساب» for this and a service account alike |
| An account with a service (ChatGPT, Netflix) | «اکانت» | «حساب», «ایمیل حساب OpenAI» |
| The customer's own Telegram, where the bot writes | «حساب تلگرام» («حساب تلگرامتان») | «اکانت تلگرام» |
| The login details a customer receives | «اطلاعات ورود اکانت» | «اطلاعات حساب», which is also the account page's name |
| Delivery onto the customer's own account | «فعال‌سازی روی اکانت شخصی»; in a sentence, «روی اکانت خودتان فعال می‌شود» | «روی اکانت شخصی شما», «روی حساب خودتون», «روی ایمیل شما» |
| A ready account for one person | «اکانت آماده‌ی اختصاصی» | «اختصاصی» for an upgrade of the customer's own account |
| A ready account several people share | «اکانت آماده‌ی اشتراکی»; «پروفایل اختصاصی» only when the profile itself is what is sold | «یک پروفایل اختصاصی» beside «نوع اکانت: اختصاصی» |
| License and gift card codes | «کد لایسنس», «کد گیفت کارت» | «کد / لایسنس»; «گیفت کارت» as a delivery type |
| Gift card | «گیفت کارت», two words | «کارت‌های هدیه», «گیفت‌کارت» |
| The option a buyer picks | on the shop, «مدت اشتراک» or «مبلغ کارت»; in the panel, «پلن» | «پلن» to a customer |
| How long delivery takes | to the customer, «زمان تحویل»; to the operator, «مهلت تحویل» | «مهلت هر محصول» to a customer |
| Warranty | «گارانتی», always with its length and starting point | «پشتیبانی: تا پایان دوره» as a specification |
| Refund | to the customer, «بازگشت وجه», always with its condition; to the operator, «بازپرداخت» | «ضمانت بازگشت وجه», «پول برمی‌گرده» |
| What is left | «مانده», «باقی‌مانده» | «مونده», «باقی‌مونده» |
| A discount that has ended | «تخفیف تمام شد» | «تخفیف تموم شد» |
| The discounts section | «تخفیف‌ها» | «تخفیف‌دارها» |
| Phone number | «شماره‌ی موبایل» | «شماره تماس», «شماره موبایل» |
| Member since | «تاریخ عضویت» | «عضو از» |
| Sign-in code | «کد ورود» | «کد تأیید» |
| A product taken off sale | «ناموجود»: its page stays only while it meets the publishing rule, with buying disabled and sale listings excluding it | «ناموجود» meaning hidden |
| Nothing found | «پیدا نکردیم» | «پیدا نشد», «رو نداریم» |
| Search | «جستجو» | |
| Support hours | «هر روز، ساعت ۱۰ تا ۲۲» | «هر روز ۱۰ تا ۲۲», «بین ساعت ۱۰ تا ۲۲» |
| The panel's sections | «فروشگاه», «مشتریان» | «کاتالوگ», «حساب‌ها» |
| An item in the panel | «مورد» | «رکورد» |
| New (password, code, item) | «جدید» | «تازه» |
| Add | «افزودن» | «اضافه کردن», «ایجاد», «ساختن» |
| Ezafe after a silent ه | «ـه‌ی» («مشاهده‌ی همه») | «مشاهده همه», «افزودن یک مشخصه دیگر» |

The rules a pattern can see are tests: `apps/catalog/tests/test_storefront_copy.py` reads every storefront page, both halves of every email and the strings the pages' scripts write; `apps/core/tests/test_persian_register.py` reads model labels and help, the Persian catalogue, template text, accessible labels, scripts and Python messages; `apps/accounts/tests/test_copy.py` reads the sign-in and account pages; `apps/core/tests/test_owner_lines.py` keeps the owner's rewritten lines in the code and the lines they replaced out of it. The rest, such as «حساب» used for a service account, needs a reader.

### Editorial review guide

PremShop sounds like a shop that knows what it sells and can explain what happens after payment. Tell the buyer what they can choose now, what the shop does next and what affects the purchase. In the panel, give the operator enough information to check a decision at a glance.

| Where | What the reader needs |
|---|---|
| Home and search | A reason to browse and a way forward. On a failed search the catalogue and Telegram serve different purposes. Keep the Telegram invitation even though the button also names Telegram. |
| Product page | A concrete reason to buy, a comparison that helps choose, and any limitation that could change the choice. Give each option the same units for price and duration. Use «مدت اشتراک» and «مبلغ کارت» where those are the actual choices. |
| Cart and checkout | Keep what changes the total, holds up checkout, or starts a deadline. Where a line is the only explanation of a disabled action, keep it by that action or point to the affected item. |
| Account and orders | Name which account, order or purchase a message concerns. On a mixed order, give each affected product its own state and next action. |
| Email, Telegram and SMS | Read the subject, title, body and button as one message. An email can be warm and complete, a bot message natural and a little warm, an SMS very short. Repeat a fact if a channel needs it to stand alone. SMS can often stop at the code, its purpose and the warning. Start a line with Persian if an English name would set the wrong direction. |
| Operator panel | A label can name a field. A hint can say what a blank value does or when the result appears. Give the full consequence for an irreversible operation. |
| Legal and security | Name the condition, scope and deadline. Check the recipient and who can act. Keep «این کد را به کسی ندهید» with the code. |

Use the reader's words. «اکانت» is natural for a service they bought, «حساب کاربری» is where they sign in at PremShop, and «پلن» is useful in the panel. The FAQ can sound like a conversation. Keep amounts, times and warranty conditions exact in every register.

Keep a sentence if it helps the reader choose, understand what happened, know who acts next or feel confident waiting. «فعلاً کاری لازم نیست» belongs in preparation and replacement review when the buyer can safely wait. Use a short status label in a badge and explain the reason, timing and next step nearby as needed. A delay can call for an apology and a useful way to follow up. In a cancellation or refund, name the shop's role when it explains the cause, and use neutral wording for the financial status. A natural passive, such as «بازگشت وجه انجام شد», suits a status label when the outcome is what the buyer needs to scan.

Read what's already on the screen before editing. A paragraph telling the buyer to choose a product repeats the empty cart's title and its «دیدن محصولات» button. Signing in to recover an earlier cart is a different action and deserves a line. Keep sound copy when it already fits the reader's situation. For each edit, name the problem it solves and read the whole message again. A shorter version earns its place when it reads more clearly and carries the same useful information. Keep owner-approved copy in place and bring proposed changes to the owner with the rendered context.

| Example | Editorial decision |
|---|---|
| «شماره‌ی موبایل را کامل، با ۰۹ و ۹ رقم بعد از آن وارد کنید.» → «شماره‌ی موبایل باید ۱۱ رقم باشد و با ۰۹ شروع شود.» | The familiar eleven-digit form gives the reader one thing to check. The validator still accepts the supported country-code spellings. |
| «برای رمز عبور، کنار عدد از حروف یا علامت هم استفاده کنید.» → «رمز عبور نباید فقط عدد باشد.» | This validator rejects an all-digit password. A password with no digits can pass this rule. |
| «تا پرداخت یا لغو سفارش فعلی، ثبت سفارش جدید امکان ندارد.» → «اول سفارش فعلی را پرداخت یا لغو کنید.» | The order link is beside the instruction. The separate sentence about keeping the cart gives a fact the button cannot convey. |
| «کد جدید با پیامک فرستاده شد.» → «کد جدید را برایتان پیامک کردیم.» | The SMS provider has accepted the send by this point. Telegram's queued request offers conditional help because the site has yet to learn whether delivery is possible. |
| «این مشکل روی سفارش‌های ثبت‌شده اثری ندارد.» on a 503 page | The error page cannot verify the effect of an outage. Give the buyer a way to retry or contact the shop. |

For money, write down the event behind the text before editing. An amount owed, a transfer made by the operator and a transfer recorded in the panel are distinct events. Check the amount after discounts and which purchase it belongs to. Review the refund amount, destination and transfer reference alongside the confirmation. Keep both the reason money is due and the condition that starts the wait. A recorded payment is the buyer's payment to the shop. A recorded refund is the shop's transfer to the buyer.

For errors and sign-in, give the cause the system can establish and the next action the reader can take. Check which action was limited, whether the limit applies to the number or network, and whether the time is an upper bound or a countdown. For delivery, check what arrives, the page where the buyer can find it, what they must verify and what support can do if they lose access.

For product and SEO copy, explain a purchased capability through a task the buyer can recognize. Describe background playback as listening while the screen is off. Keep the concrete benefits, comparisons, requirements and usage limits that help someone decide what to buy. Review source material before changing a claim. Keep edits to seeded defaults from overwriting product copy the operator has maintained.

For review, open the rendered view at 390 and 1440 pixels with ordinary and long values. Check empty, blocked, expired and successful states. Exercise the action named in each message, or check its code path in an isolated fixture. Build financial examples through checkout, payment and cancellation so the receipt, discount and refund agree. Pair comparisons by the same code branch or event in each version. Review plain-text email beside HTML. A bot or SMS preview uses a sample payload with sending disabled. Very short help can look clear in a source file and become unclear once a field is hidden or a variable changes. Re-read the result with those branches visible and ask a second reader whether the existing line, an edit or deletion best serves the reader.

### Before and after, with the reason

Each pair comes from the shop. The right-hand column is the judgment to reuse; the wording itself is not a pattern to stamp elsewhere.

| Situation | Weak | Better | Why |
|---|---|---|---|
| Delivery notice by email and Telegram | «کد را از همان صفحه بگیرید و با آن ایمیل همین سفارش را تأیید کنید.» | «در صفحه‌ی سفارش کد را به ایمیل یا با پیامک بگیرید و همان‌جا وارد کنید.» | Say what the reader does, in the page's own words for its two ways of sending the code. The weak line described the system's check and pointed at a code that did not exist yet. |
| A paid purchase cancelled, refund owed | «مبلغ بازگشت وجه: … تومان. وضعیت واریز را در صفحه‌ی سفارش ببینید.» | «… برای دریافت آن، شماره‌ی کارت یا شبای حسابی به نام خودتان را طبق راهنمای صفحه‌ی سفارش برای پشتیبانی بفرستید. مبلغ را تا یک روز کاری پس از دریافت آن واریز می‌کنیم.» | Money owed is not money sent. Give the step that releases it and the commitment, in the future tense, and never imply a transfer. When the shop failed, say why and apologize. |
| A cancellation with a zero refund | «مبلغی برای بازگشت وجه این خرید ثبت نشده است.» | «این خرید بازگشت وجهی ندارد.» | A zero refund can follow a zero paid share or rounding. State the outcome. |
| A sign-in limit on the reader's own number | «برای این شماره چند بار کد ورود درخواست شده است. تا یک ساعت صبر کنید و دوباره امتحان کنید.» | «برای این شماره در یک ساعت فقط ۵ بار می‌توانید کد بگیرید. تا یک ساعت صبر کنید و دوباره امتحان کنید.» | State the rule and give a retry window. Offer an alternate channel where it works. A network limit can give the retry time without blaming the reader. |
| The bot's footer | «پیام‌های این گفت‌وگو را نمی‌خوانیم.» | «پیام‌هایی که اینجا می‌نویسید به پشتیبانی نمی‌رسد.» | Give the consequence the reader cares about, not a refusal, and keep the way to a person beside it. |
| A security code nobody asked for | «اگر خودتان این کد را درخواست نکرده‌اید، کاری لازم نیست.» | «اگر خودتان این کد را نخواسته‌اید، ممکن است کس دیگری وارد حساب کاربری‌تان شده باشد. به پشتیبانی پیام بدهید.» | Check who can trigger a message before reassuring. Only a session signed in to the account can ask for this code. |
| A panel button that messages the customer | «ثبت درخواست اطلاعات» | «درخواست اطلاعات از مشتری», with the section saying that the delivery clock stops, the customer is messaged and day 21 cancels the purchase | An action that reaches a customer or cannot be undone says so where it is pressed. |
| A cancellation with nothing to refund | «مبلغ بازپرداخت: رایگان» | «مبلغ بازپرداخت: ندارد.» | «رایگان» is a price word. A refund line states an amount, and the button no longer promises a record that is never made. |
| The footer's invitation | «بگویید چه نیازی دارید تا در انتخاب کمکتان کنیم.» | «بگویید برای چه کاری می‌خواهید تا در انتخاب سرویس، نوع اکانت و مدت اشتراک کمکتان کنیم.» | Sell the conversation by naming the help it gives: the concrete things, then one verb. |
| A status label beside its own sentence | «مهلت پرداخت تمام شد» as the label | «پایان مهلت پرداخت», kept | The email prints the label right above «مهلت ۲۴ ساعته‌ی پرداخت تمام شد.»; a label must not repeat the sentence beside it. |

Three mechanics catch reviewers. The panel shows the Persian catalogue's text for any Persian msgid in `locale/fa/LC_MESSAGES/django.po`, so check a panel line in the rendered panel, not only in the code. Model help stays within 18 words (`apps/core/tests/test_persian_register.py`), which suits a hint the operator reads at a glance. With an inanimate plural subject the singular verb is natural («تا این موارد درست نشود»), and it also keeps clear of the spoken-Persian check.

## Decided by the owner

Each of these looks like a detail anyone could tidy away, and each is the owner's decision. A change to one waits for the owner's agreement, and a proposal that would undo one says so.

- **The shop's name on every product card** (2026-10-31), as described under Components.
- **A discount clock that counts hours, minutes and seconds** even with days to go (2026-11-08), as described under Numbers.
- **The home orbit keeps moving, with no pause button,** and only the orbit stops while a logo is pointed at (2026-10-28 and 2026-11-06), as described under Motion.
- **Department banners on the home page** (2026-11-14): each department opens with a banner in its own colour and a small loop of what it sells, as described under Motion.
- **Amber means attention** (2026-11-11): the customer's move or time running out, in the five status tones described under Colour.
- **A cancelled order reads red** (2026-11-11), with the cross icon, as described under Colour.
- **The checkout's warning about the account's email or username** (2026-11-06) is the owner's own sentence: «امکان تغییر … پس از ثبت سفارش وجود ندارد. پیش از ثبت نهایی از درستی … وارد شده اطمینان حاصل کنید.» A copy pass leaves it as written; `apps/payments/tests/test_views.py` pins it.
- **Emoji in the Telegram bot** (2026-11-11): every panel button carries a related emoji, and every order the product's own, picked from its name with a plain box for a name the list does not know. A bot button holds text only, so the site's SVG icons cannot reach it. They live in `apps/core/telegram_emoji.py`, the one file the repository's emoji check allows; the website stays without them. Real brand logos (Telegram's custom emoji) need Premium on the bot's owning account.
- **Emoji in the offers banner** (2026-11-14): one per kind of thing the shop sells, floating round the discount seal, in `templates/storefront/offer-art.html`, the second file the emoji check allows.
- **The headline without a full stop, under the range as department pills** (2026-11-15), as described under Components; on a wide screen the header's departments stay quiet tabs so the pills are not repeated.
- **«محصولات دیگر» is a shuffle of the whole shop** (2026-11-14), not ranked by department.
- **Signing out asks for confirmation** (2026-11-15).
- **Buyers' reviews sit above the product's questions** (2026-11-22), after its specs: the page says what the product is, then what buyers said, then answers what is left.

## Checklist

For any new page or component:

- [ ] Nothing under «Decided by the owner» changed without the owner's agreement
- [ ] Space between components from the nine tokens; optical spacing only inside a component
- [ ] Every gap between groups at least twice the gap within them; headings own the space after them
- [ ] Corners from `--radius-sm` and `--radius-lg`; edges that touch the viewport square
- [ ] Cobalt only on what can be pressed; amber only on attention; every status in one of the five tones, with words and an icon; new colours as tokens
- [ ] Body 17px, prose line height 1.85, text blocks at most 32rem wide
- [ ] Estedad only in headings and price figures, never both faces in one line
- [ ] Persian digits out, Latin digits in inputs; tabular figures for counts, codes and clocks
- [ ] Logical properties only; no `left` or `right`
- [ ] `:focus-visible` on everything interactive; no `transition: all`; durations from the tokens
- [ ] `prefers-reduced-motion` honoured; only the home orbit and the banners' drawings move without an action
- [ ] One ledger group per region
- [ ] Nothing centred, no three feature cards, no rounded-up customer counts, no emoji outside the offers banner
- [ ] Every promise, badge and section head a noun phrase; explanation in the FAQ
- [ ] Every product name, plan title and user value wrapped in `<bdi>`
