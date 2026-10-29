from django.http import HttpRequest

from apps.cms.models import Page


def navigation(request: HttpRequest) -> dict[str, object]:
    return {"legal_pages": Page.objects.filter(is_published=True).only("slug", "title")}
