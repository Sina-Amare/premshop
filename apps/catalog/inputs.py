"""Non-secret activation input, validated before encryption."""

import re
import unicodedata

from django.core.exceptions import ValidationError
from django.core.validators import validate_email


def normalize_customer_input(kind: str, value: str | None) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str) or len(value) > 2000 or "\x00" in value:
        raise ValidationError("اطلاعات فعال‌سازی معتبر نیست.")
    value = unicodedata.normalize("NFC", value.strip())
    if kind == "none":
        if value:
            raise ValidationError("این پلن به اطلاعات فعال‌سازی نیاز ندارد.")
        return ""
    if kind == "email":
        try:
            validate_email(value)
        except ValidationError:
            raise ValidationError("ایمیل اکانت را درست وارد کنید.") from None
        if len(value) > 254:
            raise ValidationError("ایمیل اکانت را درست وارد کنید.")
        local, domain = value.rsplit("@", 1)
        return f"{local}@{domain.lower()}"
    if kind == "username":
        value = value.removeprefix("@")
        if not value or len(value) > 150 or not re.fullmatch(r"[\w.\-#\u200c]+", value):
            raise ValidationError("نام کاربری را بدون فاصله، لینک یا ایمیل وارد کنید.")
        return value
    raise ValidationError("نوع اطلاعات فعال‌سازی معتبر نیست.")
