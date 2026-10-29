"""Encrypted text with an independent key and explicit key rotation."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import FieldError, ImproperlyConfigured
from django.db import models


def key_id(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:8]


def field_keys() -> tuple[str, dict[str, Fernet]]:
    current = settings.FIELD_ENCRYPTION_KEY
    keys = [current, *settings.FIELD_ENCRYPTION_KEY_FALLBACKS]
    try:
        if not current or len({key_id(key) for key in keys}) != len(keys):
            raise ValueError
        return key_id(current), {key_id(key): Fernet(key.encode()) for key in keys}
    except ValueError, TypeError, AttributeError:
        raise ImproperlyConfigured("Configure distinct valid field encryption keys.") from None


class EncryptedTextField(models.TextField):
    def get_prep_value(self, value: Any) -> str | None:
        value = super().get_prep_value(value)
        if value in (None, ""):
            return value
        current, keys = field_keys()
        return f"{current}${keys[current].encrypt(value.encode()).decode()}"

    def from_db_value(self, value: str | None, expression: Any, connection: Any) -> str | None:
        if value in (None, ""):
            return value
        try:
            prefix, token = value.split("$", 1)
            if not re.fullmatch(r"[a-f0-9]{8}", prefix):
                raise ValueError
            _, keys = field_keys()
            if prefix not in keys:
                raise ImproperlyConfigured(f"Field encryption key {prefix} is missing.")
            return keys[prefix].decrypt(token.encode()).decode()
        except ValueError, InvalidToken, UnicodeError:
            raise ImproperlyConfigured("Cannot decrypt encrypted field.") from None

    def get_lookup(self, lookup_name: str) -> Any:
        if lookup_name != "isnull":
            raise FieldError("Encrypted fields support only isnull lookups.")
        return super().get_lookup(lookup_name)

    def get_transform(self, lookup_name: str) -> Any:
        raise FieldError("Encrypted fields do not support transforms.")
