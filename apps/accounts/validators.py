"""Django's password validators, saying the same thing in this shop's words.

Django ships Persian translations of its validator messages, but they say
«گذرواژه» where every screen here says «رمز عبور». The hint under the field, the
error beside it and the confirmation above it have to use one word for the
thing, or the page reads as two systems bolted together. The rules themselves
are Django's, untouched — only the sentence a customer sees changes.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError

from apps.accounts.otp import normalize_digits
from apps.core.formatting import to_persian_digits


def normalize_mobile_phone(value: str) -> str:
    if not isinstance(value, str) or len(value) > 32:
        raise ValidationError("شماره‌ی موبایل درست نیست.")
    typed = normalize_digits(value)
    # Contact cards often carry invisible direction marks on RTL phones.
    number = "".join(ch for ch in typed if unicodedata.category(ch) != "Cf")
    if not number:
        return ""
    mobile = re.fullmatch(r"(?:\+98|0098|98)?0?(9[0-9]{9})", re.sub(r"[\s()-]", "", number))
    if not mobile:
        raise ValidationError("شماره‌ی موبایل باید ۱۱ رقم باشد و با ۰۹ شروع شود.")
    return "0" + mobile.group(1)


class MinimumLengthValidator(password_validation.MinimumLengthValidator):
    def validate(self, password: str, user: Any = None) -> None:
        if len(password) < self.min_length:
            raise ValidationError(
                f"رمز عبور باید حداقل {to_persian_digits(self.min_length)} کاراکتر باشد.",
                code="password_too_short",
            )

    def get_help_text(self) -> str:
        return f"رمز عبور باید حداقل {to_persian_digits(self.min_length)} کاراکتر باشد."


class NumericPasswordValidator(password_validation.NumericPasswordValidator):
    def validate(self, password: str, user: Any = None) -> None:
        if password.isdigit():
            raise ValidationError("رمز عبور نباید فقط عدد باشد.", code="password_entirely_numeric")

    def get_help_text(self) -> str:
        return "رمز عبور نباید فقط عدد باشد."


class CommonPasswordValidator(password_validation.CommonPasswordValidator):
    def validate(self, password: str, user: Any = None) -> None:
        try:
            super().validate(password, user)
        except ValidationError as exc:
            raise ValidationError("این رمز عبور خیلی رایج است.", code=exc.code) from None

    def get_help_text(self) -> str:
        return "رمز عبور نباید رایج باشد."


class UserAttributeSimilarityValidator(password_validation.UserAttributeSimilarityValidator):
    def validate(self, password: str, user: Any = None) -> None:
        try:
            super().validate(password, user)
        except ValidationError as exc:
            raise ValidationError("رمز عبور خیلی شبیه ایمیل شماست.", code=exc.code) from None

    def get_help_text(self) -> str:
        return "رمز عبور نباید شبیه ایمیل شما باشد."
