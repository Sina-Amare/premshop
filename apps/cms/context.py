from django.http import HttpRequest

from apps.cms.models import Page


def navigation(request: HttpRequest) -> dict[str, object]:
    try:
        pages = list(Page.objects.filter(is_published=True).only("slug", "title"))
    except Exception:  # noqa: BLE001 — navigation must not break an error page.
        pages = []
    return {"legal_pages": pages}
