"""The orbit keeps links reachable and stops before a visitor selects one."""

import pytest

from apps.catalog.models import Category, Product
from apps.core.tests.test_accessibility_browser import STYLED

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


def test_the_orbit_is_laid_out_before_unrelated_cart_code_finishes(browser, live_server):
    category = Category.objects.create(name="سرویس‌ها", slug="services")
    names = [
        "ChatGPT Plus",
        "Claude Pro",
        "Gemini Advanced",
        "GitHub Copilot",
        "JetBrains All Products",
        "Spotify Premium",
        "YouTube Premium",
        "Netflix",
        "Apple Gift Card",
        "Google Play Gift Card",
    ]
    Product.objects.bulk_create(
        [
            Product(category=category, name=name, slug=f"service-{i}", status="active")
            for i, name in enumerate(names)
        ]
    )
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


@pytest.mark.parametrize("count", [0, 1, 3, 6, 8, 9, 24, 60])
def test_every_service_is_reachable_once_with_static_phone_groups(browser, live_server, count):
    category = Category.objects.create(name="سرگرمی", slug="fun")
    Product.objects.bulk_create(
        [
            Product(category=category, name=f"Service {i}", slug=f"service-{i}", status="active")
            for i in range(count)
        ]
    )
    page = browser.new_page(viewport={"width": 390, "height": 844}, reduced_motion="reduce")
    try:
        page.goto(live_server.url + "/")
        page.wait_for_function(STYLED)
        page.wait_for_function(
            "document.querySelector('[data-hero-scene]').dataset.orbitMode === 'still'", polling=50
        )
        seen = []
        while True:
            visible = page.locator(".nebula__item:visible")
            seen.extend(visible.evaluate_all("items => items.map(e=>e.getAttribute('href'))"))
            assert visible.count() <= 6
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            if (
                page.locator("[data-orbit-next]").is_hidden()
                or page.locator("[data-orbit-next]").is_disabled()
            ):
                break
            page.locator("[data-orbit-next]").click()
        assert len(seen) == count == len(set(seen))
    finally:
        page.close()


def test_desktop_orbit_moves_and_freezes_for_selection(browser, live_server):
    category = Category.objects.create(name="سرگرمی", slug="fun")
    Product.objects.bulk_create(
        [
            Product(category=category, name=f"Service {i}", slug=f"service-{i}", status="active")
            for i in range(16)
        ]
    )
    page = browser.new_page(viewport={"width": 1440, "height": 1050})
    try:
        page.goto(live_server.url + "/")
        page.wait_for_function(STYLED)
        page.wait_for_function(
            "document.querySelector('[data-hero-scene]').dataset.orbitMode === 'orbit'"
        )
        item = page.locator(".nebula__item").first
        initial = item.evaluate("e=>e.style.transform")
        page.wait_for_timeout(350)
        assert item.evaluate("e=>e.style.transform") != initial
        page.locator(".nebula").hover()
        empty = item.evaluate("e=>e.style.transform")
        page.wait_for_timeout(350)
        assert item.evaluate("e=>e.style.transform") != empty
        name = item.locator(".v-service-name")
        box = name.bounding_box()
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        assert name.evaluate("e=>e.matches(':hover')")
        over_name = item.evaluate("e=>e.style.transform")
        page.wait_for_timeout(350)
        assert item.evaluate("e=>e.style.transform") != over_name
        box = item.locator(".v-service-mark").bounding_box()
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        frozen = item.evaluate("e=>e.style.transform")
        page.wait_for_timeout(350)
        assert item.evaluate("e=>e.style.transform") == frozen
        item.focus()
        page.mouse.move(0, 0)
        frozen = item.evaluate("e=>e.style.transform")
        page.wait_for_timeout(350)
        assert item.evaluate("e=>e.style.transform") == frozen
        page.locator(".v-brand").focus()
        page.mouse.move(0, 0)
        page.wait_for_timeout(350)
        assert item.evaluate("e=>e.style.transform") != frozen
        item.evaluate("e=>e.addEventListener('click',event=>event.preventDefault(),{once:true})")
        box = item.locator(".v-service-mark").bounding_box()
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        item.click()
        page.mouse.move(0, 0)
        frozen = item.evaluate("e=>e.style.transform")
        page.wait_for_timeout(350)
        assert item.evaluate("e=>e.style.transform") != frozen
        page.locator("[data-orbit-next]").click()
        assert page.locator(".nebula__item:visible").count() == 8
        item = page.locator(".nebula__item:visible").first
        frozen = item.evaluate("e=>e.style.transform")
        page.wait_for_timeout(350)
        assert item.evaluate("e=>e.style.transform") != frozen
        assert page.locator("[data-hero-toggle]").count() == 0
    finally:
        page.close()


@pytest.mark.parametrize("count", [6, 8])
@pytest.mark.parametrize("two_lines", [False, True])
def test_complete_orbit_keeps_padded_targets_apart_and_long_names_fall_back(
    browser, live_server, two_lines, count
):
    category = Category.objects.create(name="سرگرمی", slug="fun")
    products = [
        Product.objects.create(
            category=category,
            name="Google Play Gift Card" if two_lines else f"Service {i}",
            slug=f"service-{i}",
            status="active",
        )
        for i in range(count)
    ]
    page = browser.new_page(viewport={"width": 1440, "height": 1050})
    page.add_init_script(
        "window.requestAnimationFrame = callback => {window.orbitFrame = callback; return 1}; window.cancelAnimationFrame = () => {}"
    )
    try:
        page.goto(live_server.url + "/")
        page.wait_for_function(
            "window.orbitFrame && document.querySelector('[data-hero-scene]').dataset.orbitMode === 'orbit'",
            polling=50,
        )
        for step in range(121):
            failures = page.evaluate(
                """time => {
                window.orbitFrame(time);
                const track = document.querySelector('.nebula').getBoundingClientRect();
                const nodes = [...document.querySelectorAll('.nebula__item:not([hidden])')];
                const boxes = nodes.map(e => e.getBoundingClientRect());
                if (nodes.some((e,i)=>[...e.querySelectorAll('.v-service-mark,.v-service-name bdi')].some(child=> {const c=child.getBoundingClientRect(),a=boxes[i];return c.top<a.top || c.bottom>a.bottom || c.left<a.left || c.right>a.right}))) return true;
                return boxes.some((a,i)=> a.left < track.left || a.right > track.right || a.top < track.top || a.bottom > track.bottom || boxes.slice(i+1).some(b=>a.left < b.right+12 && a.right+12 > b.left && a.top < b.bottom+12 && a.bottom+12 > b.top));
            }""",
                1000 + step * 2000,
            )
            assert not failures, step
        products[0].name = "A very long service name " * 6
        products[0].save()
        page.reload()
        page.wait_for_function(
            "document.querySelector('[data-hero-scene]').dataset.orbitMode === 'still'", polling=50
        )
        assert page.locator(".nebula__item:visible").count() == 6
    finally:
        page.close()


def test_real_catalog_names_get_a_spaced_orbit_and_compact_numeric_paging(browser, live_server):
    category = Category.objects.create(name="محصولات", slug="services")
    names = [
        "Claude Pro",
        "GitHub Copilot",
        "Spotify Premium",
        "Apple Gift Card",
        "ChatGPT Plus",
        "JetBrains All Products",
        "YouTube Premium",
        "Google Play Gift Card",
    ]
    Product.objects.bulk_create(
        [
            Product(category=category, name=names[i % 8], slug=f"service-{i}", status="active")
            for i in range(24)
        ]
    )
    page = browser.new_page(viewport={"width": 1440, "height": 1050})
    try:
        page.goto(live_server.url + "/")
        page.wait_for_function(
            "document.querySelector('[data-hero-scene]').dataset.orbitMode !== undefined"
        )
        assert page.locator("[data-hero-scene]").get_attribute("data-orbit-mode") == "orbit"
        assert page.locator(".nebula__item:visible").count() == 6
        spacing = page.evaluate("""() => {
            const hero = document.querySelector('.v-hero').getBoundingClientRect();
            const heading = document.querySelector('.v-hero h1').getBoundingClientRect();
            const actions = document.querySelector('.v-hero__actions').getBoundingClientRect();
            return {height: hero.height, above: heading.top - hero.top,
                    below: hero.bottom - actions.bottom};
        }""")
        assert spacing["height"] <= 620, spacing
        assert spacing["above"] <= 145 and spacing["below"] <= 180, spacing
        assert 34 <= spacing["below"] - spacing["above"] <= 38, spacing
        assert page.locator("[data-orbit-range]").inner_text() == "۱–۶ / ۲۴"
        controls = page.locator(".v-orbit-controls").bounding_box()
        tools = page.locator(".v-scene-tools").bounding_box()
        caption = page.locator(".v-scene-caption").bounding_box()
        assert controls and caption and controls["width"] < 240
        assert abs(tools["x"] + tools["width"] / 2 - caption["x"] - caption["width"] / 2) < 1
        assert (
            page.locator("[data-orbit-range]").evaluate("e=>getComputedStyle(e).direction") == "ltr"
        )
        seen = []
        while True:
            seen.extend(
                page.locator(".nebula__item:visible").evaluate_all(
                    'es=>es.map(e=>e.getAttribute("href"))'
                )
            )
            if page.locator("[data-orbit-next]").is_disabled():
                break
            page.locator("[data-orbit-next]").click()
        assert len(seen) == len(set(seen)) == 24
    finally:
        page.close()


@pytest.mark.parametrize("javascript", [False, True])
def test_static_modes_keep_services_reachable_without_motion(browser, live_server, javascript):
    category = Category.objects.create(name="محصولات", slug="services")
    Product.objects.bulk_create(
        [
            Product(category=category, name=f"Service {i}", slug=f"service-{i}", status="active")
            for i in range(24)
        ]
    )
    page = browser.new_page(
        viewport={"width": 390 if not javascript else 1440, "height": 900},
        java_script_enabled=javascript,
        reduced_motion="reduce",
    )
    try:
        page.goto(live_server.url + "/")
        if javascript:
            page.wait_for_function(
                "document.querySelector('[data-hero-scene]').dataset.orbitMode==='still'"
            )
            assert page.locator(".nebula__item:visible").count() == 6
        else:
            assert page.locator(".nebula__item:visible").count() == 24
        assert page.locator("[data-hero-toggle]").count() == 0
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
    finally:
        page.close()


def test_resizing_keeps_the_focused_service_in_its_visible_group(browser, live_server):
    category = Category.objects.create(name="محصولات", slug="services")
    Product.objects.bulk_create(
        [
            Product(category=category, name=f"Service {i}", slug=f"service-{i}", status="active")
            for i in range(9)
        ]
    )
    page = browser.new_page(viewport={"width": 1440, "height": 1050})
    try:
        page.goto(live_server.url + "/")
        page.wait_for_function(
            "document.querySelector('[data-hero-scene]').dataset.orbitMode==='orbit'"
        )
        item = page.locator(".nebula__item").nth(7)
        item.focus()
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_function(
            "document.querySelector('[data-hero-scene]').dataset.orbitMode==='still'"
        )
        assert item.is_visible() and item.evaluate("e=>e===document.activeElement")
    finally:
        page.close()


def test_orbit_suspends_offscreen_and_on_a_visibility_event(browser, live_server):
    category = Category.objects.create(name="محصولات", slug="services")
    Product.objects.bulk_create(
        [
            Product(category=category, name=f"Service {i}", slug=f"service-{i}", status="active")
            for i in range(8)
        ]
    )
    page = browser.new_page(viewport={"width": 1440, "height": 1050})
    try:
        page.goto(live_server.url + "/")
        page.wait_for_function(
            "document.querySelector('[data-hero-scene]').dataset.orbitMode==='orbit'"
        )
        page.evaluate("scrollTo(0,document.body.scrollHeight)")
        page.wait_for_timeout(200)
        item = page.locator(".nebula__item").first
        frozen = item.evaluate("e=>e.style.transform")
        page.wait_for_timeout(250)
        assert item.evaluate("e=>e.style.transform") == frozen
        page.evaluate("scrollTo(0,0)")
        page.wait_for_timeout(300)
        assert item.evaluate("e=>e.style.transform") != frozen
        page.evaluate(
            "Object.defineProperty(document,'hidden',{value:true,configurable:true});document.dispatchEvent(new Event('visibilitychange'))"
        )
        frozen = item.evaluate("e=>e.style.transform")
        page.wait_for_timeout(250)
        assert item.evaluate("e=>e.style.transform") == frozen
        page.evaluate(
            "Object.defineProperty(document,'hidden',{value:false,configurable:true});document.dispatchEvent(new Event('visibilitychange'))"
        )
        page.wait_for_timeout(250)
        assert item.evaluate("e=>e.style.transform") != frozen
    finally:
        page.close()
