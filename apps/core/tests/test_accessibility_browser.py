"""Every key page, scanned by axe-core in a real Chromium.

axe reads the page as rendered: computed colours against their real
backgrounds, the accessibility tree, the names that labels and ARIA give. The
test client sees none of it, and neither does a browser without the built
stylesheet, which is why CI builds the CSS before this runs.

The bar is WCAG 2.2 A and AA, and a violation of impact serious or critical
fails. ALLOWED is empty since the violations the scan first found were
fixed. An exception would go there with its reason, and one that stops
occurring fails as well, so the list can only shrink.
"""

from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import Category, Plan, Product

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]

AXE = Path(settings.BASE_DIR) / "node_modules" / "axe-core" / "axe.min.js"
# The 2.1 and 2.2 tags add, among others, the 24px minimum target (2.5.8)
# that the panel's sort and search icons missed.
RUN_AXE = """() => axe.run(document, {
    runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']},
    resultTypes: ['violations'],
})"""
SERIOUS = {"serious", "critical"}
# The phone is how most customers arrive; the desktop is where the operator works.
WIDTHS = (390, 1280)
# Unstyled, all text is black on white and passes contrast. A stylesheet that
# 404s still has a sheet, an empty one.
STYLED = """() => {
    const links = [...document.querySelectorAll('link[rel=stylesheet]')];
    return links.length > 0 && links.every(link => link.sheet?.cssRules.length > 0);
}"""

VISITOR = {
    "home": "/",
    "category": "/c/ai/",
    "product": "/p/chatgpt-plus/",
    "search, a hit": "/search/?q=chatgpt",
    "search, a miss": "/search/?q=netflix",
    "faq": "/faq/",
    "sign-in": "/login/",
    "code request": "/login/code/",
    "404": "/no-such-page/",
}
CUSTOMER = {"account": "/account/"}
OPERATOR = {
    "panel: products": "/admin/catalog/product/",
    "panel: product form": "/admin/catalog/product/{pk}/change/",
}

# (rule, page, element) for a violation accepted with its reason. Per element,
# so a second violation of an allowed rule on the same page still fails.
ALLOWED: set[tuple[str, str, str]] = set()


@pytest.fixture
def shop():
    category = Category.objects.create(name="هوش مصنوعی", slug="ai")
    product = Product.objects.create(
        category=category,
        name="ChatGPT Plus",
        slug="chatgpt-plus",
        delivery_type=Product.DeliveryType.ON_CUSTOMER_ACCOUNT,
        warranty=Product.Warranty.FULL_PERIOD,
        delivery_hours=12,
        status=Product.Status.ACTIVE,
    )
    # A running discount puts the amber deadline badge on the pages scanned.
    Plan.objects.create(
        product=product,
        title="۱ ماهه",
        duration_days=30,
        cost_price=Decimal(850000),
        sale_price=Decimal(1390000),
        promo_price=Decimal(1190000),
        promo_ends_at=timezone.now() + timedelta(hours=5),
    )
    return product


def context_for(browser, live_server, width: int, user: User | None):
    context = browser.new_context(viewport={"width": width, "height": 900})
    if user:
        client = Client()
        client.force_login(user)
        cookie = client.cookies[settings.SESSION_COOKIE_NAME].value
        context.add_cookies(
            [{"name": settings.SESSION_COOKIE_NAME, "value": cookie, "url": live_server.url}]
        )
    return context


def axe(page) -> list[dict]:
    assert page.evaluate(STYLED), f"a stylesheet on {page.url} did not load"
    page.add_script_tag(path=str(AXE))
    result = page.evaluate(RUN_AXE)
    # A run that checked no rule would report no violation either.
    assert result["passes"], f"axe checked nothing on {page.url}"
    return [v for v in result["violations"] if v["impact"] in SERIOUS]


def scan(context, url: str) -> list[dict]:
    page = context.new_page()
    try:
        response = page.goto(url)
        # A redirect to sign-in, or a 404 for a slug that matched nothing, would
        # otherwise be scanned under the name of the page it replaced.
        expected = 404 if url.endswith("/no-such-page/") else 200
        assert (response.status, page.url) == (expected, url), f"{url} -> {page.url}"
        return axe(page)
    finally:
        page.close()


def test_no_key_page_has_a_serious_accessibility_violation(browser, live_server, shop):
    customer = User.objects.create_user("customer@example.test", is_verified=True)
    operator = User.objects.create_superuser("operator@example.test")
    # Otherwise the two search scans read the same page.
    assert Product.objects.public().search("chatgpt").exists()
    assert not Product.objects.public().search("netflix").exists()

    found: dict[tuple[str, str, str], str] = {}

    def record(name: str, width: int, violations: list[dict]) -> None:
        for v in violations:
            for node in v["nodes"]:
                key = (v["id"], name, " ".join(map(str, node["target"])))
                found[key] = found.get(key, f"{v['impact']}, {v['help']}, at") + f" {width}px"

    for width in WIDTHS:
        for user, pages in ((None, VISITOR), (customer, CUSTOMER), (operator, OPERATOR)):
            context = context_for(browser, live_server, width, user)
            try:
                for name, path in pages.items():
                    url = live_server.url + path.format(pk=shop.pk)
                    record(name, width, scan(context, url))
                if user is None:
                    page = context.new_page()
                    page.goto(f"{live_server.url}/login/code/")
                    page.fill("main input[name=email]", customer.email)
                    page.click("main button[type=submit]")
                    page.wait_for_url("**/login/code/verify/")
                    record("code entry", width, axe(page))
            finally:
                context.close()

    new = {key: found[key] for key in sorted(found.keys() - ALLOWED)}
    stale = sorted(ALLOWED - found.keys())
    assert not (new or stale), "\n".join(
        [f"{rule} on {name} ({where}): {detail}" for (rule, name, where), detail in new.items()]
        + [
            f"{rule} on {name} ({where}) no longer occurs; take it out of ALLOWED"
            for rule, name, where in stale
        ]
    )


def contrast(first: str, second: str) -> float:
    """WCAG's contrast ratio of two computed `rgb(...)` colours. A transparent
    edge (`rgba(0, 0, 0, 0)`) is no edge at all, not a black one."""
    parts = [[float(n) for n in re.findall(r"[0-9.]+", colour)] for colour in (first, second)]
    if any(len(rgb) == 4 and rgb[3] == 0 for rgb in parts):
        return 1.0

    def luminance(rgb: list[float]) -> float:
        linear = [
            c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
            for c in (v / 255 for v in rgb[:3])
        ]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    light, dark = sorted((luminance(rgb) for rgb in parts), reverse=True)
    return (light + 0.05) / (dark + 0.05)


@pytest.mark.parametrize(
    "first, second, expected",
    [
        ("rgb(212, 212, 212)", "rgb(255, 255, 255)", 1.48),
        ("rgb(115, 115, 115)", "rgb(255, 255, 255)", 4.74),
        ("rgba(0, 0, 0, 0)", "rgb(255, 255, 255)", 1.0),
    ],
)
def test_the_contrast_helper_measures_what_the_audit_measured(first, second, expected):
    assert round(contrast(first, second), 2) == expected


def test_every_field_has_an_edge_a_low_vision_reader_can_find(browser, live_server, shop):
    """axe measures text, not the edge of a control. Fields and code cells were
    ruled at 1.48:1 and the header search was a well at 1.09:1, where WCAG
    1.4.11 asks 3:1 for the boundary that identifies a control. The search's
    placeholder is the only label a sighted reader gets, so it is text: 4.5:1."""
    context = browser.new_context(viewport={"width": 390, "height": 800})
    page = context.new_page()
    try:
        page.goto(f"{live_server.url}/login/")
        assert page.evaluate(STYLED)
        colours = page.evaluate("""() => {
            const css = (e, pseudo) => getComputedStyle(e, pseudo || null);
            const cell = document.createElement('input');
            cell.className = 'otp__cell';
            document.querySelector('main').append(cell);
            const field = document.querySelector('main .field__input');
            const search = document.querySelector('.v-search input');
            return {
                field: [css(field).borderTopColor, css(field).backgroundColor],
                cell: [css(cell).borderTopColor, css(cell).backgroundColor],
                search: [css(search.closest('form')).borderTopColor, css(search.closest('form')).backgroundColor],
                placeholder: [css(search, '::placeholder').color, css(search.closest('form')).backgroundColor],
            };
        }""")
    finally:
        context.close()

    ratios = {name: round(contrast(*pair), 2) for name, pair in colours.items()}
    assert ratios["field"] >= 3 and ratios["cell"] >= 3 and ratios["search"] >= 3, ratios
    assert ratios["placeholder"] >= 4.5, ratios


# The focus ring each control draws: its colour, spread and blur, and what
# surrounds it. A shadow whose spread is zero, or that is blurred, is a halo.
RING = """e => {
    const focused = e.matches(':focus-visible');
    if (e.matches('.v-search input')) e = e.closest('form');
    const s = getComputedStyle(e);
    const shadow = s.boxShadow.match(/(rgba?\\([^)]*\\)) (-?[0-9.]+)px (-?[0-9.]+)px (-?[0-9.]+)px (-?[0-9.]+)px/);
    let ground = 'rgb(255, 255, 255)';
    for (let n = e.parentElement; n; n = n.parentElement) {
        const c = getComputedStyle(n).backgroundColor;
        if (c !== 'rgba(0, 0, 0, 0)') { ground = c; break; }
    }
    return {
        visible: focused,
        colour: shadow && shadow[1], blur: shadow && +shadow[4], spread: shadow && +shadow[5],
        border: s.borderTopColor, ground,
    };
}"""


def test_a_focused_field_is_ringed_at_three_to_one(browser, live_server, shop):
    """The focus change on a field was a 1px shift of hue, 1.63:1 against the
    field at rest, with a halo at 1.08:1 on white: no pixel changed by the 3:1
    a focus indicator needs."""
    context = browser.new_context(viewport={"width": 390, "height": 800})
    page = context.new_page()
    try:
        page.goto(f"{live_server.url}/login/")
        assert page.evaluate(STYLED)
        page.evaluate("""() => {
            const cell = document.createElement('input');
            cell.className = 'otp__cell';
            document.querySelector('main').append(cell);
        }""")
        rings = {}
        for selector in ("main .field__input", "main .otp__cell", ".v-search input"):
            field = page.locator(selector).first
            field.focus()
            page.wait_for_timeout(400)  # the border's colour is a transition
            rings[selector] = field.evaluate(RING)
    finally:
        context.close()

    for selector, ring in rings.items():
        assert ring["visible"] and ring["colour"], (selector, ring)
        assert ring["blur"] == 0 and ring["spread"] >= 1, (selector, ring)
        assert contrast(ring["colour"], ring["ground"]) >= 3, (selector, ring)
        assert ring["colour"] == ring["border"], (selector, ring)


UNCLIPPED_RING = """e => {
    e.focus();
    const s = getComputedStyle(e);
    const grow = parseFloat(s.outlineOffset) + parseFloat(s.outlineWidth);
    const r = e.getBoundingClientRect();
    const ring = [r.left - grow, r.top - grow, r.right + grow, r.bottom + grow];
    for (let p = e.parentElement; p; p = p.parentElement) {
        const ps = getComputedStyle(p);
        if (ps.overflowX === 'visible' && ps.overflowY === 'visible') continue;
        const pr = p.getBoundingClientRect();
        const box = [pr.left + p.clientLeft, pr.top + p.clientTop,
                     pr.left + p.clientLeft + p.clientWidth, pr.top + p.clientTop + p.clientHeight];
        const inside = ring[0] >= box[0] - 0.5 && ring[1] >= box[1] - 0.5
            && ring[2] <= box[2] + 0.5 && ring[3] <= box[3] + 0.5;
        return {visible: e.matches(':focus-visible'), style: s.outlineStyle, inside};
    }
    return {visible: e.matches(':focus-visible'), style: s.outlineStyle, inside: true};
}"""


def test_no_focus_ring_is_cut_off_by_its_container(browser, live_server, shop):
    """The FAQ's question, the quantity stepper and the department links sit
    in boxes that clip their overflow, and their rings were cut to a line or
    two sides."""
    shop.plans.update(max_quantity=5)
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    found = {}
    try:
        for path, selector in [
            ("/faq/", ".qa__q"),
            # «+»: at one, «−» is disabled and takes no focus.
            ("/p/chatgpt-plus/", ".stepper__btn[data-step='1']"),
            ("/p/chatgpt-plus/", ".stepper__input"),
            ("/", ".v-desktop-cats a"),
        ]:
            page.goto(f"{live_server.url}{path}")
            assert page.evaluate(STYLED)
            # Keyboard focus, so :focus-visible applies as it does for a reader.
            page.keyboard.press("Tab")
            found[selector] = page.locator(selector).first.evaluate(UNCLIPPED_RING)
    finally:
        context.close()

    for selector, ring in found.items():
        assert ring["visible"] and ring["style"] != "none", (selector, ring)
        assert ring["inside"], (selector, ring)


@pytest.mark.parametrize("name", ["otp_code", "item_delivered", "signin_alert"])
def test_no_email_has_a_serious_accessibility_violation(browser, live_server, settings, name):
    """Grey text at 2.4:1 in every email's footer and under the code: the
    emails are scanned as they render, from their own inline styles."""
    settings.DEBUG = True
    context = browser.new_context(viewport={"width": 600, "height": 900})
    page = context.new_page()
    # The emails link a web font for the clients that load one; the scan must
    # not wait on the network, so every request off the test server is refused.
    page.route(
        "**/*",
        lambda route: (
            route.continue_() if route.request.url.startswith(live_server.url) else route.abort()
        ),
    )
    try:
        response = page.goto(f"{live_server.url}/dev/emails/{name}/")
        assert response.status == 200
        page.add_script_tag(path=str(AXE))
        result = page.evaluate(RUN_AXE)
    finally:
        context.close()

    assert result["passes"], "axe checked nothing"
    serious = [
        f"{v['id']} at {' '.join(map(str, node['target']))}"
        for v in result["violations"]
        if v["impact"] in SERIOUS
        for node in v["nodes"]
    ]
    assert not serious, serious
