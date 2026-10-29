from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils.cache import patch_cache_control, patch_vary_headers

from apps.cms.models import Page


def page(request: HttpRequest, slug: str) -> HttpResponse:
    may_preview = request.user.is_staff and request.user.has_perm("cms.view_page")
    pages = Page.objects.all() if may_preview else Page.objects.filter(is_published=True)
    obj = get_object_or_404(pages, slug=slug)
    response = render(request, "cms/page.html", {"page": obj, "preview": not obj.is_published})
    # A shared cache must distinguish staff previews from anonymous requests.
    patch_vary_headers(response, ("Cookie",))
    if not obj.is_published:
        patch_cache_control(response, private=True, no_store=True)
        response["X-Robots-Tag"] = "noindex, nofollow"
    return response
