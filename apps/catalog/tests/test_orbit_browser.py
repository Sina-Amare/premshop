"""The orbit keeps links reachable and stops before a visitor selects one."""

import pytest

from apps.catalog.models import Category, Product
from apps.core.tests.test_accessibility_browser import STYLED

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


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
            for i in range(9)
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
        frozen = item.evaluate("e=>e.style.transform")
        page.wait_for_timeout(350)
        assert item.evaluate("e=>e.style.transform") == frozen
        item.focus()
        page.locator(".v-brand").focus()
        page.mouse.move(0, 0)
        page.wait_for_timeout(100)
        frozen = item.evaluate("e=>e.style.transform")
        page.wait_for_timeout(350)
        assert item.evaluate("e=>e.style.transform") == frozen
        assert page.locator("[data-hero-toggle]").inner_text() == "ادامه حرکت"
        page.locator("[data-hero-toggle]").click()
        page.locator(".v-brand").focus()
        page.mouse.move(0, 0)
        page.wait_for_timeout(350)
        assert item.evaluate("e=>e.style.transform") != frozen
        page.locator("[data-orbit-next]").click()
        assert page.locator(".nebula__item:visible").count() == 1
        assert page.locator("[data-hero-toggle]").is_hidden()
    finally:
        page.close()


def test_complete_orbit_keeps_padded_targets_apart_and_long_names_fall_back(browser, live_server):
    category = Category.objects.create(name="سرگرمی", slug="fun")
    products = [
        Product.objects.create(
            category=category, name=f"Service {i}", slug=f"service-{i}", status="active"
        )
        for i in range(8)
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
        assert page.locator("[data-orbit-range]").inner_text() == "۱–۶ / ۲۴"
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
