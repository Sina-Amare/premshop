"""The Persian the operator reads is held to the same rules as the shop's.

`docs/design-language.md` §Voice governs customer copy. An admin panel is a
different register — it is all labels, hints and errors, which §Voice already
exempts from the contracted colloquial style — but the bans survive, and a few
more apply that only matter when the reader is the owner.

The rules here are the regex-checkable half of a measured set: 22,859 strings
of Persian WooCommerce/WordPress admin, Django's own fa catalogue, and eleven
Iranian commercial products. Two numbers decided most of it. Of 183 real
action labels in that corpus, 2 carry a «ـید» imperative — a button is a bare
verbal noun. And the commercial market uses «نمایید» four times where
WordPress uses it 604, which is why WordPress fa is a vocabulary source and an
anti-model for register.

What is deliberately NOT checked here, because it needs a reader: whether a
sentence states the consequence rather than the definition, and whether an
example is a real value. Those are the half that matters most.
"""

from __future__ import annotations

import ast
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.apps import apps
from django.conf import settings

from apps.catalog.demo_content import PRODUCT_COPY
from apps.catalog.tests.test_panel_translations import ROOT, compile_messages, persian_msgids

# Each entry: the marker, and what to write instead. The message is the point —
# a failure that only says "banned" teaches nobody.
BANNED = [
    (r"می‌باشد", "«است», or drop the copula"),
    (r"نمایید|فرمایید", "«کنید»"),
    (r"می‌گردد(?<!بر می‌گردد)|گردید\b", "«می‌شود» / «شد»"),
    (r"(?<![\w‌])جهت(?= )(?!(?: نوشتار| متن| راست| چپ))", "«برای»"),
    (r"توسط", "active voice: name who did it"),
    (r"انجام می‌پذیرد", "a plain verb"),
    (r"گزارشات|پیشنهادات|سفارشات|آزمایشات", "a Persian plural in «ها»"),
    (r"لطفاً", "nothing — the panel talks to its owner"),
    (r"قابل (?:افزودن|خرید|استفاده)", "what the person can do: «می‌تونید بخرید»"),
    (r"موجودی فعلی:|امکان‌پذیر نیست", "a sentence a person would say"),
    (r"متأسفانه", "the cause instead of an apology"),
    (r"شیء|اشیاء", "«مورد» — «object» is a programming word"),
    (r"وب‌گاه", "«سایت» / «پنل»"),
    (r"گذرواژه", "«رمز عبور», which this shop has said since S2"),
    (r"بانک اطلاعاتی", "«پایگاه داده»"),
    # §Voice bans the em dash in body copy; two help strings had slipped.
    (r"—", "«:» or a full stop"),
    (r"بهترین|سریع‌ترین", "a number, not a superlative"),
    # The glossary's panel words (docs/design-language.md, Glossary).
    (r"رکورد", "«مورد»"),
    (r"(?<![\w‌])تازه(?:‌(?:ای|ها|های))?(?![\w‌])", "«جدید»"),
    (r"پیدا نشد", "«پیدا نکردیم»"),
    (r"(?<=\w)هٔ|(?<=\w)ۀ", "ezafe with «ه‌ی»"),
    (r"بررسی‌نشده‌اند", "finite verb with a space: «بررسی نشده‌اند»"),
    # The brand has one spelling, and «do not share» carried over word for word
    # reads as a bank form (docs/design-language.md, Written in Persian).
    (r"پریم[‌ ]?شاپ|پرم ?شاپ", "«پرم‌شاپ», with a half-space"),
    (r"در اختیار (?:کسی|دیگران) (?:نگذارید|قرار ندهید)", "«به کسی ندهید»"),
    # ZWNJ, the mistake that makes Persian look machine-set.
    (r"(?<![\w‌])می [اآبپتثجچحخدذرزژسشصضطظعغفقکگلمنوهی]", "a half-space: «می‌…»"),
]

# Spoken Persian belongs to the FAQ alone (docs/design-language.md, §Register);
# the operator panel is written. Each marker is a form that only speech has,
# matched as a whole word so «روی» and «روز» stay clean, and «می‌توان» and
# «می‌خواند» are not read as «می‌شه» and «می‌خوان».
_WORD = r"[\w‌]"
SPOKEN = [
    (rf"(?<!{_WORD})رو(?!{_WORD})", "«را»: «حسابتان را», not «حسابتون رو»"),
    (
        rf"(?<!{_WORD})(?:توی|اگه|دیگه|اون|اونا|اونجا|یه|چی|چیه|اینجوری)(?!{_WORD})",
        "«در», «اگر», «دیگر», «آن», «یک», «چه»",
    ),
    (rf"(?<!{_WORD})(?:میاد|بیار(?:ید|ین)?)(?!{_WORD})", "«می‌آید», «بیاورید»"),
    (r"همون|بذار|نشون|برگردون|کدوم", "«همان», «بگذار», «نشان», «برگردان», «کدام»"),
    (
        rf"(?<!{_WORD})ن?می‌{_WORD}*(?:ه|(?<!توا)ن)(?!{_WORD})",
        "«می‌شود», «می‌کنند»: the written ending",
    ),
    (rf"(?<!{_WORD})ن?می‌تون", "«می‌توانید»"),
    (rf"ن?می‌خوا(?:ید|ی|د|ن)(?!{_WORD})", "«می‌خواهید»"),
    (
        rf"(?<!{_WORD})(?:ب(?:شه|شن|شید|شین|دید|گید|دین|گین)|ب?کنه|ب?کنن|ن?داره|ن?دارن|باش[هن]|هستن)"
        rf"(?!{_WORD})",
        "«بشود», «بدهید», «بگویید», «باشد», «دارد»",
    ),
    (
        rf"(?<!{_WORD})(?:ممکن|کافی|لازم|مناسب|ثابت|لاتین|مطمئن|درست|معتبر|اشتباه|بخش|همین|خالی|به‌روز)ه"
        rf"(?!{_WORD})",
        "«است» written out: «ممکن است»",
    ),
    (rf"(?<!{_WORD})اینجاست(?!{_WORD})", "«اینجا است»"),
    (rf"‌(?:ست|ان)(?!{_WORD})", "«است», «اند» written out: «آماده است»"),
    (rf"(?<={_WORD})(?:تون|شون)(?:ه|ن)?(?!{_WORD})", "«‌تان», «‌شان»: «حسابتان»"),
    (rf"(?<!{_WORD})(?:ویرایش|گزارش|نمایش)ش(?!{_WORD})", "«گزارش آن»: the pronoun as a word"),
]
# Written words a marker would read as speech: «ستون» and «کارتون» end in «تون», and
# «می‌شده» in «ه». They go before a search. «رو به» stays caught: in «کد رنگ رو به شکل …»
# it is the spoken object marker, so a hint that means "facing" writes «به سمت».
WRITTEN = re.compile(rf"(?<!{_WORD})(?:ستون|کارتون|می‌شده)(?!{_WORD})")


def spoken(pattern: str, text: str) -> bool:
    return re.search(pattern, WRITTEN.sub("", text)) is not None


# Latin digits inside a Persian sentence. Identifiers, codes and format
# placeholders are exempt: this is about quantities a person reads.
#
# `[0-9]` and not `\d`. Python's `\d` is Unicode-aware and matches ۰-۹ as
# happily as 0-9, so the first version of this flagged «بین ۱ تا ۷۲ ساعت» as
# Latin digits. The negative control below is what caught it, which is the
# whole reason a lint ships with examples of what it must leave alone.
LATIN_DIGIT_IN_PERSIAN = re.compile(r"[؀-ۿ][^\n]{0,20}?(?<![%\w(])[0-9]+(?![\w)%])")

MAX_WORDS = 18  # the corpus median is 12; p90 is 27 and reads as a paragraph


def model_strings() -> list[tuple[str, str, str]]:
    """Every label and hint an operator can read, with where it came from."""
    out = []
    for model in apps.get_models():
        if not model.__module__.startswith("apps."):
            continue
        for field in model._meta.get_fields():
            if not hasattr(field, "help_text") or getattr(field, "auto_created", False):
                continue
            # Inherited from PermissionsMixin, carrying Django's own wording,
            # and absent from every form in this project — UserAdmin does not
            # list them. Holding Django's text to this project's rules would
            # fail on strings nobody can read.
            if field.name in {"is_superuser", "groups", "user_permissions", "password"}:
                continue
            where = f"{model.__name__}.{field.name}"
            if field.help_text:
                out.append((where, "help_text", str(field.help_text)))
            verbose = str(getattr(field, "verbose_name", "") or "")
            if verbose:
                out.append((where, "verbose_name", verbose))
    return out


def test_there_are_strings_to_check():
    """A scan over nothing passes. This is the control for the walk."""
    found = model_strings()
    assert len(found) > 60, found
    assert any(t == "help_text" for _, t, _ in found)


@pytest.mark.parametrize("pattern, instead", BANNED)
def test_no_model_string_uses_a_banned_form(pattern, instead):
    bad = [
        f"{where}.{kind}: {text}"
        for where, kind, text in model_strings()
        if re.search(pattern, text)
    ]
    assert not bad, f"write {instead} — {bad}"


def panel_strings() -> list[tuple[str, str]]:
    """The panel's words that are not a model's: Django's and Unfold's strings
    as the Persian catalogue words them, and the Persian the panel's own code
    and templates hand to gettext."""
    catalogue = compile_messages().parse_po(ROOT / "locale" / "fa" / "LC_MESSAGES" / "django.po")
    found = [(f"django.po: {msgid[:40]!r}", text) for msgid, text in catalogue.items() if msgid]
    return found + [("panel code", msgid) for msgid in persian_msgids()]


def test_the_panel_walk_reads_the_catalogue_and_the_code():
    found = panel_strings()
    assert len(found) > 200, len(found)
    # An entry of the catalogue, and a source string of the panel's code.
    assert any(text == "انتخاب فایل" for _, text in found)
    assert any(text == "ذخیره نشد. خطاهای زیر را درست کنید." for _, text in found)


@pytest.mark.parametrize("pattern, instead", BANNED)
def test_no_panel_string_uses_a_banned_form(pattern, instead):
    bad = [f"{where}: {text}" for where, text in panel_strings() if re.search(pattern, text)]
    assert not bad, f"write {instead} — {bad}"


@pytest.mark.parametrize("pattern, instead", SPOKEN)
def test_no_panel_string_is_spoken_persian(pattern, instead):
    """The FAQ is the one conversational surface; the panel reads as written."""
    bad = [f"{where}: {text}" for where, text in panel_strings() if spoken(pattern, text)]
    assert not bad, f"write {instead} — {bad}"


@pytest.fixture(scope="module")
def ui_source_strings():
    root = Path(settings.BASE_DIR)
    found = []
    for base, suffixes in (("templates", {".html", ".txt"}), ("static/js", {".js"})):
        for path in sorted((root / base).rglob("*")):
            if path.suffix not in suffixes:
                continue
            source = path.read_text(encoding="utf-8")
            source = re.sub(
                r"{% comment %}.*?{% endcomment %}|{#.*?#}|<!--.*?-->", "", source, flags=re.S
            )
            source = re.sub(r"/\*.*?\*/|(?m:^[\t ]*//[^\n]*$)", "", source, flags=re.S)
            if path.suffix == ".html":
                parser = UiText()
                parser.feed(source)
                found.extend((str(path.relative_to(root)), text) for text in parser.parts)
            else:
                found.extend((str(path.relative_to(root)), line) for line in source.splitlines())
    for path in sorted((root / "apps").rglob("*.py")):
        if {"tests", "migrations"} & set(path.parts):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        docs = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.body and isinstance(node.body[0], ast.Expr):
                    docs.add(id(node.body[0].value))
        found.extend(
            (f"{path.relative_to(root)}:{node.lineno}", node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docs
        )
    return [(where, text) for where, text in found if re.search(r"[\u0600-\u06ff]", text)]


class UiText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        self.parts.extend(
            value
            for key, value in attrs
            if (key.startswith("data-") or key in {"title", "alt", "placeholder", "aria-label"})
            and value
        )


@pytest.mark.parametrize("pattern,instead", BANNED)
def test_customer_and_email_sources_use_the_same_persian(ui_source_strings, pattern, instead):
    bad = [where for where, text in ui_source_strings if re.search(pattern, text)]
    assert not bad, f"write {instead} — {bad}"


def test_copy_scan_reaches_templates_scripts_and_python_messages(ui_source_strings):
    assert any(where.endswith("login_code_request.html") for where, _ in ui_source_strings)
    assert any(where.endswith(".js") for where, _ in ui_source_strings)
    assert any("accounts" in where and "views.py:" in where for where, _ in ui_source_strings)
    assert len(ui_source_strings) > 300


@pytest.mark.parametrize("attribute", ["data-busy", "data-after", "data-name", "aria-label"])
def test_copy_scan_includes_labels_that_scripts_or_assistive_technology_show(attribute):
    parser = UiText()
    parser.feed(f'<button {attribute}="رمز عبور تازه">ثبت</button>')
    assert any(re.search(pattern, text) for pattern, _ in BANNED for text in parser.parts)


def test_help_text_stays_one_short_sentence():
    """Measured, not felt: real Persian admin help runs to a median of 12
    words and this project's own strings matched it before the cap existed.
    Past about eighteen it stops being a hint and becomes a paragraph the
    operator skips."""
    long = [
        (where, len(text.split()), text)
        for where, kind, text in model_strings()
        if kind == "help_text" and len(text.split()) > MAX_WORDS
    ]
    assert not long, f"over {MAX_WORDS} words: {long}"


def test_quantities_are_written_in_persian_digits():
    """Persian digits for anything read as a quantity. The exceptions are
    identifiers and format placeholders, which the pattern already skips."""
    bad = [
        f"{where}.{kind}: {text}"
        for where, kind, text in model_strings()
        if LATIN_DIGIT_IN_PERSIAN.search(text)
    ]
    assert not bad, bad


@pytest.mark.parametrize(
    "text, why",
    [
        ("این فیلد اجباری می‌باشد.", "copula"),
        ("ذخیره نمایید", "نمایید"),
        ("سفارش ثبت گردید.", "گردید"),
        ("جهت ویرایش کلیک کنید", "جهت"),
        ("این سفارش توسط اپراتور لغو شد.", "توسط"),
        ("لطفاً دوباره تلاش کنید.", "لطفاً"),
        ("این شیء تاریخچه ندارد.", "شیء"),
        ("گذرواژه خود را بنویسید", "گذرواژه"),
        ("خالی بگذارید — صفر قبول نمی‌شود.", "em dash"),
        ("این کار انجام می پذیرد", "missing half-space"),
        ("تحویل در سریع‌ترین زمان", "superlative"),
        ("این رکورد حذف شد.", "رکورد"),
        ("رمز عبور تازه", "تازه"),
        ("رنگ تازه‌ای", "تازه‌ای"),
        ("در این هفته بررسی‌نشده‌اند.", "finite verb spacing"),
        ("کد ورود پریمشاپ", "brand misspelled"),
        ("کد به ربات پرم شاپ رسید.", "brand without its half-space"),
        ("این کد را در اختیار کسی نگذارید.", "translated «do not share»"),
    ],
)
def test_the_lint_catches_what_it_is_meant_to_catch(text, why):
    """Without these the suite cannot tell clean Persian from a blind regex.
    Every line is a form that really appeared, in Django's or WordPress's fa
    or in a draft of this shop's own copy."""
    assert any(re.search(p, text) for p, _ in BANNED), why


@pytest.mark.parametrize(
    "text",
    [
        "عدد کوچک‌تر بالاتر می‌آید.",
        "خالی بگذارید تا از روی اسم ساخته شود.",
        "مثل: ChatGPT Plus",
        "چقدر برای خودتان آب می‌خورد.",
        "تیک را بردارید تا خریدنی نباشد.",
        "بین ۱ تا ۷۲ ساعت.",
        "این ادعاها بررسی نشده‌اند.",
        "بررسی‌نشده",
        "کد ورود به پرم‌شاپ: ۴۸۲۱۹۳",
        "این کد را به کسی ندهید.",
    ],
)
def test_the_lint_leaves_good_persian_alone(text):
    """BANNED goes after the bookish forms only; spoken ones are not its target.
    The panel is held to SPOKEN as well (below), the FAQ is not."""
    assert not any(re.search(p, text) for p, _ in BANNED), text
    assert not LATIN_DIGIT_IN_PERSIAN.search(text), text


@pytest.mark.parametrize(
    "text",
    [
        "ذخیره نشد. خطاهای زیر رو اصلاح کنید.",
        "فقط توی پنل دیده می‌شه.",
        "اگه خالی بذارید، تخفیف همین الان شروع می‌شه.",
        "قیمت با تخفیف باید از قیمت فروش کمتر باشه.",
        "برای هر سفارش فقط ۱ تا نمی‌تونه باشه.",
        "قیمت خرید و فروش نمی‌تونن منفی باشن.",
        "اسم‌هایی که مشتری ممکنه جستجو کنه.",
        "محصول برای انتشار آماده‌ست.",
        "رمز عبورتون تغییر کرد.",
        "اجازه‌ی حذفشون رو نداره.",
        "کد رنگ رو به شکل #10a37f وارد کنید.",
        "همون بخش رو ویرایش کنید.",
        "این بخش توی لینک صفحه میاد.",
        "اول بگید این محصول برای کی نیست.",
        "برای هرکدوم یه مثال بزنید.",
        "نباید اطلاعات مشتری رو نشون بده.",
        "به اطلاعات مشتری نیاز داره.",
        "این بخش لازمه.",
        "حالا می‌تونید وارد بشید.",
        "نمی‌خواید این پلن فروخته بشه؟",
        "موارد زیر بهش وابسته‌ان.",
        "اینجاست.",
        "گزارشش ایمیل شد.",
        "پیش‌نمایش به‌روزه.",
    ],
)
def test_the_spoken_lint_catches_what_it_is_meant_to_catch(text):
    """Every line is spoken Persian that the panel really carried."""
    assert any(spoken(p, text) for p, _ in SPOKEN), text


@pytest.mark.parametrize(
    "text",
    [
        "روی صفحه‌ی محصول و در روز بررسی.",
        "اگر خالی بگذارید، تخفیف همین حالا شروع می‌شود.",
        "کنار نام محصول، بالای صفحه می‌آید.",
        "می‌توان همین‌جا دوباره ویرایش کرد.",
        "قیمت خرید و فروش نمی‌توانند منفی باشند.",
        "می‌خواهید این پلن فروخته نشود؟",
        "این بخش را می‌خواند و ذخیره می‌کند.",
        "میان‌برهای فهرست",
        "رمز عبورتان تغییر کرد.",
        "اجازه‌ی حذفشان را ندارد.",
        "اسم‌هایی که مشتری ممکن است جستجو کند.",
        "محصول برای انتشار آماده است.",
        "نمونه و هماهنگی",
        "شش روز بعد، پنجاه و شش مورد، پوشش کامل.",
        "همانطور که نوشته‌اید، بگذارید بماند.",
        "این ستون مبلغ هر سفارش را نشان می‌دهد.",
        "کالا در کارتون بسته‌بندی می‌شود.",
        "این پیام پیش از این هم فرستاده می‌شده است.",
    ],
)
def test_the_spoken_lint_leaves_written_persian_alone(text):
    assert not any(spoken(p, text) for p, _ in SPOKEN), text


PERSIAN_NAMES = re.compile(
    r"کلود|یوتیوب|اسپاتیفای|نتفلیکس|کوپایلت|جمینای|چت[‌ ]?جی|گیت‌هاب|جت‌برینز|میدجرنی|ادوبی|گوگل پلی"
)


def test_product_and_tool_names_stay_in_latin_on_the_page():
    """Persian spellings belong in search aliases and page titles, which shoppers search with."""
    for slug, copy in PRODUCT_COPY.items():
        for key in ("short", "heading", "body"):
            assert not PERSIAN_NAMES.search(copy.get(key, "")), (slug, key)
    for path in Path(settings.BASE_DIR, "templates").rglob("*.html"):
        text = re.sub(
            r"{% block (?:title|meta_description) %}.*?{% endblock %}",
            "",
            path.read_text("utf-8"),
            flags=re.S,
        )
        assert not PERSIAN_NAMES.search(text), path.name
