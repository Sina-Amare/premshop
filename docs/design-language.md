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
- **Colour belongs to the goods.** Product brand colours appear in the products' artwork; the interface around them stays neutral, with one accent for what can be pressed.
- **Amber means time** (deadlines and countdowns) and nothing else.
- **Nothing moves unless a person acts**, with one exception: the service scene on the home page.
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
| Time | `#D97706`, text `#92400E` | Deadlines and countdowns only |
| Success | `#16A34A`, text `#166534` | Delivered, confirmed |
| Danger | `#DC2626`, text `#991B1B` | Overdue, failed, destructive |
| Field edge | `#8C8C8C` | The border of a control a person types into: 3.4:1 on white, where WCAG 1.4.11 asks 3:1 |

- **Cobalt marks what can be pressed:** buttons, links, the discount chip and interactive icons. Bold text that cannot be pressed stays ink or grey, so the primary button is the one blue mass in its area.
- **Neutrals carry the structure.** A border means an edge, a background step means a separate surface, and a shadow means something sits above the page.
- **Badges** are a tint, a border and dark text, never a saturated fill. Colour is never the only carrier of meaning: every status also has words.
- **The panel** uses the same cobalt on light and dark surfaces (`admin-experience.css`). The only loud thing in it is time remaining.

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

- `letter-spacing: 0` everywhere: Persian is cursive and tracking breaks the joins. The one exception is the sign-in code's digits in email (below).
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
- **The one automatic motion** is the home page's service orbit. It keeps moving and has no pause button; it stops while a logo is pointed at or focused, while it is off screen or the tab is hidden, and never moves under `prefers-reduced-motion`. That is the owner's accepted trade against WCAG 2.2.2.
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
- **Product card.** One linked card. The artwork is the service's mark on a pale panel in the product's brand colour, 2:1 on wide screens and 16:9 on phones, with the shop's name, «PremShop», small in its corner; the small tiles in the cart, the checkout and the search suggestions carry none. Title, delivery time, offer and starting price are text outside the artwork. The offer names its own plan and the starting price names the cheapest available plan, so the two may differ. An offer badge is neutral; amber appears only for its remaining time. «گزینه‌های خرید» opens a read-only dialog of the plans' prices, rendered by the same server code as the product page, and the card's link still works without JavaScript.
- **Home page.** The hero introduces the shop as a whole and never headlines one product: a line naming the range, the headline «اشتراک دلاری، پرداخت تومانی.», one sentence on buying without a foreign card and seeing each product's terms before paying, the deepest current discount when there is one (a number from the catalogue, never «ارزان‌تر»), and only site-wide facts, namely the promise in `apps/catalog/promises.py` and the support hours. Each category opens with one header row (mark, name, count and «همه‌ی محصولات») over a hairline. On phones a category shows two columns and at most four products with a link to the rest.
- **Product page.** On a phone the page reads title, buy box, then details, so the price is never below the questions. Key specifications sit as chips beside the title. The benefits come before the full specification table: one sentence on what the free plan withholds, then one gain per item, the limit last. The selected plan is the accent's one appearance in the buy box. Under the buy button there is only the link to the buying conditions: delivery is already on the page, and payment in toman is stated on the home page. «پیش از خرید» appears only when it says more than the warranty beside the title.
- **Order rows.** The cart, checkout, order pages and the order preview share one product row: a 48px tile (56px in the cart), the name at 17px, the plan at 14px and the figure at 18px, with hairlines between rows and never a box inside a box. From three products up, on phones, the total and the action sit in a bottom bar that steps aside while a field has focus.
- **Cart.** No stock hold and no countdown. An unavailable selection stays visible with a neutral status, a restock-support link and a remove action; it blocks checkout, and the subtotal of the remaining rows says so. A quantity control appears only where the plan allows several units, with its limits visibly disabled and explained. Changes happen in place, keeping errors visible and focus where it was.
- **Footer.** A graphite band. Its sales band appears only where people are still choosing, never on the cart, checkout, account or order pages. A site-wide promise is stated once per screen.
- **Empty states** take the blame and offer one action: «پیدا نکردیم», never «پیدا نشد». A search that finds nothing lists the catalogue.
- **Category pages** have no filters; if one is ever needed, sorting comes first.
- **Focus and selection.** Everything interactive has a `:focus-visible` ring, one continuous edge; links keep visible keyboard focus without a hover underline. `::selection`, `caret-color` and `accent-color` are set.
- **Icons** come from the shared SVG set and carry text or an accessible name. Emoji never stand in for icons on the site; the Telegram bot is the owner's exception, under «Decided by the owner».

## Email

Email is a different medium with its own rules ([ADR-0023](decisions/0023-email-templates.md)):

- **Layout** is tables with inline styles, because Outlook renders with Word's engine.
- **Fonts.** No web font is loaded. The stack is `'Vazirmatn','Vazir','IRANSans','IRANYekan','Sahel','Shabnam',-apple-system,'SF Arabic','Segoe UI','Noto Sans Arabic','Noto Naskh Arabic',Tahoma,Arial,sans-serif`, so each reader sees the first one installed.
- **Colour.** A warm-stone ramp (`#1C1917`, `#57534E`, `#78716C` on `#FAFAF9`, lines `#E7E5E4`) with cobalt `#2459D3` buttons and links. Amber marks time only: a code's expiry and a delivery link's expiry. Prose runs at a line height of 1.95, cards and buttons have a 10px radius, and a button is padded `13px 26px 11px`.
- **The wordmark is set in type**, never as an image, because blocked images would show a broken box on the messages where trust matters most. It is `PremShop`, in Latin, everywhere: the site header and footer, the sender name, subjects and the masthead.
- **Code cells** show six isolated Latin digits in a left-to-right row; what is typed is what is shown. Persian digits are isolated forms that never join, so the reason for the tracking rule does not reach them, and the sign-in code carries `letter-spacing: 3px`. No other element does.

Templates live in `templates/email/`; `/dev/emails/` renders them while `DEBUG` is on.

## Voice

Persian copy must be short, clear and natural. State the next action directly, without repeating the status or adding sentences that say nothing new. Read each message as a whole before shipping it; joining correct phrases can still produce an awkward sentence. The owner reaffirmed this on 2026-11-09 after the order-page sketch.

### Building a line

The owner's rule, 2026-11-10, with their own example: «تأیید پرداخت، پشتیبانی، تحویل و گزارش هر سفارش را هم در تلگرام پیگیری کنید.»

- **Content first, then one verb.** Name the concrete things, then the one verb that acts on them.
- **A second verb only for a second real action:** «رمز ایمیلتان را عوض کنید و به پشتیبانی خبر بدهید.» Never a chain that narrates what happens next: «این تلگرام وصل می‌شود و خبر سفارش‌ها هم می‌رسد. اگر حساب خودتان است، دکمه را بزنید.»
- **Whole sentences a person would say,** never clipped fragments: «پرداختتان نیمه‌کاره مانده؟», not «پرداخت نیمه‌کاره؟».
- **A label and its value** where a line only names something: «پشتیبانی: @premshop_support».
- **Short sentences, one idea each.** A chain of «که» clauses becomes two sentences.

### Register

The owner set the split on 2026-11-10: conversational Persian belongs to the FAQ, where a buyer's question reads best in their own words; every other surface is written.

| Surface | Register |
|---|---|
| The FAQ: questions as buyers ask them, and the answers | Polite conversational Persian: «می‌تونید», «رو», «می‌شه», «تومن» |
| Pages, product content, cart and checkout, account and order pages, emails, bot messages, error pages | Standard written Persian, natural and polite: «می‌توانید», «را», «می‌شود» |
| Buttons, navigation, field labels | Short and neutral; a button is a verb that names what happens |
| The operator panel | Standard written Persian; labels are noun phrases |
| SEO text, articles, legal pages | Standard written Persian, without bureaucratic verbs |

A conversational FAQ answer still states money, time and warranty exactly: its commitment keeps all three parts below.

### Rules for every register

- **Address** is «شما» with «ـید»: «می‌توانید» (in the FAQ, «می‌تونید»), never «می‌تونین», «تو», «واسه» or «می‌باشه». The verb carries the address; «شما» is not repeated in every sentence.
- **One point of view:** facts are stated impersonally, the reader is addressed by the verb, and «ما» is only for what the shop itself does.
- **One register per sentence:** «حسابتان» with «می‌توانید» ✓ · «حسابتون» with «می‌تونید» in the FAQ ✓ · «حسابتان» with «می‌تونید» ✗.
- **No bureaucratic markers:** «می‌باشد», «می‌گردد», «صورت می‌گیرد», «جهت», «لذا», «نمایید», «مذکور», or a passive with «توسط».
- **Spelling never relaxes.** A half-space in «می‌» and «ها», and the ezafe «ـه‌ی» after a silent ه («مشاهده‌ی همه»), never «ـهٔ». A clitic joins with no space, except after ه («شماره‌تان»). «تأیید» has one spelling. Persian «ی» and «ک», never Arabic «ي» and «ك».
- **A Persian line never opens with a Latin word:** Telegram, plain-text email and many editors set a line's direction from its first letter. «سرویس Netflix …», not «Netflix …».
- **A commitment has three parts:** a number, the point it counts from («پس از تأیید پرداخت», «پس از تحویل»), and the shop as the one who acts. A half conditional is never how money is promised.
- **A site-wide promise is true of every public product, or it is not printed.** `apps/catalog/promises.py` holds each promise beside its condition, and `apps/catalog/tests/test_promises.py` checks every public product. A warranty, refund or replacement belongs to the product whose verified terms support it.
- **Label, do not explain.** Promises, badges and section heads are noun phrases of two to four words; buttons are verbs that name what happens. Explanation belongs in the FAQ and the product's questions.
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
| The login details a customer receives | «اطلاعات ورود اکانت» | «اطلاعات حساب», which is also the account page's name |
| Delivery onto the customer's own account | «فعال‌سازی روی اکانت شخصی»; in a sentence, «روی اکانت خودتان فعال می‌شود» | «روی اکانت شخصی شما», «روی حساب خودتون», «روی ایمیل شما» |
| A ready account for one person | «اکانت آماده‌ی اختصاصی» | «اختصاصی» for an upgrade of the customer's own account |
| A ready account several people share | «اکانت آماده‌ی اشتراکی»; «پروفایل اختصاصی» only when the profile itself is what is sold | «یک پروفایل اختصاصی» beside «نوع اکانت: اختصاصی» |
| License and gift card codes | «کد لایسنس», «کد گیفت کارت» | «کد / لایسنس»; «گیفت کارت» as a delivery type |
| Gift card | «گیفت کارت», two words | «کارت‌های هدیه», «گیفت‌کارت» |
| The option a buyer picks | on the shop, «مدت اشتراک» or «مبلغ کارت»; in the panel, «پلن» | «پلن» to a customer |
| How long delivery takes | to the customer, «زمان تحویل»; to the operator, «مهلت تحویل» | «مهلت هر محصول» to a customer |
| Warranty | «گارانتی», always with its length and starting point | «پشتیبانی: تا پایان دوره» as a specification |
| Refund | «بازگشت وجه», always with its condition | «ضمانت بازگشت وجه», «پول برمی‌گرده» |
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

The rules a pattern can see are tests: `apps/catalog/tests/test_storefront_copy.py` reads every storefront page, both halves of every email and the strings the pages' scripts write; `apps/core/tests/test_persian_register.py` reads model labels and help, the Persian catalogue, template text, accessible labels, scripts and Python messages; `apps/accounts/tests/test_copy.py` reads the sign-in and account pages. The rest, such as «حساب» used for a service account, needs a reader.

## Decided by the owner

Each of these looks like a detail anyone could tidy away, and each is the owner's decision. A change to one waits for the owner's agreement, and a proposal that would undo one says so.

- **The shop's name on every product card** (2026-10-31), as described under Components.
- **A discount clock that counts hours, minutes and seconds** even with days to go (2026-11-08), as described under Numbers.
- **The home orbit keeps moving, with no pause button,** and only the orbit stops while a logo is pointed at (2026-10-28 and 2026-11-06), as described under Motion.
- **Emoji in the Telegram bot** (2026-11-11): every panel button carries a related emoji, and every order the product's own, picked from its name with a plain box for a name the list does not know. A bot button holds text only, so the site's SVG icons cannot reach it. They live in `apps/orders/telegram_emoji.py`, the one file the repository's emoji check allows; the website stays without them. Real brand logos (Telegram's custom emoji) need Premium on the bot's owning account.

## Checklist

For any new page or component:

- [ ] Nothing under «Decided by the owner» changed without the owner's agreement
- [ ] Space between components from the nine tokens; optical spacing only inside a component
- [ ] Every gap between groups at least twice the gap within them; headings own the space after them
- [ ] Corners from `--radius-sm` and `--radius-lg`; edges that touch the viewport square
- [ ] Cobalt only on what can be pressed; amber only on time; new colours as tokens
- [ ] Body 17px, prose line height 1.85, text blocks at most 32rem wide
- [ ] Estedad only in headings and price figures, never both faces in one line
- [ ] Persian digits out, Latin digits in inputs; tabular figures for counts, codes and clocks
- [ ] Logical properties only; no `left` or `right`
- [ ] `:focus-visible` on everything interactive; no `transition: all`; durations from the tokens
- [ ] `prefers-reduced-motion` honoured; only the home orbit moves without an action
- [ ] One ledger group per region
- [ ] Nothing centred, no three feature cards, no rounded-up customer counts, no emoji
- [ ] Every promise, badge and section head a noun phrase; explanation in the FAQ
- [ ] Every product name, plan title and user value wrapped in `<bdi>`
