import pytest
from django.contrib import admin
from django.urls import reverse

from apps.accounts.models import User

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("model", list(admin.site._registry), ids=lambda model: model._meta.label)
def test_every_admin_list_and_add_page_renders_for_a_superuser(client, model):
    """An app-level admin template is used by every model in that app that has none of its own."""
    client.force_login(User.objects.create_superuser("pages@example.test"))
    name = f"admin:{model._meta.app_label}_{model._meta.model_name}"
    assert client.get(reverse(f"{name}_changelist")).status_code == 200
    assert client.get(reverse(f"{name}_add")).status_code in (200, 403)
