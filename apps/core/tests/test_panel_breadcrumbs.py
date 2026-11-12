import re

import pytest
from django.conf import settings

from apps.accounts.models import User

pytestmark = pytest.mark.django_db

# The first crumb of the header the panel's header_title.html draws.
CRUMB = re.compile(r'min-w-0 truncate tracking-tight">\s*(.*?)\s*</', re.S)


def test_every_sidebar_page_names_its_sidebar_group_as_its_breadcrumbs_root(client):
    client.force_login(User.objects.create_superuser("crumbs@example.test"))
    pages = 0
    for group in settings.UNFOLD["SIDEBAR"]["navigation"]:
        for item in group["items"]:
            response = client.get(str(item["link"]))
            assert response.status_code == 200, item["link"]
            root = CRUMB.search(response.content.decode())
            assert root and root[1] == str(group["title"]), (item["link"], root and root[1])
            pages += 1
    assert pages >= 10, "positive control: the sidebar's pages were walked"
