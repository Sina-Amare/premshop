"""Every key page, scanned by axe-core in a real Chromium.

axe reads the page as rendered: computed colours against their real
backgrounds, the accessibility tree, the names that labels and ARIA give. The
test client sees none of it, and neither does a browser without the built
stylesheet, which is why CI builds the CSS before this runs.

The bar is WCAG 2 A and AA, and a violation of impact serious or critical
fails. What the scan found the day it arrived is in ALLOWED, each entry with
its reason. An entry that no longer occurs fails as well, so the list can only
shrink.
"""

from __future__ import annotations

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
RUN_AXE = """() => axe.run(document, {
    runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa']},
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

# (rule, page, element) for each violation that fails today, with its reason.
# Per element, so a second violation of an allowed rule on the same page still
# fails.
STOREFRONT = [*VISITOR, "code entry", *CUSTOMER]
ALLOWED: set[tuple[str, str, str]] = {
    # At phone width the header's support link keeps only its icon, which is
    # aria-hidden, so the one way to reach a person has no name.
    *(("link-name", page, ".hdr__support") for page in STOREFRONT),
    # Grey #737373 on the deals band's #f5f5f4 is 4.34:1, under 4.5.
    ("color-contrast", "home", ".deals__said > .small"),
    # The struck-through price on the buy box's tint is 4.29:1; the same grey
    # passes on a white card.
    ("color-contrast", "product", ".price__was"),
    # «۴۰۴» in #d4d4d4 is 1.48:1, and text that large needs 3:1.
    ("color-contrast", "404", ".notfound__code"),
    # Unfold's tabular inline puts each spec row's inputs in table cells with no
    # label; three empty rows, three inputs each.
    *(
        ("label", "panel: product form", f"#id_specs-{row}-{field}")
        for row in range(3)
        for field in ("title", "value", "is_key")
    ),
}


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
