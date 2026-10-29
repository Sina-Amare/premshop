from unittest.mock import patch

from django.db import DatabaseError
from django.db.models.query import QuerySet


def test_missing_page_still_renders_when_legal_navigation_cannot_load(client):
    with patch.object(QuerySet, "_fetch_all", side_effect=DatabaseError("unavailable")):
        response = client.get("/this-page-does-not-exist/")

    assert response.status_code == 404
    assert "این صفحه رو پیدا نکردیم" in response.content.decode()
    assert response.context["legal_pages"] == []
