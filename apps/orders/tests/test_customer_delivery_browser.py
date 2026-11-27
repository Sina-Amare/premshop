import ipaddress
import ssl
from datetime import UTC, datetime, timedelta
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlsplit

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from django.conf import settings
from django.test import Client
from django.urls import reverse
from playwright.sync_api import expect

from apps.core.tests.test_accessibility_browser import AXE, RUN_AXE, SERIOUS, STYLED
from apps.orders import models
from apps.orders.tests.test_customer_delivery import code_from_mail, delivered, verified
from apps.orders.tests.test_input_clock import operator as operator

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


@pytest.fixture
def https_server(live_server, settings, tmp_path):
    """TLS in front of Django, including its secure-origin CSRF check."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    upstream = urlsplit(live_server.url)

    class Proxy(BaseHTTPRequestHandler):
        def forward(self):
            transport = HTTPConnection(upstream.hostname, upstream.port, timeout=20)
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            headers = {**dict(self.headers), "X-Forwarded-Proto": "https"}
            try:
                transport.request(self.command, self.path, body=body, headers=headers)
                response = transport.getresponse()
                content = response.read()
                self.send_response(response.status)
                for key, value in response.getheaders():
                    if key.lower() not in ("content-length", "connection", "transfer-encoding"):
                        self.send_header(key, value)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            finally:
                transport.close()

        do_GET = do_POST = forward

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    settings.SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def customer_context(browser, url, buyer, width):
    client = Client()
    client.force_login(buyer)
    context = browser.new_context(
        viewport={"width": width, "height": 1000}, ignore_https_errors=True
    )
    context.add_cookies(
        [{"name": settings.SESSION_COOKIE_NAME, "value": client.session.session_key, "url": url}]
    )
    return context, client.session.session_key


def capture(page, url, landmark, name, width, tmp_path):
    assert page.url == url
    expect(page.locator(landmark)).to_be_visible()
    page.evaluate("document.fonts.ready")
    assert page.evaluate(STYLED), "A page with missing styles cannot prove its layout."
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    page.screenshot(path=str(tmp_path / f"{name}-{width}.png"), full_page=True)


def scan(page):
    page.add_script_tag(path=str(AXE))
    failures = [row for row in page.evaluate(RUN_AXE)["violations"] if row["impact"] in SERIOUS]
    assert not failures, [
        (row["id"], [node["target"] for node in row["nodes"]]) for row in failures
    ]


@pytest.mark.parametrize("width", [390, 1440])
def test_https_code_and_keyboard_reveal_is_private_and_accessible(
    browser, https_server, buyer, plan, operator, width, tmp_path, mailoutbox
):
    order, item = delivered(buyer, plan, operator)
    type(buyer).objects.filter(pk=buyer.pk).update(phone="09123456789")
    models.Order.objects.filter(pk=order.pk).update(placement_phone="09123456789")
    context, _ = customer_context(browser, https_server, buyer, width)
    page = context.new_page()
    detail = https_server + reverse("order-detail", args=[order.order_number])
    reveal = https_server + reverse("order-delivery-reveal", args=[order.order_number, item.pk])
    try:
        page.goto(detail)
        capture(page, detail, "#order-delivery", "masked", width, tmp_path)
        expect(page.locator("[data-delivery-recovery]")).to_have_count(0)
        expect(page.get_by_role("button", name="فرستادن کد با پیامک")).to_contain_text(
            "۰۹۱۲•••۶۷۸۹"
        )
        scan(page)
        assert "delivered-secret" not in page.content()
        # A reload would drop this, so its survival proves each step happened in place.
        page.evaluate("window.stayed = true")
        page.get_by_role("button", name="فرستادن کد به ایمیل").focus()
        page.keyboard.press("Enter")
        # The code box is on the page before the send, so the sent notice is the evidence.
        sent = page.locator("#order-delivery [role=status]")
        expect(sent).to_have_text("کد را به ایمیل این سفارش فرستادیم.")
        expect(sent).to_be_focused()
        card = detail
        assert page.url == card
        assert page.evaluate("window.stayed") is True
        code = code_from_mail()
        cells = page.locator("#order-delivery .otp__cell")
        cells.first.focus()
        page.keyboard.type("000000" if code != "000000" else "111111")
        expect(page.locator("#delivery-code-error")).to_contain_text("کد اشتباه است")
        capture(page, card, "#order-delivery", "code-error", width, tmp_path)
        expect(cells.first).to_be_enabled()
        expect(cells.first).to_have_value("")
        # The section is replaced right after the row turns green, so the green is recorded as it happens.
        page.evaluate(
            """() => new MutationObserver(() => {
                 if (document.querySelectorAll('#code-cells .is-ok').length === 6) sessionStorage.green = '1';
               }).observe(document.getElementById('code-cells'), {subtree: true, attributes: true})"""
        )
        cells.first.focus()
        page.keyboard.type(code)
        expect(page.get_by_role("button", name="نمایش اطلاعات تحویل", exact=True)).to_be_visible()
        assert page.evaluate("sessionStorage.green") == "1"
        confirmed = page.locator("#order-delivery .form-ok")
        expect(confirmed).to_contain_text("کد تأیید شد.")
        expect(confirmed).to_be_focused()
        assert page.evaluate("window.stayed") is True
        capture(page, card, "[data-delivery-masked]", "confirmed", width, tmp_path)
        with page.expect_response(lambda response: response.url == reveal) as seen:
            page.get_by_role("button", name="نمایش اطلاعات تحویل", exact=True).click()
        response = seen.value
        assert "no-store" in response.headers["cache-control"]
        assert response.headers["content-encoding"] == "identity"
        assert response.headers["referrer-policy"] == "no-referrer"
        expect(page.locator("[data-customer-delivery]")).to_contain_text("delivered-secret")
        capture(page, reveal, "[data-customer-delivery]", "revealed", width, tmp_path)
        scan(page)
        assert models.CredentialAccessLog.objects.filter(source="customer").count() == 1
        page.get_by_role("link", name="بازگشت به سفارش", exact=True).click()
        expect(page.locator("[data-delivery-masked]")).to_be_visible()
        assert "delivered-secret" not in page.content()
        denied = context.request.post(reveal, data={})
        assert denied.status == 403
        assert "no-store" in denied.headers["cache-control"]
        assert denied.headers["content-encoding"] == "identity"
    finally:
        context.close()


def test_without_javascript_the_code_still_posts_and_the_page_loads(
    browser, https_server, buyer, plan, operator, mailoutbox
):
    order, _ = delivered(buyer, plan, operator)
    client = Client()
    client.force_login(buyer)
    context = browser.new_context(java_script_enabled=False, ignore_https_errors=True)
    context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": client.session.session_key,
                "url": https_server,
            }
        ]
    )
    page = context.new_page()
    detail = https_server + reverse("order-detail", args=[order.order_number])
    try:
        page.goto(detail)
        with page.expect_navigation():
            page.get_by_role("button", name="فرستادن کد به ایمیل").click()
        assert page.url == detail + "#order-delivery"
        expect(page.locator("#order-delivery [role=status]")).to_have_text(
            "کد را به ایمیل این سفارش فرستادیم."
        )
        assert len(mailoutbox) == 1
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1440])
def test_code_email_preview_uses_the_rtl_template(browser, https_server, width, tmp_path, settings):
    settings.DEBUG = True
    context = browser.new_context(
        viewport={"width": width, "height": 1000}, ignore_https_errors=True
    )
    page = context.new_page()
    url = https_server + "/dev/emails/delivery_code/"
    try:
        page.goto(url)
        expect(
            page.get_by_role("heading", name="کد نمایش اطلاعات تحویل", exact=True)
        ).to_be_visible()
        expect(page.locator("body")).to_contain_text("539271")
        assert page.locator("html").get_attribute("dir") == "rtl"
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.screenshot(path=str(tmp_path / f"email-{width}.png"), full_page=True)
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1440])
@pytest.mark.parametrize("state", ["unavailable", "deleted", "unreadable", "recovery", "long"])
def test_customer_delivery_states_fit_and_do_not_leak(
    browser, https_server, buyer, plan, operator, width, state, tmp_path, settings, mailoutbox
):
    order, item = delivered(buyer, plan, operator)
    context, session = customer_context(browser, https_server, buyer, width)
    page = context.new_page()
    detail = https_server + reverse("order-detail", args=[order.order_number])
    if state == "recovery":
        models.Order.objects.filter(pk=order.pk).update(placement_email="")
    else:
        verified(order, buyer, session)
    if state == "unavailable":
        models.OrderItem.objects.filter(pk=item.pk).update(
            status="CANCELLED", cancel_reason="warranty_refund"
        )
    elif state == "deleted":
        models.Delivery.objects.filter(order_item=item).delete()
    elif state == "unreadable":
        settings.FIELD_ENCRYPTION_KEY = ""
    elif state == "long":
        field = models.DeliveryField.objects.get(delivery__order_item=item)
        field.title = "عنوان بسیار طولانی برای اطلاعات حساب تحویل‌شده " * 2
        field.value = "long-secret-" * 100
        field.save()
    try:
        page.goto(detail)
        if state in ("unreadable", "long"):
            page.get_by_role("button", name="نمایش اطلاعات تحویل", exact=True).click()
            url = https_server + reverse(
                "order-delivery-reveal", args=[order.order_number, item.pk]
            )
            landmark = (
                "[data-customer-delivery]" if state == "long" else "[data-delivery-unreadable]"
            )
        else:
            url, landmark = detail, f"[data-delivery-{state}]"
        capture(page, url, landmark, state, width, tmp_path)
        scan(page)
        assert "delivered-secret" not in page.content()
        if state == "unavailable":
            expect(
                page.get_by_role("button", name="نمایش اطلاعات تحویل", exact=True)
            ).to_have_count(0)
    finally:
        context.close()
