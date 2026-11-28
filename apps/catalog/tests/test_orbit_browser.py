"""The hero's services: an orbit round a centre card from 1200px, a card beside
the headline from 761px and without scripts, nothing on a phone."""

import re
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from playwright.sync_api import expect

from apps.catalog.models import Category, Plan, Product
from apps.core.formatting import format_toman
from apps.core.tests.test_accessibility_browser import STYLED

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]

DEPARTMENTS = {
    "ai": ("هوش مصنوعی", ["ChatGPT Plus", "Claude Pro", "Gemini Advanced", "Perplexity Pro"]),
    "dev": ("برنامه‌نویسی", ["GitHub Copilot", "JetBrains All Products", "Cursor Pro", "Figma"]),
    "media": ("سرگرمی", ["Spotify Premium", "YouTube Premium", "Netflix", "Steam Wallet"]),
    "gift": ("گیفت کارت", ["Apple Gift Card", "Google Play Gift Card", "PlayStation", "Xbox"]),
}
# Every frame of a whole turn, the closest any two logos, names or the centre card come.
TURN = """() => {
    const core = document.querySelector('.po-core').getBoundingClientRect();
    const cx = core.left + core.width / 2, cy = core.top + core.height / 2, r = core.width / 2;
    const apart = (a, b) => Math.max(a.left - b.right, b.left - a.right, a.top - b.bottom, b.top - a.bottom);
    const fromCore = b => Math.hypot(Math.max(b.left - cx, 0, cx - b.right), Math.max(b.top - cy, 0, cy - b.bottom)) - r;
    let closest = Infinity, time = 1000;
    window.orbitFrame(time);
    for (let step = 0; step < 360; step++) {
        window.orbitFrame(time += 150000 / 360);
        const nodes = [...document.querySelectorAll('.po-node')].map(node =>
            [...node.querySelectorAll('.po-plate, .po-label')].map(e => e.getBoundingClientRect()).filter(b => b.width > 1));
        nodes.forEach((boxes, i) => boxes.forEach(a => {
            closest = Math.min(closest, fromCore(a));
            nodes.slice(i + 1).forEach(other => other.forEach(b => { closest = Math.min(closest, apart(a, b)); }));
        }));
    }
    return closest;
}"""
STILL_FRAMES = "window.requestAnimationFrame = f => { window.orbitFrame = f; return 1 }; window.cancelAnimationFrame = () => {}"


@pytest.fixture
def catalog():
    """Sixteen services in four departments; the orbit takes the shelf's first twelve,
    three of each, the longest names among them."""
    products = {}
    for order, (slug, (name, names)) in enumerate(DEPARTMENTS.items()):
        category = Category.objects.create(name=name, slug=slug, sort_order=order)
        for i, title in enumerate(names):
            product = Product.objects.create(
                category=category,
                name=title,
                slug=f"{slug}-{i}",
                delivery_hours=12,
                status="active",
            )
            Plan.objects.create(
                product=product,
                title="۱ ماهه",
                cost_price=1,
                sale_price=Decimal(1_000_000 + 10_000 * (order * 4 + i)),
            )
            products[title] = product
    return products


def lit(page) -> str:
    return page.locator(".po-node.is-lit .po-label").inner_text()


def test_the_orbit_is_laid_out_before_unrelated_cart_code_finishes(browser, live_server, catalog):
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    held = []
    try:
        page.add_init_script(
            "window.homeShift=0;new PerformanceObserver(xs=>xs.getEntries().forEach(x=>{if(!x.hadRecentInput)window.homeShift+=x.value})).observe({type:'layout-shift',buffered:true});"
        )
        page.route("**/static/js/cart.js", lambda route: held.append(route))
        page.goto(live_server.url + "/", wait_until="commit")
        page.wait_for_function(
            "performance.getEntriesByType('paint').some(x=>x.name==='first-contentful-paint')"
        )
        assert len(held) == 1
        held.pop().continue_()
        page.wait_for_load_state()
        page.evaluate("() => document.fonts.ready")
        page.wait_for_timeout(500)
        assert page.evaluate("window.homeShift") < 0.1
    finally:
        for route in held:
            route.continue_()
        page.close()


def test_late_fonts_do_not_move_the_home_hero(browser, live_server, catalog):
    """In the wider fallback face the hero's two facts wrapped onto two lines,
    so the copy grew a line and shifted when the web fonts arrived."""
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        page.add_init_script(
            "window.homeShift=0;new PerformanceObserver(xs=>xs.getEntries().forEach(x=>{if(!x.hadRecentInput)window.homeShift+=x.value})).observe({type:'layout-shift',buffered:true});"
        )
        page.route("**/*.woff2", lambda route: (page.wait_for_timeout(1000), route.continue_()))
        page.goto(live_server.url + "/")
        page.evaluate("() => document.fonts.ready")
        page.wait_for_timeout(300)
        assert page.evaluate("window.homeShift") < 0.01
    finally:
        page.close()


@pytest.mark.parametrize("width", [1200, 1440, 1920])
def test_a_whole_turn_keeps_logos_names_and_the_centre_apart(browser, live_server, catalog, width):
    page = browser.new_page(viewport={"width": width, "height": 1000})
    page.add_init_script(STILL_FRAMES)
    try:
        page.goto(live_server.url + "/")
        page.wait_for_function(STYLED)
        page.evaluate("() => document.fonts.ready")
        page.wait_for_function("typeof window.orbitFrame === 'function'", polling=50)
        stage = page.locator(".po-stage").bounding_box()
        assert abs(stage["width"] - stage["height"]) < 1 and stage["width"] >= 560, stage
        assert page.locator(".po-node").count() == 12
        assert page.locator(".po-node--inner").count() == 4
        closest = page.evaluate(TURN)
        assert closest >= 8, closest
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    finally:
        page.close()


def test_the_centre_names_the_lit_service_with_its_price_and_delivery(
    browser, live_server, catalog
):
    product = catalog["JetBrains All Products"]
    Plan.objects.create(product=product, title="۱ ساله", cost_price=1, sale_price=9_000_000)
    price = format_toman(product.plans.order_by("sale_price").first().sale_price, with_unit=False)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.clock.install()
    try:
        page.goto(live_server.url + "/")
        page.wait_for_function(STYLED)
        logo = page.locator(".po-node a", has_text=product.name)
        expect(logo).to_have_accessible_name(
            re.compile(rf"^{product.name}\s*، از {price} تومان\s*، تحویل تا ۱۲ ساعت\s*$")
        )
        logo.hover()
        core = page.locator(".po-core")
        expect(core.locator(".po-core__name")).to_have_text(product.name)
        expect(core.locator(".po-core__dept")).to_have_text("برنامه‌نویسی")
        expect(core.locator(".po-price")).to_have_text(re.compile(f"از\\s*{price}\\s*تومان"))
        expect(core.locator(".po-core__sla")).to_have_text("تحویل تا ۱۲ ساعت")
        go = core.locator(".po-core__go")
        expect(go).to_have_attribute("href", product.get_absolute_url())
        expect(go).to_have_accessible_name(re.compile(f"دیدن محصول.*{product.name}"))
        assert lit(page) == product.name
        # The price is the cards' own face, not a weight the font files lack.
        number = core.locator(".price__num")
        assert number.evaluate("e => getComputedStyle(e).fontWeight") == "800"
        assert page.locator(".pcard .price__num").first.evaluate(
            "e => getComputedStyle(e).fontFamily"
        ) == number.evaluate("e => getComputedStyle(e).fontFamily")
    finally:
        page.close()


def test_a_free_service_reads_free_in_the_centre(browser, live_server, catalog):
    Plan.objects.filter(product=catalog["ChatGPT Plus"]).update(sale_price=0)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    try:
        page.goto(live_server.url + "/")
        expect(page.locator(".po-core .po-price")).to_have_text("رایگان")
    finally:
        page.close()


def test_a_discount_ending_switches_the_centre_price(browser, live_server, catalog):
    product = catalog["ChatGPT Plus"]
    plan = product.plans.get()
    plan.promo_price = Decimal(500_000)
    plan.promo_ends_at = timezone.now() + timedelta(seconds=60)
    plan.save()
    after = format_toman(plan.sale_price, with_unit=False)
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
    page.clock.install()
    try:
        page.goto(live_server.url + "/")
        price = page.locator(".po-core .price__num")
        expect(price).to_have_text(format_toman(500_000, with_unit=False))
        page.clock.run_for(62_000)
        expect(price).to_have_text(after)
        expect(page.locator(".po-node a").first).to_have_accessible_name(
            re.compile(rf"^ChatGPT Plus\s*، {after} تومان\s*، ")
        )
    finally:
        page.close()


def test_pointing_at_or_focusing_a_logo_stops_the_turn_and_the_cycle(browser, live_server, catalog):
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.clock.install()
    try:
        page.goto(live_server.url + "/")
        page.wait_for_function(STYLED)
        expect(page.locator("html")).to_have_attribute("data-motion", "on")
        node = page.locator(".po-node").first
        before, first = node.get_attribute("style"), lit(page)
        page.clock.run_for(3500)
        assert node.get_attribute("style") != before, "the orbit turns on its own"
        assert lit(page) != first, "the centre moves to the next service"
        logo = page.locator(".po-node a").nth(5)
        logo.hover()
        held, name = node.get_attribute("style"), lit(page)
        assert name == logo.locator(".po-label").inner_text()
        page.clock.run_for(8000)
        assert (node.get_attribute("style"), lit(page)) == (held, name)
        # Reaching for the centre's link must not swap the service under the pointer.
        page.locator(".po-core__go").hover()
        page.clock.run_for(8000)
        assert (node.get_attribute("style"), lit(page)) == (held, name)
        page.mouse.move(5, 5)
        page.clock.run_for(3500)
        assert node.get_attribute("style") != held and lit(page) != name
        page.locator(".po-node a").nth(9).focus()
        held, name = node.get_attribute("style"), lit(page)
        page.clock.run_for(8000)
        assert (node.get_attribute("style"), lit(page)) == (held, name)
        page.locator(".v-brand").focus()
        page.clock.run_for(3500)
        assert node.get_attribute("style") != held
        assert page.locator("[data-orbit-toggle],[data-hero-toggle]").count() == 0
    finally:
        page.close()


def test_a_department_pill_dims_the_other_departments(browser, live_server, catalog):
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    try:
        page.goto(live_server.url + "/")
        page.wait_for_function(STYLED)
        page.locator('.v-hero__chip[data-dept="media"]').hover()
        expect(page.locator(".po-node.is-dim")).to_have_count(9)
        expect(page.locator('.po-node[data-dept="media"].is-dim')).to_have_count(0)
        expect(page.locator(".po-core .po-core__dept")).to_have_text("سرگرمی")
        assert lit(page) == "Spotify Premium"
        page.mouse.move(5, 5)
        expect(page.locator(".po-node.is-dim")).to_have_count(0)
    finally:
        page.close()


def test_under_reduced_motion_the_orbit_stands_still(browser, live_server, catalog):
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
    page.clock.install()
    try:
        page.goto(live_server.url + "/")
        page.wait_for_function(STYLED)
        expect(page.locator("[data-hero-scene]")).to_have_class(re.compile(r"\bis-orbit\b"))
        node = page.locator(".po-node").first
        before, first = node.get_attribute("style"), lit(page)
        page.clock.run_for(8000)
        assert (node.get_attribute("style"), lit(page)) == (before, first)
    finally:
        page.close()


def test_the_orbit_suspends_off_screen_and_on_a_visibility_event(browser, live_server, catalog):
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    try:
        page.goto(live_server.url + "/")
        page.wait_for_function(STYLED)
        expect(page.locator("html")).to_have_attribute("data-motion", "on")
        page.evaluate("scrollTo(0,document.body.scrollHeight)")
        page.wait_for_timeout(200)
        node = page.locator(".po-node").first
        frozen = node.get_attribute("style")
        page.wait_for_timeout(250)
        assert node.get_attribute("style") == frozen
        page.evaluate("scrollTo(0,0)")
        page.wait_for_timeout(300)
        assert node.get_attribute("style") != frozen
        page.evaluate(
            "Object.defineProperty(document,'hidden',{value:true,configurable:true});document.dispatchEvent(new Event('visibilitychange'))"
        )
        frozen = node.get_attribute("style")
        page.wait_for_timeout(250)
        assert node.get_attribute("style") == frozen
        page.evaluate(
            "Object.defineProperty(document,'hidden',{value:false,configurable:true});document.dispatchEvent(new Event('visibilitychange'))"
        )
        page.wait_for_timeout(250)
        assert node.get_attribute("style") != frozen
    finally:
        page.close()


def card_layout(page) -> None:
    """The centre as a row above the twelve logos, six to a row, names for screen readers only."""
    scene = page.locator("[data-hero-scene]")
    expect(scene).to_be_visible()
    expect(page.locator(".po-dial")).to_be_hidden()
    core = page.locator(".po-core").bounding_box()
    plates = [box for box in (p.bounding_box() for p in page.locator(".po-plate").all())]
    assert len(plates) == 12
    assert core["y"] + core["height"] <= min(box["y"] for box in plates)
    assert len({round(box["y"] + box["height"] / 2) for box in plates[:6]}) == 1
    assert plates[6]["y"] > plates[0]["y"] + plates[0]["height"]
    assert all(label.bounding_box()["width"] <= 1 for label in page.locator(".po-label").all())
    expect(page.locator(".po-node a").nth(5)).to_have_accessible_name(
        re.compile(r"^JetBrains All Products\s*، ")
    )
    hrefs = page.locator(".po-node a").evaluate_all(
        "links => links.map(a => a.getAttribute('href'))"
    )
    assert len(set(hrefs)) == 12
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


@pytest.mark.parametrize("width", [761, 1000, 1199])
def test_from_761_to_1199_the_services_are_a_card_beside_the_headline(
    browser, live_server, catalog, width
):
    page = browser.new_page(viewport={"width": width, "height": 900})
    try:
        page.goto(live_server.url + "/")
        page.wait_for_function(STYLED)
        card_layout(page)
        scene = page.locator("[data-hero-scene]").bounding_box()
        title = page.locator(".v-hero h1").bounding_box()
        assert (
            scene["y"] < title["y"] + title["height"] and title["y"] < scene["y"] + scene["height"]
        )
    finally:
        page.close()


@pytest.mark.parametrize("width", [1000, 1440])
def test_without_javascript_the_card_layout_keeps_every_link(browser, live_server, catalog, width):
    page = browser.new_page(viewport={"width": width, "height": 900}, java_script_enabled=False)
    try:
        page.goto(live_server.url + "/")
        page.wait_for_function(STYLED)
        card_layout(page)
        expect(page.locator(".po-core .po-core__name")).to_have_text("ChatGPT Plus")
        expect(page.locator(".po-core .po-core__go")).to_have_attribute(
            "href", catalog["ChatGPT Plus"].get_absolute_url()
        )
    finally:
        page.close()


@pytest.mark.parametrize("width", [390, 760])
def test_on_a_phone_the_hero_ends_at_its_buttons(browser, live_server, catalog, width):
    page = browser.new_page(viewport={"width": width, "height": 900})
    try:
        page.goto(live_server.url + "/")
        page.wait_for_function(STYLED)
        expect(page.locator("[data-hero-scene]")).to_be_hidden()
        hero = page.locator(".v-hero").bounding_box()
        actions = page.locator(".v-hero__actions").bounding_box()
        assert hero["y"] + hero["height"] - (actions["y"] + actions["height"]) < 60
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    finally:
        page.close()
