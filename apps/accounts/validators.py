"""What a customer types, in one form: digits, the mobile number, and Django's
password validators in this shop's words.

Django ships Persian translations of its validator messages, but they say
«گذرواژه» where every screen here says «رمز عبور». The rules themselves are
Django's, untouched; only the sentence changes. Staff are the only accounts with
a password, and they meet these on the panel's password page.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError

from apps.core.formatting import to_persian_digits

PERSIAN_TO_ASCII_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def normalize_digits(value: str) -> str:
    """Persian ۰-۹ (and Arabic-Indic ٠-٩) to ASCII.

    A customer who copies a code or a number as they were shown it, in Persian
    digits, would otherwise be told, correctly and uselessly, that it is wrong.
    """
    return (value or "").strip().translate(PERSIAN_TO_ASCII_DIGITS)


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
