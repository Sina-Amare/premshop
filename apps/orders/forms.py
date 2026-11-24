import re
from typing import cast
from unicodedata import normalize

from django import forms
from django.core.exceptions import ValidationError

# A mobile number has at least ten digits; a price or a date written with separators has fewer.
_CONTACT = re.compile(r"@|\d(?:[\W_]*\d){9}")
_RATING_ERROR = "امتیاز خریدتان را از ۱ تا ۵ انتخاب کنید."


class ReviewForm(forms.Form):
    rating = forms.IntegerField(
        min_value=1,
        max_value=5,
        error_messages=dict.fromkeys(
            ("required", "invalid", "min_value", "max_value"), _RATING_ERROR
        ),
    )
    review_text = forms.CharField(
        required=False,
        max_length=1000,
        error_messages={"max_length": "تا ۱۰۰۰ کاراکتر می‌توانید بنویسید."},
    )

    def clean_review_text(self) -> str:
        text = cast(str, self.cleaned_data["review_text"])
        if _CONTACT.search(normalize("NFKC", text)):
            raise ValidationError("پیش از ثبت نظر، ایمیل یا شماره‌ی موبایل را از متن بردارید.")
        return text
