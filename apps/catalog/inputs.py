"""Non-secret activation input, validated before encryption."""

import re
import unicodedata

from django.core.exceptions import ValidationError
from django.core.validators import validate_email


def normalize_customer_input(kind: str, value: str | None) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str) or len(value) > 2000 or "\x00" in value:
        raise ValidationError(
            "اطلاعات فعال‌سازی که وارد کردید درست نیست و لازم است آن را دوباره و به‌درستی وارد کنید."
        )
    value = unicodedata.normalize("NFC", value.strip())
    if kind == "none":
        if value:
            raise ValidationError(
                "این گزینه‌ی خرید به اطلاعات فعال‌سازی نیازی ندارد و کادر آن باید خالی بماند."
            )
        return ""
    if kind == "email":
        try:
            validate_email(value)
        except ValidationError:
            raise ValidationError(
                "ایمیل اکانتی که وارد کردید درست نیست و باید نشانی کامل ایمیل اکانت را دوباره وارد کنید."
            ) from None
        if len(value) > 254:
            raise ValidationError(
                "ایمیل اکانتی که وارد کردید درست نیست و باید نشانی کامل ایمیل اکانت را دوباره وارد کنید."
            )
        local, domain = value.rsplit("@", 1)
        return f"{local}@{domain.lower()}"
    if kind == "username":
        value = value.removeprefix("@")
        if not value or len(value) > 150 or not re.fullmatch(r"[\w.\-#\u200c]+", value):
            raise ValidationError(
                "نام کاربری اکانت را بدون فاصله، لینک یا ایمیل وارد کنید، چون فقط خود نام کاربری برای فعال‌سازی لازم است."
            )
        return value
    raise ValidationError("نوع اطلاعات فعال‌سازی معتبر نیست.")
