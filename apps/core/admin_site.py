"""Unfold's admin site, with the search palette naming things in the panel's language."""

from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.contrib.admin import sites
from django.http import HttpRequest, HttpResponse
from unfold.apps import DefaultAppConfig
from unfold.dataclasses import SearchResult
from unfold.sites import UnfoldAdminSite

from apps.core.sessions import login_session


class PanelSite(UnfoldAdminSite):
    def login(
        self, request: HttpRequest, extra_context: dict[str, Any] | None = None
    ) -> HttpResponse:
        with login_session(request):
            return super().login(request, extra_context)

    def _search_models(
        self,
        request: HttpRequest,
        app_list: list[dict[str, Any]],
        search_term: str,
        allowed_models: list[str] | None = None,
    ) -> list[SearchResult]:
        """Unfold describes a found item as «<app label> - <model>», and the label
        is the app's code name, so every product in the Persian palette was
        «Catalog - محصول». The app's own name takes its place."""
        found = super()._search_models(request, app_list, search_term, allowed_models)
        names = {app["app_label"].capitalize(): str(app["name"]) for app in app_list}
        for result in found:
            label, _, model = result.description.partition(" - ")
            result.description = f"{names.get(label, label)} - {model}"
        return found


class PanelAdminConfig(DefaultAppConfig):
    """Unfold's app, installing this site where Unfold installs its own."""

    def ready(self) -> None:
        admin.site = sites.site = PanelSite()
