"""Automatic shelves give the visitor time to read and choose."""

import re

import pytest
from playwright.sync_api import expect

from apps.catalog.models import Category, Plan, Product

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


@pytest.fixture
def catalog():
    for group in range(2):
        category = Category.objects.create(
            name=f"دسته {group}", slug=f"group-{group}", sort_order=group
        )
        for i in range(8):
            product = Product.objects.create(
                category=category,
                name=f"Service {group}-{i}",
                slug=f"service-{group}-{i}",
                status="active",
            )
            Plan.objects.create(
                product=product,
                title="۱ ماهه",
                cost_price=1,
                sale_price=200000,
                promo_price=150000 if group == 0 else None,
            )


def place_two_shelves(page):
    page.evaluate("""() => {
      const roots=[...document.querySelectorAll('[data-rail]')];
      const a=roots[0].getBoundingClientRect(), b=roots[1].getBoundingClientRect();
      const header=document.querySelector('.v-header').getBoundingClientRect().height;
      scrollTo(0,scrollY+(a.top+b.bottom)/2-(innerHeight+header)/2);
    }""")
    page.mouse.move(0, 0)
    page.wait_for_timeout(300)
    assert (
        page.locator("[data-rail]")
        .nth(0)
        .evaluate("e=>{let r=e.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight}")
    )
    assert (
        page.locator("[data-rail]")
        .nth(1)
        .evaluate("e=>{let r=e.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight}")
    )


def test_only_the_most_visible_shelf_advances_and_focus_restarts_the_full_wait(
    browser, live_server, catalog
):
    page = browser.new_page(viewport={"width": 1440, "height": 2000})
    try:
        page.goto(live_server.url + "/")
        expect(page.locator("[data-hero-toggle],[data-rail-toggle]")).to_have_count(0)
        place_two_shelves(page)
        tracks = page.locator("[data-rail-track]")
        first, second = tracks.nth(0), tracks.nth(1)
        page.wait_for_function(
            "Math.abs(document.querySelector('[data-rail-track]').scrollLeft)>50", timeout=8000
        )
        assert second.evaluate("e=>e.scrollLeft") == 0
        page.locator("[data-rail]").first.locator("[data-rail-next]").focus()
        before = first.evaluate("e=>e.scrollLeft")
        page.wait_for_timeout(6300)
        assert first.evaluate("e=>e.scrollLeft") == before
        page.evaluate("document.activeElement.blur()")
        page.wait_for_timeout(1200)
        assert first.evaluate("e=>e.scrollLeft") == before
        page.wait_for_function(
            '(before)=>Math.abs(document.querySelector("[data-rail-track]").scrollLeft-before)>50',
            arg=before,
            timeout=6500,
        )
        first.focus()
        first.press("End")
        expect(page.locator("[data-rail]").first.locator("[data-rail-next]")).to_be_disabled()
        page.evaluate("document.activeElement.blur()")
        page.wait_for_function(
            'Math.abs(document.querySelector("[data-rail-track]").scrollLeft)<3', timeout=8000
        )
        assert first.locator(".pcard").count() == 8
    finally:
        page.close()


def test_motion_preference_persists_and_system_reduction_wins(browser, live_server, catalog):
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    try:
        page.goto(live_server.url + "/")
        page.mouse.move(0, 0)
        item = page.locator(".nebula__item").first
        page.wait_for_timeout(250)
        moving = item.get_attribute("style")
        page.wait_for_timeout(350)
        assert item.get_attribute("style") != moving
        page.locator("[data-motion-settings] summary").click()
        page.locator("[data-motion-preference]").uncheck()
        page.reload()
        page.evaluate("scrollTo(0,0)")
        page.mouse.move(0, 0)
        page.wait_for_timeout(250)
        expect(page.locator("html")).to_have_attribute("data-motion", "off")
        expect(page.locator("[data-hero-scene]")).to_have_class(re.compile(r".*is-paused.*"))
        stopped = item.get_attribute("style")
        page.wait_for_timeout(350)
        assert item.get_attribute("style") == stopped
        page.locator("[data-motion-settings] summary").click()
        page.locator("[data-motion-preference]").check()
        page.emulate_media(reduced_motion="reduce")
        expect(page.locator("[data-motion-preference]")).not_to_be_checked()
        expect(page.locator("[data-motion-preference]")).to_be_disabled()
        expect(page.locator("html")).to_have_attribute("data-motion", "off")
    finally:
        page.close()


@pytest.mark.parametrize("width,reduction", [(390, "no-preference"), (1440, "reduce")])
def test_phone_and_reduced_motion_shelves_do_not_advance(
    browser, live_server, catalog, width, reduction
):
    page = browser.new_page(viewport={"width": width, "height": 1000}, reduced_motion=reduction)
    try:
        page.goto(live_server.url + "/")
        rail = page.locator("[data-rail]").first
        rail.scroll_into_view_if_needed()
        page.mouse.move(0, 0)
        page.wait_for_timeout(6300)
        assert rail.locator("[data-rail-track]").evaluate("e=>e.scrollLeft") == 0
        rail.locator("[data-rail-next]").click()
        page.wait_for_function(
            'Math.abs(document.querySelector("[data-rail-track]").scrollLeft)>50'
        )
    finally:
        page.close()


def test_open_plan_dialog_holds_all_motion(browser, live_server, catalog):
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    try:
        page.goto(live_server.url + "/")
        page.locator("[data-rail]").first.locator("[data-quick-open]").first.click()
        expect(page.locator("#quick-plans")).to_be_visible()
        expect(page.locator("html")).to_have_attribute("data-motion", "off")
        track = page.locator("[data-rail-track]").first
        before = track.evaluate("e=>e.scrollLeft")
        page.wait_for_timeout(6300)
        assert track.evaluate("e=>e.scrollLeft") == before
        page.keyboard.press("Escape")
        expect(page.locator("html")).to_have_attribute("data-motion", "on")
    finally:
        page.close()
