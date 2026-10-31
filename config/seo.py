"""Crawler entry points composed from each app's public pages."""

from xml.etree.ElementTree import Element, SubElement, tostring

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.urls import reverse

from apps.catalog.models import Category, Product
from apps.cms.models import Page


def sitemap(request: HttpRequest) -> HttpResponse:
    paths = [reverse("home"), reverse("faq")]
    paths += [reverse("category", args=[category.slug]) for category in Category.objects.all()]
    paths += [product.get_absolute_url() for product in Product.objects.public_pages()]
    paths += [page.get_absolute_url() for page in Page.objects.filter(is_published=True)]
    root = Element("urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")
    for path in paths:
        SubElement(SubElement(root, "url"), "loc").text = settings.SITE_URL + path
    return HttpResponse(
        tostring(root, encoding="utf-8", xml_declaration=True), content_type="application/xml"
    )


def robots(request: HttpRequest) -> HttpResponse:
    agents = (
        "*",
        "Googlebot",
        "Bingbot",
        "GPTBot",
        "OAI-SearchBot",
        "ChatGPT-User",
        "ClaudeBot",
        "Google-Extended",
        "CCBot",
        "PerplexityBot",
    )
    groups = [f"User-agent: {agent}\nAllow: /\n" for agent in agents]
    return HttpResponse(
        "\n".join(groups) + f"\nSitemap: {settings.SITE_URL}/sitemap.xml\n",
        content_type="text/plain",
    )
