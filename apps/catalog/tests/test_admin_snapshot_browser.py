"""Context previews stay beside the form and preserve unsaved edits."""

import re
from base64 import b64decode

import pytest
from playwright.sync_api import expect

from apps.catalog.models import Plan, ProductSection
from apps.catalog.tests.test_admin_browser import change_form, with_sections_and_a_question
from apps.catalog.tests.test_admin_browser import open_panel as open_panel
from apps.catalog.tests.test_admin_browser import product as product

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


def test_escape_closes_quick_plans_before_the_enclosing_admin_preview(open_panel, product):
    page = open_panel(change_form(product), width=390)
    page.locator("[data-snapshot-open]").click()
    expect(page.locator("[data-snapshot-status]")).to_contain_text("آماده‌ست")
    frame = page.frame_locator(".snapshot-panel iframe")
    frame.locator("[data-quick-open]").first.click()
    expect(frame.locator("#quick-plans")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator(".snapshot-panel")).to_be_visible()
    expect(frame.locator("#quick-plans")).not_to_be_visible()


def test_colour_preview_keeps_last_valid_tint_and_reset_uses_the_storefront_neutral(
    open_panel, product
):
    page = open_panel(change_form(product))
    field = page.locator("#id_brand_colour")
    art = page.locator("[data-brand-preview] .pcard__art")
    field.fill("#ff0000")
    first = art.evaluate("e=>getComputedStyle(e).backgroundColor")
    assert "oklab" in first or "rgb" in first
    field.fill("invalid")
    expect(page.locator("[data-brand-error]")).to_be_visible()
    assert art.evaluate("e=>getComputedStyle(e).backgroundColor") == first
    page.locator("[data-brand-reset]").click()
    expect(field).to_have_value("")
    expect(page.locator("[data-brand-error]")).to_be_hidden()
    assert art.evaluate("e=>getComputedStyle(e).backgroundColor") != first


@pytest.mark.parametrize("width", [390, 1440])
def test_unsaved_preview_refresh_errors_and_close_preserve_editor(open_panel, product, width):
    page = open_panel(change_form(product), width=width)
    page.fill("#id_name", "Unsaved service")
    page.locator("[data-snapshot-open]").click()
    expect(page.locator("[data-snapshot-status]")).to_contain_text("آماده‌ست")
    frame = page.frame_locator(".snapshot-panel iframe")
    expect(frame.locator(".pdp__title")).to_have_text("Unsaved service")
    expect(frame.locator("[data-preview-target=card] .pcard__name")).to_have_text("Unsaved service")
    expect(page.locator(".snapshot-panel")).to_have_attribute("role", "region")
    assert page.locator("#product_form").evaluate("e=>!e.inert")
    page.locator("[data-snapshot-close]").click()
    expect(page.locator("#id_name")).to_have_value("Unsaved service")
    page.fill("#id_name", "Changed again")
    page.locator("[data-snapshot-open]").click()
    expect(page.locator("[data-snapshot-status]")).to_contain_text("فرم تغییر کرده")
    page.locator("[data-snapshot-refresh]").click()
    expect(frame.locator(".pdp__title")).to_have_text("Changed again")
    page.locator("[data-snapshot-close]").click()
    page.fill("#id_name", "")
    page.locator("[data-snapshot-open]").click()
    page.locator("[data-snapshot-refresh]").click()
    expect(page.locator("[data-snapshot-errors] [data-field=id_name]")).to_be_visible()
    expect(frame.locator(".pdp__title")).to_have_text("Changed again")
    page.locator("[data-snapshot-errors] [data-field=id_name]").click()
    expect(page.locator("#id_name")).to_be_focused()
    expect(page.locator(".snapshot-panel")).to_be_hidden()
    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")


def test_product_jumps_open_existing_sections_and_keep_focus_clear(open_panel, product):
    page = open_panel(change_form(product), width=390)
    links = page.locator(".panel-section-jumps a")
    expect(links).to_have_count(10)
    expect(links.first).to_have_text("مشخصات اصلی")
    search = links.filter(has_text="نام‌های جستجو")
    search.click()
    target = page.locator(search.get_attribute("href"))
    expect(target.locator("details")).to_have_attribute("open", "")
    box = target.bounding_box()
    assert box["y"] >= 0
    assert box["y"] < 200
    expect(page.locator("#id_aliases")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_admin_text_and_select_focus_are_single_edges_and_errors_stay_visible(open_panel, product):
    page = open_panel(change_form(product))
    for selector in ("#id_name", "#id_status", "#id_short_description"):
        control = page.locator(selector)
        control.focus()
        style = control.evaluate(
            "e=>{const s=getComputedStyle(e);return {outline:s.outlineStyle,shadow:s.boxShadow,border:s.borderTopColor}}"
        )
        assert style["outline"] == "none"
        assert "rgb(36, 89, 211)" in style["shadow"]
        assert style["border"] == "rgb(36, 89, 211)"
    page.fill("#id_name", "")
    page.locator("#submit-row button[name=_continue]").click()
    expect(page.locator(".errornote")).to_be_visible()
    control = page.locator("#id_name")
    control.focus()
    assert control.evaluate("e=>getComputedStyle(e).borderTopColor") == "rgb(189, 45, 53)"
    assert "189, 45, 53" in control.evaluate("e=>getComputedStyle(e).boxShadow")


def test_dark_focus_and_english_section_jumps_still_work(open_panel, product):
    page = open_panel(change_form(product), width=390)
    page.evaluate(
        "document.cookie='django_language=en; path=/';localStorage.setItem('adminTheme',JSON.stringify('dark'))"
    )
    page.reload()
    expect(page.locator("html")).to_have_class("dark")
    expect(page.locator(".panel-section-jumps a").first).to_have_text("Basic details")
    field = page.locator("#id_name")
    field.focus()
    expect(field).to_be_focused()
    expect(field).to_have_css("border-top-color", "rgb(145, 175, 241)")


def test_only_current_sidebar_destination_has_the_selected_surface(open_panel, product):
    for route in ("product", "plan"):
        page = open_panel(f"/admin/catalog/{route}/")
        page.mouse.move(1, 1)
        links = page.locator(
            '#nav-sidebar a[href^="/admin/catalog/"],#nav-sidebar a[href^="/admin/accounts/"]'
        )
        selected = links.evaluate_all(
            "es=>es.filter(e=>getComputedStyle(e).backgroundColor==='rgb(237, 242, 255)').map(e=>e.getAttribute('href'))"
        )
        assert selected == [f"/admin/catalog/{route}/"]


def test_panel_guidance_is_natural_and_publishing_requirements_stay_precise(open_panel, product):
    page = open_panel(change_form(product))
    expect(page.locator("label[for=id_slug]")).to_contain_text("نام در لینک")
    expect(page.locator(".field-delivery_hours")).to_contain_text("بین ۱ تا ۷۲ ساعت")
    expect(page.locator(".field-delivery_note")).to_contain_text("اگه این بخش رو پر کنید")
    expect(page.locator(".panel-readiness")).to_contain_text("نکات استفاده رو بنویسید")
    expect(page.locator(".field-usage_notes")).to_contain_text("موضوعیت ندارد")
    page.fill("#id_name", "")
    page.locator("#submit-row button[name=_continue]").click()
    expect(page.locator(".errornote")).to_contain_text("ذخیره نشد. خطاهای زیر رو اصلاح کنید.")
    expect(page.locator(".field-name")).to_contain_text("این قسمت رو پر کنید.")


def test_plan_guidance_applies_to_costs_and_supplier_links(open_panel, product):
    page = open_panel(change_form(product))
    for field in ("supplier_url", "cost_price"):
        guidance = page.locator(f"#id_plans-0-{field}_helptext")
        expect(guidance).to_contain_text("فقط توی پنل")
        expect(guidance).to_contain_text("مشتری بهش دسترسی نداره")
    expect(page.locator("#id_plans-0-supplier_url_helptext")).not_to_contain_text("این قیمت")


def test_title_error_requires_removing_the_store_name(open_panel, product):
    page = open_panel(change_form(product))
    page.locator(".panel-section-jumps a").filter(has_text="عنوان و توضیح جستجو").click()
    page.fill("#id_seo_title", "PremShop Plus")
    page.locator("#submit-row button[name=_continue]").click()
    expect(page.locator(".field-seo_title")).to_contain_text("نام فروشگاه رو اینجا ننویسید")
    expect(page.locator(".field-seo_title")).not_to_contain_text("لازم نیست")


def test_field_destination_and_stale_network_failure_preserve_the_last_snapshot(
    open_panel, product
):
    page = open_panel(change_form(product))
    page.locator("[data-snapshot-field=name]").click()
    frame = page.frame_locator(".snapshot-panel iframe")
    expect(frame.locator(".pdp__title")).to_have_class("pdp__title snapshot-highlight")
    page.locator("[data-snapshot-close]").click()
    page.fill("#id_name", "Not loaded yet")
    page.locator("[data-snapshot-open]").click()
    page.route("**/preview/", lambda route: route.abort())
    page.locator("[data-snapshot-refresh]").click()
    expect(page.locator("[data-snapshot-status]")).to_contain_text("نسخه‌ی قبلی")
    expect(frame.locator(".pdp__title")).to_have_text(product.name)
    page.locator("[data-snapshot-close]").click()
    expect(page.locator("#id_name")).to_have_value("Not loaded yet")


def test_late_snapshot_response_cannot_replace_a_newer_one(open_panel, product):
    page = open_panel(change_form(product))
    page.locator("[data-snapshot-open]").click()
    expect(page.locator("[data-snapshot-status]")).to_contain_text("آماده‌ست")
    held = []
    page.route("**/preview/", lambda route: held.append(route))
    page.locator("[data-snapshot-refresh]").click()
    page.locator("[data-snapshot-refresh]").click()
    page.wait_for_timeout(100)
    assert len(held) == 2
    held[1].fulfill(
        status=200, content_type="text/html", body="<html><body><h1>Newer</h1></body></html>"
    )
    frame = page.frame_locator(".snapshot-panel iframe")
    expect(frame.locator("h1")).to_have_text("Newer")
    held[0].fulfill(
        status=200, content_type="text/html", body="<html><body><h1>Older</h1></body></html>"
    )
    page.wait_for_timeout(100)
    expect(frame.locator("h1")).to_have_text("Newer")


def test_field_guidance_aligns_below_its_input(open_panel, product):
    page = open_panel(change_form(product))
    control = page.locator("#id_name").bounding_box()
    note = page.locator("[data-snapshot-field=name]").locator("..").bounding_box()
    assert note["x"] >= control["x"] - 1
    assert note["x"] + note["width"] <= control["x"] + control["width"] + 1
    assert note["y"] >= control["y"] + control["height"]


@pytest.mark.parametrize("width", [390, 1024, 1280, 1440, 1920])
def test_preview_reserves_space_without_covering_the_editor(open_panel, product, width):
    page = open_panel(change_form(product), width=width)
    page.locator("[data-snapshot-open]").click()
    expect(page.locator("[data-snapshot-status]")).to_contain_text("آماده‌ست")
    panel = page.locator(".snapshot-panel").bounding_box()
    form = page.locator("#product_form").bounding_box()
    assert panel["height"] <= 740
    assert panel["x"] >= 0 and panel["x"] + panel["width"] <= width
    if width >= 1024:
        assert 320 <= panel["width"] <= 400
        assert form["width"] >= 580
        assert panel["x"] + panel["width"] + 15 <= form["x"]
        page.locator("#id_name").fill("Editing beside the preview")
        expect(page.locator("[data-snapshot-status]")).to_contain_text("فرم تغییر کرده")
        page.locator("#id_plans-0-sale_price").scroll_into_view_if_needed()
        panel = page.locator(".snapshot-panel").bounding_box()
        save = page.locator("#submit-row").bounding_box()
        assert panel["y"] >= 65
        assert panel["y"] + panel["height"] <= save["y"]
    else:
        assert panel["y"] + panel["height"] + 15 <= form["y"]
        assert page.locator("#product_form").evaluate("e=>!e.inert")
        page.locator("[data-snapshot-close]").click()
        expect(page.locator("[data-snapshot-open]")).to_be_focused()
    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")


@pytest.mark.parametrize("width", [390, 1440])
def test_full_screen_is_explicit_and_restores_inline_editing(open_panel, product, width):
    page = open_panel(change_form(product), width=width)
    trigger = page.locator("[data-snapshot-field=name]")
    trigger.click()
    panel = page.locator(".snapshot-panel")
    expect(panel).to_have_attribute("role", "region")
    expect(page.locator("[data-snapshot-status]")).to_contain_text("آماده‌ست")
    page.locator("[data-snapshot-expand]").click()
    expect(panel).to_have_attribute("aria-modal", "true")
    assert page.locator("#product_form").evaluate("e=>e.inert")
    page.locator("[data-snapshot-refresh]").focus()
    page.keyboard.press("Shift+Tab")
    expect(panel.locator("iframe")).to_be_focused()
    page.locator("[data-snapshot-expand]").click()
    expect(panel).to_have_attribute("role", "region")
    assert page.locator("#product_form").evaluate("e=>!e.inert")
    page.locator("[data-snapshot-close]").click()
    expect(trigger).to_be_focused()
    expect(page.locator("#id_name")).to_have_value(product.name)


def test_resizing_the_preview_preserves_edits_and_field_navigation(open_panel, product):
    page = open_panel(change_form(product))
    page.fill("#id_name", "An unsaved name")
    page.locator("[data-snapshot-open]").click()
    expect(page.locator("[data-snapshot-status]")).to_contain_text("آماده‌ست")
    page.set_viewport_size({"width": 390, "height": 900})
    page.locator("[data-snapshot-field=name]").click()
    panel = page.locator(".snapshot-panel")
    expect(panel).to_have_attribute("role", "region")
    assert 65 <= panel.bounding_box()["y"] <= 85
    expect(page.frame_locator(".snapshot-panel iframe").locator(".pdp__title")).to_have_text(
        "An unsaved name"
    )
    page.set_viewport_size({"width": 1440, "height": 900})
    page.locator("#id_name").fill("Still editing")
    expect(page.locator("[data-snapshot-status]")).to_contain_text("فرم تغییر کرده")
    page.locator("[data-snapshot-close]").click()
    expect(page.locator("[data-snapshot-field=name]")).to_be_focused()
    expect(page.locator("#id_name")).to_have_value("Still editing")


@pytest.mark.parametrize("field", ["title", "sale_price", "customer_input_label", "max_quantity"])
def test_plan_field_preview_selects_its_own_plan_and_exact_destination(open_panel, product, field):
    Plan.objects.create(
        product=product,
        title="Second plan",
        sort_order=5,
        cost_price=1,
        sale_price=200000,
        max_quantity=3 if field == "max_quantity" else 1,
        requires_customer_input=field == "customer_input_label",
        customer_input_label="ایمیل خودتون" if field == "customer_input_label" else "",
    )
    page = open_panel(change_form(product))
    page.locator(f"[data-snapshot-field=plans-1-{field}]").click()
    frame = page.frame_locator(".snapshot-panel iframe")
    highlighted = frame.locator(".snapshot-highlight")
    expect(highlighted).to_be_visible()
    assert field in (highlighted.get_attribute("data-preview-field") or "").split()
    expect(frame.locator('[data-preview-row="plans-1"] input')).to_be_checked()
    position = highlighted.evaluate(
        "e=>({top:e.getBoundingClientRect().top,header:document.querySelector('.v-header').getBoundingClientRect().bottom})"
    )
    assert position["top"] >= position["header"] + 6


def test_field_link_refreshes_edited_values_before_highlighting(open_panel, product):
    page = open_panel(change_form(product))
    page.locator("[data-snapshot-open]").click()
    expect(page.locator("[data-snapshot-status]")).to_contain_text("آماده‌ست")
    page.fill("#id_short_description", "این متن تازه باید همین حالا دیده بشه.")
    page.locator("[data-snapshot-field=short_description]").click()
    frame = page.frame_locator(".snapshot-panel iframe")
    expect(frame.locator(".snapshot-highlight")).to_have_text(
        "این متن تازه باید همین حالا دیده بشه."
    )
    expect(page.locator("[data-snapshot-status]")).to_contain_text("آماده‌ست")


@pytest.mark.parametrize("field", ["short_description", "refund_note", "usage_notes", "renewable"])
def test_empty_field_preview_marks_its_real_position(open_panel, product, field):
    page = open_panel(change_form(product))
    page.locator(f"[data-snapshot-field={field}]").click()
    frame = page.frame_locator(".snapshot-panel iframe")
    placeholder = frame.locator(".snapshot-placeholder.snapshot-highlight")
    expect(placeholder).to_be_visible()
    expect(placeholder).to_have_attribute("data-preview-field", field)
    expect(placeholder).to_contain_text("اینجا")
    region = (
        "#before-buying"
        if field in {"refund_note", "usage_notes"}
        else ("[data-preview-target=facts]" if field == "renewable" else ".pdp__main")
    )
    expect(frame.locator(region).locator(".snapshot-placeholder")).to_have_count(1)


def test_every_product_field_link_has_a_visible_specific_destination(open_panel, product):
    with_sections_and_a_question(product)
    page = open_panel(change_form(product))
    page.locator("#product_form details").evaluate_all("es=>es.forEach(e=>e.open=true)")
    frame = page.frame_locator(".snapshot-panel iframe")
    checked = 0
    for button in page.locator("[data-snapshot-field]").all():
        if not button.is_visible():
            continue
        name = button.get_attribute("data-snapshot-field")
        field = re.sub(r"^(plans|sections|specs|questions)-\d+-", "", name)
        field = re.sub(r"_[01]$", "", field)
        button.click()
        expect(frame.locator(".snapshot-highlight")).to_have_attribute(
            "data-preview-field", re.compile(rf"(^|\s){field}(\s|$)")
        )
        expect(frame.locator(".snapshot-highlight")).to_be_visible()
        checked += 1
    assert checked >= 35


def test_invalid_field_inspection_clears_the_previous_highlight(open_panel, product):
    page = open_panel(change_form(product))
    page.locator("[data-snapshot-field=name]").click()
    frame = page.frame_locator(".snapshot-panel iframe")
    expect(frame.locator(".pdp__title.snapshot-highlight")).to_be_visible()
    page.fill("#id_name", "")
    page.fill("#id_short_description", "تغییر ذخیره‌نشده")
    page.locator("[data-snapshot-field=short_description]").click()
    expect(page.locator("[data-snapshot-errors] [data-field=id_name]")).to_be_visible()
    expect(frame.locator(".snapshot-highlight")).to_have_count(0)


def test_existing_lazy_section_image_is_not_reported_as_empty(open_panel, product):
    ProductSection.objects.create(
        product=product,
        kind="benefits",
        body="توضیح محصول.\n\n" * 200,
        image="sections/inspection.png",
        image_alt="نمونه‌ی تنظیمات",
    )
    page = open_panel(change_form(product))
    page.route(
        "**/media/sections/inspection.png",
        lambda route: route.fulfill(
            content_type="image/png",
            body=b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a7gAAAABJRU5ErkJggg=="
            ),
        ),
    )
    page.locator("[data-snapshot-open]").click()
    expect(page.locator("[data-snapshot-status]")).to_contain_text("آماده‌ست")
    page.locator("[data-snapshot-field=sections-0-image]").click()
    frame = page.frame_locator(".snapshot-panel iframe")
    expect(frame.locator("img.pdp__img.snapshot-highlight")).to_be_visible()
    expect(frame.locator(".snapshot-placeholder")).to_have_count(0)


def test_blank_new_content_row_marks_the_content_position(open_panel, product):
    page = open_panel(change_form(product))
    page.click("#sections-group a.add-row")
    page.locator("[data-snapshot-field=sections-0-body]").click()
    frame = page.frame_locator(".snapshot-panel iframe")
    expect(frame.locator("[data-preview-target=sections] .snapshot-placeholder")).to_be_visible()
    expect(frame.locator(".snapshot-highlight")).to_have_attribute("data-preview-field", "body")


@pytest.mark.parametrize("width", [390, 1440])
def test_empty_category_menu_position_is_a_placeholder(open_panel, product, width):
    page = open_panel(f"/admin/catalog/category/{product.category_id}/change/", width=width)
    page.locator("[data-snapshot-field=sort_order]").click()
    frame = page.frame_locator(".snapshot-panel iframe")
    expect(frame.locator("[data-preview-target=navigation] .snapshot-placeholder")).to_be_visible()


@pytest.mark.parametrize("field", ["max_quantity", "customer_input_label"])
def test_deleted_plan_does_not_inspect_another_plans_shared_controls(open_panel, product, field):
    first = product.plans.get()
    first.max_quantity = 3 if field == "max_quantity" else 1
    first.requires_customer_input = field == "customer_input_label"
    first.customer_input_label = "ایمیل پلن اول" if field == "customer_input_label" else ""
    first.save()
    Plan.objects.create(
        product=product,
        title="Second plan",
        sort_order=5,
        cost_price=1,
        sale_price=200000,
        max_quantity=first.max_quantity,
        requires_customer_input=first.requires_customer_input,
        customer_input_label="ایمیل پلن دوم" if field == "customer_input_label" else "",
    )
    page = open_panel(change_form(product))
    page.locator(f"[data-snapshot-field=plans-1-{field}]").click()
    frame = page.frame_locator(".snapshot-panel iframe")
    expect(frame.locator('[data-preview-row="plans-1"] input')).to_be_checked()
    page.locator("#id_plans-1-DELETE").locator("xpath=ancestor::label").click()
    page.locator("[data-snapshot-refresh]").click()
    expect(frame.locator('[data-preview-row="plans-1"]')).to_have_count(0)
    expect(frame.locator(".snapshot-placeholder.snapshot-highlight")).to_have_attribute(
        "data-preview-field", field
    )
    expect(frame.locator(".snapshot-placeholder.snapshot-highlight")).to_contain_text("حذف")


@pytest.mark.parametrize(
    "prefix,index,field,boundary",
    [
        ("sections", 2, "body", "#before-buying"),
        ("questions", 1, "question", "#delivery"),
    ],
)
def test_blank_rows_use_the_same_position_as_completed_rows(
    open_panel, product, prefix, index, field, boundary
):
    with_sections_and_a_question(product)
    page = open_panel(change_form(product))
    page.click(f"#{prefix}-group a.add-row")
    page.locator(f"[data-snapshot-field={prefix}-{index}-{field}]").click()
    frame = page.frame_locator(".snapshot-panel iframe")
    placeholder = frame.locator(".snapshot-placeholder.snapshot-highlight")
    expect(placeholder).to_be_visible()
    assert placeholder.evaluate(
        "(e,s)=>Boolean(e.compareDocumentPosition(document.querySelector(s)) & Node.DOCUMENT_POSITION_FOLLOWING)",
        boundary,
    )


def test_faq_pairs_and_empty_markers_use_consistent_definition_groups(open_panel, product):
    with_sections_and_a_question(product)
    page = open_panel(change_form(product))
    page.locator("[data-snapshot-field=refund_note]").click()
    frame = page.frame_locator(".snapshot-panel iframe")
    expect(frame.locator(".snapshot-placeholder.snapshot-highlight")).to_be_visible()
    for faq in frame.locator("dl.faq").all():
        assert faq.evaluate("e=>[...e.children].filter(c=>!c.hidden).every(c=>c.tagName==='DIV')")
